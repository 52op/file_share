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


class SSLSettingsDialog(ttk.Toplevel):
    def __init__(self, parent, config, ssl_manager, update_callback=None):
        super().__init__(parent)
        self.config = config
        self.ssl_manager = ssl_manager
        self.update_callback = update_callback  # 添加回调函数
        self.result = None
        
        self.withdraw()  # 先隐藏窗口
        self.title("SSL设置")
        self.geometry("500x400")
        
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
        """设置UI界面"""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=BOTH, expand=True, padx=20, pady=20)
        
        # SSL启用开关
        ssl_frame = ttk.Frame(main_frame)
        ssl_frame.pack(fill=X, pady=(0, 15))
        
        self.ssl_enabled_var = tk.BooleanVar(value=self.config.ssl_enabled)
        ssl_check = ttk.Checkbutton(
            ssl_frame, 
            text="启用SSL (HTTPS)", 
            variable=self.ssl_enabled_var,
            command=self.on_ssl_toggle
        )
        ssl_check.pack(side=LEFT)
        
        # SSL端口设置
        port_frame = ttk.Frame(main_frame)
        port_frame.pack(fill=X, pady=(0, 10))
        
        ttk.Label(port_frame, text="SSL端口:").pack(side=LEFT)
        self.ssl_port_var = tk.StringVar(value=str(self.config.ssl_port))
        port_entry = ttk.Entry(port_frame, textvariable=self.ssl_port_var, width=10)
        port_entry.pack(side=LEFT, padx=(10, 0))
        
        # 证书服务器地址
        server_frame = ttk.Frame(main_frame)
        server_frame.pack(fill=X, pady=(0, 10))
        
        ttk.Label(server_frame, text="证书服务器:").pack(anchor=W)
        self.cert_server_var = tk.StringVar(value=self.config.cert_server_url)
        server_entry = ttk.Entry(server_frame, textvariable=self.cert_server_var)
        server_entry.pack(fill=X, pady=(5, 0))
        
        # 绑定域名
        domain_frame = ttk.Frame(main_frame)
        domain_frame.pack(fill=X, pady=(0, 10))
        
        ttk.Label(domain_frame, text="绑定域名:").pack(anchor=W)
        self.ssl_domain_var = tk.StringVar(value=self.config.ssl_domain)
        domain_entry = ttk.Entry(domain_frame, textvariable=self.ssl_domain_var)
        domain_entry.pack(fill=X, pady=(5, 0))
        
        # 证书状态显示
        status_frame = ttk.LabelFrame(main_frame, text="证书状态", padding=10)
        status_frame.pack(fill=X, pady=(10, 0))
        
        self.status_label = ttk.Label(status_frame, text="检查中...")
        self.status_label.pack(anchor=W)
        
        self.expiry_label = ttk.Label(status_frame, text="")
        self.expiry_label.pack(anchor=W, pady=(5, 0))
        
        # 操作按钮
        action_frame = ttk.Frame(status_frame)
        action_frame.pack(fill=X, pady=(10, 0))
        
        self.test_btn = ttk.Button(
            action_frame, 
            text="测试连接", 
            command=self.test_connection,
            style="info.TButton"
        )
        self.test_btn.pack(side=LEFT, padx=(0, 10))
        
        self.update_btn = ttk.Button(
            action_frame, 
            text="更新证书", 
            command=self.update_certificate,
            style="warning.TButton"
        )
        self.update_btn.pack(side=LEFT, padx=(0, 10))
        
        self.preview_btn = ttk.Button(
            action_frame,
            text="预览URL",
            command=self.preview_url,
            style="secondary.TButton"
        )
        self.preview_btn.pack(side=LEFT, padx=(0, 10))

        # 添加保存按钮
        self.save_btn = ttk.Button(
            action_frame,
            text="💾 保存设置",
            command=self.save_settings,
            style="success.TButton"
        )
        self.save_btn.pack(side=LEFT)

        # 确定取消按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(side=BOTTOM, pady=(20, 0))

        ttk.Button(btn_frame, text="确定", command=self.confirm).pack(side=LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="取消", command=self.cancel).pack(side=LEFT)
        
        # 初始更新状态
        self.update_certificate_status()
        self.on_ssl_toggle()
    
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
    
    def on_ssl_toggle(self):
        """SSL开关切换事件"""
        enabled = self.ssl_enabled_var.get()
        
        # 根据SSL开关状态启用/禁用相关控件
        state = "normal" if enabled else "disabled"
        
        # 这里可以添加控件状态控制逻辑
        if enabled:
            self.update_certificate_status()
    
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
            if ssl_enabled:
                cert_server = self.cert_server_var.get().strip()
                ssl_domain = self.ssl_domain_var.get().strip()

                if not cert_server:
                    tkmessagebox.showerror("错误", "启用SSL时必须填写证书服务器地址")
                    return

                if not ssl_domain:
                    tkmessagebox.showerror("错误", "启用SSL时必须填写绑定域名")
                    return

            # 保存设置到配置对象
            old_ssl_enabled = self.config.ssl_enabled

            self.config.ssl_enabled = ssl_enabled
            self.config.ssl_port = ssl_port
            self.config.cert_server_url = self.cert_server_var.get().strip()
            self.config.ssl_domain = self.ssl_domain_var.get().strip()

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
            status_msg = "SSL已启用" if ssl_enabled else "SSL已禁用"
            tkmessagebox.showinfo("保存成功", f"SSL设置已保存！\n状态: {status_msg}")

            # 如果SSL状态发生变化，提示重启服务
            if old_ssl_enabled != ssl_enabled:
                if ssl_enabled:
                    tkmessagebox.showinfo("提示", "SSL已启用，请重启服务以应用HTTPS设置")
                else:
                    tkmessagebox.showinfo("提示", "SSL已禁用，请重启服务以停止HTTPS服务")

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

            return (current_ssl_enabled != self.config.ssl_enabled or
                    current_ssl_port != self.config.ssl_port or
                    current_cert_server != self.config.cert_server_url or
                    current_ssl_domain != self.config.ssl_domain)
        except:
            return True  # 如果检查失败，假设有更改
    
    def cancel(self):
        """取消设置"""
        self.result = False
        self.destroy()
