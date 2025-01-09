import ctypes
import json
import logging
import os
import socket
import sys
import tkinter as tk
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import TimedRotatingFileHandler
from tkinter import filedialog
from tkinter import messagebox as tkmessagebox

import netifaces
import pystray
import servicemanager
import ttkbootstrap as ttk
import win32event
import win32service
import win32serviceutil
from PIL import ImageTk
from flask import Flask
from tkinterdnd2 import *
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from user_agents import parse
from werkzeug.serving import make_server


def get_app_path(tempdir=False):
    """获取应用程序路径"""
    if getattr(sys, 'frozen', False):
        if tempdir:
            return sys._MEIPASS

        return os.path.dirname(os.path.abspath(sys.executable))

    else:

        return os.path.dirname(os.path.abspath(__file__))


def setup_service_logger():
    """设置服务日志"""
    from datetime import datetime

    log_dir = os.path.join(get_app_path(), 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    current_date = datetime.now().strftime('%Y%m%d')
    log_file = os.path.join(log_dir, f'service_{current_date}.log')

    root_logger = logging.getLogger()
    root_logger.handlers = []

    handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        encoding='utf-8',
        backupCount=7
    )

    def namer(default_name):
        base_name = default_name.split('.')[0]
        return base_name

    handler.namer = namer

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    flask_app.logger.handlers = []

    flask_app.logger.propagate = False

    flask_app.logger.addHandler(handler)

    return root_logger


flask_app = Flask(__name__)
logger = setup_service_logger()

flask_app.secret_key = os.urandom(24)

serverUrl = ""
runningPort = 12345

cleanup_thread_running = False

password_change_timestamps = {
    'global': 0,
    'admin': 0,
    'directories': {},
    'shares': {}
}


def get_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.normpath(os.path.join(base_path, relative_path))


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def get_global_ipv6():
    try:

        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET6 in addrs:
                for addr in addrs[netifaces.AF_INET6]:
                    addr_ip = addr['addr'].split('%')[0]

                    if (addr_ip.startswith('2001:') or addr_ip.startswith('240')) and not addr.get('temporary', False):
                        return addr_ip
        return None
    except Exception:
        return '::1'


def validate_alias(P):
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', P))


def secure_filename_cn(filename):
    filename = filename.replace('/', '').replace('\\', '')

    filename = re.sub(r'[<>:"|?*]', '', filename)

    if filename.startswith('.'):
        filename = '_' + filename
    return filename.strip()


def get_client_info():
    user_agent_string = request.headers.get('User-Agent')
    user_agent = parse(user_agent_string)
    ip = request.remote_addr

    os_info = f"{user_agent.os.family} {user_agent.os.version_string}".strip()
    browser_info = f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()

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
            self.tooltip, text=self.text, background="#FFFFE0", relief=tk.SOLID, borderwidth=1,
            font=("宋体", 8, "normal"), foreground="green"
        )
        label.pack(ipadx=3, ipady=3)

    def hide_tooltip(self, event):
        if self.tooltip:
            self.tooltip.destroy()
        self.tooltip = None


class ShareDirectory:
    def __init__(self, path, alias="", password="", desc=""):
        self.path = path
        self.alias = alias
        self.password = password
        self.desc = desc

        if path.endswith(':\\'):
            self.name = f"drive_{path[0].lower()}"
        else:
            self.name = os.path.basename(path)

    def to_dict(self):
        return {
            "path": self.path,
            "alias": self.alias,
            "password": self.password,
            "name": self.name,
            "desc": self.desc
        }

    @staticmethod
    def from_dict(data):
        dir_obj = ShareDirectory(
            data["path"],
            data.get("alias", ""),
            data["password"],
            data.get("desc", "")
        )
        dir_obj.name = data.get("name", dir_obj.name)
        return dir_obj


class RedirectHandler(logging.Handler):
    def __init__(self, text_widget):
        logging.Handler.__init__(self)
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)

        def append():
            self.text_widget.insert('end', msg + '\n')
            self.text_widget.see('end')

        self.text_widget.after(0, append)


