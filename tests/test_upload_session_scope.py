# -*- coding: utf-8 -*-
"""上传会话目录级归属测试。

旧版上传会话绑定“标签页 client_id + 浏览器会话 cookie”，登出重登或换窗口后
无法发现未完成的上传。改造后会话归属为目录（alias）级：同一目录下任意标签页、
任意登录会话均可发现并续传，不同目录天然隔离。
"""
import io
import os
import time

import pytest


@pytest.fixture()
def upload_scope(app, tmp_path):
    """隔离上传会话持久化与临时分片目录，避免污染真实数据。"""
    from routes import routes as r
    old_file = r._UPLOAD_SESSIONS_FILE
    old_temp = r.config.upload_temp_dir
    sessions_before = set(r.upload_sessions)
    user_sessions_before = set(r._user_sessions)
    r._UPLOAD_SESSIONS_FILE = str(tmp_path / "upload_sessions_test.json")
    r.config.upload_temp_dir = str(tmp_path / "upload_tmp")
    os.makedirs(r.config.upload_temp_dir, exist_ok=True)
    yield r
    r._UPLOAD_SESSIONS_FILE = old_file
    r.config.upload_temp_dir = old_temp
    for sid in set(r.upload_sessions) - sessions_before:
        del r.upload_sessions[sid]
    for key in set(r._user_sessions) - user_sessions_before:
        del r._user_sessions[key]


def _admin_session(client, app):
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()


def _upload_chunk(client, alias, name="a.bin", chunk_index=0, total_chunks=2):
    """向指定目录上传一个分片（total_chunks=2 时不触发合并）。"""
    data = {
        "current_path": f"/dir/{alias}",
        "chunk_index": str(chunk_index),
        "total_chunks": str(total_chunks),
        "filename": name,
        "file_size": "1200000",
        "last_modified": "0",
        "file": (io.BytesIO(b"x" * 10), name),
    }
    return client.post(f"/api/upload/{alias}", data=data)


def test_session_shared_across_tabs_and_relogin(app, upload_scope):
    """A 页上传中 → 退出管理权限 → B 页（全新会话）重新登录，同一目录应能看到该会话。"""
    client_a = app.test_client()
    _admin_session(client_a, app)

    # A 页：初始化会话并上传第一个分片（2 分片中的 1 个，未完成）
    resp = client_a.get("/api/upload/init?alias=pub")
    assert resp.status_code == 200
    assert resp.get_json()["sessions"] == []

    resp = _upload_chunk(client_a, "pub")
    assert resp.status_code == 200

    resp = client_a.get("/api/upload/init?alias=pub")
    data = resp.get_json()
    assert len(data["sessions"]) == 1
    sess = data["sessions"][0]
    assert sess["total_files"] == 1
    assert sess["completed_files"] == 0
    assert sess["active"] is True

    # A 页退出管理权限
    client_a.get("/admin/logout")

    # B 页：全新浏览器会话（无 A 的 cookie），重新登录后在同一个目录应能发现 A 的会话
    client_b = app.test_client()
    _admin_session(client_b, app)
    resp = client_b.get("/api/upload/init?alias=pub")
    data = resp.get_json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == sess["session_id"]
    assert data["sessions"][0]["files"][0]["uploaded_chunks"] == 1


def test_session_isolated_by_directory(app, upload_scope):
    """目录级记忆：在 pub 的上传会话，不应出现在 locked 目录的发现结果中。"""
    client = app.test_client()
    _admin_session(client, app)
    resp = _upload_chunk(client, "pub")
    assert resp.status_code == 200

    resp = client.get("/api/upload/init?alias=locked")
    assert resp.status_code == 200
    assert resp.get_json()["sessions"] == []

    resp = client.get("/api/upload/init?alias=pub")
    assert len(resp.get_json()["sessions"]) == 1


def test_upload_center_lists_sessions_across_dirs(app, upload_scope):
    """超级管理员的上传中心能看到所有目录的未完成上传。"""
    client = app.test_client()
    _admin_session(client, app)
    _upload_chunk(client, "pub")
    _upload_chunk(client, "locked")

    resp = client.get("/api/upload/center")
    assert resp.status_code == 200
    data = resp.get_json()
    assert {s["alias"] for s in data["sessions"]} == {"pub", "locked"}
    assert all(s["total_files"] == 1 for s in data["sessions"])
    assert all(s["completed_files"] == 0 for s in data["sessions"])


