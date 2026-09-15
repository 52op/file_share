# -*- coding: utf-8 -*-
"""上传/下载/访问统计分析（SQLite 后端）。

- 存储：<主程序目录>/stats/stats.db（WAL 模式），保留策略存 stats_config.json
- 写：内存队列批量提交（每 1 秒或满 200 条），安全事件(auth_fail/auth_ok/log_admin/block/unblock)同步立即写
- 事件字段：ts/type/role/alias/file/size/ip/ua/target/detail
- 对外 API 与原 JSON 分片版一致：
  record_event/record_view/record_view_dir/query_events/summary_counts/by_file/
  view_summary/delete_range/clear_all/cleanup_old/set_retention/get_retention
"""
import atexit
import json
import os
import sqlite3
import threading
import time

_stats_dir = None
_config_path = None
_config = {}
_conn = None
_lock = threading.Lock()          # 保护 DB 与队列
_queue = []                       # 待批量写入事件行
_queue_size = 200
_queue_flush_interval = 1.0
_last_flush = 0.0

EVENT_TRANSFER = ('upload', 'download', 'share_download', 'batch')
EVENT_MANAGE = ('delete', 'rename', 'move', 'create', 'edit', 'log_admin')
EVENT_AUTH = ('auth_fail', 'auth_ok')
# 安全/管理事件：同步立即写，不排队（追责类不能丢）
SYNC_TYPES = {'auth_fail', 'auth_ok', 'log_admin', 'block', 'unblock'}

_EVENT_COLS = ('ts', 'type', 'role', 'alias', 'file', 'size', 'ip', 'ua', 'target', 'detail')


def _get_app_path_safe():
    from main import get_app_path
    return get_app_path()


def configure(dirpath):
    global _stats_dir, _config_path, _config, _conn
    _stats_dir = dirpath
    os.makedirs(_stats_dir, exist_ok=True)
    _config_path = os.path.join(_stats_dir, "stats_config.json")
    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            _config = json.load(f)
    except Exception:
        _config = {}

    db_path = os.path.join(_stats_dir, "stats.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute(
        """CREATE TABLE IF NOT EXISTS events (
            ts INTEGER, type TEXT, role TEXT, alias TEXT, file TEXT,
            size INTEGER, ip TEXT, ua TEXT, target TEXT, detail TEXT)"""
    )
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ip ON events(ip, ts)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_file ON events(type, file)")
    _conn.commit()
    atexit.register(_flush_now)


def _ensure():
    if _conn is None:
        configure(os.path.join(_get_app_path_safe(), "stats"))


def _atomic_write_config():
    tmp = _config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _config_path)


def get_retention():
    _ensure()
    return int(_config.get("retention_days", 90) or 90)


def set_retention(days):
    _ensure()
    _config["retention_days"] = max(1, int(days))
    with _lock:
        _atomic_write_config()


# ---------------- 写：队列批量 + 安全事件同步 ----------------

