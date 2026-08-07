# -*- coding: utf-8 -*-
"""验证 save_config 的"配置合并"语义：
网页端更新的字段（GUI 未手动改动时）不会被 GUI 窗体变量覆盖；
GUI 手动改动的字段仍会写回。"""
import main
from main import FileShareApp


class _Var:
    """可控的 GUI 变量替身（真实值 + set/get），用于构造 GUI 实例做合并测试。"""
    def __init__(self, v=None):
        self._v = v

    def set(self, x):
        self._v = x

    def get(self):
        return self._v


class _Entry:
    def __init__(self):
        self.state = "normal"

    def configure(self, **kw):
        if "state" in kw:
            self.state = kw["state"]


def _make_gui(tmp_path):
    """构造一个最小可用的 GUI 实例（跳过 __init__），指向临时配置文件并加载基线。"""
    cfg = main.config
    cfg.config_file = str(tmp_path / "share_config.json")
    cfg.port = 5000
    cfg.cleanup_time = 60
    cfg.auto_cleanup = True
    cfg.global_password = ""
    cfg.admin_password = "admin"
    cfg.admin_totp_secret = "SECRET123"
    cfg.admin_totp_only = True
    cfg.shared_dirs.clear()
    cfg.save()  # 先把基线配置写入磁盘

    gui = FileShareApp.__new__(FileShareApp)
    gui.password_var = _Var()
    gui.admin_password_var = _Var()
    gui.admin_totp_enabled_var = _Var(True)
    gui.admin_totp_secret_var = _Var()
    gui.admin_totp_only_var = _Var()
    gui.port_var = _Var()
    gui.cleanup_time_var = _Var()
    gui.auto_cleanup_var = _Var()
    gui.admin_totp_entry = _Entry()
    gui.refresh_dir_list = lambda: None
    gui.check_and_prompt_restart = lambda: None

    FileShareApp.load_config(gui)  # 从磁盘加载，并记录各字段基线
    return gui


def _web_updates(gui, port, secret, totp_only):
    """模拟网页端更新配置并触发 GUI 变量同步（var 与基线随之刷新）。"""
    cfg = main.config
    cfg.port = port
    cfg.admin_totp_secret = secret
    cfg.admin_totp_only = totp_only
    FileShareApp.refresh_vars_from_config(gui)


def test_gui_untouched_fields_keep_web_values(tmp_path):
    """GUI 未手动改动的字段在保存后保留网页端新值，不被窗体旧值覆盖。"""
    gui = _make_gui(tmp_path)
    cfg = main.config

    # 网页端改：端口 9999、secret NEW、免密 关闭
    _web_updates(gui, port=9999, secret="NEWSECRET", totp_only=False)

    # 用户此时不手动改动任何字段，直接保存
    FileShareApp.save_config(gui)

    assert cfg.port == 9999, "端口应保留网页端新值"
    assert cfg.admin_totp_secret == "NEWSECRET", "TOTP密钥应保留网页端新值"
    assert cfg.admin_totp_only is False, "免密开关应保留网页端新值"


def test_gui_manual_field_change_is_written(tmp_path):
    """GUI 端手动改动的字段仍能写回，且不影响其它未改动字段。"""
    gui = _make_gui(tmp_path)
    cfg = main.config

    _web_updates(gui, port=9999, secret="NEWSECRET", totp_only=False)

    # 用户在 GUI 改动了端口
    gui.port_var.set("8888")

    FileShareApp.save_config(gui)

    assert cfg.port == 8888, "用户手动改的端口应生效"
    assert cfg.admin_totp_secret == "NEWSECRET", "secrets 未改动应保留网页端值"
    assert cfg.admin_totp_only is False, "免密未改动应保留网页端值"