import os
import json
from datetime import datetime, timedelta
import secrets
import zipfile
import tempfile
from main import format_file_size

class ShareLink:
    def __init__(self, path, alias, name, password, expire_days, manage_code, desc=''):
        self.token = secrets.token_urlsafe(16)
        self.path = path
        self.alias = alias
        self.name = name
        self.password = password
        self.manage_code = manage_code
        self.desc = desc
        self.create_time = datetime.now()
        self.expire_time = datetime.now() + timedelta(days=int(expire_days)) if int(expire_days) > 0 else None
        self.is_dir = os.path.isdir(path)
        self.size = self.calculate_size()

    def calculate_size(self):
        """Calculate size for both files and directories"""
        if not os.path.exists(self.path):
            return "0 B"

        if not self.is_dir:
            size = os.path.getsize(self.path)
        else:
            size = 0
            for dirpath, dirnames, filenames in os.walk(self.path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        size += os.path.getsize(fp)

        return format_file_size(size)

    def get_file_size(self, path):
        size = os.path.getsize(path)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    def list_contents(self):
        if not self.is_dir:
            return []

        contents = []
        for entry in os.scandir(self.path):
            content = {
                'name': entry.name,
                'is_dir': entry.is_dir(),
                'size': self.get_file_size(entry.path),
                'mtime': datetime.fromtimestamp(entry.stat().st_mtime)
            }
            contents.append(content)
        return sorted(contents, key=lambda x: (-x['is_dir'], x['name'].lower()))


class ShareManager:
    def __init__(self):
        self.share_links = {}
        self.share_file = 'share_links.json'
        self.load()

    def save(self):
        data = {token: share.__dict__ for token, share in self.share_links.items()}
        with open(self.share_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def load(self):
        if os.path.exists(self.share_file):
            try:
                with open(self.share_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for token, share_data in data.items():
                        share = ShareLink(
                            share_data['path'],
                            share_data['alias'],
                            share_data['name'],
                            share_data['password'],
                            0,
                            share_data['manage_code'],
                            share_data.get('desc', '')
                        )
                        share.token = token
                        share.create_time = datetime.fromisoformat(share_data['create_time'])
                        if share_data['expire_time']:
                            share.expire_time = datetime.fromisoformat(share_data['expire_time'])
                        share.is_dir = share_data['is_dir']
                        share.size = share_data['size']     # 从配置文件取大小
                        # share.size = share.calculate_size()   # 每次重新计算大小，文件多会增加服务器压力
                        self.share_links[token] = share
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                import shutil
                backup_file = f"{self.share_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                shutil.copy2(self.share_file, backup_file)

                from main import flask_app
                flask_app.logger.info(f"错误: {str(e)}")
                flask_app.logger.info(f"分享链接配置文件读取错误，配份原文件为: {backup_file}")
                # 重建保存
                self.share_links = {}
                self.save()

    # 加个装饰器，别的文件调用类里面这个方法就不用实例化了，不然就要share_manager = ShareManager()进行实例化
    # 我在routes.py使用了实例化，这里必须注释，不然就报错了
    #@staticmethod
    def create_share(self, path, alias, name, password, expire_days, manage_code, desc):
        share = ShareLink(path, alias, name, password, expire_days, manage_code, desc)
        self.share_links[share.token] = share
        self.save()
        return share

    def reload(self):
        self.load()

    def get_share(self, token):
        self.reload()
        return self.share_links.get(token)

    def get_share_no_reload(self, token):
        # 直接返回 share_links 字典中的对象，不调用 reload()
        return self.share_links.get(token)

    def remove_expired(self):
        now = datetime.now()
        expired = [token for token, share in self.share_links.items()
                   if share.expire_time and share.expire_time < now]
        for token in expired:
            del self.share_links[token]
        if expired:
            self.save()

    # @staticmethod 装饰器用于定义一个不需要访问类或实例数据的静态方法。
    # 这意味着静态方法不会自动接收 self 或 cls 参数，它们可以被类实例和类本身直接调用。
    # 经常碰到报错，参数不对，一般就是这个情况，我在routes.py也进行了实例化，这里就注释掉了
    # @staticmethod
    def create_zip_from_dir(self, dir_path):
        temp_zip = tempfile.NamedTemporaryFile(prefix='file_share_', suffix='.zip', delete=False)
        with zipfile.ZipFile(temp_zip.name, 'w') as zf:
            for root, _, files in os.walk(dir_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, dir_path)
                    zf.write(file_path, arcname)
        return temp_zip.name

    def list_directory_contents(self, base_path, current_path=''):
        full_path = os.path.join(base_path, current_path)
        contents = []
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            rel_path = os.path.join(current_path, item) if current_path else item
            is_dir = os.path.isdir(item_path)

            contents.append({
                'name': item,
                'path': rel_path,
                'is_dir': is_dir,
                'size': format_file_size(os.path.getsize(item_path)) if not is_dir else '',
                'mtime': datetime.fromtimestamp(os.path.getmtime(item_path))
            })
        return sorted(contents, key=lambda x: (-x['is_dir'], x['name']))