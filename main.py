import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

from loguru import logger as loguru_logger

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    PYWIN32_AVAILABLE = True
except ImportError:
    servicemanager = None
    win32event = None
    win32serviceutil = None
    win32service = None
    PYWIN32_AVAILABLE = False
import ctypes
import tkinter as tk
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from tkinter import filedialog
from tkinter import messagebox as tkmessagebox

import netifaces
import pystray
import pyotp
import ttkbootstrap as ttk
from flask import Flask, render_template, request, session
from PIL import Image, ImageTk
from tkinterdnd2 import *  # 用于拖放支持
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from user_agents import parse

from encryption import get_crypto, set_key_dir
try:
    import webdav  # WebDAV 可选；依赖未装时不影响主程序
except Exception:
    webdav = None

# Cheroot服务器（替换Waitress）
from werkzeug.serving import make_server  # 开发环境使用

# 添加拼音转换支持
try:
    from pypinyin import Style, lazy_pinyin

    PINYIN_AVAILABLE = True
except ImportError:
    PINYIN_AVAILABLE = False


def get_app_path(tempdir=False):
    """获取应用程序路径 传True取临时文件夹路径"""
    if getattr(sys, "frozen", False):
        if tempdir:
            # 打包成单文件后程序运行生成的临时文件夹路径常用于取打包在EXE中的资源文件路径 如窗口图标等
            return sys._MEIPASS
        # 服务进程：统一使用主程序目录，保证与 GUI 共用同一份配置/密钥/日志
        if _SERVICE_MAIN_DIR:
            return _SERVICE_MAIN_DIR
        # 程序运行目录
        return os.path.dirname(os.path.abspath(sys.executable))

    else:
        # 开发环境路径
        return os.path.dirname(os.path.abspath(__file__))


# 服务进程主程序目录（--run-as-service 分支解析后设置；None=非服务进程）
_SERVICE_MAIN_DIR = None


