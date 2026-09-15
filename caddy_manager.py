"""
Caddy 自动 HTTPS 管理模块
当程序目录存在 caddy.exe 时，使用 Caddy 反向代理实现自动申请/续签证书：
- Caddy 监听 ssl_port，反向代理到本机 HTTP 服务 (127.0.0.1:port)
- 通过 DNS-01 验证，无需对外开放 80/443
- 证书自动申请、自动续期，全部由 Caddy 托管
"""
import os
import subprocess
import sys
import threading
import time
import requests
from loguru import logger


# ---------------------------------------------------------------------------
# 支持的 DNS 提供商（含 Caddy 插件名、环境变量、Caddyfile 配置生成）
# ---------------------------------------------------------------------------
# key: 配置中 caddy_dns_provider 的值
# display: 界面显示名
# env_vars: Caddyfile 中 {env.XXX} 对应的环境变量名（按顺序）
# needs: 至少需要的凭据个数（0 表示可选）
DNS_PROVIDERS = {
    "alidns": {
        "display": "阿里云 DNS",
        "plugin": "github.com/caddy-dns/alidns",
        "caddy_name": "alidns",
        "env_creds": ("ALIDNS_ACCESS_KEY_ID", "ALIDNS_ACCESS_KEY_SECRET"),
        "needs": 2,
        "caddyfile_block": (
            "\ttls {\n"
            "\t\tdns alidns {\n"
            "\t\t\taccess_key_id {env.ALIDNS_ACCESS_KEY_ID}\n"
            "\t\t\taccess_key_secret {env.ALIDNS_ACCESS_KEY_SECRET}\n"
            "\t\t}\n"
            "\t}\n"
        ),
    },
    "tencentcloud": {
        "display": "腾讯云 DNSPod",
        "plugin": "github.com/caddy-dns/tencentcloud",
        "caddy_name": "tencentcloud",
        "env_creds": ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY"),
        "needs": 2,
        "caddyfile_block": (
            "\ttls {\n"
            "\t\tdns tencentcloud {\n"
            "\t\t\tsecret_id {env.TENCENTCLOUD_SECRET_ID}\n"
            "\t\t\tsecret_key {env.TENCENTCLOUD_SECRET_KEY}\n"
            "\t\t}\n"
            "\t}\n"
        ),
    },
    "cloudflare": {
        "display": "Cloudflare",
        "plugin": "github.com/caddy-dns/cloudflare",
        "caddy_name": "cloudflare",
        "env_creds": ("CF_API_TOKEN",),
        "needs": 1,
        "caddyfile_block": (
            "\ttls {\n"
            "\t\tdns cloudflare {env.CF_API_TOKEN}\n"
            "\t}\n"
        ),
    },
}

# Caddy 定制下载地址（打入全部支持的 DNS 插件）
CADDY_DOWNLOAD_URL = (
    "https://caddyserver.com/api/download?os=windows&arch=amd64"
    + "".join(
        f"&p={p['plugin']}" for p in DNS_PROVIDERS.values()
    )
)

# 下载最小有效大小（Caddy exe 约 40-60MB，低于 20MB 视为下载失败/损坏）
CADDY_MIN_SIZE = 20 * 1024 * 1024


