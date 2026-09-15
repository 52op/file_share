# -*- coding: utf-8 -*-
"""Caddyfile 生成的 WebDAV 独立端口反代块 + WebDAV 内部绑定地址 + TLS 语义分离。"""
import main
from caddy_manager import CaddyManager, webdav_internal_addr


def _cfg():
    cfg = main.config
    cfg.ssl_domain = "fs.example.com"
    cfg.ssl_port = 12346
    cfg.port = 12345
    return cfg


def test_webdav_internal_addr_caddy_mode():
    """Caddy 模式：webdav 绑 127.0.0.1 内部端口，避免与 Caddy 争抢对外端口。"""
    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = True
    cfg.webdav_port = 12347
    host, port = webdav_internal_addr(cfg)
    assert host == "127.0.0.1"
    assert port == 12348


def test_webdav_internal_addr_plain_mode():
    """非 Caddy：直接监听 0.0.0.0:webdav_port。"""
    cfg = _cfg()
    cfg.ssl_enabled = False
    cfg.caddy_enabled = False
    cfg.webdav_port = 12347
    assert webdav_internal_addr(cfg) == ("0.0.0.0", 12347)


def test_webdav_manual_cert_internal_addr():
    """手动证书模式（非 Caddy）：仍直接监听 0.0.0.0:webdav_port（TLS 绑在这个端口）。"""
    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = False
    cfg.webdav_port = 12347
    assert webdav_internal_addr(cfg) == ("0.0.0.0", 12347)


def test_caddyfile_has_webdav_block():
    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = True
    cfg.webdav_enabled = True
    cfg.webdav_port = 12347
    caddyfile = CaddyManager(cfg).generate_caddyfile()
    assert "fs.example.com:12347 {" in caddyfile
    assert "reverse_proxy 127.0.0.1:12348" in caddyfile, "应反代到内部端口"
    assert "fs.example.com {" in caddyfile  # 主站块仍存在
    assert "reverse_proxy 127.0.0.1:12345" in caddyfile


def test_caddyfile_no_webdav_block_when_disabled():
    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = True
    cfg.webdav_enabled = False
    cfg.webdav_port = 12347
    caddyfile = CaddyManager(cfg).generate_caddyfile()
    assert "12347" not in caddyfile


def test_caddyfile_webdav_same_as_ssl_skipped():
    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = True
    cfg.webdav_enabled = True
    cfg.webdav_port = 12346  # 与 ssl_port 冲突 → 跳过，避免重复站点
    caddyfile = CaddyManager(cfg).generate_caddyfile()
    assert "fs.example.com:12346 {" not in caddyfile

def test_tls_semantics_caddy_no_double_tls(monkeypatch):
    """语义分离：Caddy 模式对外 https，但 webdav 监听不套 TLS（避免重复）。"""
    import caddy_manager as cm

    cfg = _cfg()
    cfg.webdav_port = 12347
    cfg.ssl_enabled = False
    cfg.caddy_enabled = False
    assert cm.webdav_external_scheme(cfg) == "http"
    assert cm.webdav_uses_tls(cfg) is False

    # Caddy 模式：对外 https，内部仍 http
    cfg.ssl_enabled = True
    cfg.caddy_enabled = True
    assert cm.webdav_external_scheme(cfg) == "https"
    assert cm.webdav_uses_tls(cfg) is False, "Caddy 模式下 webdav 内部不得套 TLS"


def test_tls_semantics_manual_cert(monkeypatch):
    """手动证书模式：证书有效 → https + 监听 TLS；证书缺失/过期 → http 兜底。"""
    import caddy_manager as cm

    cfg = _cfg()
    cfg.ssl_enabled = True
    cfg.caddy_enabled = False
    cfg.webdav_port = 12347

    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: True)
    assert cm.webdav_external_scheme(cfg) == "https"
    assert cm.webdav_uses_tls(cfg) is True

    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: False)
    assert cm.webdav_external_scheme(cfg) == "http", "证书无效应 http 兜底"
    assert cm.webdav_uses_tls(cfg) is False


def test_tls_semantics_ssl_off_ignores_cert(monkeypatch):
    """ssl 未启用：即使证书目录存在也不走 https。"""
    import caddy_manager as cm

    cfg = _cfg()
    cfg.ssl_enabled = False
    cfg.caddy_enabled = False
    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: True)
    assert cm.webdav_external_scheme(cfg) == "http"
    assert cm.webdav_uses_tls(cfg) is False
