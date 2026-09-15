# -*- coding: utf-8 -*-
"""WebDAV 挂载提示：/dir/<alias> 目录页 breadcrumb 的 WebDAV 按钮与账号提示层。

webdav_hint 由路由按当前会话角色注入，模板序列化到 JS（var hint = {...} / null）。
测试从渲染 HTML 中提取该 JSON 并精确断言三类角色（匿名 / 目录管理员 / 超级管理员）与禁用态。
"""
import json
import re
import time

import main


def _login_admin(c):
    with c.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()


def _login_dir_admin(c, alias):
    with c.session_transaction() as s:
        s["dir_admin_" + alias] = True
        s["dir_admin_time_" + alias] = time.time()
        s["auth_" + alias] = True  # 目录管理登录同时携带目录访问会话
        s["auth_time_" + alias] = time.time()


def _set_webdav(enabled=True, port=12347):
    main.config.webdav_enabled = enabled
    main.config.webdav_port = port


def _extract_hint(html):
    """从渲染 HTML 提取 `var hint = ...;` 的 JSON 值；未注入时返回 None。

    hint JS 整体包裹在 `{% if webdav_hint %}` 内，禁用时不渲染 script 块，
    故匹配不到也视为「无提示」（与 hint=null 等价）。
    """
    m = re.search(r"var hint = (.*?);\s*\n", html)
    if not m:
        return None
    return json.loads(m.group(1))


def _open_hint(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"status={r.status_code}"
    return _extract_hint(r.get_data(as_text=True))


def _users(hint):
    return [a[1] for a in hint["accounts"]]


def test_hint_ro_dir_password(app, client):
    """只读角色（目录访问密码登录）：<alias>（该目录只读）+ guest 兜底。"""
    main.config.global_password = "gpass"
    with client.session_transaction() as s:
        s["auth_locked"] = True
        s["auth_time_locked"] = time.time()
    _set_webdav()
    r = client.get("/dir/locked")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="webdavModal"' in html, "启用 webdav 时应渲染居中 modal 弹层"
    assert "webdavCopyBtn" in html, "modal 每行应有复制地址按钮"
    assert "Popover" not in html.split("<!-- 移动文件逻辑")[0], "已移除 hover popover 实现"
    hint = _extract_hint(html)
    assert hint["base"].endswith(":12347"), hint["base"]
    assert hint.get("alias") == "locked"
    assert _users(hint) == ["locked", "guest"], _users(hint)


def test_hint_global_only(app, client):
    """仅全局密码场景：全局密码已认证访问无密码目录时只显示 guest 账号。"""
    main.config.global_password = "gpass"
    with client.session_transaction() as s:
        s["auth"] = True
        s["auth_time"] = time.time()
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert _users(hint) == ["guest"], _users(hint)


def test_hint_admin(app, client):
    """超级管理员：admin（全部读写）+ guest（全部只读）。"""
    main.config.global_password = "gpass"
    _login_admin(client)
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert _users(hint) == ["admin", "guest"], _users(hint)


def test_hint_dir_admin(app, client):
    """目录管理员：dir_<alias>（该目录读写）+ <alias>（该目录只读）+ guest 兜底。"""
    main.config.shared_dirs["locked"].admin_password = "dap"
    main.config.global_password = "gpass"
    _login_dir_admin(client, "locked")
    _set_webdav()
    hint = _open_hint(client, "/dir/locked")
    assert _users(hint) == ["dir_locked", "locked", "guest"], _users(hint)


def test_hint_dir_admin_no_dir_password(app, client):
    """目录管理员但目录无访问密码：不出现 <alias> 只读账号。"""
    main.config.shared_dirs["pub"].admin_password = "dap"
    _login_dir_admin(client, "pub")
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert _users(hint) == ["dir_pub"], _users(hint)


def test_hint_disabled(app, client):
    """webdav 未启用：hint 为 null，无提示内容。"""
    _set_webdav(enabled=False)
    hint = _open_hint(client, "/dir/pub")
    assert hint is None


def test_hint_scheme_https_in_caddy_mode(app, client):
    """Caddy 反代模式（ssl+caddy 双开）：提示层应显示 https 地址。"""
    main.config.ssl_enabled = True
    main.config.caddy_enabled = True
    _login_admin(client)
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert hint["base"].startswith("https://"), hint["base"]
    assert "http://" not in hint["base"]


def test_hint_scheme_http_when_no_caddy(app, client):
    """自签证书 HTTPS（非 Caddy）模式：页面是 https，但 WebDAV 仍裸 http，提示也应 http。"""
    main.config.ssl_enabled = True
    main.config.caddy_enabled = False
    _login_admin(client)
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert hint["base"].startswith("http://"), hint["base"]


def test_hint_scheme_https_manual_cert(app, client, monkeypatch):
    """手动证书有效（非 Caddy）：对外 scheme 应为 https（与 webdav_uses_tls 一致）。"""
    import caddy_manager as cm

    main.config.ssl_enabled = True
    main.config.caddy_enabled = False
    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: True)
    _login_admin(client)
    _set_webdav()
    hint = _open_hint(client, "/dir/pub")
    assert hint["base"].startswith("https://"), hint["base"]