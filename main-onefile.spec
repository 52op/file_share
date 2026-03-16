# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['E:\\letvar\\works\\python_app\\file_share'],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static'), ('E:\\letvar\\works\\python_app\\file_share\\venv\\Lib\\site-packages\\tkinterdnd2', 'tkinterdnd2')],
    hiddenimports=[
        # 项目模块（必需，因为是相对导入）
        'routes',
        'share_manager_ui',
        'share_links',
        'firewall',
        'cleanup_manager',
        'ssl_manager',
        'ssl_settings_dialog',
        'cheroot_server',

        # 条件导入的模块（在try/except中）
        'pypinyin',
        'tkinterdnd2',
        'cryptography',

        # Windows服务相关（动态导入）
        'win32timezone',
        'win32service',
        'win32serviceutil',
        'win32event',
        'servicemanager',

        # 运行时导入的模块
        'cheroot',
        'cheroot.wsgi',
        'cheroot.ssl',
        'cheroot.ssl.builtin'
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
    a.binaries,
    a.datas,
    [],
    name='file_share',
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
    icon=['favicon.ico'],
)
