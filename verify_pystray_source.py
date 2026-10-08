"""Verify the upstream source supplied with the installed tray library."""

import hashlib
import importlib.metadata
from pathlib import Path
import zipfile


SOURCE_NAME = "pystray-0.19.5-source.zip"
SOURCE_SHA256 = "99fb0f7ec3a551e6c96d3946ecfce0e9dc04bac7994fa64ae348921a634af70e"
SOURCE_PREFIX = "pystray-0.19.5/lib/"


def verify_source(project_root=None):
    root = Path(project_root) if project_root else Path(__file__).resolve().parent
    archive = root / "third_party" / SOURCE_NAME
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("The bundled pystray source archive checksum does not match.")
    distribution = importlib.metadata.distribution("pystray")
    if distribution.version != "0.19.5":
        raise RuntimeError("Update the source bundle and notices before using another pystray version.")
    package = Path(distribution.locate_file("pystray"))
    installed = {
        "pystray/" + path.relative_to(package).as_posix(): path
        for path in package.rglob("*.py")
    }
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        expected = {
            name[len(SOURCE_PREFIX):]: name for name in names
            if name.startswith(SOURCE_PREFIX + "pystray/") and name.endswith(".py")
        }
        if not expected or installed.keys() != expected.keys():
            raise RuntimeError("Installed pystray files differ from the bundled source inventory.")
        for name, path in installed.items():
            # Wheels installed on Windows may use different line endings.
            actual = path.read_bytes().replace(b"\r\n", b"\n")
            original = source.read(expected[name]).replace(b"\r\n", b"\n")
            if actual != original:
                raise RuntimeError(f"Installed pystray source differs: {name}")
        for name in ("COPYING", "COPYING.LGPL", "setup.py", "setup.cfg"):
            if "pystray-0.19.5/" + name not in names:
                raise RuntimeError(f"Missing pystray build or license file: {name}")
    print(f"Verified pystray source archive and {len(installed)} installed Python files.")


if __name__ == "__main__":
    verify_source()
