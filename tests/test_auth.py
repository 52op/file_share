# -*- coding: utf-8 -*-
"""认证与目录访问权限测试"""
import time


def _has_password_form(body):
    return 'id="passwordForm"' in body


def test_unauthenticated_locked_dir_requires_password(client):
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert _has_password_form(resp.get_data(as_text=True))


def test_admin_bypasses_dir_password(client):
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_unauthenticated_global_password_dir(client):
    from main import config
    config.global_password = "globpass"
    resp = client.get("/dir/pub")
    assert resp.status_code == 200
    assert _has_password_form(resp.get_data(as_text=True))


def test_admin_bypasses_global_password(client):
    from main import config
    config.global_password = "globpass"
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()
    resp = client.get("/dir/pub")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_dir_password_login_sets_auth_session(client):
    resp = client.post("/check_password/locked", data={"password": "dirpass"})
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert s.get("auth_locked") is True
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_dir_password_wrong_rejected(client):
    resp = client.post("/check_password/locked", data={"password": "wrong"})
    assert resp.status_code == 403


def test_admin_login_without_totp(client):
    resp = client.post("/admin/login", data={"password": "admin"})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get("admin") is True


def test_dir_admin_login(client):
    from main import config
    config.shared_dirs["locked"].admin_password = "dadmin"
    resp = client.post("/dir-admin/login",
                       data={"password": "dadmin", "dirname": "locked"})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get("dir_admin_locked") is True