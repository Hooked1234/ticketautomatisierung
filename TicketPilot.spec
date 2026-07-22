# PyInstaller specification for the Windows desktop build.
from pathlib import Path

project_root = Path(SPECPATH).resolve()

a = Analysis(
    [str(project_root / "src" / "ticketpilot" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=["keyring.backends.Windows"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TicketPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Avoid an external UPX dependency and reduce antivirus false positives.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TicketPilot",
)