def _flush_locked():
    """批量提交队列（须持锁）"""
    global _queue, _last_flush
    if not _queue:
        return
    rows, _queue = _queue, []
    try:
        _conn.executemany(
            "INSERT INTO events(ts,type,role,alias,file,size,ip,ua,target,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        _conn.commit()
    except Exception:
        # 异常时尽量保留数据，放回队首
        _queue = rows + _queue


def _flush_now():
    with _lock:
        _flush_locked()


def _flush_before_read():
    with _lock:
        _flush_locked()


def record_event(**kw):
    """记录一条事件。安全类型同步写，其余入队批量写。返回事件 dict。"""
    _ensure()
    ts = int(kw.get("ts") or time.time())
    ev = {
        "ts": ts,
        "type": kw.get("type", ""),
        "role": kw.get("role", "anonymous"),
        "alias": kw.get("alias", ""),
        "file": kw.get("file", ""),
        "size": int(kw.get("size") or 0),
        "ip": kw.get("ip", ""),
        "ua": (kw.get("ua") or "")[:500],
        "target": kw.get("target") or "",
        "detail": str(kw.get("detail"))[:1000] if kw.get("detail") else "",
    }
    row = tuple(ev[k] for k in _EVENT_COLS)
    if ev["type"] in SYNC_TYPES:
        with _lock:
            _conn.execute(
                "INSERT INTO events(ts,type,role,alias,file,size,ip,ua,target,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            _conn.commit()
    else:
        global _queue, _last_flush
        with _lock:
            _queue.append(row)
            now = time.time()
            if len(_queue) >= _queue_size or now - _last_flush >= _queue_flush_interval:
                _flush_locked()
                _last_flush = now
    return ev


def record_view(filepath, **ctx):
    """文件预览/查看（落明细）。ctx: role/alias/ip/ua"""
    return record_event(type="view", file=filepath, **ctx)


def record_view_dir(dirpath, **ctx):
    """目录访问（落明细）。ctx: role/alias/ip/ua"""
    return record_event(type="view_dir", file=dirpath, **ctx)


# ---------------- 读 ----------------

def _range_where(start=None, end=None):
    where, params = [], []
    if start is not None:
        where.append("ts>=?")
        params.append(int(start))
    if end is not None:
        where.append("ts<=?")
        params.append(int(end))
    w = ("WHERE " + " AND ".join(where)) if where else ""
    return w, params


def _resolve_types(etype):
    if not etype:
        return None
    if etype == "manage":
        return list(EVENT_MANAGE) + ["log_admin"]
    if etype == "auth":
        return list(EVENT_AUTH)
    if "," in etype:
        return [t.strip() for t in etype.split(",") if t.strip()]
    return [etype]


def _rows_to_events(rows):
    return [dict(zip(_EVENT_COLS, r)) for r in rows]


def query_events(start=None, end=None, etype=None, role=None, keyword=None, page=1, size=50):
    """时间范围过滤 + 分页，按时间倒序。etype 支持单值/逗号/manage/auth。"""
    _ensure()
    _flush_before_read()
    w0, p0 = _range_where(start, end)
    where, params = list(p0), []
    types = _resolve_types(etype)
    if types:
        where.append("type IN (%s)" % ",".join("?" * len(types)))
        params.extend(types)
    if role:
        where.append("role=?")
        params.append(role)
    if keyword:
        kw = keyword.lower()
        where.append("(lower(file) LIKE ? OR lower(alias) LIKE ? OR ip LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with _lock:
        total = _conn.execute(f"SELECT COUNT(*) FROM events {w}", params).fetchone()[0]
        rows = _conn.execute(
            f"SELECT ts,type,role,alias,file,size,ip,ua,target,detail FROM events {w} ORDER BY ts DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        ).fetchall()
    return {"total": total, "page": page, "size": size, "events": _rows_to_events(rows)}


def summary_counts(start=None, end=None):
    _ensure()
    _flush_before_read()
    keys = list(EVENT_TRANSFER) + list(EVENT_MANAGE) + list(EVENT_AUTH)
    counts = {k: 0 for k in keys}
    counts["total"] = 0
    counts["total_bytes"] = 0
    w, params = _range_where(start, end)
    with _lock:
        rows = _conn.execute(f"SELECT type, COUNT(*), SUM(size) FROM events {w} GROUP BY type", params).fetchall()
    for t, c, s in rows:
        if t in counts:
            counts[t] = c
        counts["total"] += c
        counts["total_bytes"] += s or 0
    return counts


def by_file(limit=10):
    """Top N 被下载文件（按次数）"""
    _ensure()
    _flush_before_read()
    with _lock:
        rows = _conn.execute(
            "SELECT alias, file, COUNT(*) AS c FROM events WHERE type IN ('download','share_download') "
            "GROUP BY alias, file ORDER BY c DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"file": "/".join(x for x in (a, f) if x) or "?", "count": c} for a, f, c in rows]


def view_summary(start=None, end=None, top=10):
    """访问聚合：查看 Top 文件 / 目录访问 Top"""
    _ensure()
    _flush_before_read()

    def _q(type_):
        clauses, params = [], []
        if start is not None:
            clauses.append("ts>=?")
            params.append(int(start))
        if end is not None:
            clauses.append("ts<=?")
            params.append(int(end))
        clauses.append("type=?")
        w = "WHERE " + " AND ".join(clauses)
        with _lock:
            rows = _conn.execute(
                f"SELECT file, COUNT(*) c FROM events {w} GROUP BY file ORDER BY c DESC, file LIMIT ?",
                params + [type_, top],
            ).fetchall()
        return rows

    views = _q("view")
    dirs = _q("view_dir")
    return {
        "view_total": sum(c for _, c in views),
        "dir_total": sum(c for _, c in dirs),
        "top_views": [{"file": f, "count": c} for f, c in views],
        "top_dirs": [{"dir": f, "count": c} for f, c in dirs],
    }


def _visitor_where(start=None, end=None):
    """首页访问事件的 WHERE 子句 + 参数（type='view_dir' 且 file='/'）。"""
    clauses = ["type='view_dir'", "file='/'"]
    params = []
    if start is not None:
        clauses.append("ts>=?")
        params.append(int(start))
    if end is not None:
        clauses.append("ts<=?")
        params.append(int(end))
    return "WHERE " + " AND ".join(clauses), params


def visitor_total(start=None, end=None):
    """首页访问总数（来访计数）。"""
    _ensure()
    _flush_before_read()
    w, params = _visitor_where(start, end)
    with _lock:
        return _conn.execute(f"SELECT COUNT(*) FROM events {w}", params).fetchone()[0]


def visitor_countries(start=None, end=None):
    """按国家聚合首页访问（国家存于 detail 字段，英文名）。返回 [{country, count}]。"""
    _ensure()
    _flush_before_read()
    w, params = _visitor_where(start, end)
    with _lock:
        rows = _conn.execute(
            f"SELECT detail, COUNT(*) FROM events {w} AND detail!='' "
            "GROUP BY detail ORDER BY COUNT(*) DESC",
            params,
        ).fetchall()
    return [{"country": d, "count": c} for d, c in rows]


def delete_range(start=None, end=None):
    """删除时间范围内事件。返回删除条数。"""
    _ensure()
    _flush_now()
    w, params = _range_where(start, end)
    with _lock:
        cur = _conn.execute(f"DELETE FROM events {w}", params)
        _conn.commit()
    return cur.rowcount


def clear_all():
    _ensure()
    _flush_now()
    with _lock:
        n = _conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        _conn.execute("DELETE FROM events")
        _conn.commit()
    return n


def cleanup_old():
    """按保留策略删除过期事件。返回删除条数。"""
    _ensure()
    cutoff = time.time() - get_retention() * 86400
    return delete_range(end=cutoff)


def import_json_legacy(json_dir=None):
    """一次性迁移旧 JSON 分片（stats-YYYY-MM-DD.json）到 SQLite。返回导入条数。"""
    _ensure()
    if json_dir is None:
        json_dir = _stats_dir
    imported = 0
    for fn in sorted(os.listdir(json_dir)):
        if not fn.startswith("stats-") or not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(json_dir, fn), "r", encoding="utf-8") as f:
                events = json.load(f)
        except Exception:
            continue
        for ev in events:
            record_event(
                ts=ev.get("ts"), type=ev.get("type"), role=ev.get("role"),
                alias=ev.get("alias"), file=ev.get("file"), size=ev.get("size"),
                ip=ev.get("ip"), ua=ev.get("ua"),
                target=ev.get("target"), detail=ev.get("detail"),
            )
            imported += 1
    _flush_now()
    return imported