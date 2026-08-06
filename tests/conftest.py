# -*- coding: utf-8 -*-
"""pytest 全局夹具：mock 掉 GUI 相关依赖，提供 Flask test_client。"""
import os
import sys
import types

import pytest


class _W:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return self._n
    def _n(self, *a, **k): return self
    def pack(self, *a, **k): pass
    def pack_forget(self, *a, **k): pass
    def grid(self, *a, **k): pass
    def set(self, *a, **k): pass
    def get(self, *a, **k): return ''
    def insert(self, *a, **k): pass
    def configure(self, *a, **k): pass
    def config(self, *a, **k): pass
    def bind(self, *a, **k): pass
    def after(self, *a, **k): return 0
    def delete(self, *a, **k): pass
    def destroy(self, *a, **k): pass
    def update(self, *a, **k): pass
    def update_idletasks(self, *a, **k): pass


class _T:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _W()
    def withdraw(self): pass
    def mainloop(self): pass


class _V:
    def __init__(self, *a, **k): pass
    def set(self, *a, **k): pass
    def get(self, *a, **k): return ''


class _MB:
    @staticmethod
    def showinfo(*a, **k): pass
    @staticmethod
    def showwarning(*a, **k): pass
    @staticmethod
    def showerror(*a, **k): pass
    @staticmethod
    def askyesno(*a, **k): return False
    @staticmethod
    def askokcancel(*a, **k): return False


def _install_mocks():
    import tkinter as _tk
    _tk.Tk = _T
    _tk.BooleanVar = _V
    _tk.StringVar = _V
    _tk.IntVar = _V
    _tk.DoubleVar = _V
    _tk.Toplevel = _W
    _tk.Frame = _W
    _tk.Label = _W
    _tk.Button = _W
    _tk.Entry = _W
    _tk.Checkbutton = _W
    _tk.Radiobutton = _W
    _tk.ScrolledText = _W
    _tk.Canvas = _W
    _tk.Menu = _W
    _tk.Menubutton = _W
    _tk.PanedWindow = _W
    _tk.Scrollbar = _W
    _tk.Listbox = _W
    _tk.Text = _W
    _tk.LabelFrame = _W
    _tk.messagebox = _MB
    _tk.filedialog = types.ModuleType('tkinter.filedialog')
    _tk.filedialog.askdirectory = lambda *a, **k: ''
    _tk.filedialog.askopenfilename = lambda *a, **k: ''
    _tk.filedialog.askopenfilenames = lambda *a, **k: []

    class _M(types.ModuleType):
        def __getattr__(self, n): return _W

    for mn in ['pystray', 'tkinterdnd2', 'tkinterdnd2.DND', 'ttkbootstrap',
               'ttkbootstrap.constants', 'ttkbootstrap.scrolled']:
        if mn in sys.modules:
            continue
        m = _M(mn)
        m.__all__ = []
        sys.modules[mn] = m

    ttk = sys.modules['ttkbootstrap']
    c = sys.modules['ttkbootstrap.constants']
    for x in ['LEFT', 'RIGHT', 'TOP', 'BOTTOM', 'X', 'Y', 'BOTH', 'N', 'S', 'E', 'W', 'CENTER', 'VERTICAL']:
        setattr(c, x, x)
    ttk.constants = c

    s = sys.modules['ttkbootstrap.scrolled']
    s.__all__ = []
    ttk.scrolled = s

    if not sys.modules.get('servicemanager'):
        sm = types.ModuleType('servicemanager')
        for a in ['PYS_SERVICE', 'StartServiceCtrlDispatcher', 'LogInfoMsg']:
            setattr(sm, a, lambda *a, **k: 0)
        sys.modules['servicemanager'] = sm


_install_mocks()

# 导入被测模块（在 mock 之后）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main
from main import ShareDirectory
from routes import routes


@pytest.fixture()
def app(tmp_path):
    """每个测试独立的 Flask 应用与临时配置。"""
    cfg = main.config
    cfg.config_file = str(tmp_path / "share_config.json")
    share_root = tmp_path / "share"
    share_root.mkdir(exist_ok=True)
    cfg.shared_dirs.clear()
    cfg.global_password = ""
    cfg.admin_password = "admin"
    cfg.admin_totp_secret = ""
    cfg.admin_totp_only = False
    (share_root / "f.txt").write_text("x", encoding="utf-8")
    cfg.shared_dirs["pub"] = ShareDirectory(str(share_root), alias="pub",
                                            password="", desc="", admin_password="")
    cfg.shared_dirs["locked"] = ShareDirectory(str(share_root), alias="locked",
                                               password="dirpass", desc="", admin_password="")
    main.flask_app.config["TESTING"] = True
    return main.flask_app


@pytest.fixture()
def client(app):
    return app.test_client()
