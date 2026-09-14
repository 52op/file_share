# -*- coding: utf-8 -*-
"""上传/下载统计分析。

- 按天分片存储：<主程序目录>/stats/stats-YYYY-MM-DD.json
- 线程锁 + 原子写（tmp + os.replace）
- 事件字段：{ts, type(upload/download/share_download/batch/log_admin), role(admin/dir_admin/password/share/anonymous),
  alias, file, size, ip, ua}
- 保留策略存 stats_config.json（retention_days，默认 90 天）
"""
import json
import os
import threading
import time
from datetime import datetime

_stats_dir = None
_config_path = None
_config = {}
_lock = threading.Lock()


def _ensure():
    if _stats_dir is None:
        from main import get_app_path
        configure(os.path.join(get_app_path(), "stats"))


def configure(dirpath):
    global _stats_dir, _config_path, _config
    _stats_dir = dirpath
    os.makedirs(_stats_dir, exist_ok=True)
    _config_path = os.path.join(_stats_dir, "stats_config.json")
    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            _config = json.load(f)
    except Exception:
        _config = {}


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_retention():
    _ensure()
    return int(_config.get("retention_days", 90) or 90)


def set_retention(days):
    _ensure()
    _config["retention_days"] = max(1, int(days))
    with _lock:
        _atomic_write(_config_path, _config)


def record_event(**kw):
    """记录一条事件。kw: type/role/alias/file/size/ip/ua/ts"""
    _ensure()
    ts = int(kw.get("ts") or time.time())
    day_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    ev = {
        "ts": ts,
        "type": kw.get("type", ""),
        "role": kw.get("role", "anonymous"),
        "alias": kw.get("alias", ""),
        "file": kw.get("file", ""),
        "size": int(kw.get("size") or 0),
        "ip": kw.get("ip", ""),
        "ua": (kw.get("ua") or "")[:500],
    }
    with _lock:
        path = os.path.join(_stats_dir, f"stats-{day_str}.json")
        events = _load_day(day_str)
        events.append(ev)
        _atomic_write(path, events)
    return ev


def _load_day(day=0):
    if isinstance(day, str):
        day_str = day
    else:
        day_str = datetime.fromtimestamp(day).strftime("%Y-%m-%d")
    path = os.path.join(_stats_dir, f"stats-{day_str}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _day_files():
    out = []
    for fn in sorted(os.listdir(_stats_dir)):
        if fn.startswith("stats-") and fn.endswith(".json") and fn != "stats_config.json":
            day_str = fn[len("stats-"):-5]
            try:
                datetime.strptime(day_str, "%Y-%m-%d")
                out.append((day_str, os.path.join(_stats_dir, fn)))
            except Exception:
                continue
    return out


def _load_day_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _apply_filters(events, etype=None, role=None, keyword=None):
    out = events
    if etype:
        out = [e for e in out if e.get("type") == etype]
    if role:
        out = [e for e in out if e.get("role") == role]
    if keyword:
        kw = keyword.lower()
        out = [e for e in out
               if kw in (e.get("file") or "").lower()
               or kw in (e.get("alias") or "").lower()
               or kw in (e.get("ip") or "")]
    return out


def _range_events(day_list, start=None, end=None):
    for day_str, path in day_list:
        try:
            day_ts = datetime.strptime(day_str, "%Y-%m-%d").timestamp()
        except Exception:
            continue
        if start and day_ts + 86400 < start:
            continue
        if end and day_ts > end:
            continue
        yield from _load_day_file(path)


def query_events(start=None, end=None, etype=None, role=None, keyword=None, page=1, size=50):
    """时间范围过滤 + 分页，按时间倒序。start/end 为时间戳（秒）。"""
    _ensure()
    events = list(_range_events(_day_files(), start, end))
    events = _apply_filters(events, etype, role, keyword)
    events.sort(key=lambda e: e.get("ts", 0), reverse=True)
    total = len(events)
    start_i = (page - 1) * size
    return {
        "total": total,
        "page": page,
        "size": size,
        "events": events[start_i:start_i + size],
    }


def summary_counts(start=None, end=None):
    _ensure()
    counts = {"upload": 0, "download": 0, "share_download": 0, "batch": 0, "total": 0}
    for e in _range_events(_day_files(), start, end):
        t = e.get("type")
        if t in counts:
            counts[t] += 1
        counts["total"] += 1
    return counts


def by_file(limit=10):
    """Top N 被下载文件（次数）"""
    _ensure()
    agg = {}
    for e in _range_events(_day_files()):
        if e.get("type") in ("download", "share_download"):
            k = "/".join(x for x in (e.get("alias"), e.get("file")) if x) or "?"
            agg[k] = agg.get(k, 0) + 1
    top = sorted(agg.items(), key=lambda x: -x[1])[:limit]
    return [{"file": k, "count": c} for k, c in top]


def delete_range(start=None, end=None):
    """按时间范围删除事件（命中整天重写剔除，整片命中则删除该片）。返回删除条数。"""
    _ensure()
    removed = 0
    with _lock:
        for day_str, path in _day_files():
            try:
                day_ts = datetime.strptime(day_str, "%Y-%m-%d").timestamp()
            except Exception:
                continue
            hit = (not start or day_ts + 86400 > start) and (not end or day_ts < end)
            if not hit:
                continue
            events = _load_day_file(path)
            keep = [e for e in events
                    if not ((not start or e.get("ts", 0) >= start) and (not end or e.get("ts", 0) <= end))]
            removed += len(events) - len(keep)
            if keep:
                _atomic_write(path, keep)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
    return removed


def clear_all():
    _ensure()
    removed = 0
    with _lock:
        for _, path in _day_files():
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def cleanup_old():
    """按保留策略删除过期分片。返回删除条数。"""
    _ensure()
    retention = get_retention()
    cutoff = time.time() - retention * 86400
    return delete_range(start=None, end=cutoff)