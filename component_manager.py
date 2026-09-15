# -*- coding: utf-8 -*-
"""可选组件管理：大体积/可选依赖的统一下载与状态检查。

背景：地图功能所需的 ip2region_v4.xdb（约 11MB，IP→国家/省份离线库）不入 git，
部署机器需自行获取。GUI「组件管理」对话框里一键下载与此模块对接。

设计沿用 caddy_manager.download_caddy 的模式：断点续传(.download 临时文件)、
自动重试、进度回调、体积下限校验，下载成功替换旧文件。
组件清单集中在 COMPONENTS，新增可选组件只需在此登记。
"""
import os
import time

import requests

# 服务器(尤其精简 Windows/无系统 CA 证书环境)下载公开大文件时 SSL 验证常失败
# （certifi 未收集进包 / 系统证书不全）。对公开只读文件，关闭校验可接受。
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# 各可选组件：key → {name, file, url, mirror_url, min_size, note}
# file 为程序目录下的目标文件名；min_size 用于校验下载完整性（字节）
# mirror_url：官方源失败时的加速镜像（自动 fallback，可留空）
COMPONENTS = {
    "ip2region": {
        "name": "IP 地理定位库 (ip2region_v4.xdb)",
        "file": "ip2region_v4.xdb",
        "url": "https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v4.xdb",
        "mirror_url": "https://gh-proxy.com/https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v4.xdb",
        "min_size": 5 * 1024 * 1024,
        "note": "来访分布地图：请求 IP → 国家/省份（离线查询，不依赖外网）。"
                "缺失时来访计数正常，但分布地图无数据。下载完成后需重启服务生效。",
        "page": "https://github.com/lionsoul2014/ip2region",
    },
}


def _get_app_path():
    try:
        from main import get_app_path

        return get_app_path()
    except Exception:
        return os.getcwd()


def get_component_path(key):
    """程序目录下组件的目标文件完整路径。"""
    info = COMPONENTS[key]
    return os.path.join(_get_app_path(), info["file"])


def component_status(key):
    """返回安装状态：'ok'（已装且大小达标）/ 'downloading'（有未完成临时文件）/ 'missing'。"""
    info = COMPONENTS[key]
    path = get_component_path(key)
    if os.path.exists(path) and os.path.getsize(path) >= info["min_size"]:
        return "ok"
    tmp = path + ".download"
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        return "downloading"
    return "missing"


def list_components():
    """组件概要列表：[{key, name, note, status, size_mb}]。"""
    result = []
    for key, info in COMPONENTS.items():
        status = component_status(key)
        size_mb = 0
        path = get_component_path(key)
        if status == "ok":
            size_mb = os.path.getsize(path) / 1024 / 1024
        entry = {
            "key": key,
            "name": info["name"],
            "note": info["note"],
            "status": status,
            "size_mb": size_mb,
            "min_size_mb": round(info["min_size"] / 1024 / 1024, 1),
        }
        result.append(entry)
    return result


class _DownloadError(Exception):
    """单个 URL 下载失败（网络/校验），外层会尝试下一个候选源。"""


def _try_download(url, target, temp_file, existing, min_size, progress_cb):
    """尝试从单个 URL 下载（断点续传 + 进度 + 完整性校验）。

    成功返回 True；失败抛 _DownloadError（外层进行镜像 fallback / 重试）。
    """
    try:
        headers = {}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        with requests.get(url, stream=True, timeout=(15, 480),
                          headers=headers, verify=False) as r:
            if r.status_code == 200 and existing > 0:
                existing = 0  # 服务器不支持 Range，从头下载（覆盖临时文件）
            else:
                r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0) or 0) + existing
            downloaded = existing
            mode = "ab" if existing > 0 else "wb"
            with open(temp_file, mode) as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, max(total, downloaded))
    except requests.exceptions.HTTPError as e:
        # 416：续传点已完整 → 直接用现有临时文件
        if (
            e.response is not None
            and e.response.status_code == 416
            and os.path.exists(temp_file)
            and os.path.getsize(temp_file) >= min_size
        ):
            os.replace(temp_file, target)
            return True
        raise _DownloadError(f"HTTP {e.response.status_code if e.response else '?'}: {e}")
    except requests.exceptions.RequestException as e:
        raise _DownloadError(str(e))

    # 完整性校验：小于下限视为坏文件（如镜像返回 404 页面）
    size = os.path.getsize(temp_file)
    if size < min_size:
        os.remove(temp_file)
        raise _DownloadError(f"文件异常(大小 {size} 字节 < 下限 {min_size})")

    os.replace(temp_file, target)
    return True


def download_component(key, progress_cb=None, max_retries=3):
    """下载组件到程序目录。

    - 候选源：官方 URL → 加速镜像（COMPONENTS[key]['mirror_url']），自动 fallback
    - verify=False：兼容服务器缺系统 CA 证书的环境（见模块头注释）
    - 断点续传(.download 临时文件) / 自动重试 / 进度回调
    progress_cb(downloaded_bytes, total_bytes)；返回 (是否成功, 提示信息)。
    """
    info = COMPONENTS[key]
    target = get_component_path(key)
    temp_file = target + ".download"

    urls = [info["url"]]
    if info.get("mirror_url"):
        urls.append(info["mirror_url"])

    existing = os.path.getsize(temp_file) if os.path.exists(temp_file) else 0
    last_err = "未知错误"

    for attempt in range(max_retries + 1):
        for url in urls:
            try:
                if _try_download(url, target, temp_file, existing, info["min_size"], progress_cb):
                    return True, (
                        f"{info['name']} 下载完成 "
                        f"({os.path.getsize(target) / 1024 / 1024:.1f} MB)"
                    )
            except _DownloadError as e:
                last_err = str(e)
                continue
        if attempt < max_retries:
            time.sleep(2 * (attempt + 1))
    # 所有源都失败：保留未完成临时文件供下次续传
    return False, last_err