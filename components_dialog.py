# -*- coding: utf-8 -*-
"""可选组件管理对话框（GUI）。

参照 ssl_settings_dialog 的 Caddy 一键下载交互：列出组件与安装状态，
逐项下载（后台线程 + 进度条 + 断点续传 + 自动重试），下载到程序目录。
"""
import threading

import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox as tkmessagebox

from main import ToolTip
import component_manager as cm


class ComponentsDialog(tk.Toplevel):
    """可选组件管理：显示组件状态并支持一键下载。"""

    def __init__(self, parent, style):
        super().__init__(parent)
        self.title("📦 可选组件管理")
        self.resizable(False, False)
        self.transient(parent)
        try:
            from main import get_path

            self.iconbitmap(get_path('static/favicon.ico'))
        except Exception:
            pass

        self._rows = {}  # key -> {status, btn, bar, pct}
        self._busy = {}  # key -> bool

        main_frame = ttk.Frame(self, padding=12)
        main_frame.pack(fill=BOTH, expand=True)

        ttk.Label(
            main_frame,
            text="可选组件：部分功能依赖的大体积文件（如 IP 地理定位库）不随安装包分发。\n"
                 "在此一键下载到程序目录即可启用对应功能，也可手动下载放入。",
            foreground="#666666",
            wraplength=600,
            justify="left",
        ).pack(anchor=W, pady=(0, 10))

        for key in cm.COMPONENTS:
            self._build_row(main_frame, key)

        self._path_label = ttk.Label(main_frame, text="", foreground="#888888")
        self._path_label.pack(anchor=W, pady=(8, 0))
        try:
            from main import get_app_path

            self._path_label.configure(text=f"文件目录: {get_app_path()}")
        except Exception:
            pass

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=X, pady=(12, 0))
        ttk.Button(btn_frame, text="🔄 刷新状态", command=self.refresh_all).pack(side=LEFT)
        ttk.Button(btn_frame, text="关闭", style="secondary.TButton", command=self.destroy).pack(side=RIGHT)

        self.center_window(parent)
        self.refresh_all()

    def center_window(self, parent):
        self.update_idletasks()
        try:
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_x()
            py = parent.winfo_y()
            x = px + (pw - self.winfo_width()) // 2
            y = py + (ph - self.winfo_height()) // 2
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_row(self, parent, key):
        info = cm.COMPONENTS[key]
        frame = ttk.LabelFrame(parent, text=info["name"], padding=6)
        frame.pack(fill=X, pady=(0, 6))

        top = ttk.Frame(frame)
        top.pack(fill=X)
        status = ttk.Label(top, text="…", foreground="gray")
        status.pack(side=RIGHT)
        btn = ttk.Button(top, text="下载", width=10,
                         command=lambda k=key: self.start_download(k))
        btn.pack(side=RIGHT, padx=(8, 0))
        ToolTip(btn, "一键下载到程序目录（断点续传）")

        note = ttk.Label(frame, text=info["note"], foreground="#666666",
                         wraplength=560, justify="left")
        note.pack(anchor=W, fill=X, pady=(2, 0))

        # 下载地址（只读，可手动复制）
        url_var = tk.StringVar(value=info["url"])
        ttk.Entry(frame, textvariable=url_var, state="readonly").pack(fill=X, pady=(2, 0))

        prog_frame = ttk.Frame(frame)
        prog_frame.pack(fill=X, pady=(4, 0))
        bar = ttk.Progressbar(prog_frame, mode="determinate", maximum=100, value=0)
        pct = ttk.Label(prog_frame, text="", width=16, anchor=E)
        pct.pack(side=LEFT, padx=(6, 0))

        self._rows[key] = {"status": status, "btn": btn, "bar": bar, "pct": pct}

    def refresh_all(self):
        for item in cm.list_components():
            self._update_row(item)

    def _update_row(self, item):
        row = self._rows[item["key"]]
        s = row["status"]
        if item["status"] == "ok":
            s.configure(text=f"✓ 已安装 {item['size_mb']:.1f} MB", foreground="green")
            row["btn"].configure(state="normal", text="重新下载")
        elif item["status"] == "downloading":
            s.configure(text="⏳ 有未完成下载（可续传）", foreground="orange")
            row["btn"].configure(state="normal", text="继续下载")
        else:
            s.configure(text=f"✗ 未安装（约 {item['min_size_mb']:.0f} MB）", foreground="red")
            row["btn"].configure(state="normal", text="下载")

    def start_download(self, key):
        if self._busy.get(key):
            return
        self._busy[key] = True
        row = self._rows[key]
        row["btn"].configure(state="disabled", text="下载中...")
        row["bar"].pack(side=LEFT, fill=X, expand=True)
        row["bar"].configure(value=0)
        row["pct"].configure(text="0%")

        def progress(downloaded, total):
            self.after(0, lambda: self._on_progress(key, downloaded, total))

        def worker():
            ok, msg = cm.download_component(key, progress_cb=progress)
            self.after(0, lambda: self._on_done(key, ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_progress(self, key, downloaded, total):
        row = self._rows[key]
        if total > 0:
            pct = min(100, int(downloaded * 100 // total))
            row["bar"].configure(value=pct)
            row["pct"].configure(text=f"{downloaded / 1048576:.1f}/{total / 1048576:.1f} MB")
        else:
            row["pct"].configure(text=f"{downloaded / 1048576:.1f} MB")

    def _on_done(self, key, ok, msg):
        self._busy.pop(key, None)
        row = self._rows[key]
        row["btn"].configure(state="normal", text="重新下载")
        row["bar"].pack_forget()
        row["pct"].configure(text="")
        for item in cm.list_components():
            if item["key"] == key:
                self._update_row(item)
                break
        if ok:
            tkmessagebox.showinfo("下载完成", msg + "\n\n重启服务后生效。", parent=self)
        else:
            tkmessagebox.showerror("下载失败", msg, parent=self)