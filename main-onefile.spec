# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 在 PyInstaller 执行 spec 时，__file__ 可能未定义，使用当前工作目录兜底
if "__file__" in globals():
    project_root = Path(__file__).resolve().parent
else:
    project_root = Path(os.getcwd()).resolve()

datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "static"), "static"),
]

# 自动收集 tkinterdnd2 的数据文件，避免写死 venv 路径
try:
    datas += collect_data_files("tkinterdnd2")
except Exception:
    pass

# 自动收集 loguru 的所有子模块，避免打包后运行时报 No module named 'loguru'
loguru_hiddenimports = []
try:
    loguru_hiddenimports = collect_submodules("loguru")
except Exception:
    pass

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # 项目模块（必需，因为是相对导入）
        "routes",
        "share_manager_ui",
        "share_links",
        "firewall",
        "cleanup_manager",
        "ssl_manager",
        "ssl_settings_dialog",
        "cheroot_server",
        # 条件导入的模块（在 try/except 中）
        "pypinyin",
        "tkinterdnd2",
        "cryptography",
        "loguru",
        # Windows 服务相关（动态导入）
        "win32timezone",
        "win32service",
        "win32serviceutil",
        "win32event",
        "servicemanager",
        "pywintypes",
        "pythoncom",
        "win32api",
        "win32con",
        "win32evtlogutil",
        "win32evtlog",
        # 运行时导入的模块
        "cheroot",
        "cheroot.wsgi",
        "cheroot.ssl",
        "cheroot.ssl.builtin",
    ]
    + loguru_hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name="file_share",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=[str(project_root / "favicon.ico")]
    if (project_root / "favicon.ico").exists()
    else None,
)
