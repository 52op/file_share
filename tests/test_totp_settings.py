# -*- coding: utf-8 -*-
"""超级管理员TOTP免密开关(admin_totp_only)设置与接口测试"""
import time

import pyotp


def _login_admin(client):
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()


def test_api_totp_returns_totp_only(client):
    from main import config
    config.admin_totp_only = True
    _login_admin(client)
    resp = client.get("/api/totp")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["totp_only"] is True


def test_update_settings_saves_totp_only(client):
    from main import config
    _login_admin(client)
    resp = client.post("/api/settings",
                       data={"global_password": "",
                             "admin_password": "",
                             "admin_totp_enabled": "on",
                             "admin_totp_secret": pyotp.random_base32(),
                             "admin_totp_only": "on",
                             "session_timeout": "600"})
    assert resp.status_code == 200
    assert config.admin_totp_only is True


def test_update_settings_clears_totp_only_when_not_sent(client):
    from main import config
    _login_admin(client)
    resp = client.post("/api/settings",
                       data={"global_password": "",
                             "admin_password": "",
                             "admin_totp_enabled": "on",
                             "admin_totp_secret": pyotp.random_base32(),
                             "session_timeout": "600"})
    assert resp.status_code == 200
    assert config.admin_totp_only is False


def test_totp_only_login_without_password(client):
    from main import config
    config.admin_totp_secret = pyotp.random_base32()
    config.admin_totp_only = True
    code = pyotp.TOTP(config.admin_totp_secret).now()
    resp = client.post("/admin/login", data={"code": code})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get("admin") is True