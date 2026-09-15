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

# 各可选组件：key → {name, file, url, min_size, note}
# file 为程序目录下的目标文件名；min_size 用于校验下载完整性（字节）
COMPONENTS = {
    "ip2region": {
        "name": "IP 地理定位库 (ip2region_v4.xdb)",
        "file": "ip2region_v4.xdb",
        "url": "https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v4.xdb",
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


def download_component(key, progress_cb=None, max_retries=3):
    """下载组件到程序目录（断点续传 + 重试 + 进度回调）。

    progress_cb(downloaded_bytes, total_bytes)；成功返回 (True, 提示)；
    失败返回 (False, 错误信息)。下载过程中用 `<file>.download` 临时文件，
    完成校验后原子替换。
    """
    info = COMPONENTS[key]
    target = get_component_path(key)
    temp_file = target + ".download"

    existing = os.path.getsize(temp_file) if os.path.exists(temp_file) else 0
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                time.sleep(2 * (attempt + 1))
                if progress_cb:
                    progress_cb(existing, 0)

            headers = {}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"

            with requests.get(info["url"], stream=True, timeout=(15, 480), headers=headers) as r:
                if r.status_code == 200 and existing > 0:
                    existing = 0
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
                existing = downloaded

            file_size = os.path.getsize(temp_file)
            if file_size < info["min_size"]:
                os.remove(temp_file)
                existing = 0
                raise ValueError(f"下载文件异常(大小 {file_size} 字节 < 下限 {info['min_size']})")

            os.replace(temp_file, target)
            return True, f"{info['name']} 下载完成 ({file_size / 1024 / 1024:.1f} MB)"

        except requests.exceptions.HTTPError as e:
            # 416：服务器返回 Range Not Satisfiable，但临时文件已完整 → 直接用
            if (
                e.response is not None
                and e.response.status_code == 416
                and os.path.exists(temp_file)
                and os.path.getsize(temp_file) >= info["min_size"]
            ):
                os.replace(temp_file, target)
                return True, f"{info['name']} 已就绪（续传点已完整）"
            if attempt >= max_retries:
                return False, f"下载失败(HTTP {e.response.status_code if e.response else '?'}): {e}"
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError, ValueError) as e:
            if attempt >= max_retries:
                # 保留未完成临时文件，供下次续传
                if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                    pass  # 保留 .download
                return False, f"下载失败: {e}（已重试 {max_retries} 次）"
        except Exception as e:
            return False, f"下载失败: {e}"
    return False, "下载失败，未知原因"