class Config:
    def __init__(self):
        self.shared_dirs = {}
        self.global_password = ""
        self.admin_password = "admin"
        self.port = 12345
        self.dark_theme = False
        self.log_to_file = False
        self.config_file = "share_config.json"
        self.use_waitress = True
        self.upload_temp_dir = "temp/upload/"
        self.security_code = "12356789"
        self.cleanup_time = 3600
        self.auto_cleanup = True

        os.makedirs(self.upload_temp_dir, exist_ok=True)

    def save(self):
        config_data = {
            "shared_dirs": {
                name: {
                    "path": dir_obj.path,
                    "alias": dir_obj.alias,
                    "password": dir_obj.password,
                    "name": dir_obj.name,
                    "desc": getattr(dir_obj, 'desc', '')
                }
                for name, dir_obj in self.shared_dirs.items()
            },
            "global_password": self.global_password,
            "admin_password": self.admin_password if self.admin_password else config.admin_password,
            "port": self.port,
            "dark_theme": self.dark_theme,
            "log_to_file": self.log_to_file,
            "use_waitress": self.use_waitress
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.shared_dirs = {}
                for name, dir_data in data.get("shared_dirs", {}).items():

                    if "desc" not in dir_data:
                        dir_data["desc"] = ""
                    self.shared_dirs[name] = ShareDirectory.from_dict(dir_data)
                self.global_password = data.get("global_password", "")
                self.admin_password = data.get("admin_password", "admin")
                self.port = data.get("port", 12345)
                self.dark_theme = data.get("dark_theme", False)
                self.log_to_file = data.get("log_to_file", False)
                self.use_waitress = data.get("use_waitress", False)


config = Config()


def format_file_size(size_in_bytes):
    if size_in_bytes >= 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    elif size_in_bytes >= 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    else:
        return f"{size_in_bytes} B"


def partial_download(path, start, end):
    with open(path, 'rb') as f:
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
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            yield chunk


from routes import *


class DirectoryDialog(ttk.Toplevel):
    def __init__(self, parent, dir_obj=None):
        super().__init__(parent)
        self.withdraw()
        self.title("目录设置")
        self.geometry("400x250")
        icon_path = get_path('static/favicon.ico')
        self.iconbitmap(icon_path)

        self.result = None
        self.dir_obj = dir_obj

        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill=X, padx=10, pady=5)
        self.path_var = tk.StringVar(value=dir_obj.path if dir_obj else "")
        self.path_entry = ttk.Entry(dir_frame, textvariable=self.path_var)
        self.path_entry.pack(side=LEFT, fill=X, expand=YES)
        ttk.Button(dir_frame, text="浏览", command=self.browse_dir).pack(side=RIGHT)

        alias_frame = ttk.Frame(self)
        alias_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(alias_frame, text="显示名称:").pack(side=LEFT)
        self.alias_var = tk.StringVar(value=dir_obj.alias if dir_obj else "")
        self.alias_entry = ttk.Entry(alias_frame, textvariable=self.alias_var)
        self.alias_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(self.alias_entry, "也就是目录的别名，在WEB页面显示的目录名称\n设跟真实文件夹不一样的名称有助于安全"
                                  "\n 只支持英文与数字组合")

        pwd_frame = ttk.Frame(self)
        pwd_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(pwd_frame, text="访问密码:").pack(side=LEFT)
        self.password_var = tk.StringVar(value=dir_obj.password if dir_obj else "")
        self.pwd_entry = ttk.Entry(pwd_frame, textvariable=self.password_var, show="*")
        self.pwd_entry.pack(side=LEFT, fill=X, expand=YES)

        self.show_pwd_btn = ttk.Button(
            self.pwd_entry,
            text="○/●️",
            width=3,
            command=lambda: self.toggle_password_visibility(self.pwd_entry)
        )
        self.show_pwd_btn.pack(side=RIGHT)

        desc_frame = ttk.Frame(self)
        desc_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(desc_frame, text="描述:").pack(side=LEFT)
        self.desc_var = tk.StringVar(value=dir_obj.desc if dir_obj else "")
        self.desc_entry = ttk.Entry(desc_frame, textvariable=self.desc_var)
        self.desc_entry.pack(side=LEFT, fill=X, expand=YES)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(side=BOTTOM, pady=10)
        ttk.Button(btn_frame, text="确定", command=self.confirm).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.cancel).pack(side=LEFT)

        self.geometry("400x250")
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

        self.deiconify()

        try:
            self.path_entry.drop_target_register(DND_FILES)
            self.path_entry.dnd_bind('<<Drop>>', self.handle_drop)
        except:
            print("DND support not available for this entry")

        self.transient(parent)
        self.grab_set()

        vcmd = (self.register(validate_alias), '%P')
        self.alias_entry = ttk.Entry(
            alias_frame,
            textvariable=self.alias_var,
            validate='key',
            validatecommand=vcmd
        )

    def toggle_password_visibility(self, entry):
        if entry.cget('show') == '*':
            entry.configure(show='')
            self.show_pwd_btn.configure(style='warning.TButton')
        else:
            entry.configure(show='*')
            self.show_pwd_btn.configure(style='TButton')

    def browse_dir(self):
        path = filedialog.askdirectory()
        if path:

            normalized_path = os.path.normpath(path).replace('/', '\\')

            if normalized_path.endswith('\\') and len(normalized_path) == 3 and normalized_path[1:] == ':\\':

                default_alias = f"disk_{normalized_path[0].upper()}"
            else:

                default_alias = os.path.basename(normalized_path)

                if not default_alias:
                    drive_letter = normalized_path[0].upper()
                    default_alias = f"disk_{drive_letter}"

            self.alias_var.set(default_alias)
            self.path_var.set(normalized_path)

    def confirm(self):
        path = self.path_var.get()
        alias = self.alias_var.get()
        if not path or not alias:
            tkmessagebox.showerror("错误", "路径和显示名称都必须填写")
            return

        if not validate_alias(alias):
            tkmessagebox.showerror("错误", "显示名称只能包含字母、数字、下划线和连字符")
            return

        if path.endswith(':\\'):
            drive_letter = path[0].lower()
            dir_name = f"drive_{drive_letter}"
        else:
            dir_name = os.path.basename(path)

        if self.dir_obj and self.dir_obj.alias != alias:
            password_change_timestamps['directories'][alias] = time.time()

        self.result = ShareDirectory(
            self.path_var.get(),
            self.alias_var.get(),
            self.password_var.get(),
            self.desc_var.get()
        )
        self.result.name = dir_name
        self.destroy()

    def cancel(self):
        self.destroy()


class FileShareService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FileShareService"
    _svc_display_name_ = "FS文件分享服务"
    _svc_description_ = "提供文件共享Web服务"

    def __init__(self, args):
        global cleanup_thread_running
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        self.logger = logging.getLogger()
        self.cleanup_thread_running = cleanup_thread_running

        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

        os.environ['PYTHONPATH'] = os.path.dirname(os.path.abspath(__file__))

    def SvcDoRun(self):
        try:

            max_retries = 10
            retry_count = 0
            while retry_count < max_retries:
                try:
                    config.load()
                    break
                except Exception as e:
                    retry_count += 1
                    self.logger.warning(f"配置加载失败,重试 {retry_count}/{max_retries}: {e}")
                    time.sleep(1)

            if retry_count >= max_retries:
                raise Exception("无法加载配置文件")

            def run_server():
                from waitress import serve

                waitress_logger = logging.getLogger('waitress')
                waitress_logger.setLevel(logging.ERROR)
                waitress_logger.propagate = False

                log_dir = os.path.join(get_app_path(), 'logs')
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir)

                log_file = os.path.join(log_dir, f'waitress.log')
                file_handler = logging.FileHandler(log_file)
                file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
                waitress_logger.addHandler(file_handler)

                serve(flask_app,
                      listen=['*:{}'.format(config.port),
                              '[::]:{}'.format(config.port)], _quiet=True)

            self.server_thread = threading.Thread(target=run_server, daemon=True)
            self.server_thread.start()

            if config.auto_cleanup and not self.cleanup_thread_running:
                self.cleanup_thread_running = True
                start_cleanup_thread()

            self.ReportServiceStatus(win32service.SERVICE_RUNNING)

            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        except Exception as e:
            self.logger.error(f"服务错误: {str(e)}")
            self.logger.error(traceback.format_exc())
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if cleanup_thread_running:
            stop_cleanup_thread()


