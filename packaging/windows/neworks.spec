# PyInstaller spec for the Windows release: one self-contained neworks.exe, no Python needed.
#
#   pyinstaller --clean --noconfirm --distpath dist --workpath build/pyinstaller packaging/windows/neworks.spec
#
# Run from the repository root. It also builds on Linux (a Linux binary), which is how the spec is
# checked without a Windows machine; the shipped .exe comes from .github/workflows/release-windows.yml.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]   # SPECPATH: the directory holding this file
VERSION = re.search(r'^__version__ = "([^"]+)"', (ROOT / "newedenworks" / "__init__.py").read_text(), re.M)[1]

# Version resource: what Explorer shows under Properties > Details. Windows-only metadata.
version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32 import versioninfo as vi

    numbers = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:3]) + (0,)
    strings = [vi.StringStruct(key, value) for key, value in (
        ("CompanyName", "kirilan"),
        ("FileDescription", "New Eden Works - EVE Online command line tool"),
        ("FileVersion", VERSION),
        ("InternalName", "neworks"),
        ("LegalCopyright", "GPL-3.0-only"),
        ("OriginalFilename", "neworks.exe"),
        ("ProductName", "New Eden Works"),
        ("ProductVersion", VERSION),
    )]
    version_file = vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[vi.StringFileInfo([vi.StringTable("040904B0", strings)]),
              vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])])],
    )

a = Analysis(
    [str(Path(SPECPATH) / "neworks.py")],
    pathex=[str(ROOT)],
    # The bundled SDE snapshot and the dashboard page - the same files pyproject's package-data ships.
    datas=[(str(ROOT / "newedenworks" / "data" / pattern), "newedenworks/data") for pattern in ("*.json", "*.html")]
    # The agent skills the built-in plugins ship (`agent install`, MCP prompts).
    + [(str(path), path.parent.relative_to(ROOT).as_posix())
       for skills in ("skills", "industry/skills") for path in (ROOT / "newedenworks").glob(f"{skills}/*/*.md")],
    # The registry imports the built-in plugins by name (`registry.BUILTIN`), which PyInstaller cannot
    # see: every module of the package is listed from the source tree (collect_submodules would import
    # the package in a subprocess that cannot see this checkout), so no plugin goes missing.
    hiddenimports=sorted(".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
                         for path in (ROOT / "newedenworks").rglob("*.py") if "__pycache__" not in path.parts),
    # Standard library only at runtime; nothing GUI or test-related is ever imported.
    excludes=["tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="neworks",
    console=True,
    # UPX-packed executables are a classic antivirus false positive; the size saving is not worth it.
    upx=False,
    strip=False,
    debug=False,
    version=version_file,
)
