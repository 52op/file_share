from share_links import ShareManager
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from datetime import datetime
from .utils import format_datetime


class ShareListFrame(ttk.Frame):
    def __init__(self, parent, dialog):
        super().__init__(parent)
        self.dialog = dialog
        self.share_manager = ShareManager()

        # 创建树形控件
        columns = ("token", "name", "path", "size", "create_time", "expire_time", "has_password")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")

        # 设置列标题
        self.tree.heading("token", text="Token")
        self.tree.heading("name", text="名称")
        self.tree.heading("path", text="路径")
        self.tree.heading("size", text="大小")
        self.tree.heading("create_time", text="创建时间")
        self.tree.heading("expire_time", text="过期时间")
        self.tree.heading("has_password", text="是否加密")

        # 设置列宽
        self.tree.column("token", width=100)
        self.tree.column("name", width=150)
        self.tree.column("path", width=200)
        self.tree.column("size", width=80)
        self.tree.column("create_time", width=120)
        self.tree.column("expire_time", width=120)
        self.tree.column("has_password", width=80)

        # 添加滚动条
        scrollbar = ttk.Scrollbar(self, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        # 布局
        self.tree.pack(side=LEFT, fill=BOTH, expand=YES)
        scrollbar.pack(side=RIGHT, fill=Y)

        # 绑定右键菜单
        self.tree.bind("<Button-3>", self.show_context_menu)

        # 创建右键菜单
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="编辑", command=self.edit_share)
        self.context_menu.add_command(label="删除", command=self.delete_selected)

    def show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def load_shares(self):
        """加载分享列表"""
        self.tree.delete(*self.tree.get_children())

        for token, share in self.share_manager.share_links.items():
            self.tree.insert("", END, values=(
                token,
                share.name,
                share.path,
                share.size,
                format_datetime(share.create_time),
                format_datetime(share.expire_time) if share.expire_time else "永不过期",
                "是" if share.password else "否"
            ))

    def filter_shares(self, token, date_from, date_to):
        """筛选分享列表"""
        self.tree.delete(*self.tree.get_children())

        for share_token, share in self.share_manager.share_links.items():
            if token and token not in share_token:
                continue

            # 转换日期格式进行比较
            if date_from and share.create_time.date() < date_from.date():
                continue

            if date_to and share.create_time.date() > date_to.date():
                continue

            self.tree.insert("", END, values=(
                share_token,
                share.name,
                share.path,
                share.size,
                format_datetime(share.create_time),
                format_datetime(share.expire_time) if share.expire_time else "永不过期",
                "是" if share.password else "否"
            ))

    def delete_selected(self):
        """删除选中的分享"""
        selected = self.tree.selection()
        if not selected:
            return

        if tk.messagebox.askyesno("确认", f"确定要删除选中的 {len(selected)} 个分享吗？", parent=self):
            for item in selected:
                token = self.tree.item(item)['values'][0]
                if token in self.share_manager.share_links:
                    del self.share_manager.share_links[token]

            self.share_manager.save()
            self.load_shares()

    def edit_share(self):
        """编辑分享"""
        selected = self.tree.selection()
        if not selected:
            return

        item = selected[0]
        token = self.tree.item(item)['values'][0]
        share = self.share_manager.get_share(token)

        if share:
            from .share_edit_dialog import ShareEditDialog
            dialog = ShareEditDialog(self, share)
            self.wait_window(dialog)
            if dialog.result:
                self.share_manager.save()
                self.load_shares()
