"""Windows startup regression tests. Every registry access uses an in-memory fake."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import marblescape_download as app


class FakeKey:
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3

    def __init__(self, folder):
        self.folder = folder
        self.values = {}
        self.operations = []
        self.fail_set = 0
        self.fail_delete = {}
        self.read_error = None

    def OpenKey(self, hive, key, access):
        if self.read_error:
            raise self.read_error
        if hive != self.HKEY_CURRENT_USER or key != app.WINDOWS_RUN_KEY:
            raise AssertionError("Unexpected registry key")
        self.operations.append(("open", access))
        return FakeKey()

    def CreateKeyEx(self, hive, key, access):
        return self.OpenKey(hive, key, access)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.operations.append(("set", name, kind, value))
        self.values[name] = (value, kind)
        if self.fail_set:
            self.fail_set -= 1
            raise PermissionError("Simulated write failure after mutation")

    def DeleteValue(self, key, name):
        self.operations.append(("delete", name))
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]
        if self.fail_delete.get(name, 0):
            self.fail_delete[name] -= 1
            raise PermissionError("Simulated deletion failure after mutation")

    def ExpandEnvironmentStrings(self, text):
        return text.replace("%APP_HOME%", str(self.folder)).replace("%PY_HOME%", r"C:\Python 3")


@unittest.skipUnless(os.name == "nt", "Windows command-line and registry feature")
class WindowsStartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(r"C:\Marble Scape")
        self.script = self.folder / "marblescape_download.py"
        self.default_config = self.folder / "marblescape_config.toml"
        self.registry = FakeRegistry(self.folder)
        self.stack.enter_context(patch.dict(sys.modules, {"winreg": self.registry}))
        self.stack.enter_context(patch.object(app, "SCRIPT_DIR", self.folder))
        self.stack.enter_context(patch.object(app, "__file__", str(self.script)))
        self.stack.enter_context(patch.object(app, "DEFAULT_CONFIG_PATH", self.default_config))
        self.stack.enter_context(patch.object(app, "ACTIVE_CONFIG_PATH", self.default_config))
        self.stack.enter_context(patch.object(app.sys, "executable", r"C:\Python 3\python.exe"))
        self.stack.enter_context(patch.object(app.sys, "frozen", False, create=True))
        self.stack.enter_context(patch.object(Path, "exists", return_value=True))
        self.log = self.stack.enter_context(patch.object(app, "log"))

    def command(self, target=None, config=None, extra=()):
        args = [str(target or self.folder / "marblescape.exe")]
        if config is not None:
            args.extend(["--config", str(config)])
        args.extend(extra)
        return subprocess.list2cmdline(args)

    def source_command(self, config=None, interpreter=None):
        args = app.get_application_launch_arguments()
        if interpreter is not None:
            args[0] = interpreter
        if config is not None:
            args.extend(["--config", str(config)])
        return subprocess.list2cmdline(args)

    def install_value(self, command, kind=None, name=None):
        self.registry.values[name or app.WINDOWS_RUN_VALUE_NAME] = (
            command, self.registry.REG_SZ if kind is None else kind)

    def writes(self):
        return [op for op in self.registry.operations if op[0] in {"set", "delete"}]

    def test_startup_quotes_paths_and_preserves_active_config_without_transient_flags(self):
        app.ACTIVE_CONFIG_PATH = Path(r"C:\Personal Images\custom config.toml")
        with patch.object(app.sys, "argv", [str(self.script), "--once", "--print-urls", "--config", "other.toml"]):
            command = app.get_windows_startup_command()
        args = app._parse_windows_startup_command(command)
        self.assertEqual(args, [r"C:\Python 3\pythonw.exe", str(self.script), "--config", str(app.ACTIVE_CONFIG_PATH)])
        self.assertNotIn("--once", command)
        self.assertNotIn("--print-urls", command)
        self.assertEqual(self.writes(), [])

    def test_source_python_fallback_and_restart_argument_contract_are_unchanged(self):
        with patch.object(Path, "exists", return_value=False):
            self.assertEqual(app.get_application_launch_arguments()[0], r"C:\Python 3\python.exe")
        argv = [str(self.script), "--config", "same.toml", "--once"]
        with patch.object(app.sys, "argv", argv):
            self.assertEqual(app.get_application_launch_arguments(True)[2:], argv[1:])
            self.assertEqual(len(app.get_application_launch_arguments()), 2)

    def test_frozen_command_contains_only_executable_and_config(self):
        executable = self.folder / "MarbleScape Dev.exe"
        with patch.object(app.sys, "frozen", True), patch.object(app.sys, "executable", str(executable)):
            command = app.get_windows_startup_command()
            self.assertEqual(app._parse_windows_startup_command(command),
                             [str(executable), "--config", str(self.default_config)])
            self.install_value(command)
            self.assertTrue(app.is_windows_startup_enabled())

    def test_enabled_matches_current_source_and_config_including_old_default_entry(self):
        self.assertFalse(app.is_windows_startup_enabled())
        self.install_value(self.source_command())
        self.assertTrue(app.is_windows_startup_enabled())
        self.install_value(self.source_command(config=self.default_config))
        self.assertTrue(app.is_windows_startup_enabled())
        self.install_value(app.get_windows_startup_command())
        self.assertTrue(app.is_windows_startup_enabled())
        app.ACTIVE_CONFIG_PATH = self.folder / "custom.toml"
        self.assertFalse(app.is_windows_startup_enabled())
        self.install_value(self.source_command(config=app.ACTIVE_CONFIG_PATH))
        self.assertTrue(app.is_windows_startup_enabled())
        self.assertEqual(self.writes(), [])

    def test_source_does_not_accept_old_exe_or_other_python_environment(self):
        self.install_value(self.command())
        self.assertFalse(app.is_windows_startup_enabled())
        self.install_value(self.source_command(interpreter=r"C:\Other Python\pythonw.exe"))
        self.assertFalse(app.is_windows_startup_enabled())
        for interpreter in (r"C:\Python 3\python.exe", r"C:\Python 3\pythonw.exe"):
            self.install_value(self.source_command(interpreter=interpreter))
            self.assertTrue(app.is_windows_startup_enabled())

    def test_frozen_does_not_accept_old_same_folder_binary_or_source(self):
        source_command = self.source_command()
        with patch.object(app.sys, "frozen", True), patch.object(app.sys, "executable", str(self.folder / "marblescape-updated.exe")):
            self.install_value(self.command())
            self.assertFalse(app.is_windows_startup_enabled())
            self.install_value(source_command)
            self.assertFalse(app.is_windows_startup_enabled())
            self.install_value(app.get_windows_startup_command())
            self.assertTrue(app.is_windows_startup_enabled())

    def test_foreign_stale_diagnostic_or_wrong_type_entries_do_not_count_as_enabled(self):
        commands = [
            self.command(Path(r"C:\Old Install\marblescape.exe")),
            self.command(config=self.folder / "other.toml"),
            self.command(extra=("--once",)), self.command(extra=("--validate-config",)),
            'cmd.exe /c "C:\\Marble Scape\\marblescape.exe"',
            self.command(Path(r"C:\Tools\other.exe"), config=self.script), "",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.install_value(command)
                self.assertFalse(app.is_windows_startup_enabled())
        self.install_value(b"binary", self.registry.REG_BINARY)
        self.assertFalse(app.is_windows_startup_enabled())

    def test_expand_sz_and_case_insensitive_windows_paths(self):
        command = '"%PY_HOME%\\PYTHON.EXE" "' + str(self.script).upper() + '"'
        self.install_value(command, self.registry.REG_EXPAND_SZ)
        self.assertTrue(app.is_windows_startup_enabled())

    def test_enable_and_disable_are_idempotent_and_leave_unrelated_values(self):
        self.registry.values["OtherApp"] = ("untouched", self.registry.REG_SZ)
        app.set_windows_startup_enabled(True)
        self.assertTrue(app.is_windows_startup_enabled())
        first_writes = len(self.writes())
        app.set_windows_startup_enabled(True)
        self.assertEqual(len(self.writes()), first_writes)
        app.set_windows_startup_enabled(False)
        self.assertFalse(app.is_windows_startup_enabled())
        second_writes = len(self.writes())
        app.set_windows_startup_enabled(False)
        self.assertEqual(len(self.writes()), second_writes)
        self.assertEqual(self.registry.values, {"OtherApp": ("untouched", self.registry.REG_SZ)})

    def test_disable_does_not_delete_a_foreign_registration(self):
        foreign = (self.command(Path(r"C:\Other Install\marblescape.exe")), self.registry.REG_EXPAND_SZ)
        self.registry.values[app.WINDOWS_RUN_VALUE_NAME] = foreign
        app.set_windows_startup_enabled(False)
        self.assertEqual(app.capture_windows_startup_state(), foreign)
        self.assertEqual(self.writes(), [])

    def test_snapshot_restore_preserves_foreign_value_and_type_exactly(self):
        foreign = ('"%APP_HOME%\\another.exe" --flag', self.registry.REG_EXPAND_SZ)
        self.registry.values[app.WINDOWS_RUN_VALUE_NAME] = foreign
        saved = app.capture_windows_startup_state()
        app.set_windows_startup_enabled(True)
        app.restore_windows_startup_state(saved)
        self.assertEqual(app.capture_windows_startup_state(), foreign)
        count = len(self.writes())
        app.restore_windows_startup_state(saved)
        self.assertEqual(len(self.writes()), count)
        app.restore_windows_startup_state(None)
        self.assertIsNone(app.capture_windows_startup_state())
        count = len(self.writes())
        app.restore_windows_startup_state(None)
        self.assertEqual(len(self.writes()), count)

    def test_write_failure_restores_previous_exact_state_or_absence(self):
        for original in (None, ('"%APP_HOME%\\old.exe"', self.registry.REG_EXPAND_SZ)):
            with self.subTest(original=original):
                self.registry.values.clear()
                if original is not None:
                    self.registry.values[app.WINDOWS_RUN_VALUE_NAME] = original
                self.registry.fail_set = 1
                with self.assertRaises(PermissionError):
                    app.set_windows_startup_enabled(True)
                self.assertEqual(app.capture_windows_startup_state(), original)

    def test_delete_failure_restores_existing_startup_value(self):
        app.set_windows_startup_enabled(True)
        saved = app.capture_windows_startup_state()
        self.registry.fail_delete[app.WINDOWS_RUN_VALUE_NAME] = 1
        with self.assertRaises(PermissionError):
            app.set_windows_startup_enabled(False)
        self.assertEqual(app.capture_windows_startup_state(), saved)

    def test_oversized_startup_command_fails_before_registry_mutation(self):
        app.ACTIVE_CONFIG_PATH = self.folder / ("x" * 250 + ".toml")
        with self.assertRaisesRegex(ValueError, "260"):
            app.set_windows_startup_enabled(True)
        self.assertEqual(self.writes(), [])

    def test_registry_read_error_is_not_reported_as_disabled(self):
        self.registry.read_error = PermissionError("read denied")
        with self.assertRaises(PermissionError):
            app.is_windows_startup_enabled()

if __name__ == "__main__":
    unittest.main()
