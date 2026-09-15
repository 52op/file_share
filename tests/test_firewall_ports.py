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


def test_udp_ports_http3_on():
    cfg = _cfg(ssl=True, ssl_port=12346, webdav=True, webdav_port=12347)
    cfg.caddy_enabled = True
    cfg.caddy_http3 = True
    assert main.collect_firewall_udp_ports() == [12346, 12347]


def test_udp_ports_http3_off_or_no_caddy():
    cfg = _cfg(ssl=True, ssl_port=12346, webdav=True, webdav_port=12347)
    cfg.caddy_enabled = True
    cfg.caddy_http3 = False
    assert main.collect_firewall_udp_ports() == []
    cfg.caddy_http3 = True
    cfg.caddy_enabled = False
    assert main.collect_firewall_udp_ports() == []


def test_udp_ports_no_ssl():
    cfg = _cfg(webdav=True, webdav_port=12347)
    cfg.caddy_enabled = True
    cfg.caddy_http3 = True
    assert main.collect_firewall_udp_ports() == []


def test_apply_firewall_rules_builds_commands(monkeypatch):
    """apply：delete 一次 + 逐端口 TCP/UDP in/out 规则；全部成功返回 True。"""
    calls = []

    class R:
        returncode = 0
        stdout = "Ok."
        stderr = ""

    def fake_run(cmd, shell=False, capture_output=False, text=False,
                 creationflags=0, timeout=None):
        calls.append(cmd)
        return R()

    monkeypatch.setattr("main.subprocess.run", fake_run)
    ok = main.apply_firewall_rules([12345, 12346], udp_ports=[12346])
    assert ok is True
    deletes = [c for c in calls if "delete rule" in c]
    adds = [c for c in calls if "add rule" in c]
    assert len(deletes) == 1
    assert len(adds) == 6  # TCP in/out×2 + UDP in/out×1
    assert adds[0].startswith(
        'netsh advfirewall firewall add rule name="File_Share_Port" '
        'dir=in action=allow protocol=TCP localport=12345'
    ), adds[0]
    assert any("protocol=UDP localport=12346" in c and "dir=out" in c for c in adds)


def test_apply_firewall_rules_reports_failure(monkeypatch):
    """任一 add 失败：返回 False（调用方据此告警），失败原因进入日志。"""

    class R:
        returncode = 1
        stdout = ""
        stderr = "requested operation requires elevation (Run as administrator)"

    def fake_run(cmd, shell=False, capture_output=False, text=False,
                 creationflags=0, timeout=None):
        return R()

    monkeypatch.setattr("main.subprocess.run", fake_run)
    ok = main.apply_firewall_rules([12345])
    assert ok is False, "任一规则失败应返回 False，避免静默"