class FileShareApp:
    def handle_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        if files:
            path = files[0]
            normalized_path = os.path.normpath(path.strip('"'))

            if os.path.exists(normalized_path) and os.access(normalized_path, os.R_OK):
                if os.path.isdir(normalized_path):

                    if normalized_path.endswith(':\\'):

                        drive_letter = normalized_path[0].lower()
                        dir_name = f"drive_{drive_letter}"
                    else:
                        dir_name = os.path.basename(normalized_path)

                    dialog = DirectoryDialog(self.root)
                    dialog.path_var.set(normalized_path)

                    default_alias = f"disk_{drive_letter.upper()}" if normalized_path.endswith(':\\') else dir_name
                    dialog.alias_var.set(default_alias)

                    self.root.wait_window(dialog)
                    if dialog.result:
                        config.shared_dirs[dir_name] = dialog.result
                        self.refresh_dir_list()
                        self.save_config()
                        self.log_area.insert(END, f"已添加共享目录: {normalized_path}\n")
                        self.log_area.see(END)
                else:
                    self.log_area.insert(END, "只能添加文件夹!\n")
                    self.log_area.see(END)

    def __init__(self, root, style):
        self.root = root
        self.style = style

        self.service_debounce_timer = None
        self.service_monitor_timer = None

        self.service_status = None

        self.root.title("文件分享服务器")
        self.root.geometry("800x600")

        self.init_variables()

        self.create_gui()

        self.setup_config()

        self.setup_dnd()

        self.draw_window()
        self.setup_traces()

        self.start_service_monitor()

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
        self.about_window = None

    def create_gui(self):

        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=BOTH, expand=YES)

        self.create_theme_section()
        self.create_directory_section()
        self.create_settings_section()
        self.create_buttons_section()
        self.create_log_area()

    def setup_config(self):
        self.load_config()
        self.minimize_to_tray()

        if config.dark_theme:
            self.style.theme_use("darkly")
            self.theme_switch.state(['selected'])
        else:
            self.style.theme_use("cosmo")
            self.theme_switch.state(['!selected'])

        self.log_enabled.set(config.log_to_file)
        if config.log_to_file:
            self.file_handler = self.setup_file_logging()

        self.server_type.set(config.use_waitress)
        if config.use_waitress:
            self.server_switch.state(['selected'])
            self.waitress_label.configure(style="success.TLabel")
        else:
            self.server_switch.state(['!selected'])
            self.werkzeug_label.configure(style="success.TLabel")

    def setup_dnd(self):
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.handle_drop)
            print("Main window DND registered successfully")
        except Exception as e:
            print("DND registration error:", e)

    def draw_window(self):
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

    def create_theme_section(self):

        theme_frame = ttk.Frame(self.main_frame)
        theme_frame.pack(fill=X, pady=2)

        self.theme_switch = ttk.Checkbutton(
            theme_frame,
            bootstyle="round-toggle",
            text="",
            command=self.toggle_theme,
            padding=2
        )

        self.light_icon = ttk.Label(
            theme_frame,
            text="☀",
            font=("Segoe UI", 10)
        )
        self.dark_icon = ttk.Label(
            theme_frame,
            text="☾",
            font=("Segoe UI", 10)
        )

        self.dark_icon.pack(side=RIGHT, padx=(0, 2))
        self.theme_switch.pack(side=RIGHT, padx=2)
        self.light_icon.pack(side=RIGHT, padx=(2, 0))

    def create_directory_section(self):

        dir_frame = ttk.LabelFrame(self.main_frame, text="共享目录", padding="5")
        dir_frame.pack(fill=X, pady=5)

        btn_frame = ttk.Frame(dir_frame)
        btn_frame.pack(fill=X)

        ttk.Button(
            btn_frame,
            text="添加目录",
            command=self.add_directory,
            style="primary.TButton"
        ).pack(side=LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="修改目录",
            command=lambda: self.edit_directory(None),
            style="info.TButton"
        ).pack(side=LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="删除目录",
            command=self.remove_directory,
            style="danger.TButton"
        ).pack(side=LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="私有分享",
            command=self.open_share_manager,
            style="info.TButton"
        ).pack(side=LEFT, padx=5)

        list_frame = ttk.Frame(dir_frame)
        list_frame.pack(fill=X, pady=5)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.dir_list = ttk.Treeview(
            list_frame,
            columns=("alias", "path", "has_password"),
            show="headings",
            height=6,
            yscrollcommand=scrollbar.set
        )

        scrollbar.config(command=self.dir_list.yview)

        self.dir_list.heading("alias", text="显示名称")
        self.dir_list.heading("path", text="路径")
        self.dir_list.heading("has_password", text="密码保护")

        self.dir_list.column("alias", width=150)
        self.dir_list.column("path", width=300)
        self.dir_list.column("has_password", width=100)

        self.dir_list.pack(fill=X, expand=YES)

        self.dir_list.bind("<Double-1>", self.edit_directory)
        self.dir_list.bind("<Delete>", self.remove_directory)
        self.dir_list.bind("<Button-3>", self.show_context_menu)

    def open_share_manager(self):
        from share_manager_ui.share_dialog import ShareManagerDialog
        dialog = ShareManagerDialog(self.root, self.style)
        self.root.wait_window(dialog)

    def create_settings_section(self):

        settings_frame = ttk.LabelFrame(self.main_frame, text="全局设置", padding="5")
        settings_frame.pack(fill=X, pady=5)

        settings_container = ttk.Frame(settings_frame)
        settings_container.pack(fill=X, pady=5)

        admin_pwd_frame = ttk.Frame(settings_container)
        admin_pwd_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        ttk.Label(admin_pwd_frame, text="管理员密码:").pack(side=LEFT)

        admin_pwd_entry_container = ttk.Frame(admin_pwd_frame)
        admin_pwd_entry_container.pack(side=LEFT, fill=X, expand=YES)

        admin_pwd_entry = ttk.Entry(
            admin_pwd_entry_container,
            textvariable=self.admin_password_var,
            show="*",
            width=15
        )
        admin_pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(admin_pwd_entry, "管理密码，一码通用，WEB页提示输入密码的地方用它都行")

        admin_pwd_btn = ttk.Button(
            admin_pwd_entry_container,
            text="○",
            width=1,
            command=lambda: self.toggle_password_visibility(admin_pwd_entry, admin_pwd_btn)
        )
        admin_pwd_btn.pack(side=LEFT)
        ToolTip(admin_pwd_btn, "显隐密码")

        pwd_frame = ttk.Frame(settings_container)
        pwd_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        ttk.Label(pwd_frame, text="全局访问密码:").pack(side=LEFT)

        pwd_entry_container = ttk.Frame(pwd_frame)
        pwd_entry_container.pack(side=LEFT, fill=X, expand=YES)

        pwd_entry = ttk.Entry(
            pwd_entry_container,
            textvariable=self.password_var,
            show="*",
            width=15
        )
        pwd_entry.pack(side=LEFT, fill=X, expand=YES)
        ToolTip(pwd_entry, "全局密码也就是进入WEB页面首页用的密码")

        pwd_btn = ttk.Button(
            pwd_entry_container,
            text="○",
            width=1,
            command=lambda: self.toggle_password_visibility(pwd_entry, pwd_btn)
        )
        pwd_btn.pack(side=LEFT)
        ToolTip(pwd_btn, "显隐密码")

        port_frame = ttk.Frame(settings_container)
        port_frame.pack(side=LEFT, padx=5, fill=X, expand=YES)
        ttk.Label(port_frame, text="端口号:").pack(side=LEFT)
        ttk.Entry(port_frame, textvariable=self.port_var).pack(side=LEFT, fill=X, expand=YES)
        ToolTip(port_frame, f"HTTP服务监听端口号也就是用户WEB访问端口号\n\n "
                            f"如：http://{get_local_ip()}:{self.port_var.get()}")

        log_switch_frame = ttk.Frame(settings_frame)
        log_switch_frame.pack(fill=X, pady=5)

        ttk.Label(log_switch_frame, text="清理间隔(秒):").pack(side=tk.LEFT)
        ttk.Spinbox(log_switch_frame, from_=10, to=86400, textvariable=self.cleanup_time_var, width=3).pack(
            side=tk.LEFT)

        self.auto_cleanup_checkbox = ttk.Checkbutton(
            log_switch_frame,
            text="自动清理",
            variable=self.auto_cleanup_var,
            style="squared-toggle"
        )
        self.auto_cleanup_checkbox.pack(side=tk.LEFT, padx=5)

        ToolTip(self.auto_cleanup_checkbox, "启用此选项将自动清理用户打包下载产生临时文件和过期的共享链接。")

        self.save_btn = ttk.Button(
            log_switch_frame,
            text="保存(实时)",
            command=self.save_config,
            style="outline.TButton"
        )
        self.save_btn.pack(side=RIGHT, padx=(0, 15))
        ToolTip(self.save_btn, "虽然它能保存所有配置，\n但其实这里主要用于管理密码与全局密码的一个实时生效")

        self.log_switch = ttk.Checkbutton(
            log_switch_frame,
            text="开启记录日志",
            variable=self.log_enabled,
            command=self.toggle_file_logging,
            style="squared-toggle"
        )
        self.log_switch.pack(side=RIGHT, padx=(0, 15))
        ToolTip(self.log_switch, "启用此选项将自动将下面回显框日志记录到程序logs下面。")

        self.waitress_label = ttk.Label(log_switch_frame, text="Waitress")
        self.waitress_label.pack(side=RIGHT, padx=(0, 15))

        self.server_switch = ttk.Checkbutton(
            log_switch_frame,
            text="",
            variable=self.server_type,
            command=self.toggle_server_type,
            style="squared-toggle"
        )
        self.server_switch.pack(side=RIGHT, padx=(0, 2))
        ToolTip(self.server_switch, "切换werkzeug开发调试用单线程服务器\n或waitress生产环境适应多线程服务器")

        self.werkzeug_label = ttk.Label(log_switch_frame, text="werkzeug")
        self.werkzeug_label.pack(side=RIGHT, padx=(0, 2))

        self.server_mode_label = ttk.Label(log_switch_frame)

    def toggle_password_visibility(self, entry, btn):
        if entry.cget('show') == '*':
            entry.configure(show='')
            btn.configure(text="●")
        else:
            entry.configure(show='*')
            btn.configure(text="○")

    def create_buttons_section(self):

        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack()

        self.service_var = tk.BooleanVar(value=self.is_service_installed())
        self.service_checkbox = ttk.Checkbutton(
            btn_frame,
            text="安装为系统服务",
            variable=self.service_var,
            command=self.handle_service_toggle,
            style="squared-toggle"
        )
        self.service_checkbox.pack(side=LEFT, pady=10, padx=(0, 10))
        ToolTip(self.service_checkbox, "将程序安装成WINDOWS系统服务，实现开机运行，记得先调试好配置")

        self.start_btn = ttk.Button(
            btn_frame,
            text="启动服务" if not self.service_var.get() else "启动后台服务",
            command=self.toggle_server,
            style="success.TButton"
        )
        self.start_btn.pack(side=LEFT, pady=10)

        self.page_btn = ttk.Button(
            btn_frame,
            text="打开页面",
            command=self.open_page,
            style="info.TButton"
        )
        self.page_btn.pack(side=LEFT, pady=10)
        self.page_btn.pack_forget()

    def create_log_area(self):

        self.log_area = ScrolledText(
            self.main_frame,
            padding=5,
            height=20,
            width=80,
            wrap=tk.WORD,
            font=('Consolas', 10)
        )
        self.log_area.pack(fill=BOTH, expand=YES, pady=5)

        handler = RedirectHandler(self.log_area)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        flask_app.logger.addHandler(handler)
        flask_app.logger.setLevel(logging.INFO)

    def switch_server_type_ui(self, is_running):
        if is_running:

            self.waitress_label.pack_forget()
            self.server_switch.pack_forget()
            self.werkzeug_label.pack_forget()

            if self.server_type.get():
                self.server_mode_label.configure(
                    text="服务模式：Waitress(生产)",
                    bootstyle="success"
                )
            else:
                self.server_mode_label.configure(
                    text="服务模式：werkzeug(调试)",
                    bootstyle="warning"
                )
            self.server_mode_label.pack(side=RIGHT, padx=(0, 15))


        else:

            self.server_mode_label.pack_forget()

            self.waitress_label.pack(side=RIGHT, padx=(0, 15))
            self.server_switch.pack(side=RIGHT, padx=(0, 2))
            self.werkzeug_label.pack(side=RIGHT, padx=(0, 2))

    def setup_file_logging(self):
        if not os.path.exists('logs'):
            os.makedirs('logs')
        log_file = f'logs/fileshare_{datetime.now().strftime("%Y%m%d")}.log'
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        flask_app.logger.addHandler(file_handler)
        return file_handler

    def toggle_file_logging(self):
        config.log_to_file = self.log_enabled.get()
        if config.log_to_file:
            self.file_handler = self.setup_file_logging()
        else:
            if hasattr(self, 'file_handler'):
                flask_app.logger.removeHandler(self.file_handler)
        config.save()

    def setup_traces(self):

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

        if not serverUrl:
            ip = get_local_ip()
            webbrowser.open(f"http://{ip}:{config.port}")
        webbrowser.open(serverUrl)

    def create_tray_icon(self):
        icon_image = Image.open(get_path('static/favicon.ico'))
        menu = (

            pystray.MenuItem('显示', lambda: self.root.after(0, self.show_window), default=True),

            pystray.MenuItem('关于', self.about_app),
            pystray.MenuItem('退出', self.quit_app)
        )
        self.tray_icon = pystray.Icon('file_share', icon_image, '文件分享服务器(letvar@qq.com)', menu)

    def show_window(self, icon=None):

        if not self.root.winfo_ismapped():
            self.root.deiconify()

        self.root.state('normal')
        self.root.lift()
        self.root.focus_force()

    def about_app(self):

        if self.about_window and self.about_window.winfo_exists():
            self.about_window.deiconify()
            self.about_window.lift()
            return

        self.about_window = ttk.Toplevel(self.root)
        self.about_window.transient(self.root)
        self.about_window.title("关于")
        self.about_window.iconbitmap(get_path('static/favicon.ico'))
        self.about_window.resizable(False, False)

        window_width = 400
        window_height = 380
        x = self.root.winfo_x() + (self.root.winfo_width() - window_width) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - window_height) // 2
        self.about_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        content_frame = ttk.Frame(self.about_window)
        content_frame.pack(expand=True, fill='both', padx=20, pady=20)

        try:
            image_path = get_path("static/zs.png")
            img = Image.open(image_path)
            img = img.resize((340, 178), Image.Resampling.LANCZOS)
            img_tk = ImageTk.PhotoImage(img)
            image_label = ttk.Label(content_frame, image=img_tk)
            image_label.image = img_tk
            image_label.pack(pady=(0, 5))
        except Exception as e:
            flask_app.logger.error(f"加载图片失败: {e}", color='red')

        info_text = """
    如果喜欢，欢迎打赏支持，万分感谢！

    file_share HTTP文件分享服务器
    本工具基于python flask waitress，
    支持前台窗口服务方式和WINDOWS后台服务方式
    支持IPv4和IPv6地址访问。
    窗口一些组件鼠标放上去会会弹出说明
    反馈: letvar@qq.com（秒回）

    """
        ttk.Label(content_frame, text=info_text, justify='left').pack()

    def quit_app(self, icon=None):
        if self.server_running and self.service_status != 4:
            tkmessagebox.showwarning("提示", "请先手动停止服务后再退出")
            self.show_window()
            return

        if icon:
            icon.stop()
        self.stop_service_monitor()
        self.root.after(0, self.root.quit)

    def minimize_to_tray(self):
        if not hasattr(self, 'tray_icon') or not self.tray_icon.visible:
            self.create_tray_icon()
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def on_closing(self):
        self.root.withdraw()

    def toggle_theme(self):
        current_theme = self.style.theme.name
        if current_theme == "cosmo":
            self.style.theme_use("darkly")
            config.dark_theme = True
        else:
            self.style.theme_use("cosmo")
            config.dark_theme = False
        config.save()

    def show_context_menu(self, event):
        selection = self.dir_list.selection()
        if selection:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="修改", command=lambda: self.edit_directory(None))
            menu.add_command(label="删除", command=self.remove_directory)
            menu.post(event.x_root, event.y_root)

    def load_config(self):
        config.load()
        self.refresh_dir_list()
        self.password_var.set(config.global_password)
        self.admin_password_var.set(config.admin_password)
        self.port_var.set(str(config.port))
        self.cleanup_time_var.set(config.cleanup_time)
        self.auto_cleanup_var.set(config.auto_cleanup)

    def save_config(self):

        if config.global_password != self.password_var.get():
            password_change_timestamps['global'] = time.time()

        new_admin_password = self.admin_password_var.get()
        if new_admin_password and config.admin_password != new_admin_password:
            password_change_timestamps['admin'] = time.time()
            config.admin_password = new_admin_password

        config.global_password = self.password_var.get()

        config.port = int(self.port_var.get() or 12345)
        config.cleanup_time = self.cleanup_time_var.get()
        config.auto_cleanup = self.auto_cleanup_var.get()
        config.save()

        self.load_config()
        flask_app.logger.info(f"配置已保存并实时生效")

    def refresh_dir_list(self):
        for item in self.dir_list.get_children():
            self.dir_list.delete(item)

        self.dir_list.heading("alias", text="显示名称")
        self.dir_list.heading("path", text="路径")
        self.dir_list.heading("has_password", text="密码保护")

        self.dir_list.column("alias", width=150)
        self.dir_list.column("path", width=300)
        self.dir_list.column("has_password", width=100)

        for dir_obj in config.shared_dirs.values():
            self.dir_list.insert("", "end", values=(
                dir_obj.alias,
                dir_obj.path,
                "是" if dir_obj.password else "否"
            ))

    def add_directory(self):
        dialog = DirectoryDialog(self.root)
        self.root.wait_window(dialog)

        if dialog.result:
            path = dialog.result.path

            if path.endswith(':\\'):
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
        alias = item['values'][0]
        path = item['values'][1]

        if path.endswith(':\\'):
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
        alias = item['values'][0]
        path = item['values'][1]

        if path.endswith(':\\'):
            dir_name = f"drive_{path[0].lower()}"
        else:
            dir_name = os.path.basename(path)

        if dir_name in config.shared_dirs:
            old_password = config.shared_dirs[dir_name].password
            dialog = DirectoryDialog(self.root, config.shared_dirs[dir_name])
            self.root.wait_window(dialog)
            if dialog.result:

                if old_password != dialog.result.password:
                    password_change_timestamps['directories'][dialog.result.alias] = time.time()

                new_dir_name = f"drive_{dialog.result.path[0].lower()}" if dialog.result.path.endswith(
                    ':\\') else os.path.basename(dialog.result.path)

                del config.shared_dirs[dir_name]
                config.shared_dirs[new_dir_name] = dialog.result
                self.save_config()
                self.refresh_dir_list()

    def handle_service_toggle(self):
        if self.service_debounce_timer:
            self.root.after_cancel(self.service_debounce_timer)
        self.service_debounce_timer = self.root.after(500, self.do_service_toggle)

    def start_service_monitor(self):
        """Start monitoring service status"""

        def check_service_status():
            try:
                import win32serviceutil
                try:
                    self.service_status = win32serviceutil.QueryServiceStatus('FileShareService')[1]
                except:
                    self.service_status = None

                if self.service_status == 4:
                    self.back_server_running = True
                    self.start_btn.configure(text="停止后台服务", style="danger.TButton")
                    self.service_checkbox.configure(state="disabled")
                    if not self.page_btn.winfo_ismapped():
                        self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))

                    sync_service_logs(self)
                else:
                    button_text = "启动后台服务" if self.service_status is not None else \
                        "启动服务" if not self.server_running else "停止服务"
                    button_style = "success.TButton" if not self.server_running else "danger.TButton"
                    checkbox_state = "normal"

                    self.start_btn.configure(text=button_text, style=button_style)
                    self.service_checkbox.configure(state=checkbox_state)

                self.service_monitor_timer = self.root.after(2000, check_service_status)

            except:

                self.back_server_running = False
                self.start_btn.configure(text="启动后台服务", style="success.TButton")
                self.service_monitor_timer = self.root.after(2000, check_service_status)

        def sync_service_logs(window):
            """同步后台服务日志到回显框"""
            if not self.back_server_running:
                return
            try:

                current_date = datetime.now().strftime('%Y%m%d')
                log_file = os.path.join(get_app_path(), 'logs', f'service_{current_date}.log')

                if os.path.exists(log_file):

                    if not hasattr(window, 'last_processed_line'):
                        window.last_processed_line = 0

                    with open(log_file, 'r', encoding='utf-8') as f:
                        logs = f.readlines()

                        new_logs = logs[window.last_processed_line:]

                        for log in new_logs:
                            window.log_area.insert('end', log)
                            window.log_area.see('end')

                        window.last_processed_line = len(logs)

            except Exception as e:
                print(f"同步回显后台服务日志出错: {e}")

        check_service_status()

    def stop_service_monitor(self):
        """Stop service monitoring"""
        if self.service_monitor_timer:
            self.root.after_cancel(self.service_monitor_timer)
            self.service_monitor_timer = None
            self.service_status = None

    def do_service_toggle(self):
        """Actual service toggle logic after debounce"""
        if self.service_var.get():
            if not self.is_admin():
                tkmessagebox.showerror("错误", "安装服务需要管理员权限")
                self.service_var.set(False)
                return
            try:
                self.install_service()
                self.start_btn.configure(text="启动后台服务", state="normal")

                flask_app.logger.info("后台服务安装成功")
            except Exception as e:
                self.service_var.set(False)
                tkmessagebox.showerror("错误", f"安装服务失败: {str(e)}")
        else:
            try:
                self.uninstall_service()
                self.start_btn.configure(text="启动服务", state="normal")

                flask_app.logger.info("后台服务卸载成功")
            except Exception as e:
                self.service_var.set(True)
                tkmessagebox.showerror("错误", f"卸载服务失败: {str(e)}")

    def toggle_server_type(self):
        config.use_waitress = self.server_type.get()

        self.waitress_label.configure(style="TLabel")
        self.werkzeug_label.configure(style="TLabel")

        if config.use_waitress:
            self.waitress_label.configure(style="success.TLabel")
            flask_app.logger.info('服务模式已切换成waitress多线程服务模式，适合生产环境')

        else:
            self.werkzeug_label.configure(style="success.TLabel")
            flask_app.logger.info('服务模式已切换成werkzeug单线程服务模式，适合开发调试')
        config.save()

    def toggle_server(self):
        global serverUrl, runningPort, cleanup_thread_running
        if not config.shared_dirs:
            self.log_area.insert(END, "错误：请先添加至少一个共享目录\n")
            self.log_area.see(END)
            return

        self.save_config()
        port = int(self.port_var.get() or 12345)
        runningPort = port
        ip = get_local_ip()
        ipv6 = get_global_ipv6()
        url = f"http://{ip}:{port}"
        url_ipv6 = f"http://[{ipv6}]:{port}"
        serverUrl = url
        if self.service_var.get():
            if not self.back_server_running:
                try:
                    self.start_btn.configure(
                        text="正在启动...",
                        style="warning.TButton",
                        state='disabled'
                    )
                    win32serviceutil.StartService('FileShareService')
                    self.start_btn.configure(text="停止后台服务", style="danger.TButton", state='normal')
                    self.back_server_running = True
                    flask_app.logger.info(f"后台服务已启动 : ipv4: {url}\n ipv6: {url_ipv6}")
                    if not self.page_btn.winfo_ismapped():
                        self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))
                except Exception as e:
                    tkmessagebox.showerror("错误", f"启动服务失败: {str(e)}")
            else:
                try:
                    self.start_btn.configure(
                        text="正在停止...",
                        style="warning.TButton",
                        state='disabled'
                    )
                    win32serviceutil.StopService('FileShareService')
                    self.start_btn.configure(text="启动后台服务", style="success.TButton", state='normal')
                    self.back_server_running = False
                    flask_app.logger.info("后台服务已成功停止")
                    self.page_btn.pack_forget()
                except Exception as e:
                    tkmessagebox.showerror("错误", f"停止服务失败: {str(e)}")
        else:
            if not self.server_running:

                if self.is_port_in_use(runningPort):
                    if tkmessagebox.askyesno("端口被占用",
                                             f"端口 {runningPort} 已被占用。是否尝试强制释放该端口？"):
                        if self.force_cleanup_port(runningPort):
                            self.log_area.insert(END, f"已强制释放端口 {runningPort}\n")
                            return
                        else:
                            self.log_area.insert(END, f"无法释放端口 {runningPort}，请尝试使用其他端口\n")
                            return
                    else:
                        return

                def run_server():
                    try:
                        if config.use_waitress:
                            from waitress import serve
                            self.server_running = True
                            self.root.after(0, lambda: self.start_btn.configure(text="停止服务", style="danger.TButton"))
                            self.switch_server_type_ui(True)

                            self.server_ipv4 = create_server(flask_app, host='0.0.0.0', port=port)
                            self.server_ipv6 = create_server(flask_app, host='::', port=port)

                            with ThreadPoolExecutor(max_workers=2) as self.executor:
                                self.future_ipv4 = self.executor.submit(self.server_ipv4.run)
                                self.future_ipv6 = self.executor.submit(self.server_ipv6.run)
                        else:

                            from werkzeug.serving import WSGIRequestHandler

                            class TimeoutRequestHandler(WSGIRequestHandler):
                                timeout = 30

                            self.server_ipv4 = make_server('0.0.0.0', port, flask_app,
                                                           request_handler=TimeoutRequestHandler)
                            self.server_ipv6 = make_server('::', port, flask_app,
                                                           request_handler=TimeoutRequestHandler)
                            self.server_running = True
                            self.root.after(0, lambda: self.start_btn.configure(text="停止服务", style="danger.TButton"))
                            self.switch_server_type_ui(True)

                            self.thread_ipv4 = threading.Thread(target=self.server_ipv4.serve_forever)
                            self.thread_ipv6 = threading.Thread(target=self.server_ipv6.serve_forever)
                            self.thread_ipv4.daemon = True
                            self.thread_ipv6.daemon = True
                            self.thread_ipv4.start()
                            self.thread_ipv6.start()

                    except Exception as e:
                        self.log_area.insert(END, f"服务器错误: {str(e)}\n")
                        self.log_area.see(END)
                        self.server_running = False

                self.server_thread = threading.Thread(target=run_server)
                self.server_thread.daemon = True
                self.server_thread.start()

                server_type = "Waitress" if config.use_waitress else "Werkzeug"
                flask_app.logger.info(f"服务已启动 ({server_type}): ipv4: {url}\n ipv6: {url_ipv6}")
                if not self.page_btn.winfo_ismapped():
                    self.page_btn.pack(side=LEFT, pady=10, padx=(0, 10))
                if config.auto_cleanup and not cleanup_thread_running:
                    cleanup_thread_running = True
                    start_cleanup_thread()


            else:
                try:
                    self.start_btn.configure(
                        text="正在停止...",
                        style="warning.TButton",
                        state='disabled'
                    )

                    def force_shutdown():
                        try:
                            self.log_area.insert(END, "正在停止服务...\n")
                            self.log_area.see(END)

                            if config.use_waitress:

                                self.server_running = False

                                if hasattr(self, 'server_ipv4'):
                                    self.server_ipv4.close()
                                if hasattr(self, 'server_ipv6'):
                                    self.server_ipv6.close()

                                if hasattr(self, 'executor'):
                                    self.executor.shutdown(wait=False)
                                    self.executor = None

                                if hasattr(self, 'server_thread'):
                                    self.server_thread.join(timeout=2)
                                    if self.server_thread.is_alive():
                                        self.log_area.insert(END, "服务线程未在指定时间内停止，强制关闭...\n")
                                        self.server_thread._stop()

                                if self.force_cleanup_port(runningPort):
                                    self.log_area.insert(END, "已清理所有服务资源\n")

                                self.start_btn.configure(
                                    text="启动服务",
                                    style="success.TButton",
                                    state='normal'
                                )
                                self.switch_server_type_ui(False)
                                self.log_area.insert(END, "服务已停止✓\n")
                                self.page_btn.pack_forget()
                            else:

                                def shutdown_werkzeug():
                                    try:
                                        self.server_running = False
                                        if hasattr(self, 'server_ipv4'):
                                            self.server_ipv4.shutdown()
                                            self.server_ipv4.server_close()
                                        if hasattr(self, 'server_ipv6'):
                                            self.server_ipv6.shutdown()
                                            self.server_ipv6.server_close()
                                        self.start_btn.configure(
                                            text="启动服务",
                                            style="success.TButton",
                                            state='normal'
                                        )
                                        self.switch_server_type_ui(False)
                                        self.log_area.insert(END, "服务已完全停止✓\n")
                                        self.log_area.see(END)
                                        self.page_btn.pack_forget()
                                    except Exception as e:
                                        self.log_area.insert(END, f"停止Werkzeug服务器出错: {str(e)}\n")
                                        self.log_area.see(END)

                                threading.Thread(target=shutdown_werkzeug, daemon=True).start()

                        except Exception as e:

                            self.log_area.insert(END, f"停止过程出错: {str(e)}\n")

                            self.log_area.see(END)

                            self.switch_server_type_ui(False)
                            self.start_btn.configure(

                                text="启动服务",

                                style="success.TButton",

                                state='normal'

                            )

                    self.root.after(100, force_shutdown)
                    if cleanup_thread_running:
                        self.root.after(100, stop_cleanup_thread)

                except Exception as e:
                    self.log_area.insert(END, f"停止服务错误: {str(e)}\n")
                    self.log_area.see(END)

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return False
            except OSError:
                return True

    def force_cleanup_port(self, port):
        """Force cleanup port and related threads"""
        try:

            waitress_threads = [t for t in threading.enumerate()
                                if t.name.startswith('waitress')]

            for thread in waitress_threads:
                if hasattr(thread, '_Thread__stop'):
                    thread._Thread__stop()

            '''''
            if os.name == 'nt':
                cmd = f'for /f "tokens=5" %a in (\'netstat -aon ^| find "{port}"\') do taskkill /F /PID %a'
                os.system(cmd)
            else:
                cmd = f"lsof -i:{port} | grep LISTEN | awk '{{print $2}}' | xargs kill -9"
                os.system(cmd)
'''''

            def check_port():
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind(('0.0.0.0', port))
                    sock.close()
                    return True
                except:
                    return False

            is_released = check_port()
            self.log_area.insert(END, f"端口 {port} {'已释放' if is_released else '仍被占用'}\n")
            return is_released

        except Exception as e:
            self.log_area.insert(END, f"强制清理端口失败: {str(e)}\n")
            return False

    def is_admin(self):
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False

    def is_service_installed(self):
        try:
            win32serviceutil.QueryServiceStatus('FileShareService')
            return True
        except:
            return False

    def install_service(self):

        self.force_delete_service()

        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            exe_path = sys.argv[0]

        win32serviceutil.InstallService(
            serviceName='FileShareService',
            displayName='FS文件分享服务',
            startType=win32service.SERVICE_AUTO_START,
            exeName=exe_path,
            exeArgs='--run-as-service',
            pythonClassString='main.FileShareService',
            description='提供文件共享Web服务 AQ contact: letvar@qq.com',
        )

    def force_delete_service(self):
        """强制删除服务的终极方案"""
        import subprocess
        import time

        subprocess.run(['taskkill', '/F', '/FI', 'SERVICES eq FileShareService'],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)

        subprocess.run(['sc', 'stop', 'FileShareService'],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(1)
        subprocess.run(['sc', 'delete', 'FileShareService'],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(1)

        subprocess.run(['reg', 'delete', 'HKLM\\SYSTEM\\CurrentControlSet\\Services\\FileShareService', '/f'],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)

        time.sleep(2)

    def uninstall_service(self):
        self.force_delete_service()


def main():
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()

    root.withdraw()

    icon_path = get_path('static/favicon.ico')
    root.iconbitmap(icon_path)

    style = ttk.Style(theme="cosmo")

    file_share_app = FileShareApp(root, style)
    file_share_app.log_area.insert(END, "欢迎使用file_share，有任何问题或BUG请返馈至：letvar@qq.com "
                                        "或者github:https://github.com/52op/file_share"
                                        "\n本程序共两种服务方式："
                                        "\n1.前台窗口服务方式：直接启动服务，必须在此程序打开的前提下"
                                        "\n2.后台系统服务方式：点击安装为系服服务，程序会将自身安装成windows服务方式"
                                        "，这样就可以实现随系统自动启动服务。"
                                        "\n安装成系统服务后，可以随时再打开此程序进行配置更改及服务的卸载等 \n")
    file_share_app.log_area.see(END)
    service_status_messages = {
        4: "当前后台服务状态：运行中...",
        1: "当前后台服务状态：已安装，未启动",
        None: "当前后台服务状态：未安装"
    }

    message = service_status_messages.get(file_share_app.service_status, "未知状态")

    file_share_app.log_area.insert(END, f"\n{message}\n")
    file_share_app.log_area.see(END)

    root.mainloop()


if __name__ == "__main__":
    print("程序启动")
    print(f"运行参数: {sys.argv}")
    print(f"程序目录:{get_app_path()}")
    if len(sys.argv) > 1 and sys.argv[1].lower() == '--run-as-service':
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(FileShareService)
            servicemanager.StartServiceCtrlDispatcher()
            win32serviceutil.HandleCommandLine(FileShareService)
        except Exception as e:
            print(f"服务错误: {str(e)}")
            traceback.print_exc()

    elif len(sys.argv) > 1:
        win32serviceutil.HandleCommandLine(FileShareService)
    else:
        main()
