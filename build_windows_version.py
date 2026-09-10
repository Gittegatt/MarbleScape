"""Generate PyInstaller's Windows version resource from the application version."""

from pathlib import Path
import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

from app_version import VERSION


def write_version_resource(target):
    parts = tuple(int(part) for part in VERSION.split("."))
    if len(parts) != 3 or any(part < 0 or part > 65535 for part in parts):
        raise ValueError("A three-part numeric release version is required.")
    version = (*parts, 0)
    resource = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version, prodvers=version, mask=0x3F, flags=0,
            OS=0x40004, fileType=1, subtype=0, date=(0, 0),
        ),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("FileDescription", "MarbleScape satellite wallpaper"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "marblescape"),
                StringStruct("OriginalFilename", "marblescape.exe"),
                StringStruct("ProductName", "MarbleScape"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    Path(target).write_text(str(resource), encoding="utf-8")


if __name__ == "__main__":
    write_version_resource(sys.argv[1])
