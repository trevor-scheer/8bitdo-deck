from pathlib import Path
import json
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "packaging/entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=collect_data_files("eightbitdo", includes=["resources/*.sh"]),
    hiddenimports=collect_submodules("eightbitdo"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
(root / "out").mkdir(exist_ok=True)
(root / "out/binary-libraries.json").write_text(json.dumps(a.binaries))
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    [],
    name="eightbitdo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