def get_app_dir():
    """获取程序运行目录（与 main.py get_app_path 一致）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def webdav_internal_addr(config):
    """WebDAV 服务实际绑定地址 (host, port)。

    Caddy HTTPS 模式下，由 Caddy 对外监听 config.webdav_port（TLS 反代），
    WebDAV 服务改绑 127.0.0.1 上的内部端口，避免「同一端口双重绑定」以及
    Caddy 反代指向自身造成的回环。非 Caddy 模式则直接监听 0.0.0.0:webdav_port。
    """
    port = int(getattr(config, "webdav_port", 8081) or 8081)
    if getattr(config, "ssl_enabled", False) and getattr(config, "caddy_enabled", False):
        internal = port + 1
        busy = {
            int(getattr(config, "port", 0) or 0),
            int(getattr(config, "ssl_port", 0) or 0),
        }
        while internal in busy:
            internal += 1
        return "127.0.0.1", internal
    return "0.0.0.0", port


class CaddyManager:
    """Caddy 子进程管理：检测、Caddyfile 生成、启停、状态查询、一键下载"""

    def __init__(self, config):
        self.config = config
        self.process = None
        self.caddy_dir = os.path.join(get_app_dir(), "caddy")
        self.caddyfile_path = os.path.join(self.caddy_dir, "Caddyfile")
        # Caddy 数据/配置目录（证书持久化），服务账户也可以写程序目录
        self.data_dir = os.path.join(self.caddy_dir, "data")
        self.config_dir = os.path.join(self.caddy_dir, "config")

    # ---------- 检测 ----------

    def caddy_available(self):
        """检查程序目录下是否存在 caddy.exe"""
        return os.path.exists(self.get_caddy_exe())

    def get_caddy_exe(self):
        """获取 caddy.exe 路径"""
        return os.path.join(get_app_dir(), "caddy.exe")

    def get_version(self):
        """获取 Caddy 版本号，失败返回 None"""
        if not self.caddy_available():
            return None
        try:
            result = subprocess.run(
                [self.get_caddy_exe(), "version"],
                capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout.strip() or result.stderr.strip() or None
        except Exception as e:
            logger.warning(f"获取 Caddy 版本失败: {e}")
            return None

    def has_dns_plugin(self, provider=None):
        """检查 Caddy 是否内置指定 DNS 插件（默认检查所有已配置的）

        provider: 提供商 key，如 "alidns"/"cloudflare"；None 表示检查全部
        """
        if not self.caddy_available():
            return False
        try:
            result = subprocess.run(
                [self.get_caddy_exe(), "list-modules"],
                capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            modules = result.stdout
            if provider:
                info = DNS_PROVIDERS.get(provider)
                if not info:
                    return False
                return f"dns.providers.{info['caddy_name']}" in modules
            # 检查全部
            return all(
                f"dns.providers.{info['caddy_name']}" in modules
                for info in DNS_PROVIDERS.values()
            )
        except Exception as e:
            logger.warning(f"检查 Caddy 插件失败: {e}")
            return False

    def has_alidns_plugin(self):
        """兼容旧代码：检查 alidns 插件"""
        return self.has_dns_plugin("alidns")

    # ---------- 配置生成 ----------

    def _provider_info(self):
        """返回当前配置选中的 DNS 提供商信息；未选返回 None"""
        provider = (getattr(self.config, "caddy_dns_provider", "") or "").strip()
        return DNS_PROVIDERS.get(provider)

    def _provider_creds_present(self):
        """检查当前选中提供商所需的凭据是否已填（环境变量均非空）"""
        info = self._provider_info()
        if not info:
            return False
        env = self._provider_env()
        return all(bool(env.get(name)) for name in info["env_creds"])

    def _provider_env(self):
        """返回当前提供商的环境变量 -> 值 映射"""
        provider = (getattr(self.config, "caddy_dns_provider", "") or "").strip()
        result = {
            "ALIDNS_ACCESS_KEY_ID": self.config.caddy_access_key_id or "",
            "ALIDNS_ACCESS_KEY_SECRET": self.config.caddy_access_key_secret or "",
            "TENCENTCLOUD_SECRET_ID": self.config.caddy_tencent_secret_id or "",
            "TENCENTCLOUD_SECRET_KEY": self.config.caddy_tencent_secret_key or "",
            "CF_API_TOKEN": self.config.caddy_cloudflare_api_token or "",
        }
        return result

    def generate_caddyfile(self):
        """根据当前配置生成 Caddyfile

        DNS 凭据不写入 Caddyfile，通过环境变量传给 Caddy 进程，
        避免配置文件明文泄露凭据。

        证书验证模式：
        - 选中 DNS 提供商且已填凭据：走 DNS-01，无需对外开放 80/443
        - 否则：走默认 HTTP-01/TLS-ALPN，需对外开放 80/443
        """
        domain = (self.config.ssl_domain or "").strip()
        ssl_port = self.config.ssl_port
        target_port = self.config.port

        if not domain:
            raise ValueError("未配置绑定域名，无法生成 Caddyfile")

        info = self._provider_info()
        if info and self._provider_creds_present():
            tls_block = info["caddyfile_block"]
        else:
            # HTTP-01：Caddy 默认需要在 80 端口响应验证请求
            tls_block = ""

        caddyfile = (
            "{\n"
            "\tauto_https disable_redirects\n"  # 禁用 HTTP->HTTPS 重定向，避免占用 80 端口
            f"\thttps_port {ssl_port}\n"  # Caddy 监听端口
            "\tadmin off\n"             # 关闭管理 API（安全）
            "}\n"
            "\n"
            f"{domain} {{\n"
            f"{tls_block}"
            f"\treverse_proxy 127.0.0.1:{target_port}\n"
            "}\n"
        )

        # WebDAV 独立端口：启用 WebDAV 时对外提供 https://domain:webdav_port（TLS 反代）
        if getattr(self.config, "webdav_enabled", False):
            wd_port = int(getattr(self.config, "webdav_port", 0) or 0)
            if wd_port and wd_port != ssl_port:
                _wd_host, wd_internal = webdav_internal_addr(self.config)
                caddyfile += (
                    f"\n{domain}:{wd_port} {{\n"
                    f"{tls_block}"
                    f"\treverse_proxy {_wd_host}:{wd_internal}\n"
                    "}\n"
                )
        return caddyfile

    def write_caddyfile(self):
        """生成并写入 Caddyfile 到 caddy 目录"""
        os.makedirs(self.caddy_dir, exist_ok=True)
        caddyfile = self.generate_caddyfile()
        with open(self.caddyfile_path, "w", encoding="utf-8") as f:
            f.write(caddyfile)
        logger.info(f"Caddyfile 已生成: {self.caddyfile_path}")
        return self.caddyfile_path

    def validate_caddyfile(self):
        """验证 Caddyfile 语法 (caddy validate)"""
        if not os.path.exists(self.caddyfile_path):
            return False, "Caddyfile 不存在"
        try:
            env = self._build_env()
            result = subprocess.run(
                [self.get_caddy_exe(), "validate",
                 "--config", self.caddyfile_path, "--adapter", "caddyfile"],
                capture_output=True, text=True, timeout=15,
                stdin=subprocess.DEVNULL,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            return False, (result.stderr or result.stdout).strip()
        except Exception as e:
            return False, str(e)

    # ---------- 进程管理 ----------

    def _build_env(self):
        """构建 Caddy 运行环境变量：数据目录固定到程序目录 + DNS 凭据"""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)
        env = dict(os.environ)
        env["XDG_DATA_HOME"] = self.data_dir
        env["XDG_CONFIG_HOME"] = self.config_dir
        # 注入所有提供商的环境变量（未填的置空）
        for name, value in self._provider_env().items():
            env[name] = value
        return env

    def start(self):
        """启动 Caddy 反向代理"""
        if self.is_running():
            logger.info("Caddy 已运行，跳过启动")
            return True

        if not self.caddy_available():
            logger.error("caddy.exe 不存在，无法启动 Caddy 反代")
            return False

        if not self.config.ssl_domain:
            logger.error("未配置绑定域名，无法启动 Caddy")
            return False

        # 选中 DNS 提供商且有凭据 → DNS-01（需对应插件）；否则 → HTTP-01（需 80 端口）
        use_dns = self._provider_creds_present()
        if use_dns:
            info = self._provider_info()
            if not self.has_dns_plugin(info["caddy_name"]):
                logger.error(
                    f"当前 caddy.exe 缺少 {info['display']} 插件，无法使用 DNS 验证"
                )
                return False
        else:
            logger.info("未配置 DNS 凭据，将使用 HTTP-01 验证（需开放 80 端口）")

        try:
            self.write_caddyfile()
            ok, msg = self.validate_caddyfile()
            if not ok:
                logger.error(f"Caddyfile 验证失败: {msg}")
                return False

            env = self._build_env()
            self.process = subprocess.Popen(
                [self.get_caddy_exe(), "run",
                 "--config", self.caddyfile_path, "--adapter", "caddyfile"],
                env=env,
                cwd=self.caddy_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logger.info(f"Caddy 反代已启动: https://{self.config.ssl_domain}:{self.config.ssl_port}")
            return True
        except Exception as e:
            logger.error(f"启动 Caddy 失败: {e}")
            self.process = None
            return False

    def stop(self):
        """停止 Caddy 进程"""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                logger.info("Caddy 反代已停止")
            except Exception as e:
                logger.error(f"停止 Caddy 失败: {e}")
                return False
        self.process = None
        return True

    def is_running(self):
        """检查 Caddy 是否在运行

        双保险：
        1) 本实例持有 Caddy 子进程句柄且存活（GUI 前台模式启动）
        2) 探测 HTTPS 端口是否被监听（Caddy 由后台服务进程启动时，
           本实例没有进程句柄，但端口仍在监听）
        """
        if self.process is not None and self.process.poll() is None:
            return True

        # 未启用 Caddy 则不探测
        if not getattr(self.config, "caddy_enabled", False):
            return False

        try:
            ssl_port = getattr(self.config, "ssl_port", None)
            if not ssl_port:
                return False
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            try:
                return s.connect_ex(("127.0.0.1", ssl_port)) == 0
            finally:
                s.close()
        except Exception:
            return False

    def ensure_started(self):
        """确保 Caddy 以最新配置运行（headless/服务模式使用）。

        若检测到旧 Caddy 实例已在监听（可能由其他进程/上次服务遗留），
        先通过 `caddy stop --config` 优雅停止，再按最新 Caddyfile 启动。
        保证配置修改后重启服务时能生效。
        """
        if self.is_running():
            try:
                subprocess.run(
                    [self.get_caddy_exe(), "stop", "--config", self.caddyfile_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(1)
            except Exception:
                pass
            self.process = None
        return self.start()

    # ---------- 一键下载 ----------

    def download_caddy(self, progress_cb=None, max_retries=3):
        """下载带 alidns 插件的 Caddy 到程序目录

        特性：
        - 断点续传：已有未完成临时文件(xxx.download)时自动续传（服务器支持 Range 则续传，否则重下）
        - 自动重试：网络中断/超时自动重试 max_retries 次
        - 进度回调：progress_cb(downloaded_bytes, total_bytes) 单位字节

        progress_cb: 可选回调，用于 GUI 进度条
        返回 (成功, 提示信息)
        """
        target = self.get_caddy_exe()
        temp_file = target + ".download"

        # 已有临时文件大小（用于断点续传）
        existing = os.path.getsize(temp_file) if os.path.exists(temp_file) else 0

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"下载重试 {attempt}/{max_retries}...")
                    if progress_cb:
                        progress_cb(existing, 0)

                headers = {}
                if existing > 0:
                    headers["Range"] = f"bytes={existing}-"
                    logger.info(f"尝试断点续传，已有 {existing} 字节")

                with requests.get(
                    CADDY_DOWNLOAD_URL,
                    stream=True,
                    timeout=(15, 480),
                    headers=headers,
                ) as r:
                    # 服务器支持续传返回 206；不支持则返回 200（从头下载）
                    if r.status_code == 200 and existing > 0:
                        existing = 0
                        logger.info("服务器不支持断点续传，从头下载")
                    else:
                        r.raise_for_status()

                    total = int(r.headers.get("Content-Length", 0) or 0) + existing

                    mode = "ab" if existing > 0 else "wb"
                    downloaded = existing
                    with open(temp_file, mode) as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if progress_cb:
                                    progress_cb(downloaded, max(total, downloaded))
                    existing = downloaded  # 更新续传点（供下次重试复用）

                # 校验大小
                file_size = os.path.getsize(temp_file)
                if file_size < CADDY_MIN_SIZE:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        existing = 0
                    raise ValueError(f"下载文件异常(大小 {file_size} 字节)")

                # 替换旧文件
                if os.path.exists(target):
                    os.remove(target)
                os.rename(temp_file, target)

                # 校验可执行 + 插件
                ver = self.get_version()
                present = [
                    info["display"]
                    for key, info in DNS_PROVIDERS.items()
                    if self.has_dns_plugin(info["caddy_name"])
                ]
                msg = f"下载成功: {ver or '未知版本'}"
                if present:
                    msg += f"（含 DNS 插件: {', '.join(present)}）"
                return True, msg

            except requests.exceptions.HTTPError as e:
                # 416 Range Not Satisfiable：临时文件已完整，直接使用
                if (
                    e.response is not None
                    and e.response.status_code == 416
                    and os.path.exists(temp_file)
                    and os.path.getsize(temp_file) >= CADDY_MIN_SIZE
                ):
                    logger.info("服务器返回 416，临时文件已完整，直接使用")
                    if os.path.exists(target):
                        os.remove(target)
                    os.rename(temp_file, target)
                    ver = self.get_version()
                    return True, f"下载成功: {ver or '未知版本'}（恢复于续传点）"
                if attempt >= max_retries:
                    return False, f"下载失败(HTTP {e.response.status_code if e.response else '?'}): {e}"
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                ValueError,
            ) as e:
                if attempt >= max_retries:
                    # 保留未完成临时文件，供下次续传
                    if os.path.exists(temp_file):
                        logger.info(f"保留未完成文件 {temp_file} 供下次续传")
                    return False, f"下载失败: {e}（已重试 {max_retries} 次）"
                logger.warning(f"下载失败(第{attempt+1}次): {e}")

            # 等待后重试，并刷新续传点
            time.sleep(2 * (attempt + 1))
            existing = os.path.getsize(temp_file) if os.path.exists(temp_file) else 0

        return False, "下载失败，已达到最大重试次数"