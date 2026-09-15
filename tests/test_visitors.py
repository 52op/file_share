# -*- coding: utf-8 -*-
"""来访统计：首页访问记录、XFF 真实客户端 IP 修复、聚合 API、地图页。"""
import pytest

import geoip
import main
import stats


@pytest.fixture()
def stat(tmp_path):
    """将全局 stats 重定向到临时库，避免测试污染真实统计库。"""
    stats.configure(str(tmp_path / "stats"))
    return stats


def _last_home_visit():
    d = stats.query_events(size=5)
    for ev in d["events"]:
        if ev["type"] == "view_dir" and ev["file"] == "/":
            return ev
    return None


# ---------------- geoip 规整 ----------------

def test_geoip_country_normalization(monkeypatch):
    """国家名规整：中国/港澳台→China、韩国别名→Korea、英文原样、内网/IPv6→None。"""

    class FakeSearcher:
        def search(self, ip):
            return {
                "113.118.113.77": "中国|广东省|深圳市|电信|CN",
                "203.80.0.1": "中国|香港特别行政区|0|0|CN",
                "60.246.128.0": "中国|澳门特别行政区|0|0|CN",
                "8.8.8.8": "United States|California|0|Google LLC|US",
                "1.200.0.1": "South Korea|Seoul|0|0|KR",
                "127.0.0.1": "Reserved|Reserved|0|0|0",
                "45.64.0.0": "Indonesia|Jakarta|0|0|ID",
                "9.9.9.9": "",
            }.get(ip, "")

    monkeypatch.setattr(geoip, "_searcher", FakeSearcher())
    assert geoip.ip_country("113.118.113.77") == "China"
    assert geoip.ip_country("203.80.0.1") == "China"  # 香港
    assert geoip.ip_country("60.246.128.0") == "China"  # 澳门
    assert geoip.ip_country("8.8.8.8") == "United States"
    assert geoip.ip_country("1.200.0.1") == "Korea"  # 别名 South Korea→Korea
    assert geoip.ip_country("127.0.0.1") is None  # Reserved 内网
    assert geoip.ip_country("45.64.0.0") == "Indonesia"
    assert geoip.ip_country("9.9.9.9") is None  # 查询结果为空
    assert geoip.ip_country("") is None
    assert geoip.ip_country("::1") is None  # IPv6 暂不支持
    assert geoip.ip_country(None) is None


def test_geoip_province(monkeypatch):
    """ip_geo：中国提取省份（含简称规整），非中国/内网返回省为 None。"""

    class FakeSearcher:
        def search(self, ip):
            return {
                "113.118.113.77": "中国|广东省|深圳市|电信|CN",
                "221.122.0.1": "中国|内蒙古|呼和浩特市|联通|CN",
                "8.8.8.8": "United States|California|0|Google LLC|US",
                "127.0.0.1": "Reserved|Reserved|0|0|0",
            }.get(ip, "")

    monkeypatch.setattr(geoip, "_searcher", FakeSearcher())
    assert geoip.ip_geo("113.118.113.77") == ("China", "广东省")
    assert geoip.ip_geo("221.122.0.1") == ("China", "内蒙古自治区")  # 简称规整
    assert geoip.ip_geo("8.8.8.8") == ("United States", None)  # 非中国不记省
    assert geoip.ip_geo("127.0.0.1") == (None, None)
    assert geoip.ip_geo("") == (None, None)
    assert geoip.ip_country("113.118.113.77") == "China"  # 兼容入口


# ---------------- stats 聚合 ----------------

def test_stats_visitor_aggregate(stat):
    assert stats.visitor_total() == 0
    stats.record_view_dir("/", role="admin", alias="", detail="China")
    stats.record_view_dir("/", role="anonymous", alias="", detail="")  # 国家未知
    stats.record_view_dir("/", role="anonymous", alias="", detail="China")
    stats.record_view_dir("/pub", role="anonymous", alias="pub", detail="US")  # 非首页
    stats._flush_now()
    assert stats.visitor_total() == 3, "仅首页(type=view_dir, file=/)计入来访"
    cs = stats.visitor_countries()
    assert {"country": "China", "count": 2} in cs
    assert all(c["country"] for c in cs), "未知国家不参与分布"


# ---------------- routes ----------------

def test_index_records_visit_and_pages(client, app, stat):
    r = client.get("/")
    assert r.status_code == 200
    ev = _last_home_visit()
    assert ev is not None and ev["ip"]

    s = client.get("/api/visitors/summary").get_json()
    assert s["total"] >= 1

    m = client.get("/api/visitors/map").get_json()
    assert isinstance(m["countries"], list)
    assert m["total"] == s["total"]

    p = client.get("/visitors")
    assert p.status_code == 200
    assert "visitorMap" in p.get_data(as_text=True)


def test_xff_real_client_ip(client, app, stat):
    """重点：Caddy 反代下 remote_addr=127.0.0.1，统计应取 X-Forwarded-For 第一跳。"""
    r = client.get("/", headers={"X-Forwarded-For": "8.8.8.8, 127.0.0.1"})
    assert r.status_code == 200
    ev = _last_home_visit()
    assert ev is not None
    assert ev["ip"] == "8.8.8.8", f"应记录 XFF 第一跳真实客户端 IP，实际 {ev['ip']}"


def test_xff_single_hop(client, app, stat):
    client.get("/", headers={"X-Forwarded-For": "203.0.113.7"})
    ev = _last_home_visit()
    assert ev is not None
    assert ev["ip"] == "203.0.113.7"


def test_direct_access_no_xff_keeps_remote_addr(client, app, stat):
    """直连（无 XFF）时保持 remote_addr，不猜测。"""
    client.get("/")
    ev = _last_home_visit()
    assert ev is not None
    assert ev["ip"] == "127.0.0.1"


def test_stats_visitor_provinces(stat):
    """按省份聚合首页访问；无省份(外网/未知)不参与。"""
    stats.record_view_dir("/", role="anonymous", alias="", detail="China", province="广东省")
    stats.record_view_dir("/", role="anonymous", alias="", detail="China", province="")
    stats.record_view_dir("/", role="anonymous", alias="", detail="US", province="")
    stats._flush_now()
    ps = stats.visitor_provinces()
    assert {"province": "广东省", "count": 1} in ps
    assert all(p["province"] for p in ps), "省份分布不应含空省"


def test_provinces_api(client, app, stat):
    """省份分布 API：包含手工记录 + 真实首页访问的省份。"""
    stats.record_view_dir("/", role="anonymous", alias="", detail="China", province="广东省")
    client.get("/")
    r = client.get("/api/visitors/provinces")
    assert r.status_code == 200
    d = r.get_json()
    assert isinstance(d["provinces"], list)
    names = [p["province"] for p in d["provinces"]]
    assert "广东省" in names