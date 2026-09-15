# -*- coding: utf-8 -*-
"""IP → 国家/省份离线解析（ip2region xdb）。

- 数据文件：<主程序目录>/ip2region_v4.xdb；缺失时 geo 功能自动禁用（不影响统计）
- 全内存缓存单例：利用 xdb 提供的 load_content_from_file 加载全文，
  new_with_buffer 创建的 searcher 可跨线程安全共享（官方 README 确认）
- ip_geo(ip) → (国家英文名, 省份或None)
  * ip2region 返回 "国家|区域|省份|城市|ISP"；中国/港澳台首段统一为“中国”→ China
  * 省份(第二段)规整为与 china.json(DataV areas_v3) 一致的名称；仅中国记录省份
  * 内网保留段(Reserved/0) → None；IPv6 暂不解析（v4 xdb）→ None；异常 → None
"""
import os
import threading

_searcher = None
_load_lock = threading.Lock()

_ZH_NAME_MAP = {"中国": "China"}  # 含港澳台（ip2region 港澳台首段均为“中国”）
_IGNORED = {"Reserved", "0", "", "内网", "本机"}
# ip2region 英文首段 → 世界地图 worldEN.json 键名 的差异修正
_EN_NAME_MAP = {
    "South Korea": "Korea",
    "North Korea": "Dem. Rep. Korea",
}
# ip2region 省份简称 → 中国地图 china.json(DataV areas_v3) 全名 的差异修正
_PROVINCE_ALIAS = {
    "内蒙古": "内蒙古自治区",
    "新疆": "新疆维吾尔自治区",
    "广西": "广西壮族自治区",
    "宁夏": "宁夏回族自治区",
    "西藏": "西藏自治区",
    "黑龙江": "黑龙江省",
    "海南": "海南省",
    "香港": "香港特别行政区",
    "澳门": "澳门特别行政区",
    "台湾": "台湾省",
}


def _get_xdb_path():
    try:
        from main import get_app_path

        return os.path.join(get_app_path(), "ip2region_v4.xdb")
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip2region_v4.xdb")


def _load():
    """惰性加载 xdb 并构建全局 searcher（线程安全，幂等）。"""
    global _searcher
    if _searcher is not None:
        return _searcher
    with _load_lock:
        if _searcher is not None:
            return _searcher
        try:
            import ip2region.searcher as xdb
            import ip2region.util as util

            path = _get_xdb_path()
            if not os.path.exists(path):
                return None
            _searcher = xdb.new_with_buffer(util.IPv4, util.load_content_from_file(path))
        except Exception:
            _searcher = None
        return _searcher


def available():
    """geo 数据库是否可用。"""
    return _load() is not None


def ip_geo(ip):
    """返回 (国家英文名, 省份/地区名或None)；无法解析返回 (None, None)。"""
    if not ip:
        return None, None
    ip = str(ip).strip()
    if not ip or ":" in ip:  # IPv6 暂不支持（v4 xdb）
        return None, None
    try:
        searcher = _load()
        if searcher is None:
            return None, None
        region = searcher.search(ip)
    except Exception:
        return None, None
    if not region:
        return None, None
    parts = region.split("|")
    first = (parts[0] or "").strip()
    if not first or first in _IGNORED:
        return None, None
    if first in _ZH_NAME_MAP:
        country = _ZH_NAME_MAP[first]
    else:
        country = _EN_NAME_MAP.get(first, first)

    province = None
    if country == "China" and len(parts) > 1:
        second = (parts[1] or "").strip()
        if second and second not in _IGNORED:
            province = _PROVINCE_ALIAS.get(second, second)
    return country, province


def ip_country(ip):
    """仅返回国家英文名（兼容入口）。"""
    country, _ = ip_geo(ip)
    return country