def test_upload_center_scoped_by_permission(app, upload_scope):
    """目录管理员只能看到自己目录的上传；无权限用户 403；页面路由无权限时重定向。"""
    from main import config
    config.shared_dirs["locked"].admin_password = "dadmin"

    # 超级管理员在 pub、locked 各留一个未完成上传
    admin = app.test_client()
    _admin_session(admin, app)
    _upload_chunk(admin, "pub")
    _upload_chunk(admin, "locked")

    # 目录管理员（locked）：只能看到 locked
    dir_admin = app.test_client()
    with dir_admin.session_transaction() as s:
        s["dir_admin_locked"] = True
        s["dir_admin_time_locked"] = time.time()
    resp = dir_admin.get("/api/upload/center")
    assert resp.status_code == 200
    assert {s["alias"] for s in resp.get_json()["sessions"]} == {"locked"}

    # 无权限用户：API 403，页面重定向到首页
    anon = app.test_client()
    resp = anon.get("/api/upload/center")
    assert resp.status_code == 403
    resp = anon.get("/upload-center")
    assert resp.status_code == 302

    # 超级管理员能看到全部
    resp = admin.get("/api/upload/center")
    assert {s["alias"] for s in resp.get_json()["sessions"]} == {"pub", "locked"}


def test_deactivate_marks_session_inactive_for_other_tab(app, upload_scope):
    """A 页关闭（deactivate）后，B 页发现同一会话时 active 为 False，可显示恢复条。"""
    client_a = app.test_client()
    _admin_session(client_a, app)
    resp = _upload_chunk(client_a, "pub")
    assert resp.status_code == 200

    resp = client_a.get("/api/upload/init?alias=pub")
    sess = resp.get_json()["sessions"][0]
    sid = sess["session_id"]
    assert sess["active"] is True

    # A 页关闭：beforeunload sendBeacon 等价请求
    resp = client_a.post(f"/api/upload/session/{sid}/deactivate?alias=pub")
    assert resp.status_code == 200

    # B 页（不同标签页）：看到同一会话且 active=False
    client_b = app.test_client()
    _admin_session(client_b, app)
    resp = client_b.get("/api/upload/init?alias=pub")
    data = resp.get_json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == sid
    assert data["sessions"][0]["active"] is False


def test_orphan_session_cleanup(app, upload_scope):
    """孤立会话清理：空会话、分片已丢失的会话被删除；有分片可续传的会话保留。"""
    r = upload_scope
    prefix = r._upload_dir_prefix("pub")
    sid = r._get_or_create_session(prefix, alias="pub", prefix=prefix)

    # 1) 空会话：files 为空 → 删除
    r._cleanup_orphan_sessions()
    assert sid not in r.upload_sessions

    # 2) 有文件但分片目录不存在（分片被系统清理丢失）→ 删除
    sid2 = r._get_or_create_session(prefix, alias="pub", prefix=prefix)
    sess2 = r.upload_sessions[sid2]
    sess2["files"]["f_missing"] = {
        "relPath": "lost.bin", "size": 10, "last_modified": 0,
        "chunks": 3, "uploaded": 1,
        "temp_dir": os.path.join(r.config.upload_temp_dir, "f_missing"),
        "final_path": "/x/lost.bin", "status": "uploading",
    }
    r._cleanup_orphan_sessions()
    assert sid2 not in r.upload_sessions

    # 3) 有文件且分片在磁盘上 → 保留（可续传）
    sid3 = r._get_or_create_session(prefix, alias="pub", prefix=prefix)
    sess3 = r.upload_sessions[sid3]
    temp3 = os.path.join(r.config.upload_temp_dir, "f_kept")
    os.makedirs(temp3, exist_ok=True)
    with open(os.path.join(temp3, "chunk_0"), "wb") as f:
        f.write(b"x")
    sess3["files"]["f_kept"] = {
        "relPath": "kept.bin", "size": 10, "last_modified": 0,
        "chunks": 3, "uploaded": 1,
        "temp_dir": temp3, "final_path": "/x/kept.bin", "status": "uploading",
    }
    r._cleanup_orphan_sessions()
    assert sid3 in r.upload_sessions

    # 4) 全部文件已完成（分片目录已删）→ 会话无存在意义，删除
    #    注意 _get_or_create_session 对同一 alias 会复用已有会话，这里用独立 alias
    prefix4 = r._upload_dir_prefix("locked")
    sid4 = r._get_or_create_session(prefix4, alias="locked", prefix=prefix4)
    sess4 = r.upload_sessions[sid4]
    sess4["files"]["f_done"] = {
        "relPath": "done.bin", "size": 10, "last_modified": 0,
        "chunks": 2, "uploaded": 2,
        "temp_dir": "/nonexistent/f_done", "final_path": "/x/done.bin", "status": "completed",
    }
    r._cleanup_orphan_sessions()
    assert sid4 not in r.upload_sessions


def test_upload_center_sse_stream(app, upload_scope):
    """上传中心 SSE 端点：返回 text/event-stream，推送内容含会话摘要。"""
    client = app.test_client()
    _admin_session(client, app)
    _upload_chunk(client, "pub")

    with app.test_request_context("/api/upload/center/stream"):
        from flask import session as fs
        fs["admin"] = True
        fs["admin_time"] = time.time()
        from routes import routes as r
        resp = r.upload_center_stream()
        assert resp.mimetype == "text/event-stream"
        gen = iter(resp.response)
        try:
            frame = next(gen)
        finally:
            gen.close()
    assert frame.startswith("data: ")
    import json as _json
    payload = _json.loads(frame[len("data: "):])
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["alias"] == "pub"
