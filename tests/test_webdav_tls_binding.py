# -*- coding: utf-8 -*-
"""start_webdav 按 webdav_uses_tls 选择 HTTP/HTTPS 绑定（TLS 分支不重复套 TLS）。"""
import threading

import main
import webdav


class FakeCherootTls:
    """模拟 cheroot_server.CherootServer，记录构造参数。"""

    instances = []

    def __init__(self, app, host="0.0.0.0", port=8081, ssl_cert=None, ssl_key=None, threads=10):
        FakeCherootTls.instances.append({
            "host": host, "port": port,
            "ssl_cert": ssl_cert, "ssl_key": ssl_key, "threads": threads,
        })

    def create_server(self):
        pass

    def start(self):
        pass


class FakeCherootHttp:
    """模拟 cheroot.wsgi.Server（HTTP 分支）。"""

    instances = []

    def __init__(self, bind, app, numthreads=10):
        FakeCherootHttp.instances.append({"bind": bind, "numthreads": numthreads})

    def start(self):
        pass

    def stop(self):
        pass


def test_start_webdav_tls_manual_cert(monkeypatch):
    """手动证书模式：绑 0.0.0.0:webdav_port + TLS（ssl_cert/ssl_key 传给 CherootServer）。"""
    cfg = main.config
    cfg.webdav_enabled = True
    cfg.webdav_port = 12347
    cfg.ssl_enabled = True
    cfg.caddy_enabled = False
    monkeypatch.setattr("cheroot_server.CherootServer", FakeCherootTls)
    monkeypatch.setattr(webdav, "build_webdav_app", lambda cfg: object())

    # 让 webdav_uses_tls 返回 True（证书有效），证书路径来自 ssl_manager
    import caddy_manager as cm

    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: True)
    # start_webdav 内部还会调 ssl_manager.SSLCertificateManager.get_cert_file_path
    # → 用假证书路径，但走真校验会失败；直接替换读取路径的对象
    from ssl_manager import SSLCertificateManager

    fake_sm = type(
        "FakeSM",
        (),
        {"get_cert_file_path": lambda self: "C:/fake/cert.pem",
         "get_key_file_path": lambda self: "C:/fake/key.pem"},
    )
    monkeypatch.setattr("ssl_manager.SSLCertificateManager", lambda cfg: fake_sm())

    FakeCherootTls.instances = []
    server = webdav.start_webdav(cfg)
    assert server is not None
    assert len(FakeCherootTls.instances) == 1
    rec = FakeCherootTls.instances[0]
    assert rec["host"] == "0.0.0.0"
    assert rec["port"] == 12347
    assert rec["ssl_cert"] == "C:/fake/cert.pem"
    assert rec["ssl_key"] == "C:/fake/key.pem"


def test_start_webdav_http_fallback(monkeypatch):
    """证书无效（webdav_uses_tls=False）：裸 HTTP 绑 0.0.0.0:webdav_port，不走 CherootServer。"""
    cfg = main.config
    cfg.webdav_enabled = True
    cfg.webdav_port = 12347
    cfg.ssl_enabled = True
    cfg.caddy_enabled = False
    import caddy_manager as cm

    monkeypatch.setattr(cm, "_manual_cert_valid", lambda c: False)
    monkeypatch.setattr(webdav, "build_webdav_app", lambda cfg: object())
    monkeypatch.setattr("cheroot.wsgi.Server", FakeCherootHttp)

    FakeCherootHttp.instances = []
    server = webdav.start_webdav(cfg)
    assert server is not None
    assert len(FakeCherootHttp.instances) == 1
    assert FakeCherootHttp.instances[0]["bind"] == ("0.0.0.0", 12347)


def test_start_webdav_disabled_returns_none():
    """未启用 webdav：直接返回 None。"""
    cfg = main.config
    cfg.webdav_enabled = False
    assert webdav.start_webdav(cfg) is None