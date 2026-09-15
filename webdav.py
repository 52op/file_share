# -*- coding: utf-8 -*-
"""WebDAV 集成（wsgidav，独立端口）。

- 认证映射（HTTP Basic，映射到现有密码体系；Basic 用户名不能含冒号）：
    admin         = 超级管理员密码    → 全部目录读写
    dir_<alias>   = 该目录管理密码     → 该目录读写
    guest         = 全局密码          → 全部目录只读
    <alias>       = 该目录访问密码     → 该目录只读
- 写权限：自定义中间件拦截 PUT/MKCOL/DELETE/MOVE/COPY/PROPPATCH/LOCK/UNLOCK，
  角色不含 admin/dir_admin → 403
- 审计：写操作记 dav_write 事件（角色/IP/UA）；只读方法不记
- 独立端口：webdav_enabled 时同进程起 Cheroot server（默认 8081）
"""
import threading

from wsgidav.mw.base_mw import BaseMiddleware

# WebDAV 写方法（需要写权限，且记审计）
WRITE_METHODS = {"PUT", "MKCOL", "DELETE", "MOVE", "COPY", "PROPPATCH", "LOCK", "UNLOCK"}
# 具备写权限的角色（见 build_webdav_app 的 user_mapping roles）
WRITE_ROLES = {"admin", "dir_admin"}


class DavAccessAuditMiddleware(BaseMiddleware):
    """写方法权限校验 + dav_write 审计（位于 HTTPAuthenticator 之后）。"""

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "")
        user = environ.get("wsgidav.auth.user_name", "") or ""
        if method in WRITE_METHODS:
            roles = tuple(environ.get("wsgidav.auth.roles") or ())
            _record_dav(user, environ, method)
            if not (WRITE_ROLES & set(roles)):
                body = b"Write access denied"
                start_response(
                    "403 Forbidden",
                    [
                        ("Content-Type", "text/plain; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                    ],
                )
                return [body]
        return self.next_app(environ, start_response)


def _record_dav(user, environ, method):
    """写操作审计：dav_write 事件（角色/IP/UA）"""
    try:
        import stats

        stats.record_event(
            type="dav_write",
            role=user or "anonymous",
            alias="",
            file=environ.get("PATH_INFO", "") or "",
            detail=method,
            ip=environ.get("REMOTE_ADDR", ""),
            ua=(environ.get("HTTP_USER_AGENT") or "")[:500],
        )
    except Exception:
        pass


def build_user_mapping(config):
    """根据现有密码体系构建 wsgidav simple_dc user_mapping（含 roles）。

    注意：HTTP Basic 用户名不能含冒号，目录管理员用 `dir_<alias>` 前缀。
    """
    # 全局用户（admin / guest）
    global_users = {}
    if config.admin_password:
        global_users["admin"] = {"password": config.admin_password, "roles": ["admin"]}
        if config.global_password:
            global_users["guest"] = {"password": config.global_password, "roles": ["readonly"]}

    user_mapping = {}
    for _name, d in config.shared_dirs.items():
        alias = d.alias
        if not alias:
            continue
        share_users = dict(global_users)
        if getattr(d, "admin_password", None):
            share_users[f"dir_{alias}"] = {
                "password": d.admin_password,
                "roles": ["dir_admin"],
            }
        if getattr(d, "password", None):
            share_users[alias] = {"password": d.password, "roles": ["readonly"]}
        if share_users:
            user_mapping["/" + alias] = share_users

    if global_users:
        user_mapping["*"] = dict(global_users)
    return user_mapping


def build_provider_mapping(config):
    provider_mapping = {}
    for _name, d in config.shared_dirs.items():
        alias = d.alias
        if alias:
            provider_mapping["/" + alias] = {"root": d.path, "readonly": False}
    return provider_mapping


def build_webdav_app(config):
    """构建 wsgidav WSGI 应用（供独立端口 server 使用）。"""
    from wsgidav.wsgidav_app import WsgiDAVApp

    dav_config = {
        "provider_mapping": build_provider_mapping(config),
        "http_authenticator": {
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
            "domain_controller": None,  # 默认 SimpleDomainController
        },
        "simple_dc": {"user_mapping": build_user_mapping(config)},
        "middleware_stack": [
            "wsgidav.error_printer.ErrorPrinter",
            "wsgidav.http_authenticator.HTTPAuthenticator",
            DavAccessAuditMiddleware,
            "wsgidav.dir_browser.WsgiDavDirBrowser",
            "wsgidav.request_resolver.RequestResolver",  # 必须最后
        ],
        "verbose": 1,
        "logging": {"enable": False},
        "dir_browser": {"enable": True, "show_user": False, "show_logout": False},
        "lock_storage": False,
        "property_manager": False,
        "hotfixes": {"re_encode_path_info": True},
    }
    return WsgiDAVApp(dav_config)


def start_webdav(config):
    """在独立端口启动 WebDAV server（线程，daemon）。启用则返回 server，否则 None。

    绑定模式（见 caddy_manager.webdav_internal_addr / webdav_uses_tls）：
    - Caddy HTTPS 模式：绑 127.0.0.1 内部端口（HTTP），Caddy 对外监听 webdav_port 做 TLS 反代
    - 手动证书模式（ssl_enabled 且证书有效）：绑 0.0.0.0:webdav_port 直接 HTTPS
    - 其他：绑 0.0.0.0:webdav_port 裸 HTTP
    """
    if not getattr(config, "webdav_enabled", False):
        return None
    try:
        app = build_webdav_app(config)
        try:
            from caddy_manager import webdav_internal_addr, webdav_uses_tls

            bind_host, port = webdav_internal_addr(config)
            use_tls = webdav_uses_tls(config)
        except Exception:
            bind_host = "0.0.0.0"
            port = int(getattr(config, "webdav_port", 8081) or 8081)
            use_tls = False

        if use_tls:
            # 手动证书模式：webdav 端口直接绑定 TLS（不建 TLS server 的 Caddy 分支）
            from ssl_manager import SSLCertificateManager

            ssl_manager = SSLCertificateManager(config)
            cert = ssl_manager.get_cert_file_path()
            key = ssl_manager.get_key_file_path()
            if not cert or not key:
                raise FileNotFoundError("未找到可用的 SSL 证书/私钥，WebDAV HTTPS 无法启动")
            from cheroot_server import CherootServer

            server = CherootServer(
                app, host=bind_host, port=port, ssl_cert=cert, ssl_key=key, threads=10
            )
            server.create_server()  # 提前创建，证书无效时立即失败而非静默不监听
        else:
            from cheroot.wsgi import Server as CherootWSGIServer

            server = CherootWSGIServer((bind_host, port), app, numthreads=10)
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        return server
    except Exception as e:
        try:
            import loguru

            loguru.logger.error(f"WebDAV 启动失败: {e}")
        except Exception:
            pass
        return None


def stop_webdav(server):
    """停止 WebDAV server（安全容错）。"""
    if server is None:
        return
    try:
        server.stop()
    except Exception:
        pass
