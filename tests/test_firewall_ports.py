# -*- coding: utf-8 -*-
"""防火墙放行端口汇总：HTTP + HTTPS(Caddy) + WebDAV（启用时）。"""
import main


def _cfg(port=12345, ssl=False, ssl_port=443, webdav=False, webdav_port=8081):
    cfg = main.config
    cfg.port = port
    cfg.ssl_enabled = ssl
    cfg.ssl_port = ssl_port
    cfg.webdav_enabled = webdav
    cfg.webdav_port = webdav_port
    return cfg


def test_http_only():
    cfg = _cfg()
    assert main.collect_firewall_ports() == [12345]


def test_http_https_webdav():
    cfg = _cfg(ssl=True, ssl_port=12346, webdav=True, webdav_port=12347)
    assert main.collect_firewall_ports() == [12345, 12346, 12347]


def test_ssl_same_as_http_not_duplicated():
    cfg = _cfg(ssl=True, ssl_port=12345)
    assert main.collect_firewall_ports() == [12345]


def test_webdav_same_as_http_deduped():
    cfg = _cfg(webdav=True, webdav_port=12345)
    assert main.collect_firewall_ports() == [12345]