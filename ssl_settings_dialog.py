"""
SSL设置对话框
用于配置SSL证书相关设置
"""
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox as tkmessagebox
from datetime import datetime
import threading
import os
import webbrowser

from caddy_manager import CADDY_DOWNLOAD_URL, DNS_PROVIDERS


class ToolTip:
    """鼠标悬停浮动提示（延迟显示，主窗口同款样式）"""

    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self.tooltip = None
        self.widget.bind("<Enter>", self._schedule)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, event):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 15
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tooltip,
            text=self.text,
            justify="left",
            background="#FFFFE0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("宋体", 9),
        )
        label.pack(ipadx=4, ipady=3)

    def _hide(self, event=None):
        self._cancel()
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


class SSLSettingsDialog(ttk.Toplevel):
    def __init__(self, parent, config, ssl_manager, update_callback=None):
        super().__init__(parent)
        self.config = config
        self.ssl_manager = ssl_manager
        self.update_callback = update_callback  # 添加回调函数
        self.result = None

        # Caddy 管理器
        from caddy_manager import CaddyManager

        self.caddy_manager = CaddyManager(config)

        self.withdraw()  # 先隐藏窗口
        self.title("SSL设置")
        self.geometry("560x600")
        
        # 设置图标
        try:
            from main import get_path
            icon_path = get_path('static/favicon.ico')
            self.iconbitmap(icon_path)
        except:
            pass
        
        self.setup_ui()
        self.load_current_settings()
        self.center_window(parent)
        
        self.transient(parent)
        self.grab_set()
        self.deiconify()  # 显示窗口
    
    def setup_ui(self):
        """设置UI界面：两个 TAB（证书下载 / Caddy 自动 HTTPS）"""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=BOTH, expand=True, padx=15, pady=12)

        # 顶部帮助按钮行
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=X, pady=(0, 6))

        # SSL 端口（两种模式共用，放顶部） 
        ttk.Label(header_frame, text="SSL端口:").pack(side=LEFT)
        self.ssl_port_var = tk.StringVar(value=str(self.config.ssl_port))
        port_entry = ttk.Entry(header_frame, textvariable=self.ssl_port_var, width=8)
        port_entry.pack(side=LEFT, padx=(6, 0))
        ToolTip(port_entry, "HTTPS 监听端口（如 443/12346）。Caddy 模式下即 Caddy 的 HTTPS 端口")

        # 绑定域名（两种模式共用，放顶部）
        ttk.Label(header_frame, text="绑定域名:").pack(side=LEFT, padx=(10, 0))
        self.ssl_domain_var = tk.StringVar(value=self.config.ssl_domain)
        domain_entry = ttk.Entry(header_frame, textvariable=self.ssl_domain_var, width=18)
        domain_entry.pack(side=LEFT, padx=(6, 0), fill=X, expand=True)
        ToolTip(domain_entry, "网站绑定域名，如 pan.example.com")

        self.help_btn = ttk.Button(
            header_frame,
            text="?",
            width=3,
            command=self.show_help,
            style="secondary.TButton",
        )
        self.help_btn.pack(side=RIGHT)
        ToolTip(self.help_btn, "查看 SSL 设置说明")

        # ---------- TAB 组件 ----------
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        # ===== TAB 1：证书下载模式 =====
        tab_download = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab_download, text="📄 证书下载模式")

        # 方式说明
        ttk.Label(
            tab_download,
            text="从自建证书服务器下载 .zip 证书包，解压到 certs 目录供 Cheroot 使用。",
            foreground="gray",
            justify="left",
            wraplength=490,
        ).pack(anchor=W, pady=(0, 8))

        # SSL启用开关
        ssl_frame = ttk.Frame(tab_download)
        ssl_frame.pack(fill=X, pady=(0, 6))
        self.ssl_enabled_var = tk.BooleanVar(value=self.config.ssl_enabled)
        ssl_check = ttk.Checkbutton(
            ssl_frame, 
            text="启用SSL (HTTPS)", 
            variable=self.ssl_enabled_var,
            command=self.on_ssl_toggle
        )
        ssl_check.pack(side=LEFT)
        ToolTip(ssl_check, "启用或禁用 HTTPS 加密访问，需重启服务后生效")

        # 证书服务器地址
        server_frame = ttk.Frame(tab_download)
        server_frame.pack(fill=X, pady=(0, 6))
        ttk.Label(server_frame, text="证书服务器:").pack(anchor=W)
        self.cert_server_var = tk.StringVar(value=self.config.cert_server_url)
        server_entry = ttk.Entry(server_frame, textvariable=self.cert_server_var)
        server_entry.pack(fill=X, pady=(3, 0))
        ToolTip(server_entry, "自建证书服务器地址，用于下载路径为 {服务器}/{域名}_{日期}.zip 的证书包")

        # 证书状态显示
        status_frame = ttk.LabelFrame(tab_download, text="证书状态", padding=8)
        status_frame.pack(fill=X, pady=(8, 0))
        self.status_label = ttk.Label(status_frame, text="检查中...")
        self.status_label.pack(anchor=W)
        self.expiry_label = ttk.Label(status_frame, text="")
        self.expiry_label.pack(anchor=W, pady=(4, 0))

        # 操作按钮
        action_frame = ttk.Frame(status_frame)
        action_frame.pack(fill=X, pady=(8, 0))
        self.test_btn = ttk.Button(
            action_frame, 
            text="测试连接", 
            command=self.test_connection,
            style="info.TButton"
        )
        self.test_btn.pack(side=LEFT, padx=(0, 8))
        ToolTip(self.test_btn, "测试证书服务器连接是否可达")

        self.update_btn = ttk.Button(
            action_frame, 
            text="更新证书", 
            command=self.update_certificate,
            style="warning.TButton"
        )
        self.update_btn.pack(side=LEFT, padx=(0, 8))
        ToolTip(self.update_btn, "从证书服务器重新下载最新证书文件")

        self.preview_btn = ttk.Button(
            action_frame,
            text="预览URL",
            command=self.preview_url,
            style="secondary.TButton"
        )
        self.preview_btn.pack(side=LEFT)
        ToolTip(self.preview_btn, "查看将下载的证书文件完整链接")

        # ===== TAB 2：Caddy 自动 HTTPS =====
        tab_caddy = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab_caddy, text="🚀 Caddy 自动 HTTPS")

        # 启用开关 + 说明
        caddy_toggle_frame = ttk.Frame(tab_caddy)
        caddy_toggle_frame.pack(fill=X, pady=(0, 6))
        self.caddy_enabled_var = tk.BooleanVar(value=self.config.caddy_enabled)
        self.caddy_check = ttk.Checkbutton(
            caddy_toggle_frame,
            text="启用 Caddy 反向代理自动 HTTPS",
            variable=self.caddy_enabled_var,
            command=self.on_caddy_toggle,
        )
        self.caddy_check.pack(side=LEFT)
        ToolTip(self.caddy_check, "启用后 Caddy 将自动申请/续期证书，并反向代理到本机 HTTP 服务")

        ttk.Label(
            tab_caddy,
            text=(
                "Caddy 可自动申请、续期 HTTPS 证书（Let's Encrypt），无需开放 80/443 端口。\n"
                "使用条件：程序目录存在 caddy.exe + 配置 DNS 提供商凭据（支持阿里云/腾讯云/Cloudflare）。"
            ),
            foreground="gray",
            justify="left",
            wraplength=490,
        ).pack(anchor=W, pady=(0, 8))

        # 下载区
        dl_frame = ttk.Frame(tab_caddy)
        dl_frame.pack(fill=X, pady=(0, 6))
        self.caddy_status_label = ttk.Label(dl_frame, text="检测中...")
        self.caddy_status_label.pack(side=LEFT)
        self.download_btn = ttk.Button(
            dl_frame,
            text="一键下载 Caddy",
            command=self.download_caddy,
            style="info.TButton",
        )
        self.download_btn.pack(side=RIGHT)
        ToolTip(self.download_btn, "下载带全部 DNS 插件（阿里云/腾讯云/Cloudflare）的 caddy.exe 到程序目录")
        self.open_page_btn = ttk.Button(
            dl_frame,
            text="打开下载页",
            command=self.open_caddy_download_page,
            style="secondary.TButton",
        )
        self.open_page_btn.pack(side=RIGHT, padx=(0, 5))
        ToolTip(self.open_page_btn, "在浏览器打开 Caddy 官方下载页，可手动下载后放入程序目录")

        # 下载进度条
        progress_frame = ttk.Frame(tab_caddy)
        progress_frame.pack(fill=X, pady=(4, 0))
        self.download_progress = ttk.Progressbar(
            progress_frame, mode="determinate", maximum=100, value=0
        )
        self.download_progress.pack(side=LEFT, fill=X, expand=True)
        self.download_pct_label = ttk.Label(progress_frame, text="", width=8)
        self.download_pct_label.pack(side=LEFT, padx=(6, 0))

        # 下载地址（可复制）
        self.caddy_url_var = tk.StringVar(value=CADDY_DOWNLOAD_URL)
        ttk.Entry(tab_caddy, textvariable=self.caddy_url_var, state="readonly").pack(fill=X, pady=(6, 0))

        # DNS 提供商选择
        provider_frame = ttk.Frame(tab_caddy)
        provider_frame.pack(fill=X, pady=(6, 0))
        ttk.Label(provider_frame, text="DNS 提供商:").pack(side=LEFT)
        self.caddy_provider_var = tk.StringVar(
            value=self.config.caddy_dns_provider or "alidns"
        )
        provider_choices = list(DNS_PROVIDERS.keys())
        self.provider_combo = ttk.Combobox(
            provider_frame,
            textvariable=self.caddy_provider_var,
            values=provider_choices,
            state="readonly",
            width=16,
        )
        self.provider_combo.pack(side=LEFT, padx=(8, 0))
        self.provider_combo.bind("<<ComboboxSelected>>", self.on_provider_change)
        ToolTip(self.provider_combo, "选择域名所属的 DNS 服务商，用于自动验证域名归属（DNS-01）")

        # DNS 凭据区（随提供商动态变化）
        cred_frame = ttk.Frame(tab_caddy)
        cred_frame.pack(fill=X, pady=(6, 0))
        self.cred1_label = ttk.Label(cred_frame, text="")
        self.cred1_label.grid(row=0, column=0, sticky=W)
        self.cred1_var = tk.StringVar()
        self.cred1_entry = ttk.Entry(cred_frame, textvariable=self.cred1_var)
        self.cred1_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self._cred1_tip = ToolTip(self.cred1_entry, "")
        self.cred2_label = ttk.Label(cred_frame, text="")
        self.cred2_label.grid(row=1, column=0, sticky=W, pady=(5, 0))
        self.cred2_var = tk.StringVar()
        self.cred2_entry = ttk.Entry(cred_frame, textvariable=self.cred2_var, show="*")
        self.cred2_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(5, 0))
        self._cred2_tip = ToolTip(self.cred2_entry, "")
        cred_frame.columnconfigure(1, weight=1)

        # DNS 凭据用途说明
        self.cred_hint_label = ttk.Label(
            tab_caddy,
            text="",
            foreground="gray",
            justify="left",
            wraplength=490,
        )
        self.cred_hint_label.pack(anchor=W, pady=(6, 0))

        # ===== 底部按钮（两个 TAB 共用）=====
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=X, pady=(12, 0))

        # 保存按钮放底部，两个模式共用
        self.save_btn = ttk.Button(
            btn_frame,
            text="💾 保存设置",
            command=self.save_settings,
            style="success.TButton",
        )
        self.save_btn.pack(side=LEFT)
        ToolTip(self.save_btn, "保存当前 SSL/Caddy 设置，保存后需重启服务生效")

        ttk.Button(btn_frame, text="确定", command=self.confirm).pack(side=RIGHT, padx=(0, 0))
        ttk.Button(btn_frame, text="取消", command=self.cancel).pack(side=RIGHT, padx=(0, 10))
        
        # 初始更新状态
        self.update_certificate_status()
        self.update_caddy_status()
        self.on_ssl_toggle()
        self.on_caddy_toggle()
        # 根据是否启用 Caddy 选择默认 TAB
        if self.config.caddy_enabled:
            self.notebook.select(1)
        else:
            self.notebook.select(0)
    
    def center_window(self, parent):
        """窗口居中显示"""
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
    
    def load_current_settings(self):
        """加载当前设置"""
        self.ssl_enabled_var.set(self.config.ssl_enabled)
        self.ssl_port_var.set(str(self.config.ssl_port))
        self.cert_server_var.set(self.config.cert_server_url)
        self.ssl_domain_var.set(self.config.ssl_domain)
        self.caddy_enabled_var.set(self.config.caddy_enabled)
        self.caddy_provider_var.set(self.config.caddy_dns_provider or "alidns")
        # 按当前提供商填充凭据
        creds = {
            "alidns": (self.config.caddy_access_key_id, self.config.caddy_access_key_secret),
            "tencentcloud": (self.config.caddy_tencent_secret_id, self.config.caddy_tencent_secret_key),
            "cloudflare": (self.config.caddy_cloudflare_api_token, ""),
        }.get(self.config.caddy_dns_provider or "alidns", ("", ""))
        self.cred1_var.set(creds[0])
        self.cred2_var.set(creds[1])
        self.on_provider_change()

    def on_provider_change(self, event=None):
        """DNS 提供商切换：更新凭据字段标签、提示与显隐"""
        provider = self.caddy_provider_var.get()
        info = DNS_PROVIDERS.get(provider)
        if not info:
            return
        if provider == "cloudflare":
            self.cred1_label.configure(text="Cloudflare API Token:")
            self._cred1_tip.text = (
                "Cloudflare API Token，需 Zone.DNS:Edit + Zone.Zone:Read 权限。\n"
                "在 Cloudflare 控制台 → My Profile → API Tokens 创建。"
            )
            # 隐藏第二行（label + entry）
            self._hide_cred2(True)
            self.cred_hint_label.configure(
                text=(
                    "Cloudflare 凭据：API Token（需 Zone.DNS:Edit + Zone.Zone:Read 权限）。\n"
                    "留空则改用 HTTP-01 验证（需开放 80 端口）。"
                )
            )
        else:
            if provider == "alidns":
                self.cred1_label.configure(text="阿里云 AccessKey ID:")
                self.cred2_label.configure(text="阿里云 AccessKey Secret:")
                self._cred1_tip.text = "阿里云 AccessKey ID，在阿里云控制台 → AccessKey 管理获取"
                self._cred2_tip.text = "阿里云 AccessKey Secret，与 AccessKey ID 成对使用"
            elif provider == "tencentcloud":
                self.cred1_label.configure(text="腾讯云 SecretId:")
                self.cred2_label.configure(text="腾讯云 SecretKey:")
                self._cred1_tip.text = "腾讯云 SecretId（需开通 DNSPod API），在腾讯云控制台 → API 密钥管理获取"
                self._cred2_tip.text = "腾讯云 SecretKey，与 SecretId 成对使用"
            self._hide_cred2(False)
            self.cred_hint_label.configure(
                text=(
                    "凭据用于 DNS-01 验证（CA 通过改 TXT 记录确认域名归属）。\n"
                    "留空则改用 HTTP-01 验证（需开放 80 端口）。"
                )
            )
        self.update_caddy_status()

    def _hide_cred2(self, hide):
        """隐藏/显示第二个凭据行（仅 Cloudflare 用单 Token）"""
        if hide:
            self.cred2_label.grid_remove()
            self.cred2_entry.grid_remove()
        else:
            self.cred2_label.grid(row=1, column=0, sticky=W, pady=(6, 0))
            self.cred2_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))

    def on_ssl_toggle(self):
        """SSL开关切换事件"""
        enabled = self.ssl_enabled_var.get()
        
        # 根据SSL开关状态启用/禁用相关控件
        state = "normal" if enabled else "disabled"
        
        # 这里可以添加控件状态控制逻辑
        if enabled:
            self.update_certificate_status()

    def on_tab_changed(self, event=None):
        """TAB 切换：证书下载(0) 与 Caddy(1) 二选一，同步 caddy_enabled
        切到 Caddy 模式时自动勾选"启用SSL"，因为 Caddy 反代本身就是 HTTPS
        """
        try:
            idx = self.notebook.index(self.notebook.select())
            self.caddy_enabled_var.set(idx == 1)
            if idx == 1:
                self.ssl_enabled_var.set(True)
            self.on_caddy_toggle()
        except Exception:
            pass

    def on_caddy_toggle(self):
        """Caddy 开关切换事件：根据启用状态刷新状态显示"""
        self.update_caddy_status()

    def update_caddy_status(self):
        """更新 Caddy 状态显示"""
        try:
            if not self.caddy_manager.caddy_available():
                self.caddy_status_label.configure(
                    text="✗ 未检测到 caddy.exe", foreground="red"
                )
                return

            version = self.caddy_manager.get_version() or ""
            # 检查当前所选提供商的插件
            provider = self.caddy_provider_var.get()
            info = DNS_PROVIDERS.get(provider)
            has_plugin = (
                self.caddy_manager.has_dns_plugin(info["caddy_name"])
                if info else False
            )
            running = self.caddy_manager.is_running()

            status = f"✓ 已安装 {version}"
            if not has_plugin:
                status += f"（缺少 {info['display']} 插件）"
                self.caddy_status_label.configure(text=status, foreground="orange")
            elif running:
                status += "（运行中）"
                self.caddy_status_label.configure(text=status, foreground="green")
            else:
                status += "（未运行）"
                self.caddy_status_label.configure(text=status, foreground="blue")
        except Exception as e:
            self.caddy_status_label.configure(
                text=f"✗ 状态检查失败: {str(e)}", foreground="red"
            )

    def open_caddy_download_page(self):
        """打开 Caddy 官方下载页面"""
        webbrowser.open(CADDY_DOWNLOAD_URL)

    def show_help(self):
        """弹出 SSL 设置说明窗口"""
        help_win = tk.Toplevel(self)
        help_win.title("SSL 设置说明")
        help_win.geometry("560x520")
        help_win.transient(self)
        help_win.grab_set()
        help_win.attributes("-topmost", True)
        try:
            from main import get_path
            help_win.iconbitmap(get_path('static/favicon.ico'))
        except Exception:
            pass

        help_text = (
            "【SSL/HTTPS 两种工作方式】\n\n"
            "一、证书下载模式（默认）\n"
            "    从自建证书服务器下载 {域名}_{日期}.zip 证书包，解压到 certs 目录。\n"
            "    适用：已有专用证书服务器签发证书的场景。\n\n"
            "二、Caddy 自动 HTTPS（推荐）\n"
            "    Caddy 反向代理自动申请、续期 Let's Encrypt 证书，无需开放 80/443 端口。\n"
            "    需先在程序目录放 caddy.exe（可用下方「一键下载」）。\n\n"
            "【Caddy 域名验证方式】\n"
            "    1. DNS-01：填写 DNS 提供商凭据（阿里云/腾讯云/Cloudflare），\n"
            "       CA 通过修改 TXT 记录确认域名归属，无需开放任何入站端口。\n"
            "    2. HTTP-01：不填凭据时自动使用，需服务器 80 端口对外开放。\n\n"
            "【各 DNS 提供商凭据】\n"
            "    - 阿里云：AccessKey ID + Secret（AccessKey 管理页）\n"
            "    - 腾讯云：SecretId + SecretKey（需开通 DNSPod API）\n"
            "    - Cloudflare：API Token（需 Zone.DNS:Edit + Zone.Zone:Read 权限）\n\n"
            "【端口说明】\n"
            "    SSL 端口即 Caddy 的 HTTPS 监听端口（如 443/12346）。\n"
            "    访问形式：https://域名:端口，例如 https://pan.example.com:12346\n\n"
            "【注意事项】\n"
            "    - 保存设置后需重启服务才生效。\n"
            "    - Caddy 证书默认 90 天自动续期，无需手动干预。\n"
            "    - 若 80/443 无法对外开放，务必填写 DNS 凭据，否则无法自动签证书。"
        )

        # 帮助文本（带滚动）
        outer = ttk.Frame(help_win)
        outer.pack(fill=BOTH, expand=True, padx=10, pady=10)

        scroll = ttk.Scrollbar(outer)
        scroll.pack(side=RIGHT, fill=Y)

        text = tk.Text(
            outer,
            wrap="word",
            yscrollcommand=scroll.set,
            font=("宋体", 10),
            padx=8,
            pady=8,
        )
        text.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=text.yview)

        text.insert("1.0", help_text)
        text.configure(state="disabled")

        close_btn = ttk.Button(help_win, text="关闭", command=help_win.destroy)
        close_btn.pack(pady=(0, 10))
        ToolTip(close_btn, "关闭说明窗口")

    def download_caddy(self):
        """一键下载 Caddy（带 alidns 插件），含进度条、断点续传、自动重试"""
        def download_thread():
            try:
                self.download_btn.configure(text="下载中...", state="disabled")
                self.download_progress.configure(value=0)
                self.download_pct_label.configure(text="0%")

                # 检测是否已有未完成文件（续传）
                import os as _os

                exe_path = self.caddy_manager.get_caddy_exe()
                temp_path = exe_path + ".download"
                if _os.path.exists(temp_path) and _os.path.getsize(temp_path) > 0:
                    resume_mb = _os.path.getsize(temp_path) / 1024 / 1024
                    self.download_pct_label.configure(
                        text=f"续传 {resume_mb:.1f}MB"
                    )

                def progress(downloaded, total):
                    # 后台线程通过 root.after 安全更新 UI
                    def _update():
                        if total and total > 0:
                            pct = min(100, downloaded * 100.0 / total)
                            self.download_progress.configure(value=pct)
                            self.download_pct_label.configure(text=f"{pct:.0f}%")
                            self.download_btn.configure(
                                text=f"下载中 {downloaded/1024/1024:.1f}/{total/1024/1024:.1f}MB"
                            )
                        else:
                            # 总大小未知（如续传后服务端未给长度），显示已下载量
                            self.download_btn.configure(
                                text=f"下载中 {downloaded/1024/1024:.1f}MB"
                            )

                    self.after(0, _update)

                ok, msg = self.caddy_manager.download_caddy(progress_cb=progress)

                if ok:
                    self.download_progress.configure(value=100)
                    self.download_pct_label.configure(text="100%")
                    tkmessagebox.showinfo("下载成功", msg)
                else:
                    tkmessagebox.showerror(
                        "下载失败",
                        msg + "\n\n可手动复制上方下载地址到浏览器下载，下载后放入程序目录。",
                    )
            except Exception as e:
                tkmessagebox.showerror("错误", f"下载过程中发生错误: {str(e)}")
            finally:
                self.download_btn.configure(text="一键下载 Caddy", state="normal")
                self.download_progress.configure(value=0)
                self.download_pct_label.configure(text="")
                self.update_caddy_status()

        threading.Thread(target=download_thread, daemon=True).start()
    
    def update_certificate_status(self):
        """更新证书状态显示"""
        try:
            if self.ssl_manager.has_valid_certificate():
                expiry_date = self.ssl_manager.get_certificate_expiry_date()
                if expiry_date:
                    days_until_expiry = (expiry_date - datetime.now()).days
                    if days_until_expiry > 10:
                        self.status_label.configure(text="● 证书有效", foreground="green")
                        self.expiry_label.configure(text=f"到期时间: {expiry_date.strftime('%Y-%m-%d')} ({days_until_expiry}天后)")
                    else:
                        self.status_label.configure(text="⚠ 证书即将到期", foreground="orange")
                        self.expiry_label.configure(text=f"到期时间: {expiry_date.strftime('%Y-%m-%d')} ({days_until_expiry}天后)")
                else:
                    self.status_label.configure(text="⚠ 无法读取证书信息", foreground="orange")
                    self.expiry_label.configure(text="")
            else:
                self.status_label.configure(text="✗ 无有效证书", foreground="red")
                self.expiry_label.configure(text="请下载或更新证书")
        except Exception as e:
            self.status_label.configure(text="✗ 证书状态检查失败", foreground="red")
            self.expiry_label.configure(text=f"错误: {str(e)}")
    
    def test_connection(self):
        """测试证书服务器连接"""
        def test_thread():
            try:
                self.test_btn.configure(text="测试中...", state="disabled")

                server_url = self.cert_server_var.get().strip()
                domain = self.ssl_domain_var.get().strip()

                if not server_url:
                    tkmessagebox.showwarning("警告", "请先填写证书服务器地址")
                    return

                # 首先测试服务器基本连接
                import requests
                try:
                    # 测试服务器根目录
                    base_response = requests.head(server_url, timeout=10)
                    server_reachable = True
                except:
                    server_reachable = False

                if not server_reachable:
                    tkmessagebox.showerror("错误", f"无法连接到证书服务器: {server_url}")
                    return

                # 如果填写了域名，测试具体的证书文件
                if domain:
                    # 临时更新配置以生成URL
                    old_server = self.config.cert_server_url
                    old_domain = self.config.ssl_domain

                    self.config.cert_server_url = server_url
                    self.config.ssl_domain = domain

                    test_url = self.ssl_manager.get_cert_download_url()

                    # 恢复配置
                    self.config.cert_server_url = old_server
                    self.config.ssl_domain = old_domain

                    if test_url:
                        try:
                            response = requests.head(test_url, timeout=10)
                            if response.status_code == 200:
                                tkmessagebox.showinfo("成功", f"连接测试成功！\n证书文件存在: {test_url}")
                            elif response.status_code == 404:
                                tkmessagebox.showwarning("提示", f"服务器连接正常，但今日证书文件不存在:\n{test_url}\n\n这是正常的，证书文件可能还未生成。")
                            else:
                                tkmessagebox.showinfo("提示", f"服务器连接正常\n响应代码: {response.status_code}")
                        except requests.RequestException:
                            tkmessagebox.showinfo("提示", f"服务器连接正常\n证书文件URL: {test_url}")
                    else:
                        tkmessagebox.showerror("错误", "无法生成证书下载URL")
                else:
                    tkmessagebox.showinfo("成功", f"证书服务器连接正常: {server_url}")

            except Exception as e:
                tkmessagebox.showerror("错误", f"测试过程中发生错误: {str(e)}")
            finally:
                self.test_btn.configure(text="测试连接", state="normal")

        threading.Thread(target=test_thread, daemon=True).start()
    
    def update_certificate(self):
        """更新证书"""
        def update_thread():
            try:
                self.update_btn.configure(text="更新中...", state="disabled")
                
                # 临时更新配置
                self.config.cert_server_url = self.cert_server_var.get().strip()
                self.config.ssl_domain = self.ssl_domain_var.get().strip()
                
                if self.ssl_manager.download_certificate():
                    tkmessagebox.showinfo("成功", "证书更新成功！")
                    self.update_certificate_status()
                else:
                    tkmessagebox.showerror("错误", "证书更新失败，请检查日志")
                    
            except Exception as e:
                tkmessagebox.showerror("错误", f"更新证书时发生错误: {str(e)}")
            finally:
                self.update_btn.configure(text="更新证书", state="normal")
        
        threading.Thread(target=update_thread, daemon=True).start()
    
    def preview_url(self):
        """预览证书下载URL"""
        server_url = self.cert_server_var.get().strip()
        domain = self.ssl_domain_var.get().strip()

        if not server_url or not domain:
            tkmessagebox.showwarning("警告", "请先填写证书服务器地址和域名")
            return

        # 临时更新配置以生成URL
        old_server = self.config.cert_server_url
        old_domain = self.config.ssl_domain

        self.config.cert_server_url = server_url
        self.config.ssl_domain = domain

        url = self.ssl_manager.get_cert_download_url()

        # 恢复配置
        self.config.cert_server_url = old_server
        self.config.ssl_domain = old_domain

        if url:
            tkmessagebox.showinfo("预览URL", f"证书下载地址:\n{url}")
        else:
            tkmessagebox.showerror("错误", "无法生成证书下载URL")

    def save_settings(self):
        """保存SSL设置"""
        try:
            self.save_btn.configure(text="保存中...", state="disabled")

            # 验证端口号
            ssl_port_str = self.ssl_port_var.get().strip()
            if not ssl_port_str:
                tkmessagebox.showerror("错误", "请输入SSL端口")
                return

            ssl_port = int(ssl_port_str)
            if not (1 <= ssl_port <= 65535):
                tkmessagebox.showerror("错误", "SSL端口必须在1-65535之间")
                return

            # 如果启用SSL，验证必要字段
            ssl_enabled = self.ssl_enabled_var.get()
            caddy_enabled = self.caddy_enabled_var.get()

            # Caddy 模式本身就是 HTTPS：选中 Caddy 时强制启用 SSL
            if caddy_enabled:
                ssl_enabled = True
                self.ssl_enabled_var.set(True)

            cert_server = self.cert_server_var.get().strip()
            ssl_domain = self.ssl_domain_var.get().strip()

            if ssl_enabled:
                if not ssl_domain:
                    tkmessagebox.showerror("错误", "启用SSL时必须填写绑定域名")
                    return

                if caddy_enabled:
                    # Caddy 模式：证书由 Caddy 自动申请，需要 caddy.exe
                    if not self.caddy_manager.caddy_available():
                        tkmessagebox.showerror(
                            "错误",
                            "启用 Caddy 自动 HTTPS 需要程序目录存在 caddy.exe，\n请先点击「一键下载 Caddy」。",
                        )
                        return
                    provider = self.caddy_provider_var.get()
                    info = DNS_PROVIDERS.get(provider)
                    cred1 = self.cred1_var.get().strip()
                    cred2 = self.cred2_var.get().strip()
                    if cred1 and (provider == "cloudflare" or cred2):
                        # 填了凭据 → DNS-01，需要对应插件
                        if not self.caddy_manager.has_dns_plugin(
                            info["caddy_name"] if info else None
                        ):
                            tkmessagebox.showerror(
                                "错误",
                                f"当前 caddy.exe 缺少 {info['display'] if info else provider} 插件（DNS 验证需要），\n请重新下载带插件的版本。",
                            )
                            return
                    else:
                        # 未填凭据 → HTTP-01，需开放 80 端口
                        import tkinter.messagebox as _tkmsg

                        if not _tkmsg.askyesno(
                            "提示",
                            f"未填写 {info['display'] if info else provider} 凭据，将使用 HTTP-01 验证。\n"
                            "这要求服务器 80 端口对外开放，否则证书申请会失败。\n\n"
                            "继续？",
                        ):
                            return
                else:
                    # 证书下载模式：需要证书服务器地址
                    if not cert_server:
                        tkmessagebox.showerror(
                            "错误", "启用SSL时必须填写证书服务器地址"
                        )
                        return

            # 保存设置到配置对象
            old_ssl_enabled = self.config.ssl_enabled
            old_caddy_enabled = self.config.caddy_enabled

            self.config.ssl_enabled = ssl_enabled
            self.config.ssl_port = ssl_port
            self.config.cert_server_url = cert_server
            self.config.ssl_domain = ssl_domain
            self.config.caddy_enabled = caddy_enabled
            self.config.caddy_dns_provider = self.caddy_provider_var.get() or "alidns"
            # 按提供商保存凭据
            cred1 = self.cred1_var.get().strip()
            cred2 = self.cred2_var.get().strip()
            self.config.caddy_access_key_id = ""
            self.config.caddy_access_key_secret = ""
            self.config.caddy_tencent_secret_id = ""
            self.config.caddy_tencent_secret_key = ""
            self.config.caddy_cloudflare_api_token = ""
            if self.config.caddy_dns_provider == "alidns":
                self.config.caddy_access_key_id = cred1
                self.config.caddy_access_key_secret = cred2
            elif self.config.caddy_dns_provider == "tencentcloud":
                self.config.caddy_tencent_secret_id = cred1
                self.config.caddy_tencent_secret_key = cred2
            elif self.config.caddy_dns_provider == "cloudflare":
                self.config.caddy_cloudflare_api_token = cred1

            # 保存配置到文件
            self.config.save()

            # 更新证书状态显示
            self.update_certificate_status()

            # 调用回调函数更新主窗口状态
            if self.update_callback:
                try:
                    self.update_callback()
                except Exception as e:
                    print(f"更新主窗口状态时发生错误: {e}")

            # 显示保存成功消息
            if caddy_enabled:
                status_msg = f"SSL已启用（Caddy 自动HTTPS，端口 {ssl_port}）"
                restart_note = "Caddy 将自动申请并续期证书，请重启服务以生效"
            elif ssl_enabled:
                status_msg = "SSL已启用（证书下载模式）"
                restart_note = "请重启服务以应用HTTPS设置"
            else:
                status_msg = "SSL已禁用"
                restart_note = "请重启服务以停止HTTPS服务"
            tkmessagebox.showinfo("保存成功", f"SSL设置已保存！\n状态: {status_msg}")

            # 如果SSL状态发生变化，提示重启服务
            if old_ssl_enabled != ssl_enabled or old_caddy_enabled != caddy_enabled:
                tkmessagebox.showinfo("提示", restart_note)

        except ValueError:
            tkmessagebox.showerror("错误", "SSL端口必须是有效的数字")
        except Exception as e:
            tkmessagebox.showerror("错误", f"保存设置时发生错误: {str(e)}")
        finally:
            self.save_btn.configure(text="💾 保存设置", state="normal")
    
    def confirm(self):
        """确认设置（关闭对话框）"""
        # 检查是否有未保存的更改
        if self.has_unsaved_changes():
            result = tkmessagebox.askyesnocancel(
                "未保存的更改",
                "您有未保存的更改，是否要保存？\n\n是：保存并关闭\n否：不保存直接关闭\n取消：返回继续编辑"
            )
            if result is True:  # 是：保存并关闭
                self.save_settings()
                if self.has_unsaved_changes():  # 如果保存失败，不关闭对话框
                    return
            elif result is None:  # 取消：返回继续编辑
                return
            # 否：不保存直接关闭，继续执行下面的代码

        self.result = True
        self.destroy()

    def has_unsaved_changes(self):
        """检查是否有未保存的更改"""
        try:
            current_ssl_enabled = self.ssl_enabled_var.get()
            current_ssl_port = int(self.ssl_port_var.get().strip()) if self.ssl_port_var.get().strip() else self.config.ssl_port
            current_cert_server = self.cert_server_var.get().strip()
            current_ssl_domain = self.ssl_domain_var.get().strip()
            current_caddy_enabled = self.caddy_enabled_var.get()
            current_provider = self.caddy_provider_var.get()
            current_cred1 = self.cred1_var.get().strip()
            current_cred2 = self.cred2_var.get().strip()

            # 当前提供商对应的配置字段
            provider_creds = {
                "alidns": (self.config.caddy_access_key_id, self.config.caddy_access_key_secret),
                "tencentcloud": (self.config.caddy_tencent_secret_id, self.config.caddy_tencent_secret_key),
                "cloudflare": (self.config.caddy_cloudflare_api_token, ""),
            }.get(current_provider, ("", ""))

            return (current_ssl_enabled != self.config.ssl_enabled or
                    current_ssl_port != self.config.ssl_port or
                    current_cert_server != self.config.cert_server_url or
                    current_ssl_domain != self.config.ssl_domain or
                    current_caddy_enabled != self.config.caddy_enabled or
                    current_provider != self.config.caddy_dns_provider or
                    current_cred1 != provider_creds[0] or
                    current_cred2 != provider_creds[1])
        except:
            return True  # 如果检查失败，假设有更改
    
    def cancel(self):
        """取消设置"""
        self.result = False
        self.destroy()
