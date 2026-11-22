from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
from PyInstaller.building.datastruct import Tree
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "packaging/entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=collect_data_files("eightbitdo", includes=["resources/*.sh", "resources/*.json"]),
    hiddenimports=collect_submodules("eightbitdo"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
manifest = Path(os.environ.get("EIGHTBITDO_LIBRARY_MANIFEST", str(root / "out/binary-libraries.json")))
manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(json.dumps(a.binaries))
notices = Path(CONF["workpath"]) / "runtime-licenses"
shutil.rmtree(notices, ignore_errors=True)
subprocess.run([sys.executable, str(root / "packaging/collect_licenses.py"), str(notices),
                "--manifest", str(manifest)], check=True)
shutil.copy2(root / "packaging/BINARY_NOTICES.md", notices)
a.datas += Tree(str(notices), prefix="runtime-licenses")
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
