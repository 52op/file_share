# -*- coding: utf-8 -*-
"""验证网页保存配置后触发GUI窗体变量同步的回调机制"""
import time

import main


def test_gui_sync_callback_registered_and_called():
    """替换 _gui_config_sync_cb 后，notify_gui_config_saved 应调用它"""
    calls = []
    main.set_gui_config_sync_cb(lambda: calls.append(True))
    try:
        main.notify_gui_config_saved()
        assert calls == [True]
    finally:
        main.set_gui_config_sync_cb(None)


def test_gui_sync_noop_without_callback():
    """未注册回调时应安全返回，不抛异常"""
    main.set_gui_config_sync_cb(None)
    main.notify_gui_config_saved()  # 不应抛异常
    assert True


def test_api_settings_triggers_gui_sync(app):
    """网页保存设置后应触发GUI同步回调（通过root.after）"""
    import pyotp
    from main import config
    triggered = []
    # 模拟GUI：设置_cb为记录型（实际GUI会用root.after）
    main.set_gui_config_sync_cb(lambda: triggered.append('synced'))
    try:
        with app.test_client() as c:
            with c.session_transaction() as s:
                s["admin"] = True
                s["admin_time"] = time.time()
            resp = c.post("/api/settings", data={
                "global_password": "",
                "admin_password": "",
                "admin_totp_enabled": "on",
                "admin_totp_secret": pyotp.random_base32(),
                "admin_totp_only": "on",
                "session_timeout": "600",
            })
            assert resp.status_code == 200
        assert triggered == ["synced"], "网页保存后应触发GUI同步回调"
    finally:
        main.set_gui_config_sync_cb(None)