# -*- coding: utf-8 -*-
"""可选组件管理：状态判定、下载（断点续传/进度/小文件拒绝/镜像回退）。"""
import os

import requests

import component_manager as cm


class _Resp:
    """模拟 requests 响应（stream 上下文）。"""

    def __init__(self, chunks, status=200, headers=None):
        self._chunks = chunks
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for c in self._chunks:
            yield c

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HTTPError(self.status_code)


class _HTTPError(Exception):
    response = None

    def __init__(self, code):
        self.response = type("Resp", (), {"status_code": code})()
        super().__init__(f"HTTP {code}")


def _mega(n):
    return b"a" * 1024 * 1024 * n


def test_status_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))
    assert cm.component_status("ip2region") == "missing"


def test_status_ok_and_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))
    p = cm.get_component_path("ip2region")
    with open(p, "wb") as f:
        f.write(_mega(6))  # 6MB > 5MB 下限
    assert cm.component_status("ip2region") == "ok"
    os.remove(p)
    with open(p + ".download", "wb") as f:
        f.write(_mega(1))
    assert cm.component_status("ip2region") == "downloading"


def test_download_success_with_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))

    def fake_get(url, stream=False, timeout=None, headers=None, **kw):
        assert kw.get("verify") is False, "应关闭 SSL 校验(兼容无系统CA环境)"
        return _Resp([_mega(1)] * 6, status=200)  # 6MB

    monkeypatch.setattr(cm.requests, "get", fake_get)
    progress = []
    ok, msg = cm.download_component("ip2region",
                                    progress_cb=lambda d, t: progress.append((d, t)))
    assert ok
    p = cm.get_component_path("ip2region")
    assert os.path.getsize(p) == 6 * 1024 * 1024
    assert progress and progress[-1][0] == 6 * 1024 * 1024, "应有完整进度回调"
    assert not os.path.exists(p + ".download"), "成功后临时文件应已替换"


def test_download_fallback_to_mirror(tmp_path, monkeypatch):
    """官方源连接失败 → 自动切换到加速镜像（gh-proxy）。"""
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))
    calls = []

    def fake_get(url, stream=False, timeout=None, headers=None, **kw):
        calls.append(url)
        if url.startswith("https://raw.githubusercontent.com"):
            raise requests.exceptions.ConnectionError("official source down")
        return _Resp([_mega(1)] * 6, status=200)

    monkeypatch.setattr(cm.requests, "get", fake_get)
    ok, _ = cm.download_component("ip2region", max_retries=0)
    assert ok
    assert len(calls) == 2, calls
    assert "gh-proxy.com" in calls[-1], "应回退到镜像源"
    assert os.path.getsize(cm.get_component_path("ip2region")) == 6 * 1024 * 1024


def test_download_resume(tmp_path, monkeypatch):
    """断点续传：已有 .download 从 Range 继续，最终完整替换。"""
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))
    p = cm.get_component_path("ip2region")
    with open(p + ".download", "wb") as f:
        f.write(_mega(3))  # 已有 3MB

    def fake_get(url, stream=False, timeout=None, headers=None, **kw):
        assert headers.get("Range") == "bytes=3145728-", headers
        assert kw.get("verify") is False
        return _Resp([_mega(1)] * 3, status=206)

    monkeypatch.setattr(cm.requests, "get", fake_get)
    ok, _ = cm.download_component("ip2region", max_retries=0)
    assert ok
    assert os.path.getsize(p) == 6 * 1024 * 1024


def test_download_rejects_small_file(tmp_path, monkeypatch):
    """低于体积下限视为坏文件，删除且不替换现有。"""
    monkeypatch.setattr(cm._get_app_path and cm, "_get_app_path", lambda: str(tmp_path))
    p = cm.get_component_path("ip2region")
    with open(p, "wb") as f:
        f.write(_mega(6))  # 原有的合法文件

    def fake_get(url, stream=False, timeout=None, headers=None, **kw):
        return _Resp([b"tiny"], status=200)

    monkeypatch.setattr(cm.requests, "get", fake_get)
    ok, _ = cm.download_component("ip2region", max_retries=0)
    assert not ok, "坏文件应下载失败"
    assert os.path.getsize(p) == 6 * 1024 * 1024, "原有合法文件不应被覆盖"


def test_list_components(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_get_app_path", lambda: str(tmp_path))
    items = cm.list_components()
    assert len(items) == len(cm.COMPONENTS)
    assert items[0]["key"] == "ip2region"
    assert items[0]["status"] == "missing"