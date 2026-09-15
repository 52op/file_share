# -*- coding: utf-8 -*-
"""IP → 国家离线解析（ip2region xdb）。

- 数据文件：<主程序目录>/ip2region_v4.xdb；缺失时 geo 功能自动禁用（不影响统计）
- 全内存缓存单例：利用 xdb 提供的 load_content_from_file 加载全文，
  new_with_buffer 创建的 searcher 可跨线程安全共享（官方 README 确认）
- ip_country(ip) → 规整的英文国家名（与前端世界地图 worldEN.json 键匹配）
  * ip2region 返回 "国家|区域|省份|城市|ISP"；中国/港澳台首段统一为“中国”→ China
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


def ip_country(ip):
    """返回规整的英文国家名；无法解析（内网/保留/IPv6/库缺失/异常）返回 None。"""
    if not ip:
        return None
    ip = str(ip).strip()
    if not ip or ":" in ip:  # IPv6 暂不支持（v4 xdb）
        return None
    try:
        searcher = _load()
        if searcher is None:
            return None
        region = searcher.search(ip)
    except Exception:
        return None
    if not region:
        return None
    first = region.split("|", 1)[0].strip()
    if not first or first in _IGNORED:
        return None
    if first in _ZH_NAME_MAP:
        return _ZH_NAME_MAP[first]
    return _EN_NAME_MAP.get(first, first)