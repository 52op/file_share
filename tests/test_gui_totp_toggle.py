# -*- coding: utf-8 -*-
"""验证GUI端 TOTP 免密开关联动：未启用TOTP时免密置灰并复位，启用时可操作。"""
import main
from main import FileShareApp, DirectoryDialog


class _Var:
    def __init__(self, v=None):
        self._v = v

    def set(self, x):
        self._v = x

    def get(self):
        return self._v


class _Chk:
    def __init__(self):
        self.state = "normal"

    def configure(self, **kw):
        if "state" in kw:
            self.state = kw["state"]


def _admin_gui(enabled, secret, only):
    gui = FileShareApp.__new__(FileShareApp)
    gui.admin_totp_enabled_var = _Var(enabled)
    gui.admin_totp_secret_var = _Var(secret)
    gui.admin_totp_entry = _Chk()
    gui.admin_totp_only_var = _Var(only)
    gui.admin_totp_only_chk = _Chk()
    FileShareApp.toggle_admin_totp(gui)
    return gui


def _dir_gui(enabled, secret, only):
    gui = FileShareApp.__new__(DirectoryDialog)
    gui.totp_enabled_var = _Var(enabled)
    gui.totp_secret_var = _Var(secret)
    gui.totp_entry = _Chk()
    gui.totp_only_var = _Var(only)
    gui.totp_only_chk = _Chk()
    DirectoryDialog.toggle_totp(gui)
    return gui


def test_admin_totp_disabled_resets_only():
    gui = _admin_gui(False, "", True)
    assert gui.admin_totp_only_var.get() is False, "未启用TOTP时免密应复位"
    assert gui.admin_totp_only_chk.state == "disabled", "未启用TOTP时免密应置灰"


def test_admin_totp_enabled_keeps_only():
    gui = _admin_gui(True, "SECRETADMIN", True)
    assert gui.admin_totp_only_var.get() is True
    assert gui.admin_totp_only_chk.state == "normal"


def test_dir_totp_disabled_resets_only():
    gui = _dir_gui(False, "", True)
    assert gui.totp_only_var.get() is False
    assert gui.totp_only_chk.state == "disabled"


def test_dir_totp_enabled_keeps_only():
    gui = _dir_gui(True, "SECRETDIR", True)
    assert gui.totp_only_var.get() is True
    assert gui.totp_only_chk.state == "normal"