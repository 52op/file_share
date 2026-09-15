# -*- coding: utf-8 -*-
"""Caddyfile 生成的 WebDAV 独立端口反代块 + WebDAV 内部绑定地址。"""
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