def _resolve_service_main_dir():
    """解析服务进程应使用的主程序目录。

    标准单文件部署布局为: <主目录>/file_share_svc/file_share_svc.exe，
    主配置 share_config.json、config.key 都在 <主目录>。此时服务进程必须
    以 <主目录> 为运行根，否则会读写到 file_share_svc 子目录的独立配置，
    导致前端网页修改设置不生效、密钥不一致。
    服务版独立部署（配置就在自身目录）时返回自身目录。
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.basename(exe_dir) == "file_share_svc":
        parent = os.path.dirname(exe_dir)
        if parent and parent != exe_dir and os.path.isfile(os.path.join(parent, "share_config.json")):
            return parent
    return exe_dir


def _append_svc_diag(msg):
    """服务启动诊断：仅依赖标准库，逐阶段写入 exe 同目录 svc_diag.log。
    用于定位服务进程在 SCM 环境下启动即退出的确切崩溃点。"""
    try:
        diag_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "svc_diag.log")
        with open(diag_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PID:{os.getpid()} {msg}\n")
    except Exception:
        pass


def show_password_toggle_enabled():
    """程序目录存在 showpasswd 文件时启用「显隐密码」按钮；否则隐藏。"""
    return os.path.exists(os.path.join(get_app_path(), "showpasswd"))


_loguru_initialized = False  # 全局标志，确保 loguru 只初始化一次


def setup_service_logger(flask_app=None):
    """设置服务日志"""
    global _loguru_initialized

    # 获取日志目录
    log_dir = os.path.join(get_app_path(), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 只在第一次初始化时设置日志处理器
    if not _loguru_initialized:
        # 移除默认处理器
        loguru_logger.remove()

        # 添加文件处理器
        log_file = os.path.join(log_dir, "service_{time:YYYYMMDD}.log")
        loguru_logger.add(
            log_file,
            rotation="00:00",
            retention="15 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | PID:{process} | {level} | {message}",
            level="INFO",
            enqueue=True,
            backtrace=True,  # 添加异常追踪
            diagnose=True,  # 添加诊断信息
            filter=lambda record: (
                "Task queue depth" not in record["message"]
            ),  # 添加过滤器
        )

        _loguru_initialized = True
        loguru_logger.info("日志系统初始化完成")

    # 配置 logging
    logging.basicConfig(level=logging.INFO)
    # 过滤Cheroot的警告日志
    logging.getLogger("cheroot").setLevel(logging.ERROR)
    # 手动添加 LoguruHandler 将 logging 的日志重定向到 loguru
    logging.getLogger().addHandler(loguru_handler())

    # 如果传入了 flask_app，重定向 Flask 的日志
    if flask_app:
        flask_app.logger.handlers = []  # 清除 Flask 默认的日志处理器
        flask_app.logger.propagate = False  # 阻止日志传播到 root logger
        flask_app.logger.addHandler(loguru_handler())  # 添加自定义的 LoguruHandler
        flask_app.logger.setLevel(logging.INFO)  # 设置日志级别

    return loguru_logger


def loguru_handler():
    """创建一个将日志转发到 loguru 的处理器"""

    class LoguruHandler(logging.Handler):
        def emit(self, record):
            try:
                level = loguru_logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            loguru_logger.opt(depth=6, exception=record.exc_info).log(
                level, record.getMessage()
            )

    return LoguruHandler()


def get_optimal_threads():
    """根据CPU核心计算最优线程数"""
    import multiprocessing

    cpu_count = multiprocessing.cpu_count()
    threads = cpu_count * 2

    # 设置线程  最小值 最大值
    min_threads = 4
    max_threads = 16

    return max(min_threads, min(threads, max_threads))


flask_app = Flask(__name__)

# 在 Flask 应用初始化时添加 secret_key
flask_app.secret_key = os.urandom(24)

# 设置加密密钥文件所在目录（程序运行目录）
set_key_dir(get_app_path())

serverUrl = ""
runningPort = 12345

# WebDAV server 句柄（独立端口，可选功能）
_webdav_server = None


def _maybe_start_webdav():
    """WebDAV 可选：启用时启动独立端口 server，返回 server 或 None"""
    global _webdav_server
    if webdav is None:
        return None
    _webdav_server = webdav.start_webdav(config)
    return _webdav_server


def _maybe_stop_webdav():
    global _webdav_server
    if webdav is not None and _webdav_server is not None:
        webdav.stop_webdav(_webdav_server)
    _webdav_server = None

# 网页保存配置后同步GUI窗体的回调（由FileShareApp注册，无GUI时为空）
_gui_config_sync_cb = None

# 添加一个全局字典来存储密码修改时间戳
password_change_timestamps = {
    "global": 0,  # 全局密码最后修改时间
    "admin": 0,  # 管理员密码最后修改时间
    "directories": {},  # 各目录密码最后修改时间
    "shares": {},  # 分享链接密码最后修改时间
}


def set_gui_config_sync_cb(cb):
    """注册GUI配置同步回调（由FileShareApp调用）"""
    global _gui_config_sync_cb
    _gui_config_sync_cb = cb


def notify_gui_config_saved():
    """网页保存配置后通知GUI刷新窗体var（线程安全，主线程执行）"""
    cb = _gui_config_sync_cb
    if cb:
        try:
            cb()
        except Exception:
            pass


def get_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.normpath(os.path.join(base_path, relative_path))


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_global_ipv6():
    try:
        # 遍历所有网络接口
        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET6 in addrs:
                for addr in addrs[netifaces.AF_INET6]:
                    addr_ip = addr["addr"].split("%")[0]  # 去掉接口后缀
                    # 选择全局单播地址（2001: 或 240 开头），并排除临时地址
                    if (
                        addr_ip.startswith("2001:") or addr_ip.startswith("240")
                    ) and not addr.get("temporary", False):
                        return addr_ip
        return None
    except Exception:
        return "::1"


def chinese_to_pinyin(text):
    """将中文转换为拼音"""
    if not PINYIN_AVAILABLE:
        # 如果没有pypinyin库，返回简单的处理
        return re.sub(r"[^\w]", "", text.lower())

    if not text:
        return ""

    # 使用pypinyin转换中文为拼音
    pinyin_list = lazy_pinyin(text, style=Style.NORMAL)
    # 连接拼音并移除非字母数字字符
    result = "".join(pinyin_list)
    # 只保留字母数字和下划线
    result = re.sub(r"[^\w]", "", result.lower())

    # 如果结果为空或以数字开头，添加前缀
    if not result or result[0].isdigit():
        result = "dir_" + result

    return result


def validate_alias(P):
    # 只允许字母、数字、下划线和连字符
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", P))


def cleanup_old_logos(logo_dir, current_logo_filename=None):
    """清理旧的logo文件，只保留当前使用的logo"""
    try:
        if not os.path.exists(logo_dir):
            return

        # 获取所有logo文件
        logo_files = []
        for file in os.listdir(logo_dir):
            if file.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                logo_files.append(file)

        # 删除除当前logo外的所有文件
        deleted_count = 0
        for file in logo_files:
            if current_logo_filename and file != current_logo_filename:
                try:
                    file_path = os.path.join(logo_dir, file)
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    print(f"删除旧logo文件失败 {file}: {e}")
            elif not current_logo_filename:
                # 如果没有当前logo，删除所有logo文件
                try:
                    file_path = os.path.join(logo_dir, file)
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    print(f"删除logo文件失败 {file}: {e}")

        if deleted_count > 0:
            print(f"已清理 {deleted_count} 个旧logo文件")

    except Exception as e:
        print(f"清理logo目录时发生错误: {e}")


def secure_filename_cn(filename):
    # 移除路径分隔符
    filename = filename.replace("/", "").replace("\\", "")
    # 移除其他危险字符
    filename = re.sub(r'[<>:"|?*]', "", filename)
    # 确保文件名不以点开头（隐藏文件）
    if filename.startswith("."):
        filename = "_" + filename
    return filename.strip()


def safe_relative_path(rel_path):
    """将前端传来的相对路径（可能含子目录）逐段净化，防止路径穿越。
    返回 POSIX 风格安全相对路径；非法输入返回 None。
    """
    if not rel_path:
        return None
    rel_path = rel_path.replace("\\", "/").strip("/")
    if not rel_path:
        return None
    parts = rel_path.split("/")
    safe_parts = []
    for p in parts:
        if p in ("", ".", ".."):
            return None
        cleaned = secure_filename_cn(p)
        if not cleaned:
            return None
        safe_parts.append(cleaned)
    return "/".join(safe_parts)


def get_client_info():
    user_agent_string = request.headers.get("User-Agent")
    user_agent = parse(user_agent_string)
    ip = request.remote_addr

    # Get detailed system and browser info
    os_info = f"{user_agent.os.family} {user_agent.os.version_string}".strip()
    browser_info = (
        f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()
    )

    return f"IP:{ip} 系统:{os_info} 浏览器:{browser_info}"


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        label = ttk.Label(
            self.tooltip,
            text=self.text,
            background="#FFFFE0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("宋体", 8, "normal"),
            foreground="green",
        )
        label.pack(ipadx=3, ipady=3)

    def hide_tooltip(self, event):
        if self.tooltip:
            self.tooltip.destroy()
        self.tooltip = None


class ShareDirectory:
    def __init__(self, path, alias="", password="", desc="", admin_password="", totp_secret="", totp_only=False):
        self.path = path
        self.alias = alias
        self.password = password
        self.desc = desc
        self.admin_password = admin_password  # 新增：目录管理密码
        self.totp_secret = totp_secret  # 目录管理员的 TOTP 双因素密钥（空=未启用）
        self.totp_only = totp_only  # 目录管理员启用TOTP时，仅凭验证码登录（免密）
        # 处理分区根目录
        if path.endswith(":\\"):
            self.name = f"drive_{path[0].lower()}"
        else:
            self.name = os.path.basename(path)

    def to_dict(self):
        _c = get_crypto()
        return {
            "path": self.path,
            "alias": self.alias,
            "password": _c.encrypt(self.password),
            "name": self.name,  # 保存唯一标识名
            "desc": self.desc,
            "admin_password": _c.encrypt(self.admin_password),  # 新增：保存目录管理密码
            "totp_secret": _c.encrypt(self.totp_secret),
            "totp_only": self.totp_only,
        }

    @staticmethod
    def from_dict(data):
        _c = get_crypto()
        dir_obj = ShareDirectory(
            data["path"],
            data.get("alias", ""),
            _c.decrypt(data["password"]),
            data.get("desc", ""),
            _c.decrypt(data.get("admin_password", "")),  # 新增：从配置文件恢复目录管理密码
            _c.decrypt(data.get("totp_secret", "")),  # 从配置文件恢复目录管理员 TOTP 密钥
        )
        dir_obj.name = data.get("name", dir_obj.name)  # 恢复唯一标识名
        dir_obj.totp_only = data.get("totp_only", False)
        return dir_obj


class RedirectHandler:
    """自定义sink，将日志消息输出到 ScrolledText 组件"""

    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        """将日志消息写入 ScrolledText"""

        def append():
            self.text_widget.insert("end", message)
            self.text_widget.see("end")

        self.text_widget.after(0, append)

    def flush(self):
        """实现 flush 方法以兼容 loguru"""
        pass


class Config:
    def __init__(self):
        self.shared_dirs = {}
        self.global_password = ""
        self.admin_password = "admin"  # 默认管理员密码
        self.admin_totp_secret = ""  # 超级管理员的 TOTP 双因素密钥（空=未启用）
        self.admin_totp_only = False  # 超级管理员启用TOTP时，仅凭验证码登录（免密）
        self.port = 12345
        self.dark_theme = False  # Add theme setting
        self.log_to_file = False  # Add logging setting
        self.config_file = "share_config.json"
        self.use_waitress = True  # True=Cheroot, False=Werkzeug
        self.upload_temp_dir = "temp/upload/"
        self.security_code = "12356789"
        self.cleanup_time = 3600  # 定义清理临时文件及过期分享链接函数间隔时间
        self.auto_cleanup = True  # 添加auto_cleanup属性并设置默认值
        self.session_timeout = 600  # 会话空闲超时（秒），0=禁用超时
        self.upload_timeout = 1800  # Cheroot channel_timeout：单个上传请求最长处理时间（秒），防止大文件分片被服务端切断
        self.upload_concurrency = 5  # 前端同时上传文件数，内网可调大
        self.upload_chunk_size = 1048576  # 分片大小（字节），默认1MB，内网高速链路可调大

        # SSL相关配置
        self.ssl_enabled = False  # 是否启用SSL
        self.ssl_port = 443  # SSL端口
        self.cert_server_url = ""  # 证书服务器地址
        self.ssl_domain = ""  # SSL绑定域名
        self.cert_dir = "certs"  # 证书存储目录

        # Caddy 自动 HTTPS 配置
        self.caddy_enabled = False  # 是否使用 Caddy 反向代理自动 HTTPS
        self.caddy_dns_provider = "alidns"  # DNS 提供商: alidns/tencentcloud/cloudflare
        self.caddy_access_key_id = ""  # 阿里云 AccessKey ID（DNS-01 验证）
        self.caddy_access_key_secret = ""  # 阿里云 AccessKey Secret（DNS-01 验证）
        self.caddy_tencent_secret_id = ""  # 腾讯云 SecretId（DNSPod DNS-01 验证）
        self.caddy_tencent_secret_key = ""  # 腾讯云 SecretKey（DNSPod DNS-01 验证）
        self.caddy_cloudflare_api_token = ""  # Cloudflare API Token（DNS-01 验证）

        # 页面设置
        self.page_title = "FS文件分享服务工具"
        self.logo_name = "File Share"
        self.logo_image_url = ""  # 存储相对于static目录的路径，如 "logos/my_logo.png"
        # logo存储目录 - 使用程序运行目录而不是临时目录
        self.logo_dir = os.path.join(get_app_path(), "static", "logos")

        # WebDAV（可选，默认关闭；独立端口）
        self.webdav_enabled = False
        self.webdav_port = 8081

        # 确保必要目录存在
        os.makedirs(self.upload_temp_dir, exist_ok=True)
        os.makedirs(self.cert_dir, exist_ok=True)
        os.makedirs(self.logo_dir, exist_ok=True)

    def save(self):
        config_data = {
            "shared_dirs": {
                name: dir_obj.to_dict()
                for name, dir_obj in self.shared_dirs.items()
            },
            "global_password": get_crypto().encrypt(self.global_password),
            "admin_password": get_crypto().encrypt(self.admin_password),
            "admin_totp_secret": get_crypto().encrypt(self.admin_totp_secret),
            "admin_totp_only": self.admin_totp_only,
            "port": self.port,
            "dark_theme": self.dark_theme,
            "log_to_file": self.log_to_file,
            "use_waitress": self.use_waitress,
            "cleanup_time": self.cleanup_time,  # 新增：保存清理间隔
            "auto_cleanup": self.auto_cleanup,  # 新增：保存自动清理设置
            "upload_temp_dir": self.upload_temp_dir,  # 新增：保存上传临时目录
            "session_timeout": self.session_timeout,  # 会话空闲超时（秒）
            "upload_timeout": self.upload_timeout,  # Cheroot channel_timeout（秒）
            "upload_concurrency": self.upload_concurrency,  # 前端并发上传文件数
            "upload_chunk_size": self.upload_chunk_size,  # 分片大小（字节）
            # SSL相关配置
            "ssl_enabled": self.ssl_enabled,
            "ssl_port": self.ssl_port,
            "cert_server_url": self.cert_server_url,
            "ssl_domain": self.ssl_domain,
            "cert_dir": self.cert_dir,
            # Caddy 自动 HTTPS 配置
            "caddy_enabled": self.caddy_enabled,
            "caddy_dns_provider": self.caddy_dns_provider,
            "caddy_access_key_id": get_crypto().encrypt(self.caddy_access_key_id),
            "caddy_access_key_secret": get_crypto().encrypt(self.caddy_access_key_secret),
            "caddy_tencent_secret_id": get_crypto().encrypt(self.caddy_tencent_secret_id),
            "caddy_tencent_secret_key": get_crypto().encrypt(self.caddy_tencent_secret_key),
            "caddy_cloudflare_api_token": get_crypto().encrypt(self.caddy_cloudflare_api_token),
            # 页面设置
            "page_title": self.page_title,
            "logo_name": self.logo_name,
            "logo_image_url": self.logo_image_url,
            # logo_dir不需要保存到配置文件，因为它总是基于程序运行目录计算
            # WebDAV
            "webdav_enabled": self.webdav_enabled,
            "webdav_port": self.webdav_port,
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        # 统一通知GUI：任何来源（网页/GUI/目录操作）保存配置后刷新窗体
        notify_gui_config_saved()

    def load(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.shared_dirs = {}
                for name, dir_data in data.get("shared_dirs", {}).items():
                    # Ensure desc exists in the data
                    if "desc" not in dir_data:
                        dir_data["desc"] = ""
                    # 新增：确保admin_password字段存在
                    if "admin_password" not in dir_data:
                        dir_data["admin_password"] = ""
                    # 确保totp_secret字段存在
                    if "totp_secret" not in dir_data:
                        dir_data["totp_secret"] = ""
                    self.shared_dirs[name] = ShareDirectory.from_dict(dir_data)
                self.global_password = get_crypto().decrypt(data.get("global_password", ""))
                self.admin_password = get_crypto().decrypt(data.get("admin_password", "admin"))
                self.admin_totp_secret = get_crypto().decrypt(data.get("admin_totp_secret", ""))
                self.admin_totp_only = data.get("admin_totp_only", False)
                self.port = data.get("port", 12345)
                self.dark_theme = data.get("dark_theme", False)
                self.log_to_file = data.get("log_to_file", False)
                self.use_waitress = data.get("use_waitress", False)
                self.cleanup_time = data.get("cleanup_time", 3600)  # 新增：加载清理间隔
                self.auto_cleanup = data.get(
                    "auto_cleanup", True
                )  # 新增：加载自动清理设置
                self.upload_temp_dir = data.get(
                    "upload_temp_dir", "temp/upload/"
                )  # 新增：加载上传临时目录
                self.session_timeout = data.get(
                    "session_timeout", 600
                )  # 会话空闲超时（秒），0=禁用
                self.upload_timeout = data.get(
                    "upload_timeout", 1800
                )  # Cheroot channel_timeout（秒）
                self.upload_concurrency = data.get(
                    "upload_concurrency", 5
                )  # 前端并发上传文件数
                self.upload_chunk_size = data.get(
                    "upload_chunk_size", 1048576
                )  # 分片大小（字节），1048576=1MB
                # SSL相关配置
                self.ssl_enabled = data.get("ssl_enabled", False)
                self.ssl_port = data.get("ssl_port", 443)
                self.cert_server_url = data.get("cert_server_url", "")
                self.ssl_domain = data.get("ssl_domain", "")
                self.cert_dir = data.get("cert_dir", "certs")
                # Caddy 自动 HTTPS 配置
                self.caddy_enabled = data.get("caddy_enabled", False)
                self.caddy_dns_provider = data.get("caddy_dns_provider", "alidns")
                self.caddy_access_key_id = get_crypto().decrypt(
                    data.get("caddy_access_key_id", "")
                )
                self.caddy_access_key_secret = get_crypto().decrypt(
                    data.get("caddy_access_key_secret", "")
                )
                self.caddy_tencent_secret_id = get_crypto().decrypt(
                    data.get("caddy_tencent_secret_id", "")
                )
                self.caddy_tencent_secret_key = get_crypto().decrypt(
                    data.get("caddy_tencent_secret_key", "")
                )
                self.caddy_cloudflare_api_token = get_crypto().decrypt(
                    data.get("caddy_cloudflare_api_token", "")
                )
                # 页面设置
                self.page_title = data.get("page_title", "FS文件分享服务工具")
                self.logo_name = data.get("logo_name", "File Share")
                self.logo_image_url = data.get("logo_image_url", "")
                # logo_dir始终使用程序运行目录，不从配置文件读取
                self.logo_dir = os.path.join(get_app_path(), "static", "logos")
                # WebDAV
                self.webdav_enabled = data.get("webdav_enabled", False)
                try:
                    self.webdav_port = int(data.get("webdav_port", 8081) or 8081)
                except (TypeError, ValueError):
                    self.webdav_port = 8081

                # 确保logo目录存在
                os.makedirs(self.logo_dir, exist_ok=True)

                # 旧版明文密码自动迁移为加密格式（仅在本次加载含明文密码时触发）
                _migrate = False
                for _field in ("global_password", "admin_password", "admin_totp_secret"):
                    _val = data.get(_field)
                    if _val and not str(_val).startswith("enc:"):
                        _migrate = True
                        break
                if _migrate:
                    self.save()


config = Config()


def format_file_size(size_in_bytes):
    if size_in_bytes >= 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    elif size_in_bytes >= 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    else:
        return f"{size_in_bytes} B"


def partial_download(path, start, end):
    with open(path, "rb") as f:
        f.seek(start)
        chunk = 8192
        while True:
            read_size = min(chunk, end - f.tell() + 1)
            if read_size <= 0:
                break
            data = f.read(read_size)
            if not data:
                break
            yield data


def send_file_generator(path):
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            yield chunk


from cleanup_manager import (
    is_cleanup_running,
    start_cleanup_thread,
    stop_cleanup_thread,
)
from routes import *


class DirectoryDialog(ttk.Toplevel):
    def __init__(self, parent, dir_obj=None):
        super().__init__(parent)
        self.withdraw()  # 先隐藏窗口
        self.title("目录设置")
        self.geometry("490x330")
        icon_path = get_path("static/favicon.ico")
        self.iconbitmap(icon_path)

        self.result = None
        self.dir_obj = dir_obj

        # 目录选择
        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill=X, padx=10, pady=5)
        self.path_var = tk.StringVar(value=dir_obj.path if dir_obj else "")
        self.path_entry = ttk.Entry(dir_frame, textvariable=self.path_var)
        self.path_entry.pack(side=LEFT, fill=X, expand=YES)
        ttk.Button(dir_frame, text="浏览", command=self.browse_dir).pack(side=RIGHT)

        # 别名设置
        alias_frame = ttk.Frame(self)
        alias_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(alias_frame, text="显示名称:").pack(side=LEFT)
        self.alias_var = tk.StringVar(value=dir_obj.alias if dir_obj else "")

        # 添加别名验证
        vcmd = (self.register(validate_alias), "%P")
        self.alias_entry = ttk.Entry(
            alias_frame,
            textvariable=self.alias_var,
            validate="key",
            validatecommand=vcmd,
        )
        self.alias_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(
            self.alias_entry,
            "也就是目录的别名，在WEB页面显示的目录名称\n设跟真实文件夹不一样的名称有助于安全"
            "\n 只支持英文与数字组合",
        )

        # 密码设置
        pwd_frame = ttk.Frame(self)
        pwd_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(pwd_frame, text="访问密码:").pack(side=LEFT)
        self.password_var = tk.StringVar(value=dir_obj.password if dir_obj else "")
        self.pwd_entry = ttk.Entry(pwd_frame, textvariable=self.password_var, show="*")
        self.pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        if show_password_toggle_enabled():
            self.show_pwd_btn = ttk.Button(pwd_frame, text="👁", width=3,
                                           command=lambda: self.toggle_password_visibility(self.pwd_entry, self.show_pwd_btn))
            self.show_pwd_btn.pack(side=LEFT, padx=2)
            ToolTip(self.show_pwd_btn, "显示/隐藏密码")

        # 新增：目录管理密码设置
        admin_pwd_frame = ttk.Frame(self)
        admin_pwd_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(admin_pwd_frame, text="管理密码:").pack(side=LEFT)
        self.admin_password_var = tk.StringVar(
            value=dir_obj.admin_password if dir_obj else ""
        )
        self.admin_pwd_entry = ttk.Entry(
            admin_pwd_frame, textvariable=self.admin_password_var, show="*"
        )
        self.admin_pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        if show_password_toggle_enabled():
            self.show_admin_pwd_btn = ttk.Button(admin_pwd_frame, text="👁", width=3,
                                                 command=lambda: self.toggle_password_visibility(self.admin_pwd_entry, self.show_admin_pwd_btn))
            self.show_admin_pwd_btn.pack(side=LEFT, padx=2)
            ToolTip(self.show_admin_pwd_btn, "显示/隐藏密码")
        ToolTip(
            self.admin_pwd_entry,
            "设置此目录的管理密码，拥有此密码的用户可以管理此目录\n留空表示只有超级管理员可以管理",
        )

        # 目录管理员 TOTP 两步验证设置
        totp_frame = ttk.Frame(self)
        totp_frame.pack(fill=X, padx=10, pady=5)
        # 第一行：启用开关 + 密钥 + 生成/复制
        totp_row = ttk.Frame(totp_frame)
        totp_row.pack(fill=X)
        self.totp_enabled_var = tk.BooleanVar(
            value=bool(dir_obj.totp_secret) if dir_obj else False)
        ttk.Checkbutton(totp_row, text="目录管理员两步验证(TOTP)",
                        variable=self.totp_enabled_var,
                        command=self.toggle_totp).pack(side=LEFT)
        self.totp_secret_var = tk.StringVar(
            value=dir_obj.totp_secret if dir_obj else "")
        self.totp_entry = ttk.Entry(totp_row, textvariable=self.totp_secret_var,
                                    width=20, state="readonly")
        self.totp_entry.pack(side=LEFT, padx=3)
        ttk.Button(totp_row, text="生成", width=8,
                   command=lambda: self.gen_totp_secret(self.totp_secret_var)).pack(side=LEFT)
        ttk.Button(totp_row, text="复制", width=8,
                   command=lambda: DirectoryDialog.copy_totp_link(
                       self.winfo_toplevel(), self.totp_secret_var.get())).pack(side=LEFT, padx=3)
        # 第二行：仅验证码登录(免密)，独立一行保证可见
        totp_row2 = ttk.Frame(totp_frame)
        totp_row2.pack(fill=X, pady=(2, 0))
        self.totp_only_var = tk.BooleanVar(
            value=bool(dir_obj.totp_only) if dir_obj else False)
        self.totp_only_chk = ttk.Checkbutton(
            totp_row2, text="仅验证码登录(免密)", variable=self.totp_only_var)
        self.totp_only_chk.pack(side=LEFT)
        ttk.Label(totp_row2, text="启用TOTP后可用：登录此目录只需6位验证码，免密码").pack(side=LEFT, padx=4)
        self.toggle_totp()  # 同步初始状态：未启用TOTP时免密置灰

        # 添加描述输入框
        desc_frame = ttk.Frame(self)
        desc_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(desc_frame, text="描述:").pack(side=LEFT)
        self.desc_var = tk.StringVar(value=dir_obj.desc if dir_obj else "")
        self.desc_entry = ttk.Entry(desc_frame, textvariable=self.desc_var)
        self.desc_entry.pack(side=LEFT, fill=X, expand=YES)

        # 确定取消按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=BOTTOM, pady=10)
        ttk.Button(btn_frame, text="确定", command=self.confirm).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.cancel).pack(side=LEFT)

        # 设置窗口居中显示 - 增加高度以容纳新的管理密码字段
        self.geometry("490x330")
        self.update_idletasks()

        # 获取主窗口和对话框的尺寸
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()

        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()

        # 计算居中位置
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2

        # 设置对话框位置
        self.geometry(f"+{x}+{y}")

        self.deiconify()  # 显示窗口

        # 设置拖放支持
        try:
            self.path_entry.drop_target_register(DND_FILES)
            self.path_entry.dnd_bind("<<Drop>>", self.handle_drop)
        except:
            print("DND support not available for this entry")

        self.transient(parent)
        self.grab_set()

    def handle_drop(self, event):
        """处理拖拽到路径输入框的文件"""
        files = self.tk.splitlist(event.data)
        if files:
            path = files[0]
            normalized_path = os.path.normpath(path.strip('"'))

            if os.path.exists(normalized_path) and os.path.isdir(normalized_path):
                # 设置路径
                self.path_var.set(normalized_path)

                # 生成默认别名
                if normalized_path.endswith(":\\"):
                    # 处理磁盘根目录
                    default_alias = f"disk_{normalized_path[0].upper()}"
                else:
                    # 处理普通文件夹，获取最后一级目录名
                    dir_name = os.path.basename(normalized_path)
                    if not dir_name:
                        drive_letter = normalized_path[0].upper()
                        default_alias = f"disk_{drive_letter}"
                    else:
                        # 将中文目录名转换为拼音
                        default_alias = chinese_to_pinyin(dir_name)
                        # 如果转换后为空，使用原名称的安全版本
                        if not default_alias:
                            default_alias = re.sub(r"[^\w]", "", dir_name.lower())
                            if not default_alias or default_alias[0].isdigit():
                                default_alias = "dir_" + default_alias

                # 设置别名，使用和browse_dir相同的逻辑
                # 临时禁用验证，设置别名后重新启用
                self.alias_entry.configure(validate="none")
                self.alias_var.set(default_alias)
                self.alias_entry.configure(validate="key")

    def browse_dir(self):
        path = filedialog.askdirectory()
        if path:
            # 标准化Windows路径格式
            normalized_path = os.path.normpath(path).replace("/", "\\")

            # 检查是否是磁盘根目录
            if (
                normalized_path.endswith("\\")
                and len(normalized_path) == 3
                and normalized_path[1:] == ":\\"
            ):
                # 处理磁盘根目录
                default_alias = f"disk_{normalized_path[0].upper()}"
            else:
                # 处理普通文件夹，获取最后一级目录名
                dir_name = os.path.basename(normalized_path)
                # 如果是空字符串（可能发生在选择磁盘根目录时），使用磁盘别名
                if not dir_name:
                    drive_letter = normalized_path[0].upper()
                    default_alias = f"disk_{drive_letter}"
                else:
                    # 将中文目录名转换为拼音
                    default_alias = chinese_to_pinyin(dir_name)
                    # 如果转换后为空，使用原名称的安全版本
                    if not default_alias:
                        default_alias = re.sub(r"[^\w]", "", dir_name.lower())
                        if not default_alias or default_alias[0].isdigit():
                            default_alias = "dir_" + default_alias

            # 设置路径和别名
            self.path_var.set(normalized_path)
            # 临时禁用验证，设置别名后重新启用
            self.alias_entry.configure(validate="none")
            self.alias_var.set(default_alias)
            self.alias_entry.configure(validate="key")

    def confirm(self):
        path = self.path_var.get()
        alias = self.alias_var.get()
        if not path or not alias:
            tkmessagebox.showerror("错误", "路径和显示名称都必须填写")
            return
        # 验证别名格式
        if not validate_alias(alias):
            tkmessagebox.showerror("错误", "显示名称只能包含字母、数字、下划线和连字符")
            return

        # 处理分区根目录
        if path.endswith(":\\"):
            drive_letter = path[0].lower()
            dir_name = f"drive_{drive_letter}"
        else:
            dir_name = os.path.basename(path)

        # 如果是编辑现有目录，检查别名是否变化
        if self.dir_obj and self.dir_obj.alias != alias:
            # 在新的别名下设置时间戳
            password_change_timestamps["directories"][alias] = time.time()

        self.result = ShareDirectory(
            self.path_var.get(),
            self.alias_var.get(),
            self.password_var.get(),
            self.desc_var.get(),
            self.admin_password_var.get(),  # 新增：包含目录管理密码
            self.totp_secret_var.get().strip() if self.totp_enabled_var.get() else "",
            self.totp_only_var.get(),  # 目录管理员仅验证码登录(免密)
        )
        self.result.name = dir_name  # 设置唯一标识名
        self.destroy()

    def cancel(self):
        self.destroy()

    def toggle_password_visibility(self, entry, btn):
        """在遮蔽(*)和明文之间切换指定密码输入框，并更新按钮图标"""
        if entry.cget("show") == "*":
            entry.configure(show="")
            btn.configure(text="🚫")
        else:
            entry.configure(show="*")
            btn.configure(text="👁")

    def toggle_totp(self):
        """启用/禁用目录管理员TOTP两步验证"""
        enabled = self.totp_enabled_var.get()
        if enabled and not self.totp_secret_var.get().strip():
            self.gen_totp_secret(self.totp_secret_var)
        state = "readonly" if enabled else "normal"
        self.totp_entry.configure(state=state)
        # 仅在启用TOTP时"免密"开关可操作，否则复位（与网页端联动一致）
        only_state = "normal" if enabled else "disabled"
        if hasattr(self, 'totp_only_chk'):
            self.totp_only_chk.configure(state=only_state)
        if not enabled:
            self.totp_only_var.set(False)

    def gen_totp_secret(self, var):
        """生成新的TOTP密钥并填充到指定的StringVar"""
        var.set(pyotp.random_base32())
        tkmessagebox.showinfo(
            "两步验证",
            "已生成新密钥。请复制绑定链接，在身份验证器应用中添加账户。"
            "启用保存后，登录该目录时需要输入动态验证码。")

    @staticmethod
    def copy_totp_link(parent, secret):
        """复制TOTP绑定链接到剪贴板"""
        secret = (secret or '').strip()
        if not secret:
            tkmessagebox.showwarning("提示", "请先生成TOTP密钥")
            return
        link = f"https://2fa.it0731.cn/tok/{secret}"
        parent.clipboard_clear()
        parent.clipboard_append(link)
        tkmessagebox.showinfo("已复制", f"绑定链接已复制到剪贴板：\n{link}")


class PageSettingsDialog(ttk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()  # 先隐藏窗口
        self.title("页面设置")
        self.geometry("500x400")
        icon_path = get_path("static/favicon.ico")
        self.iconbitmap(icon_path)

        self.result = None

        # 页面标题设置
        title_frame = ttk.Frame(self)
        title_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(title_frame, text="页面标题:").pack(side=LEFT)
        self.title_var = tk.StringVar(value=config.page_title)
        self.title_entry = ttk.Entry(title_frame, textvariable=self.title_var)
        self.title_entry.pack(side=LEFT, fill=X, expand=YES, padx=(10, 0))
        ToolTip(self.title_entry, "设置网页标题，显示在浏览器标签页上")

        # Logo名称设置
        logo_name_frame = ttk.Frame(self)
        logo_name_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(logo_name_frame, text="Logo名称:").pack(side=LEFT)
        self.logo_name_var = tk.StringVar(value=config.logo_name)
        self.logo_name_entry = ttk.Entry(
            logo_name_frame, textvariable=self.logo_name_var
        )
        self.logo_name_entry.pack(side=LEFT, fill=X, expand=YES, padx=(10, 0))
        ToolTip(self.logo_name_entry, "设置左上角显示的Logo名称")

        # Logo图片设置
        logo_frame = ttk.LabelFrame(self, text="Logo图片", padding="5")
        logo_frame.pack(fill=X, padx=10, pady=5)

        # 图片路径输入
        path_frame = ttk.Frame(logo_frame)
        path_frame.pack(fill=X, pady=2)
        ttk.Label(path_frame, text="图片路径:").pack(side=LEFT)
        self.logo_path_var = tk.StringVar(value=config.logo_image_url)
        self.logo_path_entry = ttk.Entry(path_frame, textvariable=self.logo_path_var)
        self.logo_path_entry.pack(side=LEFT, fill=X, expand=YES, padx=(10, 5))
        ttk.Button(path_frame, text="浏览", command=self.browse_logo).pack(side=RIGHT)

        # 图片预览
        preview_frame = ttk.Frame(logo_frame)
        preview_frame.pack(fill=X, pady=5)
        ttk.Label(preview_frame, text="预览:").pack(side=LEFT)
        self.preview_label = ttk.Label(
            preview_frame, text="无图片", relief="sunken", width=20
        )
        self.preview_label.pack(side=LEFT, padx=(10, 0))

        # 提示信息
        info_frame = ttk.Frame(logo_frame)
        info_frame.pack(fill=X, pady=2)
        info_text = "支持本地图片文件和远程URL\n推荐尺寸: 高度30px，格式: PNG/JPG/GIF"
        ttk.Label(info_frame, text=info_text, font=("宋体", 8), foreground="gray").pack(
            side=LEFT
        )

        # 确定取消按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=BOTTOM, pady=10)
        ttk.Button(btn_frame, text="确定", command=self.confirm).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.cancel).pack(side=LEFT)

        # 设置窗口居中显示
        self.update_idletasks()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()

        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()

        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2

        self.geometry(f"+{x}+{y}")
        self.deiconify()  # 显示窗口

        self.transient(parent)
        self.grab_set()

        # 绑定路径变化事件
        self.logo_path_var.trace("w", self.update_preview)
        self.update_preview()

    def browse_logo(self):
        """浏览选择logo图片"""
        filetypes = [
            ("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp"),
            ("PNG文件", "*.png"),
            ("JPEG文件", "*.jpg *.jpeg"),
            ("GIF文件", "*.gif"),
            ("所有文件", "*.*"),
        ]
        filename = filedialog.askopenfilename(title="选择Logo图片", filetypes=filetypes)
        if filename:
            self.logo_path_var.set(filename)

    def update_preview(self, *args):
        """更新图片预览"""
        path = self.logo_path_var.get()
        if not path:
            self.preview_label.configure(text="无图片", image="")
            return

        try:
            # 检查是否是本地文件
            if os.path.exists(path):
                image = Image.open(path)
                # 调整预览大小
                image.thumbnail((100, 30), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=photo, text="")
                self.preview_label.image = photo  # 保持引用
            elif path.startswith(("http://", "https://")):
                self.preview_label.configure(text="远程图片", image="")
            else:
                self.preview_label.configure(text="无效路径", image="")
        except Exception as e:
            self.preview_label.configure(text="预览失败", image="")

    def confirm(self):
        """确认设置"""
        page_title = self.title_var.get().strip()
        logo_name = self.logo_name_var.get().strip()
        logo_path = self.logo_path_var.get().strip()

        if not page_title:
            tkmessagebox.showerror("错误", "页面标题不能为空")
            return

        if not logo_name:
            tkmessagebox.showerror("错误", "Logo名称不能为空")
            return

        # 处理本地图片文件
        final_logo_url = ""
        new_filename = None

        if logo_path:
            if os.path.exists(logo_path):
                # 本地文件，复制到static/logos目录
                try:
                    import shutil

                    filename = os.path.basename(logo_path)
                    # 生成唯一文件名避免冲突
                    name, ext = os.path.splitext(filename)
                    timestamp = str(int(time.time()))
                    new_filename = f"{name}_{timestamp}{ext}"

                    target_path = os.path.join(config.logo_dir, new_filename)
                    shutil.copy2(logo_path, target_path)
                    final_logo_url = f"logos/{new_filename}"

                    # 清理旧的logo文件
                    cleanup_old_logos(config.logo_dir, new_filename)

                except Exception as e:
                    tkmessagebox.showerror("错误", f"复制图片文件失败: {str(e)}")
                    return
            elif logo_path.startswith(("http://", "https://")):
                # 远程URL，直接使用，清理所有本地logo文件
                final_logo_url = logo_path
                cleanup_old_logos(config.logo_dir)
            else:
                tkmessagebox.showerror("错误", "无效的图片路径")
                return
        else:
            # 如果清空了logo路径，清理所有logo文件
            cleanup_old_logos(config.logo_dir)

        self.result = {
            "page_title": page_title,
            "logo_name": logo_name,
            "logo_image_url": final_logo_url,
        }
        self.destroy()

    def cancel(self):
        self.destroy()


class FileShareService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FileShareService"
    _svc_display_name_ = "FS文件分享服务"
    _svc_description_ = "提供文件共享Web服务"

    def __init__(self, args):
        try:
            _append_svc_diag("FileShareService.__init__ 开始")
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.server = None

            # 服务器实例引用（用于停止服务器）
            self.http_servers = []
            self.https_servers = []
            self.server_thread = None
            self.executor = None

            # 设置工作目录与密钥/logo 目录：服务进程统一使用主程序目录，与 GUI 共用同一份配置
            _service_run_dir = _SERVICE_MAIN_DIR or os.path.dirname(os.path.abspath(sys.executable))
            os.chdir(_service_run_dir)
            try:
                set_key_dir(_service_run_dir)
                config.logo_dir = os.path.join(_service_run_dir, "static", "logos")
                os.makedirs(config.logo_dir, exist_ok=True)
            except Exception:
                pass

            # 确保日志目录存在并可写
            log_dir = os.path.join(get_app_path(), "logs")
            os.makedirs(log_dir, exist_ok=True)

            # 初始化日志
            self.logger = setup_service_logger()

            # 初始化SSL管理器
            from ssl_manager import SSLCertificateManager

            self.ssl_manager = SSLCertificateManager(config)

            # 初始化 Caddy 管理器（服务模式同样支持 Caddy 自动 HTTPS）
            from caddy_manager import CaddyManager

            self.caddy_manager = CaddyManager(config)

            self.logger.info("服务初始化完成")
        except Exception as e:
            # 使用 Windows 事件日志记录初始化错误
            servicemanager.LogErrorMsg(f"服务初始化失败: {str(e)}")
            raise

    def _caddy_mode_active(self):
        """服务模式：判断是否使用 Caddy 反代自动 HTTPS"""
        try:
            return bool(
                config.ssl_enabled
                and config.caddy_enabled
                and self.caddy_manager.caddy_available()
            )
        except Exception:
            return False

    def SvcDoRun(self):
        _append_svc_diag("SvcDoRun 开始")
        try:
            # 等待配置文件就绪
            max_retries = 10
            retry_count = 0
            while retry_count < max_retries:
                try:
                    config.load()
                    break
                except Exception as e:
                    retry_count += 1
                    self.logger.warning(
                        f"配置加载失败,重试 {retry_count}/{max_retries}: {e}"
                    )
                    time.sleep(1)

            if retry_count >= max_retries:
                raise Exception("无法加载配置文件")

            def run_server():
                # 系统服务模式的服务器启动函数
                # 根据配置选择Cheroot或Werkzeug

                import threading
                from concurrent.futures import ThreadPoolExecutor

                try:
                    if config.use_waitress:
                        # 使用Cheroot（高性能生产模式）
                        from cheroot_server import (
                            create_cheroot_http_server,
                            create_cheroot_https_server,
                        )

                        # 创建HTTP服务器
                        http_server = create_cheroot_http_server(
                            flask_app,
                            host="0.0.0.0",
                            port=config.port,
                            threads=get_optimal_threads(),
                            connection_limit=1000,
                            channel_timeout=config.upload_timeout,
                        )

                        # 保存服务器引用
                        self.http_servers.append(http_server)
                        servers = [("HTTP", http_server)]
                        flask_app.logger.info(
                            f"Cheroot HTTP服务器已创建，端口: {config.port}"
                        )
                        _maybe_start_webdav()  # 可选 WebDAV 独立端口

                        # 如果启用SSL，创建HTTPS服务器
                        if config.ssl_enabled:
                            if self._caddy_mode_active():
                                # Caddy 反代模式：HTTPS 由 Caddy 托管，无需 Cheroot HTTPS
                                self.logger.info(
                                    f"Caddy 反代模式：HTTPS 端口 {config.ssl_port} 由 Caddy 接管"
                                )
                                if self.caddy_manager.start():
                                    self.logger.info(
                                        f"Caddy 反代已启动: "
                                        f"https://{config.ssl_domain}:{config.ssl_port}"
                                    )
                                else:
                                    self.logger.error("Caddy 反代启动失败")
                            elif self.ssl_manager.has_valid_certificate():
                                cert_path = self.ssl_manager.get_cert_file_path()
                                key_path = self.ssl_manager.get_key_file_path()
                                if cert_path and key_path:
                                    try:
                                        https_server = create_cheroot_https_server(
                                            flask_app,
                                            host="0.0.0.0",
                                            port=config.ssl_port,
                                            cert_file=cert_path,
                                            key_file=key_path,
                                            threads=get_optimal_threads(),
                                            connection_limit=1000,
                                            channel_timeout=config.upload_timeout,
                                        )
                                        # 保存服务器引用
                                        self.https_servers.append(https_server)
                                        servers.append(("HTTPS", https_server))
                                        flask_app.logger.info(
                                            f"Cheroot HTTPS服务器已创建，端口: {config.ssl_port}"
                                        )
                                    except Exception as e:
                                        flask_app.logger.error(
                                            f"Cheroot HTTPS服务器创建失败: {e}"
                                        )
                                else:
                                    flask_app.logger.warning(
                                        "SSL已启用但证书文件路径无效"
                                    )
                            else:
                                flask_app.logger.warning("SSL已启用但没有有效证书")

                        # 启动Cheroot服务器
                        self.executor = ThreadPoolExecutor(max_workers=len(servers))
                        futures = []
                        for server_type, server in servers:
                            future = self.executor.submit(server.run)
                            futures.append((server_type, future))
                            flask_app.logger.info(f"Cheroot {server_type}服务器已启动")

                        # 等待所有服务器
                        for server_type, future in futures:
                            try:
                                future.result()
                            except Exception as e:
                                flask_app.logger.error(
                                    f"Cheroot {server_type}服务器错误: {e}"
                                )

                    else:
                        # 使用Werkzeug（调试模式）
                        from werkzeug.serving import make_server

                        servers = []

                        # 创建HTTP服务器
                        http_server = make_server("0.0.0.0", config.port, flask_app)
                        self.http_servers.append(http_server)
                        servers.append(("HTTP", http_server, config.port))
                        flask_app.logger.info(
                            f"Werkzeug HTTP服务器已创建，端口: {config.port}"
                        )

                        # 如果启用SSL，创建HTTPS服务器
                        if config.ssl_enabled:
                            if self._caddy_mode_active():
                                # Caddy 反代模式：HTTPS 由 Caddy 托管，无需 Werkzeug HTTPS
                                self.logger.info(
                                    f"Caddy 反代模式：HTTPS 端口 {config.ssl_port} 由 Caddy 接管"
                                )
                                if self.caddy_manager.start():
                                    self.logger.info(
                                        f"Caddy 反代已启动: "
                                        f"https://{config.ssl_domain}:{config.ssl_port}"
                                    )
                                else:
                                    self.logger.error("Caddy 反代启动失败")
                            elif self.ssl_manager.has_valid_certificate():
                                cert_path = self.ssl_manager.get_cert_file_path()
                                key_path = self.ssl_manager.get_key_file_path()
                                if cert_path and key_path:
                                    try:
                                        import ssl

                                        ssl_context = ssl.SSLContext(
                                            ssl.PROTOCOL_TLS_SERVER
                                        )
                                        ssl_context.load_cert_chain(cert_path, key_path)
                                        ssl_context.check_hostname = False
                                        ssl_context.verify_mode = ssl.CERT_NONE

                                        https_server = make_server(
                                            "0.0.0.0",
                                            config.ssl_port,
                                            flask_app,
                                            ssl_context=ssl_context,
                                        )
                                        self.https_servers.append(https_server)
                                        servers.append(
                                            ("HTTPS", https_server, config.ssl_port)
                                        )
                                        flask_app.logger.info(
                                            f"Werkzeug HTTPS服务器已创建，端口: {config.ssl_port}"
                                        )
                                    except Exception as e:
                                        flask_app.logger.error(
                                            f"Werkzeug HTTPS服务器创建失败: {e}"
                                        )
                                else:
                                    flask_app.logger.warning(
                                        "SSL已启用但证书文件路径无效"
                                    )
                            else:
                                flask_app.logger.warning("SSL已启用但没有有效证书")

                        # 启动Werkzeug服务器
                        self.executor = ThreadPoolExecutor(max_workers=len(servers))
                        futures = []
                        for server_type, server, port in servers:
                            future = self.executor.submit(server.serve_forever)
                            futures.append((server_type, future, port))
                            flask_app.logger.info(
                                f"Werkzeug {server_type}服务器已启动，端口: {port}"
                            )

                        # 等待所有服务器
                        for server_type, future, port in futures:
                            try:
                                future.result()
                            except Exception as e:
                                flask_app.logger.error(
                                    f"Werkzeug {server_type}服务器错误: {e}"
                                )

                except Exception as e:
                    flask_app.logger.error(f"服务器启动失败: {e}")
                    raise

            self.server_thread = threading.Thread(target=run_server, daemon=True)
            self.server_thread.start()

            # 尽早报告服务运行状态，避免 SCM 在 ServicesPipeTimeout(默认30秒) 内因启动缓慢终止服务进程。
            # PyInstaller 单文件版启动时需要解压全部资源，配置加载、防火墙、证书监控等耗时步骤放到 RUNNING 之后执行。
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)

            # 添加防火墙规则（HTTP + HTTPS 合并为一条，逗号分隔）
            firewall_ports = [config.port]
            if config.ssl_enabled and config.ssl_port and config.ssl_port != config.port:
                firewall_ports.append(config.ssl_port)
            self.add_firewall_rule(firewall_ports)

            if config.auto_cleanup and not is_cleanup_running():
                start_cleanup_thread()  # 启动清理线程

            # 启动SSL证书监控（系统服务模式）- Caddy 模式下由 Caddy 自行申请/续签
            if config.ssl_enabled and not self._caddy_mode_active():
                self.ssl_manager.start_certificate_monitor()
                self.logger.info("系统服务SSL证书监控已启动")

            # 使用 Windows 事件对象等待
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        except Exception as e:
            self.logger.error(f"服务错误: {str(e)}")
            self.logger.error(traceback.format_exc())
            # 同步写入 Windows 事件日志，方便在服务管理器/事件查看器里定位
            try:
                servicemanager.LogErrorMsg(
                    f"FS文件分享服务运行失败: {str(e)}\n{traceback.format_exc()}"
                )
            except Exception:
                pass
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.logger.info("开始停止系统服务...")

        try:
            # 1. 优雅停止所有HTTP服务器（给活跃连接一些时间）
            self.logger.info("尝试优雅停止服务器...")
            for server in self.http_servers:
                try:
                    if hasattr(server, "stop"):
                        # Cheroot服务器
                        server.stop()
                        self.logger.info("HTTP服务器已停止")
                    elif hasattr(server, "shutdown"):
                        # Werkzeug服务器
                        server.shutdown()
                        self.logger.info("HTTP服务器已停止")
                except Exception as e:
                    self.logger.error(f"停止HTTP服务器时发生错误: {e}")

            # 2. 优雅停止所有HTTPS服务器
            for server in self.https_servers:
                try:
                    if hasattr(server, "stop"):
                        # Cheroot服务器
                        server.stop()
                        self.logger.info("HTTPS服务器已停止")
                    elif hasattr(server, "shutdown"):
                        # Werkzeug服务器
                        server.shutdown()
                        self.logger.info("HTTPS服务器已停止")
                except Exception as e:
                    self.logger.error(f"停止HTTPS服务器时发生错误: {e}")

            # 2.5 停止 Caddy 反向代理（如有）
            if hasattr(self, "caddy_manager"):
                try:
                    if self.caddy_manager.is_running():
                        self.caddy_manager.stop()
                        self.logger.info("Caddy 反代已停止")
                except Exception as e:
                    self.logger.error(f"停止 Caddy 反代时发生错误: {e}")

            # 3. 等待一段时间让连接自然结束
            import time

            self.logger.info("等待活跃连接结束...")
            time.sleep(3)

            # 4. 强制停止线程池（不等待任务完成）
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                    self.logger.info("线程池已强制停止")
                except Exception as e:
                    self.logger.error(f"停止线程池时发生错误: {e}")

            # 5. 强制终止服务器线程
            if self.server_thread and self.server_thread.is_alive():
                self.logger.info("等待服务器线程结束...")
                self.server_thread.join(timeout=5)
                if self.server_thread.is_alive():
                    self.logger.warning("服务器线程未能在5秒内正常结束，将强制终止")
                    # 强制终止线程（注意：这是不安全的，但在服务停止时是必要的）
                    try:
                        import ctypes

                        thread_id = self.server_thread.ident
                        if thread_id:
                            ctypes.windll.kernel32.TerminateThread(
                                ctypes.c_ulong(thread_id), 0
                            )
                            self.logger.info("服务器线程已强制终止")
                    except Exception as e:
                        self.logger.error(f"强制终止线程失败: {e}")
                else:
                    self.logger.info("服务器线程已正常结束")

            # 6. 强制关闭所有网络端口
            self._force_close_ports()

            # 7. 停止SSL证书监控
            if hasattr(self, "ssl_manager"):
                self.ssl_manager.stop_certificate_monitor()
                self.logger.info("系统服务SSL证书监控已停止")

            # 8. 停止清理线程
            if is_cleanup_running():
                stop_cleanup_thread()
                self.logger.info("清理线程已停止")

            self.logger.info("系统服务停止完成")

        except Exception as e:
            self.logger.error(f"停止服务时发生错误: {e}")
        finally:
            # 最后设置停止事件
            win32event.SetEvent(self.stop_event)

    def _force_close_ports(self):
        """强制关闭服务使用的端口"""
        try:
            import subprocess

            ports_to_close = [config.port]
            if config.ssl_enabled:
                ports_to_close.append(config.ssl_port)

            for port in ports_to_close:
                try:
                    # 查找占用端口的进程
                    result = subprocess.run(
                        ["netstat", "-ano", "|", "findstr", f":{port}"],
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )

                    if result.stdout:
                        lines = result.stdout.strip().split("\n")
                        pids = set()
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 5 and f":{port}" in parts[1]:
                                pid = parts[-1]
                                if pid.isdigit():
                                    pids.add(pid)

                        # 终止占用端口的进程
                        for pid in pids:
                            try:
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid],
                                    capture_output=True,
                                    timeout=5,
                                )
                                self.logger.info(
                                    f"已强制终止占用端口{port}的进程PID:{pid}"
                                )
                            except Exception as e:
                                self.logger.error(f"终止进程PID:{pid}失败: {e}")

                except Exception as e:
                    self.logger.error(f"处理端口{port}时发生错误: {e}")

        except Exception as e:
            self.logger.error(f"强制关闭端口时发生错误: {e}")

    def add_firewall_rule(self, ports):
        """为指定端口列表统一添加防火墙放行规则（HTTP/HTTPS 合并为一条，用逗号分隔）
        ports: 端口列表，如 [12345, 443]；netsh localport 支持逗号分隔多端口
        """
        try:
            # 统一规则名，端口变更时自动替换，避免产生重复规则
            rule_name = "File_Share_Port"
            # 去重保序
            ports = list(dict.fromkeys(int(p) for p in ports if p))
            ports_str = ",".join(str(p) for p in ports)
            self.logger.info(f"自动配置防火墙放行规则: {rule_name} 端口 {ports_str}")

            # 1) 清理所有旧的 File_Share_* 规则（含历史遗留/重复项）
            self._delete_firewall_rules_by_prefix("File_Share_")

            # 2) 新建入站 + 出站放行规则（统一名，指向当前 HTTP/HTTPS 端口）
            commands = [
                f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={ports_str}',
                f'netsh advfirewall firewall add rule name="{rule_name}" dir=out action=allow protocol=TCP localport={ports_str}',
            ]

            for cmd in commands:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    check=True,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.stderr is None:
                    self.logger.info(
                        f"自动处理防火墙放行规则{rule_name}命令,处理结果：{result.stdout} \n错误: {result.stderr}"
                    )

        except Exception as e:
            self.logger.info(f"自动添加防火墙放行规则{rule_name},错误: {str(e)}")

    def _list_firewall_rules_by_prefix(self, prefix):
        """枚举名称以 prefix 开头的防火墙规则名（用于清理旧规则）"""
        try:
            result = subprocess.run(
                "netsh advfirewall firewall show rule name=all",
                shell=True,
                capture_output=True,
                text=True,
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            names = []
            for line in result.stdout.splitlines():
                line = line.strip()
                # 兼容中英文系统："Rule Name:" / "规则名称:"
                if line.lower().startswith("rule name:") or "规则名称" in line:
                    if ":" in line:
                        name = line.split(":", 1)[1].strip()
                        if name.startswith(prefix):
                            names.append(name)
            # 去重（同一条规则名可能出现在多行）
            return list(dict.fromkeys(names))
        except Exception as e:
            self.logger.info(f"枚举防火墙规则失败: {e}")
            return []

    def _delete_firewall_rules_by_prefix(self, prefix):
        """删除所有名称以 prefix 开头的防火墙规则"""
        for name in self._list_firewall_rules_by_prefix(prefix):
            try:
                subprocess.run(
                    f'netsh advfirewall firewall delete rule name="{name}"',
                    shell=True,
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.logger.info(f"已清理旧防火墙规则: {name}")
            except Exception as e:
                self.logger.info(f"删除防火墙规则 {name} 失败: {e}")


def run_headless_server():
    """无界面服务器模式（--headless-server）：不创建 GUI、不走 Windows 服务握手，
    仅加载配置并启动 HTTP/HTTPS 服务器后阻塞运行。

    用途：
    - 作为 NSSM/计划任务托管的「后台服务」业务进程（服务握手由 NSSM 完成）
    - Linux systemd 直接拉起本模式
    与 GUI / pywin32 服务共用同一份主目录配置/密钥/日志，SSL(Caddy) 同样受支持。
    """
    from concurrent.futures import ThreadPoolExecutor

    try:
        config.load()
    except Exception as e:
        _append_svc_diag(f"headless 配置加载失败: {e}")
        return 1
    _append_svc_diag("headless 服务器模式启动")

    try:
        os.makedirs(os.path.join(get_app_path(), "logs"), exist_ok=True)
    except Exception:
        pass

    servers = []  # [(名称, 运行函数)]
    caddy_manager = None

    try:
        # SSL/Caddy 反代：优先 Caddy 托管 HTTPS（反代到 HTTP 端口），否则手动证书 HTTPS
        caddy_active = bool(config.ssl_enabled and getattr(config, "caddy_enabled", False))
        if caddy_active:
            from caddy_manager import CaddyManager

            caddy_manager = CaddyManager(config)
            if caddy_manager.caddy_available():
                if caddy_manager.ensure_started():
                    _append_svc_diag("headless Caddy 反代已启动，HTTPS 由 Caddy 托管")
                else:
                    _append_svc_diag("headless Caddy 启动失败，仅提供 HTTP")
                    caddy_manager = None
            else:
                _append_svc_diag("headless caddy.exe 不存在，仅提供 HTTP")
                caddy_manager = None

        if config.use_waitress:
            from cheroot_server import (
                create_cheroot_http_server,
                create_cheroot_https_server,
            )

            http_server = create_cheroot_http_server(
                flask_app,
                host="0.0.0.0",
                port=config.port,
                threads=get_optimal_threads(),
                connection_limit=1000,
                channel_timeout=config.upload_timeout,
            )
            servers.append(("HTTP", http_server.run))

            # 手动证书 HTTPS（非 Caddy 模式）
            if config.ssl_enabled and caddy_manager is None:
                try:
                    from ssl_manager import SSLCertificateManager

                    ssl_manager = SSLCertificateManager(config)
                    if ssl_manager.has_valid_certificate():
                        cert_path = ssl_manager.get_cert_file_path()
                        key_path = ssl_manager.get_key_file_path()
                        if cert_path and key_path:
                            https_server = create_cheroot_https_server(
                                flask_app,
                                host="0.0.0.0",
                                port=config.ssl_port,
                                cert_file=cert_path,
                                key_file=key_path,
                                threads=get_optimal_threads(),
                                connection_limit=1000,
                                channel_timeout=config.upload_timeout,
                            )
                            servers.append(("HTTPS", https_server.run))
                except Exception as e:
                    _append_svc_diag(f"headless HTTPS 启动跳过: {e}")
        else:
            from werkzeug.serving import make_server

            http_server = make_server("0.0.0.0", config.port, flask_app)
            servers.append(("HTTP", http_server.serve_forever))
    except Exception as e:
        _append_svc_diag(f"headless 服务器创建失败: {e}")
        if caddy_manager:
            try:
                caddy_manager.stop()
            except Exception:
                pass
        return 1

    if config.auto_cleanup and not is_cleanup_running():
        try:
            start_cleanup_thread()
        except Exception:
            pass

    _append_svc_diag("headless 服务器已创建: " + ", ".join(n for n, _ in servers))
    _maybe_start_webdav()  # 可选 WebDAV 独立端口
    executor = ThreadPoolExecutor(max_workers=max(1, len(servers)))
    futures = [executor.submit(run) for _, run in servers]
    try:
        for future in futures:
            future.result()  # 阻塞直到服务器退出
    except KeyboardInterrupt:
        pass
    finally:
        # 正常退出（Ctrl+C / 服务器停止）时清理 Caddy、WebDAV 与清理线程
        _maybe_stop_webdav()
        if caddy_manager:
            try:
                caddy_manager.stop()
            except Exception:
                pass
        try:
            if is_cleanup_running():
                stop_cleanup_thread()
        except Exception:
            pass
    _append_svc_diag("headless 服务器退出")
    return 0


class FileShareApp:
    def handle_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        if files:
            path = files[0]
            normalized_path = os.path.normpath(path.strip('"'))

            if os.path.exists(normalized_path) and os.access(normalized_path, os.R_OK):
                if os.path.isdir(normalized_path):
                    # 处理分区根目录
                    if normalized_path.endswith(":\\"):
                        # 使用驱动器字母作为唯一标识
                        drive_letter = normalized_path[0].lower()
                        dir_name = f"drive_{drive_letter}"
                        default_alias = f"disk_{drive_letter.upper()}"
                    else:
                        dir_name = os.path.basename(normalized_path)
                        # 将中文目录名转换为拼音
                        default_alias = chinese_to_pinyin(dir_name)
                        # 如果转换后为空，使用原名称的安全版本
                        if not default_alias:
                            default_alias = re.sub(r"[^\w]", "", dir_name.lower())
                            if not default_alias or default_alias[0].isdigit():
                                default_alias = "dir_" + default_alias

                    dialog = DirectoryDialog(self.root)
                    dialog.path_var.set(normalized_path)
                    # 设置默认别名，使用和browse_dir相同的逻辑
                    # 临时禁用验证，设置别名后重新启用
                    dialog.alias_entry.configure(validate="none")
                    dialog.alias_var.set(default_alias)
                    dialog.alias_entry.configure(validate="key")

                    self.root.wait_window(dialog)
                    if dialog.result:
                        # 使用唯一标识作为目录名
                        config.shared_dirs[dir_name] = dialog.result
                        self.refresh_dir_list()
                        self.save_config()
                        self.log_area.insert(
                            END, f"已添加共享目录: {normalized_path}\n"
                        )
                        self.log_area.see(END)
                else:
                    self.log_area.insert(END, "只能添加文件夹!\n")
                    self.log_area.see(END)

    def __init__(self, root, style):
        self.root = root
        self.style = style

        # 网页保存配置时同步窗体（root.after 保证在主线程执行）
        set_gui_config_sync_cb(lambda: self.root.after(0, self.refresh_vars_from_config))
        # 跨进程兜底：低频监听配置文件变化（后台服务等独立进程写盘时刷新窗体）
        self._last_config_mtime = 0.0
        self._poll_config_mtime()

        # 初始化日志
        self.logger = setup_service_logger(flask_app)

        # 初始化SSL管理器
        from ssl_manager import SSLCertificateManager

        self.ssl_manager = SSLCertificateManager(config)

        # 初始化 Caddy 管理器（程序目录存在 caddy.exe 时启用自动 HTTPS）
        from caddy_manager import CaddyManager

        self.caddy_manager = CaddyManager(config)

        # 后台服务模式下时钟变量
        self.service_debounce_timer = None
        self.service_monitor_timer = None

        # 后台服务运行中标识变量
        self.service_status = None

        # 1. 设置窗口基本属性
        self.root.title("文件分享服务器")
        self.root.geometry("860x620")

        # 2. 初始化变量
        self.init_variables()

        # 3. 创建GUI
        self.create_gui()

        # 4. 配置和加载设置
        self.setup_config()

        # 5. 注册DND
        self.setup_dnd()

        # 6. 显示窗口
        self.draw_window()
        self.setup_traces()  # 开启变量监听

        # 7. 开始监听后台服务状态
        self.start_service_monitor()

        # 8. 启动SSL证书监控（Caddy 模式下由 Caddy 自行申请/续签）
        if config.ssl_enabled and not self.is_caddy_mode_active():
            self.ssl_manager.start_certificate_monitor()

        # 9. 更新SSL状态显示（延迟执行，确保UI已完全初始化）
        self.root.after(100, self.update_ssl_status)

    def init_variables(self):
        self.server_running = False
        self.back_server_running = False
        self.server_thread = None
        self.log_enabled = tk.BooleanVar(value=False)
        self.server_type = tk.BooleanVar(value=config.use_waitress)
        self.admin_password_var = tk.StringVar(value=config.admin_password)
        self.password_var = tk.StringVar()
        self.port_var = tk.StringVar(value="12345")
        self.cleanup_time_var = tk.IntVar(value=config.cleanup_time)
        self.auto_cleanup_var = tk.BooleanVar(value=config.auto_cleanup)
        self.upload_concurrency_var = tk.IntVar(value=config.upload_concurrency)
        self.upload_chunk_size_var = tk.IntVar(value=config.upload_chunk_size // 1048576)
        self.about_window = None

    def create_gui(self):
        # 创建主框架
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=BOTH, expand=YES)

        # 创建各个部分
        self.create_theme_section()
        self.create_directory_section()
        self.create_settings_section()
        self.create_buttons_section()
        self.create_log_area()

    def setup_config(self):
        self.load_config()
        self.minimize_to_tray()

        # 设置主题
        if config.dark_theme:
            self.style.theme_use("darkly")
            self.theme_switch.state(["selected"])
        else:
            self.style.theme_use("cosmo")
            self.theme_switch.state(["!selected"])

        # 设置日志
        self.log_enabled.set(config.log_to_file)
        if config.log_to_file:
            self.file_handler = self.setup_file_logging()

        # 设置服务器类型
        self.server_type.set(config.use_waitress)
        if config.use_waitress:
            self.server_switch.state(["selected"])
            self.waitress_label.configure(style="success.TLabel")
        else:
            self.server_switch.state(["!selected"])
            self.werkzeug_label.configure(style="success.TLabel")

    def setup_dnd(self):
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.handle_drop)
            print("Main window DND registered successfully")
        except Exception as e:
            print("DND registration error:", e)

    def draw_window(self):
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # 以下是创建各个部分的方法
    def create_theme_section(self):
        # 添加主题切换开关
        theme_frame = ttk.Frame(self.main_frame)
        theme_frame.pack(fill=X, pady=2)

        # 使用ttkbootstrap的图标
        self.theme_switch = ttk.Checkbutton(
            theme_frame,
            bootstyle="round-toggle",
            text="",
            command=self.toggle_theme,
            padding=2,
        )

        # 添加主题切换图标标签
        self.light_icon = ttk.Label(theme_frame, text="☀", font=("Segoe UI", 10))
        self.dark_icon = ttk.Label(theme_frame, text="☾", font=("Segoe UI", 10))

        # SSL设置按钮
        self.ssl_settings_btn = ttk.Button(
            theme_frame,
            text="🔒 SSL设置",
            command=self.open_ssl_settings,
            style="secondary.TButton",
            width=20,
        )
        self.ssl_settings_btn.pack(side=LEFT, padx=(0, 10))

        # 减小水平间距
        self.dark_icon.pack(side=RIGHT, padx=(0, 2))
        self.theme_switch.pack(side=RIGHT, padx=2)
        self.light_icon.pack(side=RIGHT, padx=(2, 0))

    def create_directory_section(self):
        # 目录列表框架
        dir_frame = ttk.LabelFrame(self.main_frame, text="共享目录", padding="5")
        dir_frame.pack(fill=X, pady=5)

        # 按钮框架
        btn_frame = ttk.Frame(dir_frame)
        btn_frame.pack(fill=X)

        # 添加目录按钮
        ttk.Button(
            btn_frame,
            text="添加目录",
            command=self.add_directory,
            style="primary.TButton",
        ).pack(side=LEFT, padx=5)

        # 修改目录按钮
        ttk.Button(
            btn_frame,
            text="修改目录",
            command=lambda: self.edit_directory(None),
            style="info.TButton",
        ).pack(side=LEFT, padx=5)

        # 删除目录按钮
        ttk.Button(
            btn_frame,
            text="删除目录",
            command=self.remove_directory,
            style="danger.TButton",
        ).pack(side=LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="私有分享",
            command=self.open_share_manager,
            style="info.TButton",
        ).pack(side=LEFT, padx=5)

        # 创建带滚动条的列表框架
        list_frame = ttk.Frame(dir_frame)
        list_frame.pack(fill=X, pady=5)

        # 创建滚动条
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        # 创建目录列表
        self.dir_list = ttk.Treeview(
            list_frame,
            columns=("alias", "path", "has_password"),
            show="headings",
            height=6,
            yscrollcommand=scrollbar.set,
        )

        # 配置滚动条
        scrollbar.config(command=self.dir_list.yview)

        # 设置列标题
        self.dir_list.heading("alias", text="显示名称")
        self.dir_list.heading("path", text="路径")
        self.dir_list.heading("has_password", text="密码保护")

        # 设置列宽
        self.dir_list.column("alias", width=150)
        self.dir_list.column("path", width=300)
        self.dir_list.column("has_password", width=100)

        self.dir_list.pack(fill=X, expand=YES)

        # 绑定事件
        self.dir_list.bind("<Double-1>", self.edit_directory)
        self.dir_list.bind("<Delete>", self.remove_directory)
        self.dir_list.bind("<Button-3>", self.show_context_menu)

    def open_share_manager(self):
        from share_manager_ui.share_dialog import ShareManagerDialog

        dialog = ShareManagerDialog(self.root, self.style)
        self.root.wait_window(dialog)

    def create_settings_section(self):
        # 全局设置框架
        settings_frame = ttk.LabelFrame(self.main_frame, text="全局设置", padding="5")
        settings_frame.pack(fill=X, pady=5)

        # 创建水平框架容纳所有设置
        settings_container = ttk.Frame(settings_frame)
        settings_container.pack(fill=X, pady=5)

        # 管理员密码设置
        admin_pwd_frame = ttk.Frame(settings_container)
        admin_pwd_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        ttk.Label(admin_pwd_frame, text="管理员密码:").pack(side=LEFT)

        # 创建管理员密码输入框容器
        admin_pwd_entry_container = ttk.Frame(admin_pwd_frame)
        admin_pwd_entry_container.pack(side=LEFT, fill=X, expand=YES)

        # 管理员密码输入框和显隐按钮
        self.admin_pwd_entry = ttk.Entry(
            admin_pwd_entry_container,
            textvariable=self.admin_password_var,
            show="*",
            width=15,
        )
        self.admin_pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(self.admin_pwd_entry, "管理密码，一码通用，WEB页提示输入密码的地方用它都行")
        if show_password_toggle_enabled():
            self.admin_pwd_btn = ttk.Button(admin_pwd_entry_container, text="👁", width=3,
                                            command=lambda: self.toggle_password_visibility(self.admin_pwd_entry, self.admin_pwd_btn))
            self.admin_pwd_btn.pack(side=LEFT, padx=2)
            ToolTip(self.admin_pwd_btn, "显示/隐藏密码")

        # 全局密码设置
        pwd_frame = ttk.Frame(settings_container)
        pwd_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        ttk.Label(pwd_frame, text="全局访问密码:").pack(side=LEFT)

        # 创建全局密码输入框容器
        pwd_entry_container = ttk.Frame(pwd_frame)
        pwd_entry_container.pack(side=LEFT, fill=X, expand=YES)

        # 全局密码输入框和显隐按钮
        self.pwd_entry = ttk.Entry(
            pwd_entry_container, textvariable=self.password_var, show="*", width=15
        )
        self.pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(self.pwd_entry, "全局密码也就是进入WEB页面首页用的密码")
        if show_password_toggle_enabled():
            self.pwd_btn = ttk.Button(pwd_entry_container, text="👁", width=3,
                                      command=lambda: self.toggle_password_visibility(self.pwd_entry, self.pwd_btn))
            self.pwd_btn.pack(side=LEFT, padx=2)
            ToolTip(self.pwd_btn, "显示/隐藏密码")

        # 超级管理员 TOTP 两步验证设置
        admin_totp_frame = ttk.Frame(settings_container)
        admin_totp_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        # 第一行：启用开关 + 密钥 + 生成/复制
        admin_totp_row = ttk.Frame(admin_totp_frame)
        admin_totp_row.pack(fill=X)
        self.admin_totp_enabled_var = tk.BooleanVar(
            value=bool(getattr(config, 'admin_totp_secret', '')))
        ttk.Checkbutton(admin_totp_row, text="管理员两步验证(TOTP)",
                        variable=self.admin_totp_enabled_var,
                        command=self.toggle_admin_totp).pack(side=LEFT)
        self.admin_totp_secret_var = tk.StringVar(
            value=getattr(config, 'admin_totp_secret', ''))
        self.admin_totp_entry = ttk.Entry(admin_totp_row,
                                          textvariable=self.admin_totp_secret_var,
                                          width=20, state="readonly")
        self.admin_totp_entry.pack(side=LEFT, padx=3)
        ttk.Button(admin_totp_row, text="生成", width=4,
                   command=lambda: self.gen_totp_secret(self.admin_totp_secret_var)).pack(side=LEFT)
        ttk.Button(admin_totp_row, text="复制", width=4,
                   command=lambda: self.copy_totp_link(self.admin_totp_secret_var.get())).pack(side=LEFT, padx=1)
        # 第二行：仅验证码登录(免密)，独立一行保证可见
        admin_totp_row2 = ttk.Frame(admin_totp_frame)
        admin_totp_row2.pack(fill=X, pady=(2, 0))
        self.admin_totp_only_var = tk.BooleanVar(
            value=bool(getattr(config, 'admin_totp_only', False)))
        self.admin_totp_only_chk = ttk.Checkbutton(
            admin_totp_row2, text="仅验证码登录(免密)", variable=self.admin_totp_only_var)
        self.admin_totp_only_chk.pack(side=LEFT)
        ttk.Label(admin_totp_row2, text="启用TOTP后可用：登录时只需6位验证码，免密码").pack(side=LEFT, padx=4)
        self.toggle_admin_totp()  # 同步初始状态：未启用TOTP时免密置灰

        # 开关框架
        log_switch_frame = ttk.Frame(settings_frame)
        log_switch_frame.pack(fill=X, pady=5)

        # 端口设置
        port_frame = ttk.Frame(log_switch_frame)
        port_frame.pack(side=LEFT, padx=5)
        ttk.Label(port_frame, text="端口号:").pack(side=LEFT)
        ttk.Entry(
            port_frame,
            textvariable=self.port_var,
            width=8
        ).pack(side=LEFT)
        ToolTip(
            port_frame,
            f"HTTP服务监听端口号也就是用户WEB访问端口号\n\n "
            f"如：http://{get_local_ip()}:{self.port_var.get()}",
        )

        # 添加清理间隔设置
        ttk.Label(log_switch_frame, text="清理间隔(秒):").pack(side=tk.LEFT)
        ttk.Spinbox(
            log_switch_frame,
            from_=10,
            to=86400,
            textvariable=self.cleanup_time_var,
            width=3,
        ).pack(side=tk.LEFT)

        # 添加自动清理复选框
        self.auto_cleanup_checkbox = ttk.Checkbutton(
            log_switch_frame,
            text="自动清理",
            variable=self.auto_cleanup_var,
            style="squared-toggle",
        )
        self.auto_cleanup_checkbox.pack(side=tk.LEFT, padx=5)

        # 添加工具提示
        ToolTip(
            self.auto_cleanup_checkbox,
            "启用此选项将自动清理用户打包下载产生临时文件和过期的共享链接。",
        )

        # 上传并发数设置
        upload_frame = ttk.Frame(log_switch_frame)
        upload_frame.pack(side=LEFT, padx=2)
        ttk.Label(upload_frame, text="上传并发:").pack(side=tk.LEFT)
        ttk.Spinbox(
            upload_frame,
            from_=1,
            to=20,
            textvariable=self.upload_concurrency_var,
            width=3,
        ).pack(side=tk.LEFT)
        ToolTip(
            upload_frame,
            "同时上传的文件数量，内网高速链路可调大（5-10）",
        )

        # 分片大小设置（MB）
        chunk_frame = ttk.Frame(log_switch_frame)
        chunk_frame.pack(side=LEFT, padx=2)
        ttk.Label(chunk_frame, text="分片大小(MB):").pack(side=tk.LEFT)
        ttk.Spinbox(
            chunk_frame,
            from_=0.25,
            to=16,
            textvariable=self.upload_chunk_size_var,
            width=3,
            increment=0.25,
        ).pack(side=tk.LEFT)
        ToolTip(
            chunk_frame,
            "每个分片的大小（MB），内网高速链路可调大（1-4）以减少请求次数",
        )

        # 保存按钮
        self.save_btn = ttk.Button(
            log_switch_frame,
            text="保存(实时)",
            command=self.save_config,
            style="outline.TButton",
        )
        self.save_btn.pack(side=RIGHT, padx=(0, 15))
        ToolTip(
            self.save_btn,
            "虽然它能保存所有配置，\n但其实这里主要用于管理密码与全局密码的一个实时生效",
        )

        # 日志开关
        self.log_switch = ttk.Checkbutton(
            log_switch_frame,
            text="开启记录日志",
            variable=self.log_enabled,
            command=self.toggle_file_logging,
            style="squared-toggle",  # round-toggle
        )
        self.log_switch.pack(side=RIGHT, padx=(0, 15))
        ToolTip(self.log_switch, "启用此选项将自动将下面回显框日志记录到程序logs下面。")

        # 服务器类型开关
        self.waitress_label = ttk.Label(log_switch_frame, text="Cheroot")
        self.waitress_label.pack(side=RIGHT, padx=(0, 15))

        self.server_switch = ttk.Checkbutton(
            log_switch_frame,
            text="",
            variable=self.server_type,
            command=self.toggle_server_type,
            style="squared-toggle",
        )
        self.server_switch.pack(side=RIGHT, padx=(0, 2))
        ToolTip(
            self.server_switch,
            "切换werkzeug开发调试用单线程服务器\n或Cheroot生产环境适应多线程服务器",
        )

        self.werkzeug_label = ttk.Label(log_switch_frame, text="werkzeug")
        self.werkzeug_label.pack(side=RIGHT, padx=(0, 2))

        # 创建但不显示模式标签
        self.server_mode_label = ttk.Label(log_switch_frame)

    def create_buttons_section(self):
        # 启动按钮框架
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack()

        self.service_var = tk.BooleanVar(value=self.is_service_installed())
        self.service_checkbox = ttk.Checkbutton(
            btn_frame,
            text="安装为系统服务",
            variable=self.service_var,
            command=self.handle_service_toggle,
            style="squared-toggle",
        )
        self.service_checkbox.pack(side=LEFT, pady=10, padx=(0, 10))
        ToolTip(
            self.service_checkbox,
            "将程序安装成WINDOWS系统服务，实现开机运行，记得先调试好配置",
        )

        self.start_btn = ttk.Button(
            btn_frame,
            text="启动服务" if not self.service_var.get() else "启动后台服务",
            command=self.toggle_server,
            style="success.TButton",
        )
        self.start_btn.pack(side=LEFT, pady=10)

        # "打开页面"按钮
        self.page_btn = ttk.Button(
            btn_frame, text="打开页面", command=self.open_page, style="info.TButton"
        )
        self.page_btn.pack(side=LEFT, pady=10)
        self.page_btn.pack_forget()  # 初始不显示

        # "页面设置"按钮
        self.page_settings_btn = ttk.Button(
            btn_frame,
            text="页面设置",
            command=self.open_page_settings,
            style="secondary.TButton",
        )
        self.page_settings_btn.pack(side=LEFT, pady=10, padx=(10, 0))

    def create_log_area(self):
        # 日志显示区域
        self.log_area = ScrolledText(
            self.main_frame,
            padding=5,
            height=20,
            width=80,
            wrap=tk.WORD,
            font=("Consolas", 10),
        )
        self.log_area.pack(fill=BOTH, expand=YES, pady=5)

        # 设置日志处理（确保只添加一次）
        if not hasattr(self, "_log_handler_added"):  # 检查是否已经添加过处理器
            handler = RedirectHandler(self.log_area)
            self.logger.add(
                handler,
                format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
                level="INFO",
            )
            self._log_handler_added = True  # 标记为已添加

    def switch_server_type_ui(self, is_running):
        if is_running:
            # 隐藏开关组件
            self.waitress_label.pack_forget()
            self.server_switch.pack_forget()
            self.werkzeug_label.pack_forget()

            # 显示模式标签
            # mode_text = "服务模式：Waitress(生产)" if self.server_type.get() else "服务模式：werkzeug(调试)"
            if self.server_type.get():
                self.server_mode_label.configure(
                    text="服务模式：Cheroot(生产)",
                    bootstyle="success",  # 绿色
                )
            else:
                self.server_mode_label.configure(
                    text="服务模式：werkzeug(调试)",
                    bootstyle="warning",  # 橙色
                )
            self.server_mode_label.pack(side=RIGHT, padx=(0, 15))

        else:
            # 隐藏模式标签
            self.server_mode_label.pack_forget()

            # 显示原始组件
            self.waitress_label.pack(side=RIGHT, padx=(0, 15))
            self.server_switch.pack(side=RIGHT, padx=(0, 2))
            self.werkzeug_label.pack(side=RIGHT, padx=(0, 2))

    def toggle_file_logging(self):
        """切换文件日志记录状态"""
        config.log_to_file = self.log_enabled.get()
        if config.log_to_file:
            # 启用文件日志记录
            self.setup_file_logging()
        else:
            # 禁用文件日志记录
            self.disable_file_logging()
        config.save()

    def setup_file_logging(self):
        """设置文件日志记录"""
        if not os.path.exists("logs"):
            os.makedirs("logs")

        # 添加文件日志处理器
        log_file = f"logs/window_{datetime.now().strftime('%Y%m%d')}.log"
        self.logger.add(
            log_file,
            rotation="00:00",  # 每天午夜轮换
            retention="15 days",  # 保留最近15天的日志
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
            level="INFO",
        )

    def disable_file_logging(self):
        """禁用文件日志记录"""
        # 移除所有处理器并重新添加必要的处理器
        self.logger.remove()  # 移除所有处理器

        # 重新添加GUI日志处理器
        if hasattr(self, "_log_handler_added") and self._log_handler_added:
            handler = RedirectHandler(self.log_area)
            self.logger.add(
                handler,
                format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
                level="INFO",
            )

    def setup_traces(self):
        # 监听变量变化
        self.cleanup_time_var.trace_add("write", self.update_cleanup_time)
        self.auto_cleanup_var.trace_add("write", self.update_auto_cleanup)

    def update_cleanup_time(self, *args):
        config.cleanup_time = self.cleanup_time_var.get()
        print(f"Updated cleanup_time to {config.cleanup_time}")

    def update_auto_cleanup(self, *args):
        config.auto_cleanup = self.auto_cleanup_var.get()
        if config.auto_cleanup:
            flask_app.logger.info("开启自动清理")
        else:
            flask_app.logger.info("关闭自动清理")

    def open_page(self):
        # 在这里编写打开页面的逻辑代码
        if not serverUrl:
            ip = get_local_ip()
            webbrowser.open(f"http://{ip}:{config.port}")
        else:
            webbrowser.open(serverUrl)

    def create_tray_icon(self):
        icon_image = Image.open(get_path("static/favicon.ico"))
        menu = (
            # pystray.MenuItem('显示', self.show_window),
            pystray.MenuItem(
                "显示", lambda: self.root.after(0, self.show_window), default=True
            ),
            # 绑定显示窗口为默认事件，这样实现鼠标点击图标显示窗口比绑定on_click成功率高
            pystray.MenuItem("关于", self.about_app),
            pystray.MenuItem("退出", self.quit_app),
        )
        self.tray_icon = pystray.Icon(
            "file_share", icon_image, "文件分享服务器(letvar@qq.com)", menu
        )

    def show_window(self, icon=None):
        # 检查窗口是否已经显示，如果没有显示，则执行以下操作
        if not self.root.winfo_ismapped():
            self.root.deiconify()  # 取消窗口的图标化
        # 无论窗口是否可见，都将其状态设置为正常并提升到顶层，确保获得焦点
        self.root.state("normal")  # 将窗口状态设置为正常
        self.root.lift()  # 将窗口提升到顶层
        self.root.focus_force()  # 强制窗口获得焦点

    def about_app(self):

        # 如果窗口已经存在，直接显示并返回
        if self.about_window and self.about_window.winfo_exists():
            self.about_window.deiconify()
            self.about_window.lift()
            return

        # 创建新窗口
        self.about_window = ttk.Toplevel(self.root)
        self.about_window.transient(self.root)
        self.about_window.title("关于")
        self.about_window.iconbitmap(get_path("static/favicon.ico"))
        self.about_window.resizable(False, False)

        # 设置窗口大小和位置
        window_width = 400
        window_height = 380
        x = self.root.winfo_x() + (self.root.winfo_width() - window_width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - window_height) // 2
        self.about_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        # 创建内容框架
        content_frame = ttk.Frame(self.about_window)
        content_frame.pack(expand=True, fill="both", padx=20, pady=20)

        # 加载图片
        try:
            image_path = get_path("static/zs.png")  #
            img = Image.open(image_path)
            img = img.resize((340, 178), Image.Resampling.LANCZOS)
            img_tk = ImageTk.PhotoImage(img)
            image_label = ttk.Label(content_frame, image=img_tk)
            image_label.image = img_tk  # 保持引用
            image_label.pack(pady=(0, 5))
        except Exception as e:
            flask_app.logger.error(f"加载图片失败: {e}", color="red")

        # 添加文字信息
        info_text = """
    如果喜欢，欢迎打赏支持，万分感谢！

    file_share HTTP文件分享服务器
    本工具基于python flask cheroot，
    支持前台窗口服务方式和WINDOWS后台服务方式
    支持IPv4和IPv6地址访问。
    窗口一些组件鼠标放上去会会弹出说明
    反馈: letvar@qq.com（秒回）

    """
        ttk.Label(content_frame, text=info_text, justify="left").pack()

        # 添加关闭按钮
        # ttk.Button(content_frame, text="关闭", command=close_about_window).pack(pady=(10, 0))

        # 绑定窗口关闭事件
        # about_window.protocol("WM_DELETE_WINDOW", lambda: close_about_window())

    def quit_app(self, icon=None):
        if self.server_running and self.service_status != 4:
            # self.toggle_server() # 直接关掉服务退出
            tkmessagebox.showwarning("提示", "请先手动停止服务后再退出")
            self.show_window()
            return

        if icon:
            icon.stop()
        self.stop_service_monitor()
        self.root.after(0, self.root.quit)

    def minimize_to_tray(self):
        if not hasattr(self, "tray_icon") or not self.tray_icon.visible:
            self.create_tray_icon()
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def on_closing(self):
        self.root.withdraw()
        # if self.server_running:
        #    self.root.withdraw()
        # else:
        #    if hasattr(self, 'tray_icon'):
        #        try:
        #            self.tray_icon.stop()
        #        except:
        #            pass
        #    self.root.quit()

    # 添加切换主题的方法：
    def toggle_theme(self):
        current_theme = self.style.theme.name
        if current_theme == "cosmo":
            self.style.theme_use("darkly")
            config.dark_theme = True
        else:
            self.style.theme_use("cosmo")
            config.dark_theme = False
        config.save()

    def open_ssl_settings(self):
        """打开SSL设置对话框"""
        try:
            from ssl_settings_dialog import SSLSettingsDialog

            # 创建更新回调函数
            def update_main_window():
                """更新主窗口SSL状态的回调函数"""
                self.update_ssl_status()
                self.log_area.insert(END, "SSL设置已更新\n")
                self.log_area.see(END)

                # Caddy 模式由 Caddy 自行申请/续签证书，不启用 ssl_manager 监控
                if config.ssl_enabled and not self.is_caddy_mode_active():
                    self.ssl_manager.start_certificate_monitor()
                else:
                    self.ssl_manager.stop_certificate_monitor()

            # 传递回调函数给对话框
            dialog = SSLSettingsDialog(
                self.root, config, self.ssl_manager, update_main_window
            )
            self.root.wait_window(dialog)

            if dialog.result:
                # 对话框关闭时再次更新状态（确保同步）
                self.update_ssl_status()

        except Exception as e:
            tkmessagebox.showerror("错误", f"打开SSL设置时发生错误: {str(e)}")

    def update_ssl_status(self):
        """更新SSL状态显示"""
        try:
            if hasattr(self, "ssl_settings_btn"):
                if config.ssl_enabled:
                    if self.is_caddy_mode_active():
                        # Caddy 反代模式
                        if self.caddy_manager.is_running():
                            self.ssl_settings_btn.configure(
                                text="🔒 SSL已启用(Caddy)", style="success.TButton"
                            )
                        else:
                            self.ssl_settings_btn.configure(
                                text="🔒 SSL待启动(Caddy)", style="warning.TButton"
                            )
                    elif self.ssl_manager.has_valid_certificate():
                        self.ssl_settings_btn.configure(
                            text="🔒 SSL已启用", style="success.TButton"
                        )
                    else:
                        self.ssl_settings_btn.configure(
                            text="🔒 SSL配置中", style="warning.TButton"
                        )
                else:
                    self.ssl_settings_btn.configure(
                        text="🔒 SSL设置", style="secondary.TButton"
                    )
        except Exception as e:
            self.logger.error(f"更新SSL状态显示时发生错误: {e}")

    def show_context_menu(self, event):
        selection = self.dir_list.selection()
        if selection:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="修改", command=lambda: self.edit_directory(None))
            menu.add_command(label="删除", command=self.remove_directory)
            menu.post(event.x_root, event.y_root)

    def toggle_password_visibility(self, entry, btn):
        """在遮蔽(*)和明文之间切换指定密码输入框，并更新按钮图标"""
        if entry.cget("show") == "*":
            entry.configure(show="")
            btn.configure(text="🚫")
        else:
            entry.configure(show="*")
            btn.configure(text="👁")

    def toggle_admin_totp(self):
        """启用/禁用超级管理员TOTP两步验证"""
        enabled = self.admin_totp_enabled_var.get()
        if enabled and not self.admin_totp_secret_var.get().strip():
            # 启用时若尚无密钥，自动生成
            self.gen_totp_secret(self.admin_totp_secret_var)
        state = "readonly" if enabled else "normal"
        self.admin_totp_entry.configure(state=state)
        # 仅在启用TOTP时"免密"开关可操作，否则复位（与网页端联动一致）
        only_state = "normal" if enabled else "disabled"
        if hasattr(self, 'admin_totp_only_chk'):
            self.admin_totp_only_chk.configure(state=only_state)
        if not enabled:
            self.admin_totp_only_var.set(False)

    def gen_totp_secret(self, var):
        """生成新的TOTP密钥并填充到指定的StringVar"""
        var.set(pyotp.random_base32())
        tkmessagebox.showinfo(
            "两步验证",
            "已生成新密钥。请复制绑定链接，在身份验证器应用中添加账户。"
            "启用保存后，登录时需要输入该应用的6位动态验证码。")

    def copy_totp_link(self, secret):
        """复制TOTP绑定链接到剪贴板"""
        secret = (secret or '').strip()
        if not secret:
            tkmessagebox.showwarning("提示", "请先生成TOTP密钥")
            return
        link = f"https://2fa.it0731.cn/tok/{secret}"
        self.root.clipboard_clear()
        self.root.clipboard_append(link)
        tkmessagebox.showinfo("已复制", f"绑定链接已复制到剪贴板：\n{link}")


    def load_config(self):
        config.load()
        self.refresh_dir_list()
        self._update_config_mtime()
        self._set_gui_var('password_var', config.global_password)
        self._set_gui_var('admin_password_var', config.admin_password)
        # 恢复超级管理员 TOTP 两步验证显示状态
        if hasattr(self, 'admin_totp_enabled_var'):
            self._set_gui_var('admin_totp_secret_var', getattr(config, 'admin_totp_secret', ''))
            self.admin_totp_enabled_var.set(bool(getattr(config, 'admin_totp_secret', '')))
            self.admin_totp_entry.configure(state="readonly" if self.admin_totp_enabled_var.get() else "normal")
            # 恢复"仅验证码登录(免密)"开关，避免GUI保存时用过期的False覆盖已开启的设置
            if hasattr(self, 'admin_totp_only_var'):
                self._set_gui_var('admin_totp_only_var', bool(getattr(config, 'admin_totp_only', False)))
        self._set_gui_var('port_var', str(config.port))
        self._set_gui_var('cleanup_time_var', config.cleanup_time)
        self._set_gui_var('auto_cleanup_var', config.auto_cleanup)
        self._set_gui_var('upload_concurrency_var', config.upload_concurrency)
        self._set_gui_var('upload_chunk_size_var', config.upload_chunk_size // 1048576)

    def _set_gui_var(self, name, value):
        """设置GUI变量并记录基线，用于保存/启动时判断该字段是否被用户改动过"""
        if not hasattr(self, name):
            return
        if not hasattr(self, '_var_baseline'):
            self._var_baseline = {}
        getattr(self, name).set(value)
        self._var_baseline[name] = value

    def _gui_var_changed(self, name):
        """GUI当前变量是否相对基线不同（即用户是否改动了该字段）"""
        if not hasattr(self, name):
            return False
        if not hasattr(self, '_var_baseline'):
            self._var_baseline = {}
        return getattr(self, name).get() != self._var_baseline.get(name)

    def refresh_vars_from_config(self):
        """网页保存配置后刷新GUI变量（同时更新基线，避免覆盖网页端最新值）"""
        if hasattr(self, 'password_var'):
            self._set_gui_var('password_var', config.global_password)
        if hasattr(self, 'admin_password_var'):
            self._set_gui_var('admin_password_var', config.admin_password)
        if hasattr(self, 'admin_totp_enabled_var'):
            self._set_gui_var('admin_totp_secret_var', getattr(config, 'admin_totp_secret', ''))
            self.admin_totp_enabled_var.set(bool(getattr(config, 'admin_totp_secret', '')))
            self.admin_totp_entry.configure(state="readonly" if self.admin_totp_enabled_var.get() else "normal")
            if hasattr(self, 'admin_totp_only_var'):
                self._set_gui_var('admin_totp_only_var', bool(getattr(config, 'admin_totp_only', False)))
        if hasattr(self, 'port_var'):
            self._set_gui_var('port_var', str(config.port))
        if hasattr(self, 'cleanup_time_var'):
            self._set_gui_var('cleanup_time_var', config.cleanup_time)
        if hasattr(self, 'auto_cleanup_var'):
            self._set_gui_var('auto_cleanup_var', config.auto_cleanup)
        if hasattr(self, 'upload_concurrency_var'):
            self._set_gui_var('upload_concurrency_var', config.upload_concurrency)
        if hasattr(self, 'upload_chunk_size_var'):
            self._set_gui_var('upload_chunk_size_var', config.upload_chunk_size // 1048576)
        if hasattr(self, 'refresh_dir_list'):
            self.refresh_dir_list()

    def _update_config_mtime(self):
        """记录当前配置文件修改时间，供跨进程兜底判断外部改动使用"""
        try:
            if not hasattr(self, '_last_config_mtime'):
                self._last_config_mtime = 0.0
            if os.path.exists(config.config_file):
                self._last_config_mtime = os.path.getmtime(config.config_file)
        except Exception:
            pass

    def _poll_config_mtime(self):
        """低频跨进程兜底：当配置文件被其他进程（如后台服务）改写时刷新窗体。
        仅当文件修改时间变化才真正读盘，避免高频刷盘开销。"""
        try:
            if os.path.exists(config.config_file):
                mtime = os.path.getmtime(config.config_file)
                if mtime > self._last_config_mtime:
                    self._update_config_mtime()
                    self.load_config()
        except Exception:
            pass
        try:
            # 每 3 秒检查一次，开销极低
            self.root.after(3000, self._poll_config_mtime)
        except Exception:
            pass

    def save_config(self):
        # 全局密码：仅当用户在GUI改动过时才回写，保留网页端最新值
        if self._gui_var_changed('password_var'):
            config.global_password = self.password_var.get()
            password_change_timestamps["global"] = time.time()

        # 管理员密码：仅当新密码非空时才修改（保持原有策略）
        new_admin_password = self.admin_password_var.get()
        if new_admin_password and config.admin_password != new_admin_password:
            password_change_timestamps["admin"] = time.time()
            config.admin_password = new_admin_password

        # 超级管理员 TOTP 两步验证密钥
        if hasattr(self, 'admin_totp_enabled_var') and self._gui_var_changed(
            'admin_totp_secret_var'
        ):
            if self.admin_totp_enabled_var.get():
                config.admin_totp_secret = self.admin_totp_secret_var.get().strip()
            else:
                config.admin_totp_secret = ""
        # 仅验证码登录(免密) 开关：仅当用户改动过时才回写，避免覆盖网页端最新设置
        if hasattr(self, 'admin_totp_only_var') and self._gui_var_changed(
            'admin_totp_only_var'
        ):
            config.admin_totp_only = bool(
                getattr(config, 'admin_totp_secret', '')
                and self.admin_totp_enabled_var.get()
                and self.admin_totp_only_var.get()
            )

        # 端口/清理选项：仅当用户改动过时才回写
        if self._gui_var_changed('port_var'):
            config.port = int(self.port_var.get() or 12345)
        if self._gui_var_changed('cleanup_time_var'):
            config.cleanup_time = self.cleanup_time_var.get()
        if self._gui_var_changed('auto_cleanup_var'):
            config.auto_cleanup = self.auto_cleanup_var.get()
        if self._gui_var_changed('upload_concurrency_var'):
            config.upload_concurrency = int(self.upload_concurrency_var.get() or 5)
        if self._gui_var_changed('upload_chunk_size_var'):
            config.upload_chunk_size = int((self.upload_chunk_size_var.get() or 1) * 1048576)

        config.save()
        # 保存后立即重新加载配置（同步GUI变量与基线）
        self.load_config()
        self.check_and_prompt_restart()
        flask_app.logger.info(f"配置已保存并实时生效")
        # 添加详细日志
        # flask_app.logger.info(f"配置已保存，当前共享目录配置:")
        # for dir_name, dir_obj in config.shared_dirs.items():
        #   flask_app.logger.info(f"{dir_name}: password={dir_obj.password}")

    def refresh_dir_list(self):
        for item in self.dir_list.get_children():
            self.dir_list.delete(item)

        # 设置三列标题
        self.dir_list.heading("alias", text="显示名称")
        self.dir_list.heading("path", text="路径")
        self.dir_list.heading("has_password", text="密码保护")  # 添加第三列标题

        # 设置列宽
        self.dir_list.column("alias", width=150)
        self.dir_list.column("path", width=300)
        self.dir_list.column("has_password", width=100)  # 设置第三列宽度

        # 插入数据
        for dir_obj in config.shared_dirs.values():
            self.dir_list.insert(
                "",
                "end",
                values=(
                    dir_obj.alias,
                    dir_obj.path,
                    "是" if dir_obj.password else "否",
                ),
            )

    def add_directory(self):
        dialog = DirectoryDialog(self.root)
        self.root.wait_window(dialog)  # 先显示对话框，让用户可以选择使用浏览按钮

        if dialog.result:
            path = dialog.result.path
            # 确保路径格式正确
            if path.endswith(":\\"):
                drive_letter = path[0].lower()
                dir_name = f"drive_{drive_letter}"
            else:
                dir_name = os.path.basename(path)

            config.shared_dirs[dir_name] = dialog.result
            self.refresh_dir_list()
            self.save_config()

    def remove_directory(self, event=None):
        selected = self.dir_list.selection()
        if not selected:
            return

        item = self.dir_list.item(selected[0])
        alias = item["values"][0]  # 获取别名
        path = item["values"][1]  # 获取路径

        # 生成目录标识名
        if path.endswith(":\\"):
            dir_name = f"drive_{path[0].lower()}"
        else:
            dir_name = os.path.basename(path)

        if tkmessagebox.askyesno("确认", f"确定要删除共享 '{alias}' 吗？"):
            if dir_name in config.shared_dirs:
                del config.shared_dirs[dir_name]
                self.save_config()
                self.refresh_dir_list()

    def edit_directory(self, event=None):
        selected = self.dir_list.selection()
        if not selected:
            return

        item = self.dir_list.item(selected[0])
        alias = item["values"][0]  # 获取别名
        path = item["values"][1]  # 获取路径

        # 生成目录标识名
        if path.endswith(":\\"):
            dir_name = f"drive_{path[0].lower()}"
        else:
            dir_name = os.path.basename(path)

        if dir_name in config.shared_dirs:
            old_password = config.shared_dirs[dir_name].password
            dialog = DirectoryDialog(self.root, config.shared_dirs[dir_name])
            self.root.wait_window(dialog)
            if dialog.result:
                # 检查密码是否变化
                if old_password != dialog.result.password:
                    password_change_timestamps["directories"][dialog.result.alias] = (
                        time.time()
                    )
                # 使用相同的目录标识名逻辑
                new_dir_name = (
                    f"drive_{dialog.result.path[0].lower()}"
                    if dialog.result.path.endswith(":\\")
                    else os.path.basename(dialog.result.path)
                )

                # 删除旧配置并添加新配置
                del config.shared_dirs[dir_name]
                config.shared_dirs[new_dir_name] = dialog.result
                self.save_config()
                self.refresh_dir_list()

    def handle_service_toggle(self):
        if self.service_debounce_timer:
            self.root.after_cancel(self.service_debounce_timer)
        self.service_debounce_timer = self.root.after(500, self.do_service_toggle)

    def start_service_monitor(self):
        """开始监控后台服务状态"""

        def check_service_status():
            try:
                import win32serviceutil

                try:
                    self.service_status = win32serviceutil.QueryServiceStatus(
                        "FileShareService"
                    )[1]
                except:
                    self.service_status = None

                # 根据后台服务状态更新按钮
                if self.service_status == 4:  # 运行状态码
                    self.back_server_running = True
                    self.start_btn.configure(
                        text="停止后台服务", style="danger.TButton"
                    )
                    self.service_checkbox.configure(state="disabled")
                    if not self.page_btn.winfo_ismapped():
                        self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))
                    # 同步显示后台服务日志
                    sync_service_logs(self)
                else:
                    button_text = (
                        "启动后台服务"
                        if self.service_status is not None
                        else "启动服务"
                        if not self.server_running
                        else "停止服务"
                    )
                    button_style = (
                        "success.TButton"
                        if not self.server_running
                        else "danger.TButton"
                    )
                    checkbox_state = "normal"

                    # 更新 UI
                    self.start_btn.configure(text=button_text, style=button_style)
                    self.service_checkbox.configure(state=checkbox_state)

                # 等待进行下次检查
                self.service_monitor_timer = self.root.after(2000, check_service_status)

            except:
                # 出错处理
                self.back_server_running = False
                self.service_monitor_timer = self.root.after(2000, check_service_status)

        def sync_service_logs(window):
            """同步今天的后台服务日志到回显框"""
            if not self.back_server_running:
                return
            try:
                current_date = datetime.now().strftime("%Y%m%d")
                log_file = os.path.join(
                    get_app_path(), "logs", f"service_{current_date}.log"
                )

                if os.path.exists(log_file):
                    if not hasattr(window, "last_processed_line"):
                        window.last_processed_line = 0

                    with open(log_file, "r", encoding="utf-8") as f:
                        logs = f.readlines()
                        new_logs = logs[window.last_processed_line :]

                        for log in new_logs:
                            window.log_area.insert("end", log)
                            window.log_area.see("end")

                        window.last_processed_line = len(logs)

            except Exception as e:
                self.logger.warning(f"同步回显后台服务日志出错: {e}")

        check_service_status()

    def stop_service_monitor(self):
        """停止监控后台服务状态"""
        if self.service_monitor_timer:
            self.root.after_cancel(self.service_monitor_timer)
            self.service_monitor_timer = None
            self.service_status = None

    def do_service_toggle(self):
        """安装卸载后台服务相应动作"""
        if self.service_var.get():
            if not self.is_admin():
                tkmessagebox.showerror("错误", "安装服务需要管理员权限")
                self.service_var.set(False)
                return
            try:
                self.install_service()
                self.start_btn.configure(text="启动后台服务", state="normal")
                # self.start_service_monitor()
                flask_app.logger.info("后台服务安装成功")
            except Exception as e:
                self.service_var.set(False)
                tkmessagebox.showerror("错误", f"安装服务失败: {str(e)}")
        else:
            try:
                self.uninstall_service()
                self.start_btn.configure(text="启动服务", state="normal")
                # Stop monitoring after uninstall
                # self.stop_service_monitor()
                flask_app.logger.info("后台服务卸载成功")
            except Exception as e:
                self.service_var.set(True)
                tkmessagebox.showerror("错误", f"卸载服务失败: {str(e)}")

    def is_caddy_mode_active(self):
        """判断是否使用 Caddy 反向代理自动 HTTPS（SSL启用 + Caddy启用 + caddy.exe存在）"""
        try:
            return bool(
                config.ssl_enabled
                and config.caddy_enabled
                and self.caddy_manager.caddy_available()
            )
        except Exception:
            return False

    def toggle_server_type(self):
        config.use_waitress = self.server_type.get()

        # 重置为默认颜色
        self.waitress_label.configure(style="TLabel")
        self.werkzeug_label.configure(style="TLabel")

        # 根据状态设置颜色
        if config.use_waitress:
            self.waitress_label.configure(style="success.TLabel")
            flask_app.logger.info("服务模式已切换成Cheroot多线程服务模式，适合生产环境")

        else:
            self.werkzeug_label.configure(style="success.TLabel")
            flask_app.logger.info(
                "服务模式已切换成werkzeug单线程服务模式，适合开发调试"
            )
        config.save()

    def toggle_server(self):
        global serverUrl, runningPort
        if not config.shared_dirs:
            self.log_area.insert(END, "错误：请先添加至少一个共享目录\n")
            self.log_area.see(END)
            return

        port = int(config.port)
        runningPort = port
        ip = get_local_ip()
        ipv6 = get_global_ipv6()
        url = f"http://{ip}:{port}"
        url_ipv6 = f"http://[{ipv6}]:{port}"
        serverUrl = url
        if self.service_var.get():
            if not self.back_server_running:
                try:
                    self.save_config()
                    self.start_btn.configure(
                        text="正在启动...", style="warning.TButton", state="disabled"
                    )

                    # 启动前检查端口占用，避免服务绑定失败后很快退出
                    if self.is_port_in_use(runningPort):
                        self.start_btn.configure(
                            text="启动后台服务", style="success.TButton", state="normal"
                        )
                        tkmessagebox.showwarning(
                            "端口被占用",
                            f"端口 {runningPort} 已被占用。\n请先停止占用该端口的进程或程序（如前台运行的本程序/旧服务实例）后再启动后台服务。",
                        )
                        return

                    win32serviceutil.StartService("FileShareService")

                    # 轮询等待服务进入运行状态（最多35秒，覆盖解压+初始化耗时）
                    svc_started = False
                    for _ in range(35):
                        time.sleep(1)
                        try:
                            current_status = win32serviceutil.QueryServiceStatus(
                                "FileShareService"
                            )[1]
                        except Exception:
                            current_status = None
                        if current_status == 4:  # SERVICE_RUNNING
                            svc_started = True
                            break
                        if current_status == 1:  # SERVICE_STOPPED（启动失败已退出）
                            break

                    if not svc_started:
                        self.start_btn.configure(
                            text="启动后台服务", style="success.TButton", state="normal"
                        )
                        self.back_server_running = False
                        tkmessagebox.showerror(
                            "错误",
                            "服务启动失败或启动后很快退出。\n请查看程序目录 logs/service_*.log 日志文件，或打开事件查看器-应用程序日志查找「FS文件分享服务」错误记录。",
                        )
                        return

                    self.start_btn.configure(
                        text="停止后台服务", style="danger.TButton", state="normal"
                    )
                    self.back_server_running = True
                    flask_app.logger.info(
                        f"后台服务已启动 : ipv4: {url}\n ipv6: {url_ipv6}"
                    )
                    if not self.page_btn.winfo_ismapped():
                        self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))
                except Exception as e:
                    self.start_btn.configure(
                        text="启动后台服务", style="success.TButton", state="normal"
                    )
                    tkmessagebox.showerror("错误", f"启动服务失败: {str(e)}")
            else:
                try:
                    self.start_btn.configure(
                        text="正在停止...", style="warning.TButton", state="disabled"
                    )

                    # 停止前确认服务确实在运行，避免服务已退出时 StopService 报 1062"服务尚未启动"
                    try:
                        current_status = win32serviceutil.QueryServiceStatus(
                            "FileShareService"
                        )[1]
                    except Exception:
                        current_status = None
                    if current_status not in (2, 3, 4):  # START_PENDING/STOP_PENDING/RUNNING
                        self.start_btn.configure(
                            text="启动后台服务", style="success.TButton", state="normal"
                        )
                        self.back_server_running = False
                        self.page_btn.pack_forget()
                        tkmessagebox.showinfo(
                            "服务状态",
                            "服务当前未在运行（可能已退出或启动失败），状态已自动刷新。\n请查看 logs/service_*.log 日志确认原因。",
                        )
                        return

                    win32serviceutil.StopService("FileShareService")

                    # 轮询等待服务完全停止
                    for _ in range(15):
                        time.sleep(1)
                        try:
                            stopped_status = win32serviceutil.QueryServiceStatus(
                                "FileShareService"
                            )[1]
                        except Exception:
                            stopped_status = 1
                        if stopped_status == 1:  # SERVICE_STOPPED
                            break

                    self.start_btn.configure(
                        text="启动后台服务", style="success.TButton", state="normal"
                    )
                    self.back_server_running = False
                    flask_app.logger.info("后台服务已成功停止")
                    self.page_btn.pack_forget()
                except Exception as e:
                    # 停止过程中服务可能刚好自行退出：若查询到已停止则按成功处理，避免误报 1062
                    try:
                        final_status = win32serviceutil.QueryServiceStatus(
                            "FileShareService"
                        )[1]
                    except Exception:
                        final_status = None
                    if final_status == 1:  # SERVICE_STOPPED
                        self.start_btn.configure(
                            text="启动后台服务", style="success.TButton", state="normal"
                        )
                        self.back_server_running = False
                        self.page_btn.pack_forget()
                        flask_app.logger.info("后台服务已停止（状态已刷新）")
                    else:
                        self.start_btn.configure(
                            text="停止后台服务", style="danger.TButton", state="normal"
                        )
                        tkmessagebox.showerror("错误", f"停止服务失败: {str(e)}")
        else:
            if not self.server_running:
                self.save_config()
                if self.is_port_in_use(runningPort):
                    if tkmessagebox.askyesno(
                        "端口被占用",
                        f"端口 {runningPort} 已被占用。是否尝试强制释放该端口？",
                    ):
                        if self.force_cleanup_port(runningPort):
                            self.log_area.insert(END, f"已强制释放端口 {runningPort}\n")
                            return
                        else:
                            self.log_area.insert(
                                END, f"无法释放端口 {runningPort}，请尝试使用其他端口\n"
                            )
                            return
                    else:
                        return

                def run_server():
                    try:
                        # 统一启动 Caddy 反向代理（Cheroot/Werkzeug 两种服务器均适用）
                        if self.is_caddy_mode_active():
                            if self.caddy_manager.start():
                                self.root.after(
                                    0,
                                    lambda: self.log_area.insert(
                                        END,
                                        f"✓ Caddy 反代已启动: "
                                        f"https://{config.ssl_domain}:{config.ssl_port}\n",
                                    ),
                                )
                                self.root.after(0, lambda: self.log_area.see(END))
                            else:
                                self.logger.error(
                                    "Caddy 反代启动失败，请检查配置和日志"
                                )

                        if config.use_waitress:
                            # 使用Cheroot替代Waitress
                            self.server_running = True
                            self.root.after(
                                0,
                                lambda: self.start_btn.configure(
                                    text="停止服务", style="danger.TButton"
                                ),
                            )
                            self.switch_server_type_ui(True)

                            # 创建两个服务器实例
                            optimal_threads = max(2, get_optimal_threads() // 2)
                            # 对半分，因为我这里是ipv4ipv6分开监听的
                            self.logger.info(
                                f"根据当前CPU核心数自动设置ipv4与ipv6的服务线程数分别为：{optimal_threads}"
                            )

                            # 创建HTTP服务器（使用Cheroot替换Waitress）
                            from cheroot_server import create_cheroot_http_server

                            self.server_ipv4 = create_cheroot_http_server(
                                flask_app,
                                host="0.0.0.0",
                                port=port,
                                threads=optimal_threads,
                                connection_limit=1000,
                                channel_timeout=config.upload_timeout,
                            )
                            self.server_ipv6 = create_cheroot_http_server(
                                flask_app,
                                host="::",
                                port=port,
                                threads=optimal_threads,
                                connection_limit=1000,
                                channel_timeout=config.upload_timeout,
                            )

                            # 如果启用SSL，创建HTTPS服务器（使用Cheroot原生SSL）
                            self.ssl_server_ipv4 = None
                            self.ssl_server_ipv6 = None

                            if config.ssl_enabled:
                                if self.is_caddy_mode_active():
                                    # Caddy 反代模式：HTTPS 已由 run_server 顶部的 Caddy 接管
                                    self.logger.info(
                                        f"Caddy 反代模式：HTTPS 端口 {config.ssl_port} 由 Caddy 接管"
                                    )
                                elif self.ssl_manager.has_valid_certificate():
                                    cert_path = self.ssl_manager.get_cert_file_path()
                                    key_path = self.ssl_manager.get_key_file_path()
                                    if cert_path and key_path:
                                        ssl_port = config.ssl_port
                                        self.logger.info(
                                            f"SSL已启用，使用证书: {cert_path}，端口: {ssl_port}"
                                        )

                                        try:
                                            # 使用Cheroot的原生SSL支持（高性能且稳定）
                                            from cheroot_server import (
                                                create_cheroot_https_server,
                                            )

                                            # 创建SSL Cheroot服务器
                                            self.ssl_server_ipv4 = (
                                                create_cheroot_https_server(
                                                    flask_app,
                                                    host="0.0.0.0",
                                                    port=ssl_port,
                                                    cert_file=cert_path,
                                                    key_file=key_path,
                                                    threads=optimal_threads,
                                                    connection_limit=1000,
                                                    channel_timeout=config.upload_timeout,
                                                )
                                            )

                                            # 检查IPv6支持
                                            import socket

                                            if socket.has_ipv6:
                                                try:
                                                    self.ssl_server_ipv6 = (
                                                        create_cheroot_https_server(
                                                            flask_app,
                                                            host="::",
                                                            port=ssl_port,
                                                            cert_file=cert_path,
                                                            key_file=key_path,
                                                            threads=optimal_threads,
                                                            connection_limit=1000,
                                                            channel_timeout=config.upload_timeout,
                                                        )
                                                    )
                                                except Exception as ipv6_error:
                                                    self.logger.warning(
                                                        f"IPv6 SSL服务器创建失败: {ipv6_error}"
                                                    )
                                                    self.ssl_server_ipv6 = None
                                            else:
                                                self.logger.info(
                                                    "系统不支持IPv6，跳过IPv6 SSL服务器"
                                                )
                                                self.ssl_server_ipv6 = None

                                            self.logger.info(
                                                f"HTTPS服务器已创建（使用Cheroot原生SSL），端口: {ssl_port}"
                                            )

                                            # 更新UI显示SSL服务器创建成功
                                            self.root.after(
                                                0,
                                                lambda: self.log_area.insert(
                                                    END, "✓ HTTPS服务器创建成功\n"
                                                ),
                                            )
                                            self.root.after(
                                                0, lambda: self.log_area.see(END)
                                            )

                                        except Exception as e:
                                            self.logger.error(
                                                f"创建Cheroot SSL服务器失败: {e}"
                                            )
                                            self.ssl_server_ipv4 = None
                                            self.ssl_server_ipv6 = None

                                            # 更新UI显示SSL服务器创建失败
                                            self.root.after(
                                                0,
                                                lambda: self.log_area.insert(
                                                    END,
                                                    f"✗ HTTPS服务器创建失败: {str(e)}\n",
                                                ),
                                            )
                                            self.root.after(
                                                0, lambda: self.log_area.see(END)
                                            )
                                    else:
                                        self.logger.warning(
                                            "SSL已启用但证书文件路径无效"
                                        )
                                else:
                                    self.logger.warning("SSL已启用但没有有效证书")

                            # 计算需要的线程数
                            max_workers = 2  # HTTP IPv4 + IPv6
                            if self.ssl_server_ipv4 and self.ssl_server_ipv6:
                                max_workers = 4  # HTTP IPv4 + IPv6 + HTTPS IPv4 + IPv6

                            # 使用线程池同时运行服务器
                            with ThreadPoolExecutor(
                                max_workers=max_workers
                            ) as self.executor:
                                # 启动HTTP服务器（Cheroot）
                                self.future_ipv4 = self.executor.submit(
                                    self.server_ipv4.run
                                )
                                self.future_ipv6 = self.executor.submit(
                                    self.server_ipv6.run
                                )

                                # 启动HTTPS服务器（如果存在）
                                ssl_servers_started = []

                                if self.ssl_server_ipv4:
                                    # Cheroot SSL服务器
                                    self.ssl_future_ipv4 = self.executor.submit(
                                        self.ssl_server_ipv4.run
                                    )
                                    ssl_servers_started.append("IPv4 Cheroot SSL")

                                if self.ssl_server_ipv6:
                                    # Cheroot SSL服务器
                                    self.ssl_future_ipv6 = self.executor.submit(
                                        self.ssl_server_ipv6.run
                                    )
                                    ssl_servers_started.append("IPv6 Cheroot SSL")

                                if ssl_servers_started:
                                    self.logger.info(
                                        f"HTTPS服务器已启动: {', '.join(ssl_servers_started)}"
                                    )
                                else:
                                    self.logger.warning("没有HTTPS服务器启动")
                        else:
                            # 为Werkzeug服务器设置超时
                            from werkzeug.serving import WSGIRequestHandler

                            class TimeoutRequestHandler(WSGIRequestHandler):
                                timeout = 30  # 设置30秒超时

                            # 创建两个服务器实例
                            self.server_ipv4 = make_server(
                                "0.0.0.0",
                                port,
                                flask_app,
                                request_handler=TimeoutRequestHandler,
                            )
                            self.server_ipv6 = make_server(
                                "::",
                                port,
                                flask_app,
                                request_handler=TimeoutRequestHandler,
                            )
                            self.server_running = True
                            self.root.after(
                                0,
                                lambda: self.start_btn.configure(
                                    text="停止服务", style="danger.TButton"
                                ),
                            )
                            self.switch_server_type_ui(True)
                            # 使用线程同时运行两个服务器
                            self.thread_ipv4 = threading.Thread(
                                target=self.server_ipv4.serve_forever
                            )
                            self.thread_ipv6 = threading.Thread(
                                target=self.server_ipv6.serve_forever
                            )
                            self.thread_ipv4.daemon = True
                            self.thread_ipv6.daemon = True
                            self.thread_ipv4.start()
                            self.thread_ipv6.start()

                    except Exception as e:
                        self.log_area.insert(END, f"服务器错误: {str(e)}\n")
                        self.log_area.see(END)
                        self.server_running = False

                # 在 toggle_server 中保持原有的线程启动方式
                self.server_thread = threading.Thread(target=run_server)
                self.server_thread.daemon = True
                self.server_thread.start()
                _maybe_start_webdav()  # 可选 WebDAV 独立端口

                server_type = "Cheroot" if config.use_waitress else "Werkzeug"
                flask_app.logger.info(
                    f"HTTP服务已启动 ({server_type}): ipv4: {url}\n ipv6: {url_ipv6}"
                )

                # SSL状态会在run_server函数内部正确显示，这里不需要重复检查
                if config.ssl_enabled:
                    self.log_area.insert(END, "SSL服务已启用，详细状态请查看上方日志\n")
                else:
                    self.log_area.insert(END, "仅HTTP服务已启动\n")
                # 启动完成后延迟刷新 SSL 按钮状态（等待 Caddy 绑定端口）
                self.root.after(3000, self.update_ssl_status)
                if not self.page_btn.winfo_ismapped():
                    self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))
                if config.auto_cleanup and not is_cleanup_running():
                    start_cleanup_thread()  # 启动清理线程

            else:
                try:
                    self.start_btn.configure(
                        text="正在停止...", style="warning.TButton", state="disabled"
                    )

                    def force_shutdown():
                        try:
                            self.log_area.insert(END, "正在停止服务...\n")
                            self.log_area.see(END)

                            # 停止 Caddy 反向代理（如有）
                            if self.is_caddy_mode_active() or self.caddy_manager.is_running():
                                if self.caddy_manager.stop():
                                    self.log_area.insert(END, "Caddy 反代已停止\n")

                            _maybe_stop_webdav()  # 可选 WebDAV 独立端口

                            if config.use_waitress:
                                # Mark server as stopped
                                self.server_running = False
                                # 关闭HTTP服务器（Cheroot）
                                if hasattr(self, "server_ipv4"):
                                    self.server_ipv4.stop()
                                if hasattr(self, "server_ipv6"):
                                    self.server_ipv6.stop()

                                # 关闭HTTPS服务器
                                ssl_servers_closed = []

                                if (
                                    hasattr(self, "ssl_server_ipv4")
                                    and self.ssl_server_ipv4
                                ):
                                    try:
                                        # Cheroot SSL服务器
                                        self.ssl_server_ipv4.stop()
                                        ssl_servers_closed.append("IPv4 Cheroot SSL")
                                    except Exception as e:
                                        self.logger.error(
                                            f"关闭SSL IPv4服务器时发生错误: {e}"
                                        )

                                if (
                                    hasattr(self, "ssl_server_ipv6")
                                    and self.ssl_server_ipv6
                                ):
                                    try:
                                        # Cheroot SSL服务器
                                        self.ssl_server_ipv6.stop()
                                        ssl_servers_closed.append("IPv6 Cheroot SSL")
                                    except Exception as e:
                                        self.logger.error(
                                            f"关闭SSL IPv6服务器时发生错误: {e}"
                                        )

                                if ssl_servers_closed:
                                    self.logger.info(
                                        f"HTTPS服务器已关闭: {', '.join(ssl_servers_closed)}"
                                    )
                                else:
                                    self.logger.info("没有HTTPS服务器需要关闭")

                                # 关闭线程池
                                if hasattr(self, "executor"):
                                    self.executor.shutdown(
                                        wait=False
                                    )  # 不等待，立即关闭
                                    self.executor = None  # 释放线程池资源

                                    # 2. 等待服务线程结束
                                if hasattr(self, "server_thread"):
                                    self.server_thread.join(timeout=2)  # 等待5秒
                                    if self.server_thread.is_alive():
                                        self.log_area.insert(
                                            END,
                                            "服务线程未在指定时间内停止，强制关闭...\n",
                                        )
                                        self.server_thread._stop()  # 强制停止线程

                                # Force cleanup port and threads
                                if self.force_cleanup_port(runningPort):
                                    self.log_area.insert(END, "已清理所有服务资源\n")

                                # Update UI
                                self.start_btn.configure(
                                    text="启动服务",
                                    style="success.TButton",
                                    state="normal",
                                )
                                self.switch_server_type_ui(False)
                                self.log_area.insert(END, "服务已停止✓\n")
                                self.root.after(100, self.update_ssl_status)
                                self.page_btn.pack_forget()
                            else:
                                # Werkzeug服务器关闭逻辑
                                def shutdown_werkzeug():
                                    try:
                                        self.server_running = False
                                        if hasattr(self, "server_ipv4"):
                                            self.server_ipv4.shutdown()
                                            self.server_ipv4.server_close()
                                        if hasattr(self, "server_ipv6"):
                                            self.server_ipv6.shutdown()
                                            self.server_ipv6.server_close()
                                        self.start_btn.configure(
                                            text="启动服务",
                                            style="success.TButton",
                                            state="normal",
                                        )
                                        self.switch_server_type_ui(False)
                                        self.log_area.insert(END, "服务已完全停止✓\n")
                                        self.log_area.see(END)
                                        # 后台线程调用 root.after 安全刷新
                                        self.root.after(100, self.update_ssl_status)
                                        self.page_btn.pack_forget()
                                    except Exception as e:
                                        self.log_area.insert(
                                            END, f"停止Werkzeug服务器出错: {str(e)}\n"
                                        )
                                        self.log_area.see(END)

                                # 在新线程中执行关闭操作
                                threading.Thread(
                                    target=shutdown_werkzeug, daemon=True
                                ).start()

                        except Exception as e:
                            self.log_area.insert(END, f"停止过程出错: {str(e)}\n")

                            self.log_area.see(END)

                            # 即使出错也恢复按钮状态
                            self.switch_server_type_ui(False)
                            self.start_btn.configure(
                                text="启动服务", style="success.TButton", state="normal"
                            )

                    self.root.after(100, force_shutdown)
                    if is_cleanup_running():
                        self.root.after(100, stop_cleanup_thread)  # 终止清理线程

                    # 强制清理端口（延迟执行，给服务器时间停止）
                    def delayed_cleanup():
                        self.force_cleanup_port(config.port)
                        if config.ssl_enabled:
                            self.force_cleanup_port(config.ssl_port)

                    self.root.after(3000, delayed_cleanup)  # 3秒后强制清理

                except Exception as e:
                    self.log_area.insert(END, f"停止服务错误: {str(e)}\n")
                    self.log_area.see(END)

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return False
            except OSError:
                return True

    def force_cleanup_port(self, port):
        """强制清理端口及残留线程"""
        try:
            self.log_area.insert(END, f"强制清理端口 {port}...\n")
            self.log_area.see(END)

            # 1. 停止所有服务器线程
            server_threads = [
                t
                for t in threading.enumerate()
                if t.name.startswith(("cheroot", "waitress"))
            ]

            for thread in server_threads:
                if hasattr(thread, "_Thread__stop"):
                    thread._Thread__stop()

            # 2. 强制终止占用端口的进程
            import subprocess

            try:
                # 查找占用端口的进程
                result = subprocess.run(
                    ["netstat", "-ano", "|", "findstr", f":{port}"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.stdout:
                    lines = result.stdout.strip().split("\n")
                    pids = set()
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 5 and f":{port}" in parts[1]:
                            pid = parts[-1]
                            if pid.isdigit():
                                pids.add(pid)

                    # 终止占用端口的进程
                    for pid in pids:
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True,
                                timeout=5,
                            )
                            self.log_area.insert(
                                END, f"已终止占用端口{port}的进程PID:{pid}\n"
                            )
                            self.log_area.see(END)
                        except Exception as e:
                            self.log_area.insert(END, f"终止进程PID:{pid}失败: {e}\n")
                            self.log_area.see(END)

            except Exception as e:
                self.log_area.insert(END, f"查找占用端口的进程失败: {e}\n")
                self.log_area.see(END)

            # 3. 等待一段时间
            import time

            time.sleep(2)

            # 4. 验证端口是否释放
            def check_port():
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind(("0.0.0.0", port))
                    sock.close()
                    return True
                except:
                    return False

            is_released = check_port()
            self.log_area.insert(
                END, f"端口 {port} {'已释放' if is_released else '仍被占用'}\n"
            )
            self.log_area.see(END)
            return is_released

        except Exception as e:
            self.log_area.insert(END, f"强制清理端口失败: {str(e)}\n")
            self.log_area.see(END)
            return False

    def is_admin(self):
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False

    def is_service_installed(self):
        try:
            win32serviceutil.QueryServiceStatus("FileShareService")
            return True
        except:
            return False

    def install_service(self):
        # 先确保服务完全删除
        self.force_delete_service()

        svc_exe = self._get_current_exe() or self._get_service_exe_path()
        if not svc_exe or not os.path.isfile(svc_exe):
            raise RuntimeError("无法定位服务可执行文件，请使用单文件 file_share.exe 部署后重试。")

        nssm = self._get_nssm_path()
        if nssm:
            # NSSM 包装方案：服务镜像 = NSSM（原生 C，SCM 握手由它完成），
            # 应用 = 本程序 --headless-server。规避 PyInstaller 打包应用在不同 Windows
            # 版本（尤其 Server 2012 R2）上 StartServiceCtrlDispatcher 1063 的兼容问题，
            # 各版本统一可用，服务管理器正常启停、崩溃可自动重启。
            self.logger.info(f"使用 NSSM 安装后台服务，应用: {svc_exe} --headless-server")
            self._run_nssm([nssm, "install", "FileShareService", svc_exe, "--headless-server"])
            self._run_nssm([nssm, "set", "FileShareService", "AppDirectory", os.path.dirname(svc_exe)])
            self._run_nssm([nssm, "set", "FileShareService", "Start", "SERVICE_AUTO_START"])
            log_dir = os.path.join(get_app_path(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            self._run_nssm([
                nssm, "set", "FileShareService", "AppStdout",
                os.path.join(log_dir, "service_stdout.log"),
            ])
            self._run_nssm([
                nssm, "set", "FileShareService", "AppStderr",
                os.path.join(log_dir, "service_stderr.log"),
            ])
            if not self.is_service_installed():
                raise RuntimeError("NSSM 安装服务失败，请确认以管理员身份运行")
        else:
            # 无 NSSM 时回退 pywin32 直接服务（PyInstaller 4.10 单文件在正常 Windows 上可用）
            win32serviceutil.InstallService(
                serviceName="FileShareService",
                displayName="FS文件分享服务",
                startType=win32service.SERVICE_AUTO_START,
                exeName=svc_exe,
                exeArgs="--run-as-service",
                pythonClassString="main.FileShareService",  # 添加类的完整路径
                description="提供文件共享Web服务 AQ contact: letvar@qq.com",
            )

        # 增大 SCM 服务启动超时（系统默认 30000ms）。
        # PyInstaller 单文件版服务启动时要先解压全部资源再初始化，解压较慢时可能超过
        # 30 秒被服务控制管理器强制终止，导致"服务启动后立即停止/停止报1062"。
        try:
            reg_result = subprocess.run(
                [
                    "reg",
                    "add",
                    "HKLM\\SYSTEM\\CurrentControlSet\\Control",
                    "/v",
                    "ServicesPipeTimeout",
                    "/t",
                    "REG_DWORD",
                    "/d",
                    "60000",
                    "/f",
                ],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if reg_result.returncode == 0:
                self.logger.info("已设置服务启动超时(ServicesPipeTimeout)为60秒")
        except Exception as e:
            self.logger.warning(f"设置 ServicesPipeTimeout 失败: {e}")

    def _run_nssm(self, args):
        """执行 NSSM 命令，失败仅记日志不中断"""
        try:
            subprocess.run(
                args,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
        except Exception as e:
            self.logger.warning(f"NSSM 命令失败: {' '.join(args)} -> {e}")

    def _get_nssm_path(self):
        """定位 NSSM：优先 exe 同目录 nssm.exe，其次从打包资源释放到系统临时目录"""
        try:
            base = os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidate = os.path.join(base, "nssm.exe")
        if os.path.isfile(candidate):
            return candidate
        if getattr(sys, "frozen", False):
            src = os.path.join(sys._MEIPASS, "nssm.exe")
            if os.path.isfile(src):
                try:
                    import tempfile

                    dst = os.path.join(tempfile.gettempdir(), "file_share_nssm.exe")
                    shutil.copyfile(src, dst)
                    return dst
                except Exception:
                    pass
        return None

    def _get_current_exe(self):
        """当前程序可执行文件路径（打包后即单文件 exe，可直接作为服务镜像）"""
        try:
            if getattr(sys, "frozen", False):
                return os.path.abspath(sys.executable)
            return os.path.abspath(sys.argv[0])
        except Exception:
            return None

    def _get_service_exe_path(self):
        """服务版（onedir）可执行文件路径：优先 <程序目录>/file_share_svc/file_share_svc.exe"""
        try:
            base = os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidate = os.path.join(base, "file_share_svc", "file_share_svc.exe")
        if os.path.isfile(candidate):
            return candidate
        return None

    def _ensure_service_dir(self):
        """确保服务版（onedir）已位于 exe 同目录。

        单文件发布时，onedir 服务版内置于打包资源（_MEIPASS/file_share_svc）；
        首次安装后台服务时自动释放到 <exe同目录>/file_share_svc，实现"一个 exe 即可部署"。
        返回是否就绪（服务 exe 存在）。
        """
        if self._get_service_exe_path():
            return True
        if not getattr(sys, "frozen", False):
            return False
        try:
            src = os.path.join(sys._MEIPASS, "file_share_svc")
            if not os.path.isdir(src) or not os.path.isfile(os.path.join(src, "file_share_svc.exe")):
                return False
            base = os.path.dirname(os.path.abspath(sys.executable))
            dst = os.path.join(base, "file_share_svc")
            shutil.copytree(src, dst, dirs_exist_ok=True)
            self.logger.info(f"已自动释放服务版到: {dst}")
            return os.path.isfile(os.path.join(dst, "file_share_svc.exe"))
        except Exception as e:
            self.logger.error(f"释放服务版目录失败: {e}")
            return False

    def force_delete_service(self):
        """强制删除服务的终极方案"""

        # 1. 强制停止服务进程 添加 creationflags 参数 屏敝黑色窗口
        subprocess.run(
            ["taskkill", "/F", "/FI", "SERVICES eq FileShareService"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # 2. 强制删除服务配置
        subprocess.run(
            ["sc", "stop", "FileShareService"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(1)
        subprocess.run(
            ["sc", "delete", "FileShareService"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(1)

        # 3. 使用 reg delete 强制删除注册表
        subprocess.run(
            [
                "reg",
                "delete",
                "HKLM\\SYSTEM\\CurrentControlSet\\Services\\FileShareService",
                "/f",
            ],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # 4. 等待系统处理
        time.sleep(2)

    def uninstall_service(self):
        # NSSM 安装的服务先通过 NSSM 移除（会清理其注册表子键），再 force_delete 兜底
        nssm = self._get_nssm_path()
        if nssm:
            try:
                subprocess.run(
                    [nssm, "remove", "FileShareService", "confirm"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=30,
                )
            except Exception:
                pass
        self.force_delete_service()

    def check_and_prompt_restart(self):
        if self.service_status == 4:
            if tkmessagebox.askyesno(
                "服务重启",
                "检测到后台服务正在运行，需要重启服务使新配置生效。是否现在重启？",
            ):
                self.restart_service()

    def restart_service(self):
        try:
            win32serviceutil.RestartService("FileShareService")
            return True
        except Exception as e:
            return False

    def open_page_settings(self):
        """打开页面设置对话框"""
        dialog = PageSettingsDialog(self.root)
        self.root.wait_window(dialog)

        if dialog.result:
            # 更新配置
            config.page_title = dialog.result["page_title"]
            config.logo_name = dialog.result["logo_name"]
            config.logo_image_url = dialog.result["logo_image_url"]

            # 保存配置
            config.save()

            # 显示成功消息
            self.log_area.insert(END, f"页面设置已更新并保存\n")
            if dialog.result["logo_image_url"]:
                self.log_area.insert(END, f"Logo图片将作为网页favicon显示\n")
            self.log_area.see(END)

            # 如果后台服务正在运行，提示重启
            if self.service_status == 4:
                if tkmessagebox.askyesno(
                    "重启服务",
                    "页面设置已更新，需要重启后台服务使新设置生效。是否现在重启？",
                ):
                    self.restart_service()


def main():
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()  # 使用TkinterDnD.Tk替代ttk.Window
    except ImportError:
        root = tk.Tk()  # 降级使用普通窗口

    # 立即隐藏窗口
    root.withdraw()
    # 设置窗口图标
    icon_path = get_path("static/favicon.ico")
    root.iconbitmap(icon_path)

    # 设置默认主题
    style = ttk.Style(theme="cosmo")

    file_share_app = FileShareApp(root, style)
    file_share_app.log_area.insert(
        END,
        "欢迎使用file_share，有任何问题或BUG请返馈至：letvar@qq.com "
        "或者github:https://github.com/52op/file_share"
        "\n本程序共两种服务方式："
        "\n1.前台窗口服务方式：直接启动服务，必须在此程序打开的前提下"
        "\n2.后台系统服务方式：点击安装为系服服务，程序会将自身安装成windows服务方式"
        "，这样就可以实现随系统自动启动服务。"
        "\n安装成系统服务后，可以随时再打开此程序进行配置更改及服务的卸载等 \n",
    )
    file_share_app.log_area.see(END)
    service_status_messages = {
        4: "当前后台服务状态：运行中...",
        1: "当前后台服务状态：已安装，未启动",
        None: "当前后台服务状态：未安装",
    }

    # 获取对应的消息
    message = service_status_messages.get(file_share_app.service_status, "未知状态")

    # 插入日志
    file_share_app.log_area.insert(END, f"\n{message}\n")
    file_share_app.log_area.see(END)

    root.mainloop()


if __name__ == "__main__":
    print("程序启动")  # 入口点检查
    print(f"运行参数: {sys.argv}")  # 参数检查
    print(f"程序目录:{get_app_path()}")

    # 确保静态文件目录存在
    static_dir = os.path.join(get_app_path(), "static")
    logos_dir = os.path.join(static_dir, "logos")
    os.makedirs(logos_dir, exist_ok=True)
    print(f"静态文件目录: {static_dir}")
    print(f"Logo目录: {logos_dir}")

    if len(sys.argv) > 1 and sys.argv[1].lower() == "--run-as-service":
        _append_svc_diag("进入 --run-as-service 服务分支")
        # 服务进程统一使用主程序目录（file_share_svc 的上级目录），保证与 GUI 共用同一份
        # share_config.json / config.key / 日志。目录/密钥/logo 对齐在 FileShareService.__init__
        # 中完成 —— 握手(StartServiceCtrlDispatcher)之前不做任何带副作用的操作，避免影响 SCM 通道。
        _SERVICE_MAIN_DIR = _resolve_service_main_dir()
        _append_svc_diag(f"主程序目录: {_SERVICE_MAIN_DIR}")
        if not PYWIN32_AVAILABLE:
            print(
                "当前环境缺少 pywin32 组件（servicemanager/win32service*），无法以系统服务模式运行。"
            )
            print("请安装 pywin32 后重试，或不带 --run-as-service 参数以普通模式启动。")
            sys.exit(1)
        try:
            _append_svc_diag("servicemanager.Initialize() 前")
            servicemanager.Initialize()
            _append_svc_diag("servicemanager.Initialize() 完成")
            servicemanager.PrepareToHostSingle(FileShareService)
            _append_svc_diag("PrepareToHostSingle 完成")
            servicemanager.StartServiceCtrlDispatcher()
            _append_svc_diag("StartServiceCtrlDispatcher 返回")
            win32serviceutil.HandleCommandLine(FileShareService)
            _append_svc_diag("HandleCommandLine 返回（正常退出）")
        except Exception as e:
            _append_svc_diag(f"服务分支异常: {e}\n{traceback.format_exc()}")
            print(f"服务错误: {str(e)}")  # 错误捕获
            traceback.print_exc()
            # 以非零码退出，便于 SCM 记录启动失败（1067）
            sys.exit(1)

    elif len(sys.argv) > 1 and sys.argv[1].lower() == "--headless-server":
        # 无界面服务器模式：统一主程序目录（与配置/密钥对齐），配合计划任务开机自启
        _SERVICE_MAIN_DIR = _resolve_service_main_dir()
        try:
            os.chdir(_SERVICE_MAIN_DIR)
            set_key_dir(_SERVICE_MAIN_DIR)
            config.logo_dir = os.path.join(_SERVICE_MAIN_DIR, "static", "logos")
            os.makedirs(config.logo_dir, exist_ok=True)
        except Exception:
            pass
        sys.exit(run_headless_server())

    elif len(sys.argv) > 1:
        if not PYWIN32_AVAILABLE:
            print(
                "当前环境缺少 pywin32 组件（servicemanager/win32service*），服务管理命令不可用。"
            )
            print("请安装 pywin32 后重试，或不带参数直接启动程序。")
            sys.exit(1)
        win32serviceutil.HandleCommandLine(FileShareService)
    else:
        main()
