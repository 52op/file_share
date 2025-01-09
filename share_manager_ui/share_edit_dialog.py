import threading
import tkinter as tk
import requests
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from datetime import datetime, timedelta
import socket
from main import config, get_path, flask_app


class ShareEditDialog(ttk.Toplevel):
    def __init__(self, parent, share):
        super().__init__(parent)
        self.share = share
        self.result = None

        self.title("编辑分享")
        self.geometry("400x520")
        icon_path = get_path('static/favicon.ico')
        self.iconbitmap(icon_path)

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=BOTH, expand=YES)

        # 基本信息
        ttk.Label(main_frame, text="Token:").pack(fill=X)
        self.token_var = tk.StringVar(value=share.token)
        ttk.Entry(main_frame, state="readonly", textvariable=self.token_var).pack(fill=X, pady=2)

        ttk.Label(main_frame, text="分享名称:").pack(fill=X)
        self.name_var = tk.StringVar(value=share.name)
        ttk.Entry(main_frame, textvariable=self.name_var).pack(fill=X, pady=2)

        ttk.Label(main_frame, text="访问密码:").pack(fill=X)
        self.password_var = tk.StringVar(value=share.password)
        ttk.Entry(main_frame, textvariable=self.password_var).pack(fill=X, pady=2)

        ttk.Label(main_frame, text="管理密码:").pack(fill=X)
        self.manage_code_var = tk.StringVar(value=share.manage_code)
        ttk.Entry(main_frame, textvariable=self.manage_code_var).pack(fill=X, pady=2)

        # 日期选择框架
        ttk.Label(main_frame, text="过期时间:").pack(fill=X)
        date_frame = ttk.Frame(main_frame)
        date_frame.pack(fill=X, pady=2)

        # 年月日选择
        current_year = datetime.now().year
        years = list(range(current_year, current_year + 10))
        months = list(range(1, 13))
        days = list(range(1, 32))

        self.year_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.day_var = tk.StringVar()

        # 设置初始值
        if share.expire_time:
            self.year_var.set(share.expire_time.year)
            self.month_var.set(share.expire_time.month)
            self.day_var.set(share.expire_time.day)

        ttk.Combobox(date_frame, textvariable=self.year_var, values=years, width=6).pack(side=LEFT, padx=2)
        ttk.Label(date_frame, text="年").pack(side=LEFT)

        ttk.Combobox(date_frame, textvariable=self.month_var, values=months, width=4).pack(side=LEFT, padx=2)
        ttk.Label(date_frame, text="月").pack(side=LEFT)

        ttk.Combobox(date_frame, textvariable=self.day_var, values=days, width=4).pack(side=LEFT, padx=2)
        ttk.Label(date_frame, text="日").pack(side=LEFT)

        ttk.Label(main_frame, text="描述:").pack(fill=X)
        self.desc_var = tk.StringVar(value=share.desc)
        ttk.Entry(main_frame, textvariable=self.desc_var).pack(fill=X, pady=2)

        # 在Token输入框后面添加完整链接显示
        ttk.Label(main_frame, text="分享链接:").pack(fill=X)

        link_frame = ttk.Frame(main_frame)
        link_frame.pack(fill=X, pady=2)

        # # 调试信息
        # print("Parent:", parent)
        # print("Parent type:", type(parent))
        # print("Parent children:", parent.winfo_children())
        #
        # # 获取主窗口
        # root = self.winfo_toplevel()
        # print("Root:", root)
        # print("Root children:", root.winfo_children())
        #
        # # 尝试获取主应用实例
        # try:
        #     main_app = root.winfo_children()[0]
        #     print("Main app:", main_app)
        #     print("Server running:", getattr(main_app, 'server_running', None))
        #     print("Port var:", getattr(main_app, 'port_var', None))
        # except Exception as e:
        #     print("Error getting main app:", e)
        #
        # # 构建链接
        # try:
        #     if hasattr(main_app, 'server_running') and main_app.server_running:
        #         ip = socket.gethostbyname(socket.gethostname())
        #         port = main_app.port_var.get() or 12345
        #         full_link = f"http://{ip}:{port}/s/{share.token}"
        #     else:
        #         full_link = f"服务未启动/s/{share.token}"
        # except Exception as e:
        #     print("Error building link:", e)
        #     full_link = f"链接生成错误/s/{share.token}"


        # 设置链接到界面
        ip = socket.gethostbyname(socket.gethostname())
        port = config.port
        full_link = f"http://{ip}:{port}/s/{share.token}"
        self.link_var = tk.StringVar(value=full_link)
        link_entry = ttk.Entry(link_frame, textvariable=self.link_var, state="readonly")
        link_entry.pack(side=LEFT, fill=X, expand=YES)

        # 添加复制按钮
        copy_btn = ttk.Button(
            link_frame,
            text="复制",
            command=lambda: self.copy_to_clipboard(full_link),
            style="info.TButton"
        )
        copy_btn.pack(side=LEFT, padx=5)

        # 点击链接也可以复制
        link_entry.bind('<Button-1>', lambda e: self.copy_to_clipboard(full_link))

        # 按钮区域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=X, pady=10)

        ttk.Button(
            btn_frame,
            text="保存",
            command=self.save,
            style="primary.TButton"
        ).pack(side=RIGHT, padx=5)

        ttk.Button(
            btn_frame,
            text="取消",
            command=self.cancel,
            style="secondary.TButton"
        ).pack(side=RIGHT)

    def copy_to_clipboard(self, text):
        """复制文本到剪贴板"""
        self.clipboard_clear()
        self.clipboard_append(text)

        # 显示复制成功提示
        label = ttk.Label(self, text="已复制到剪贴板!", style="success")
        label.pack(pady=5)
        self.after(1500, label.destroy)

    def clear_session_async(self, token):
        url = f"http://localhost:{config.port}/api/clear_session/{token}/{config.security_code}"
        threading.Thread(target=lambda: requests.get(url)).start()

    def save(self):
        # 检查密码是否被修改
        if self.share.password != self.password_var.get():
            # 发送清除session的请求
            self.clear_session_async(self.share.token)
        self.share.name = self.name_var.get()
        self.share.password = self.password_var.get()
        self.share.manage_code = self.manage_code_var.get()
        self.share.desc = self.desc_var.get()

        # 设置过期时间
        try:
            year = int(self.year_var.get())
            month = int(self.month_var.get())
            day = int(self.day_var.get())
            self.share.expire_time = datetime(year, month, day)
        except:
            self.share.expire_time = None

        self.result = True
        self.destroy()

    def cancel(self):
        self.destroy()
