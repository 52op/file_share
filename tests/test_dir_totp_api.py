# -*- coding: utf-8 -*-
"""验证网页端目录管理员 TOTP 设置：PUT 启用/关闭/自动生成/免密联动，GET 回填。"""
import time


def _login(c):
    with c.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()


def _get_dir(c, alias):
    r = c.get(f"/api/directory/{alias}")
    assert r.status_code == 200
    return r.get_json()


def test_put_enables_dir_totp_with_secret(app, client):
    _login(client)
    r = client.put("/api/directory/pub", data={
        "alias": "pub",
        "dir_totp_enabled": "on",
        "dir_totp_secret": "SECRETABC",
        "dir_totp_only": "on",
    })
    assert r.status_code == 200
    d = _get_dir(client, "pub")
    assert d["totp_enabled"] is True
    assert d["totp_secret"] == "SECRETABC"
    assert d["totp_only"] is True


def test_put_generates_secret_when_empty(app, client):
    _login(client)
    client.put("/api/directory/pub", data={
        "alias": "pub", "dir_totp_enabled": "on",
    })
    d = _get_dir(client, "pub")
    assert d["totp_enabled"] is True
    assert d["totp_secret"], "未提供密钥时应自动生成"
    assert d["totp_only"] is False, "未勾选免密时应为False"


def test_put_requires_enabled_for_totp_only(app, client):
    _login(client)
    client.put("/api/directory/pub", data={
        "alias": "pub",
        "dir_totp_enabled": "on",
        "dir_totp_secret": "SEC1",
        "dir_totp_only": "on",
    })
    client.put("/api/directory/pub", data={
        "alias": "pub",
        "dir_totp_only": "on",   # 仅勾选免密，未启用TOTP
    })
    d = _get_dir(client, "pub")
    assert d["totp_enabled"] is False
    assert d["totp_secret"] == ""
    assert d["totp_only"] is False, "未启用TOTP时免密应复位"


def test_put_disables_and_clears_totp(app, client):
    _login(client)
    client.put("/api/directory/pub", data={
        "alias": "pub", "dir_totp_enabled": "on", "dir_totp_secret": "SEC2",
    })
    # 关闭：不提交 dir_totp_enabled
    client.put("/api/directory/pub", data={"alias": "pub"})
    d = _get_dir(client, "pub")
    assert d["totp_enabled"] is False
    assert d["totp_secret"] == ""
    assert d["totp_only"] is False


def test_get_returns_totp_state_for_refill(app, client):
    _login(client)
    client.put("/api/directory/pub", data={
        "alias": "pub", "dir_totp_enabled": "on", "dir_totp_secret": "REFILL1", "dir_totp_only": "on",
    })
    d = _get_dir(client, "pub")
    assert d["totp_enabled"] is True
    assert d["totp_secret"] == "REFILL1"
    assert d["totp_only"] is True