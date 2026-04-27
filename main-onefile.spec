# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

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
datas += collect_data_files("tkinterdnd2")

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
        # 条件导入的模块（在try/except中）
        "pypinyin",
        "tkinterdnd2",
        "cryptography",
        # Windows服务相关（动态导入）
        "win32timezone",
        "win32service",
        "win32serviceutil",
        "win32event",
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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(project_root / "favicon.ico")]
    if (project_root / "favicon.ico").exists()
    else None,
)
