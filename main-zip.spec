# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# 项目根目录（兼容 PyInstaller 执行 spec 时 __file__ 不存在）
if "__file__" in globals():
    PROJECT_ROOT = Path(__file__).resolve().parent
else:
    PROJECT_ROOT = Path(os.getcwd()).resolve()

# 动态收集 tkinterdnd2 数据目录，避免写死虚拟环境路径
try:
    import tkinterdnd2

    TKDND2_DATA_DIR = Path(tkinterdnd2.__file__).resolve().parent
except Exception:
    TKDND2_DATA_DIR = None

datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static"), "static"),
]

if TKDND2_DATA_DIR and TKDND2_DATA_DIR.exists():
    datas.append((str(TKDND2_DATA_DIR), "tkinterdnd2"))

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_ROOT)],
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
        "netifaces",
        # Windows 服务相关（动态导入）
        "pywintypes",
        "pythoncom",
        "win32timezone",
        "win32service",
        "win32serviceutil",
        "win32event",
        "win32api",
        "win32con",
        "servicemanager",
        # 运行时导入的模块
        "cheroot",
        "cheroot.wsgi",
        "cheroot.ssl",
        "cheroot.ssl.builtin",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# 自动收集 loguru 的所有子模块，避免打包后缺少内部模块
a.hiddenimports += collect_submodules("loguru")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="file_share",
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
    icon=[str(PROJECT_ROOT / "favicon.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="main",
)
