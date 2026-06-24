# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()
SCRIPT_FILES = [
    "bridge_json_log.py",
    "doubao_downloader.py",
    "doubao_full.py",
    "doubao_login.py",
    "doubao_pipeline.py",
    "doubao_scanner.py",
    "doubao_uploader.py",
    "md2pdf.py",
    "post_process.py",
    "scan_to_clipboard.py",
    "video_sub_runner.py",
]
DATA_FILES = [
    "chat_url.txt",
    "pipeline_state.json",
    "upload_progress.json",
    "podcasts_list.json",
    "md_mapping.json",
    "doubao_state.json",
]

datas = []
for name in SCRIPT_FILES + DATA_FILES:
    path = ROOT / name
    if path.exists():
        datas.append((str(path), "."))

assets_dir = ROOT / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))

def add_tool_dir(source, target):
    source = Path(source)
    if not source.exists():
        return
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build", "downloads"}
    skip_suffixes = {".pyc", ".pyo", ".log"}
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(source)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        if path.name.startswith("test_") or path.name.startswith("download_report_"):
            continue
        datas.append((str(path), str(Path(target) / rel.parent)))

add_tool_dir(r"E:\Projects\ai\video-sub-md", "tools/video-sub-md")
add_tool_dir(r"E:\Projects\tools\github-repo-downloader", "tools/github-repo-downloader")
add_tool_dir(r"E:\Projects\tools\auto-unzip", "tools/auto-unzip")


a = Analysis(
    ["doubao_frontend.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        "markdown",
        "tkinter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DoubaoPodcastBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "doubao_bridge.ico") if (ROOT / "assets" / "doubao_bridge.ico").exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DoubaoPodcastBridge",
)
