# -*- mode: python ; coding: utf-8 -*-
# 服务专用 onedir 构建：onedir 无父/子进程分离，可正常作为 Windows 服务运行
# 与 main-onefile 的 hiddenimports/datas 保持一致

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

if "__file__" in globals():
    project_root = Path(__file__).resolve().parent
else:
    project_root = Path(os.getcwd()).resolve()

datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "static"), "static"),
]

try:
    datas += collect_data_files("tkinterdnd2")
except Exception:
    pass

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
        "routes",
        "share_manager_ui",
        "share_links",
        "firewall",
        "cleanup_manager",
        "ssl_manager",
        "ssl_settings_dialog",
        "pyotp",
        "cheroot_server",
        "pypinyin",
        "tkinterdnd2",
        "cryptography",
        "loguru",
        "netifaces",
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
        "cheroot",
        "cheroot.wsgi",
        "cheroot.ssl",
        "cheroot.ssl.builtin",
        # WebDAV（可选）
        "wsgidav",
        "wsgidav.wsgidav_app",
        "wsgidav.request_resolver",
        "wsgidav.http_authenticator",
        "wsgidav.error_printer",
        "wsgidav.dir_browser",
        "wsgidav.dc.simple_dc",
        "wsgidav.dc.base_dc",
        "wsgidav.fs_dav_provider",
        "wsgidav.mw.base_mw",
        "defusedxml",
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
    [],
    exclude_binaries=True,
    name="file_share_svc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=[str(project_root / "favicon.ico")]
    if (project_root / "favicon.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="file_share_svc",
)