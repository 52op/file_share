from share_links import ShareManager
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from datetime import datetime, timedelta
from .share_list import ShareListFrame
from main import get_path


class ShareManagerDialog(ttk.Toplevel):
    def __init__(self, parent, style):
        super().__init__(parent)
        self.share_manager = ShareManager()

        self.title("私有分享管理")
        self.geometry("900x600")
        icon_path = get_path('static/favicon.ico')
        self.iconbitmap(icon_path)

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=BOTH, expand=YES)

        # 搜索框架
        search_frame = ttk.LabelFrame(main_frame, text="搜索", padding="5")
        search_frame.pack(fill=X, pady=(0, 5))

        # Token搜索
        token_frame = ttk.Frame(search_frame)
        token_frame.pack(fill=X, pady=2)
        ttk.Label(token_frame, text="Token:").pack(side=LEFT)
        self.token_var = tk.StringVar()
        ttk.Entry(token_frame, textvariable=self.token_var).pack(side=LEFT, fill=X, expand=YES, padx=5)

        # 日期范围搜索
        date_frame = ttk.Frame(search_frame)
        date_frame.pack(fill=X, pady=2)
        ttk.Label(date_frame, text="日期范围:").pack(side=LEFT)

        # 计算年份范围
        current_year = datetime.now().year
        years = list(range(current_year - 10, current_year + 11))
        months = list(range(1, 13))
        days = list(range(1, 32))

        # 开始日期选择
        start_date_frame = ttk.Frame(date_frame)
        start_date_frame.pack(side=LEFT, padx=5)

        self.start_year_var = tk.StringVar()
        self.start_month_var = tk.StringVar()
        self.start_day_var = tk.StringVar()

        ttk.Combobox(start_date_frame, textvariable=self.start_year_var, values=years, width=6).pack(side=LEFT, padx=2)
        ttk.Label(start_date_frame, text="年").pack(side=LEFT)
        ttk.Combobox(start_date_frame, textvariable=self.start_month_var, values=months, width=4).pack(side=LEFT,
                                                                                                       padx=2)
        ttk.Label(start_date_frame, text="月").pack(side=LEFT)
        ttk.Combobox(start_date_frame, textvariable=self.start_day_var, values=days, width=4).pack(side=LEFT, padx=2)
        ttk.Label(start_date_frame, text="日").pack(side=LEFT)

        ttk.Label(date_frame, text="至").pack(side=LEFT, padx=5)

        # 结束日期选择
        end_date_frame = ttk.Frame(date_frame)
        end_date_frame.pack(side=LEFT, padx=5)

        self.end_year_var = tk.StringVar()
        self.end_month_var = tk.StringVar()
        self.end_day_var = tk.StringVar()

        ttk.Combobox(end_date_frame, textvariable=self.end_year_var, values=years, width=6).pack(side=LEFT, padx=2)
        ttk.Label(end_date_frame, text="年").pack(side=LEFT)
        ttk.Combobox(end_date_frame, textvariable=self.end_month_var, values=months, width=4).pack(side=LEFT, padx=2)
        ttk.Label(end_date_frame, text="月").pack(side=LEFT)
        ttk.Combobox(end_date_frame, textvariable=self.end_day_var, values=days, width=4).pack(side=LEFT, padx=2)
        ttk.Label(end_date_frame, text="日").pack(side=LEFT)

        # 搜索和刷新按钮
        btn_container = ttk.Frame(search_frame)
        btn_container.pack(side=RIGHT)

        ttk.Button(
            btn_container,
            text="刷新",
            command=self.refresh_shares,
            style="secondary.TButton"
        ).pack(side=RIGHT, padx=5)

        ttk.Button(
            btn_container,
            text="搜索",
            command=self.search_shares,
            style="primary.TButton"
        ).pack(side=RIGHT, padx=5)

        # 设置当前日期
        today = datetime.now()
        self.start_year_var.set(today.year)
        self.start_month_var.set(today.month)
        self.start_day_var.set(today.day)

        self.end_year_var.set(today.year)
        self.end_month_var.set(today.month)
        self.end_day_var.set(today.day)

        # 操作按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=X, pady=5)

        ttk.Button(
            btn_frame,
            text="删除过期分享",
            command=self.delete_expired,
            style="warning.TButton"
        ).pack(side=LEFT, padx=2)

        ttk.Button(
            btn_frame,
            text="清空所有分享",
            command=self.clear_all,
            style="danger.TButton"
        ).pack(side=LEFT, padx=2)

        # 分享列表
        self.share_list = ShareListFrame(main_frame, self)
        self.share_list.pack(fill=BOTH, expand=YES)

        self.refresh_shares()

    def search_shares(self):
        """搜索分享"""
        token = self.token_var.get().strip()

        # 获取开始日期
        date_from = None
        try:
            if self.start_year_var.get() and self.start_month_var.get() and self.start_day_var.get():
                date_from = datetime(
                    int(self.start_year_var.get()),
                    int(self.start_month_var.get()),
                    int(self.start_day_var.get())
                )
        except:
            pass

        # 获取结束日期
        date_to = None
        try:
            if self.end_year_var.get() and self.end_month_var.get() and self.end_day_var.get():
                date_to = datetime(
                    int(self.end_year_var.get()),
                    int(self.end_month_var.get()),
                    int(self.end_day_var.get())
                )
        except:
            pass

        # 如果只有开始日期,设置结束日期为开始日期的下一天
        if date_from and not date_to:
            date_to = date_from + timedelta(days=1)

        self.share_list.filter_shares(token, date_from, date_to)

    def refresh_shares(self):
        """刷新分享列表"""
        self.share_manager.load()
        self.share_list.load_shares()

    def delete_expired(self):
        """删除过期分享"""
        if tk.messagebox.askyesno("确认", "确定要删除所有过期分享吗？", parent=self):
            self.share_manager.remove_expired()
            self.refresh_shares()

    def clear_all(self):
        """清空所有分享"""
        if tk.messagebox.askyesno("确认", "确定要清空所有分享吗？", parent=self):
            self.share_manager.share_links.clear()
            self.share_manager.save()
            self.refresh_shares()
