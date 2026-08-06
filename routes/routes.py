import base64
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import zipfile
from io import BytesIO
from urllib.parse import quote
from datetime import datetime, timedelta

from functools import wraps
from PIL import Image
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from flask import session, render_template, request, send_file, jsonify, redirect, url_for, after_this_request
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from werkzeug.utils import secure_filename
from waitress.server import create_server  # 生产环境使用

from main import flask_app, config, format_file_size, partial_download, send_file_generator, \
    get_client_info, secure_filename_cn, safe_relative_path, ShareDirectory, password_change_timestamps, get_app_path
from share_links import ShareManager   # 这个文件被全部引入了main.py main.py已经引入了这个，所以注释
from firewall import IPLimiter
import pyotp


def get_dir_obj(alias):
    """按别名查找目录对象，未找到返回 None。"""
    for d in config.shared_dirs.values():
        if d.alias == alias:
            return d
    return None


def safe_join_path(base_path, *paths):
    """安全路径拼接，防止路径遍历攻击"""
    try:
        # 规范化基础路径
        base_path = os.path.abspath(base_path)

        # 拼接路径
        joined_path = os.path.join(base_path, *paths)

        # 规范化拼接后的路径
        normalized_path = os.path.abspath(joined_path)

        # 检查是否在基础路径内
        if not normalized_path.startswith(base_path + os.sep) and normalized_path != base_path:
            raise ValueError("Path traversal detected")

        return normalized_path
    except (ValueError, OSError) as e:
        flask_app.logger.warning(f"路径安全检查失败: {e}")
        raise ValueError("Invalid path")

# 实例化 share_links/share_manager.py 里面的 ShareManager
share_manager = ShareManager()    # 这个文件被全部引入了main.py main.py已经引入了这个，所以注释
ip_limiter = IPLimiter()

# 清理线程相关变量
cleanup_thread_running = False
cleanup_thread = None


def check_ip_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.remote_addr

        # 检查是否被封禁
        if ip_limiter.is_blocked(ip):
            remaining_time = ip_limiter.get_remaining_time(ip)
            return render_template('ip_blocked.html',
                                   remaining_time=remaining_time,
                                   pageMark='访问受限')

        return f(*args, **kwargs)

    return decorated_function


def check_directory_admin_permission(f):
    """检查目录管理员权限的装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 如果是超级管理员，直接通过
        if session.get('admin'):
            return f(*args, **kwargs)

        # 获取目录别名
        dirname = None
        if 'alias' in kwargs:
            dirname = kwargs['alias']
        elif 'dirname' in kwargs:
            dirname = kwargs['dirname'].split('/')[0]
        elif 'filepath' in kwargs:
            dirname = kwargs['filepath'].split('/')[0]
        elif request.method == 'POST':
            if request.json:
                if 'path' in request.json:
                    dirname = request.json['path'].split('/')[0]
            elif request.form:
                current_path = request.form.get('current_path', '')
                if current_path:
                    dirname = current_path.strip('/').split('/')[1] if current_path.startswith('/dir/') else current_path.split('/')[0]

        if not dirname:
            return 'Unauthorized - No directory specified', 403

        # 检查是否是该目录的管理员
        if session.get(f'dir_admin_{dirname}'):
            return f(*args, **kwargs)

        return 'Unauthorized - Directory admin access required', 403

    return decorated_function


# 修正预览文件直链/preview/disk_D/downloads/test/Tulip3s.jpg无法跳出密码验证之前使用
def check_auth_timestamp(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 原有的全局密码检查
        if config.global_password and config.global_password.strip():
            if request.path.startswith('/s/'):  # 全局密码检查跳过私有分享链接
                return f(*args, **kwargs)
            user_auth_time = session.get('auth_time', 0)
            if user_auth_time < password_change_timestamps['global']:
                session.pop('auth', None)
                return render_template('global_password.html', alias='global', pageMark=f'全局密码')

        # 检查分享链接认证
        token = kwargs.get('token')
        if token:
            share = share_manager.get_share(token)
            if share and share.password:
                share_auth_key = f'share_auth_{token}'
                share_auth_time = session.get(f'share_auth_time_{token}', 0)
                if share_auth_time < password_change_timestamps['shares'].get(token, float('inf')):
                    session.pop(share_auth_key, None)
                    return render_template('share_password.html', token=token, pageMark=f'分享密码')

        # 原有的目录认证检查
        dirname = None
        if 'dirname' in kwargs:
            dirname = kwargs['dirname'].split('/')[0]
        elif 'filepath' in kwargs:
            dirname = kwargs['filepath'].split('/')[0]
        elif request.method == 'POST':
            if request.json:
                if 'path' in request.json:
                    dirname = request.json['path'].split('/')[0]
                elif 'items' in request.json:
                    files = request.json.get('items', [])
                    if files:
                        dirname = files[0]['path'].split('/')[0]
        # print(f"dirname:{dirname},args{args},kwargs:{kwargs}")
        # print(f"password_change_timestamps['directories']:{password_change_timestamps['directories']}")
        # print(f"config.shared_dirs:{config.shared_dirs}")
        # 通过 alias 查找对应的目录配置
        dir_config = get_dir_obj(dirname)

        # 检查目录是否需要认证
        if dir_config and getattr(dir_config, 'password', None):
            dir_auth_time = session.get(f'auth_time_{dirname}', 0)
            if dirname in password_change_timestamps['directories'] and dir_auth_time < password_change_timestamps[
                'directories'].get(dirname, 0):
                session.pop(f'auth_{dirname}', None)
                return render_template('directory_password.html', alias=dirname, pageMark=f'{dirname}访问密码')

        return f(*args, **kwargs)
    return decorated_function


def require_dir_access(dir_obj, base_dir=None, alias=None, is_api=False):
    """统一校验对某目录的访问权限，防止直连完整路径绕过认证。

    规则：
    - 目录设置了独立访问密码 → 只校验该目录会话，缺省返回目录密码页/403
    - 目录未设独立密码 → 若配置了全局密码，则校验全局会话，缺省返回全局密码页/403
    返回响应对象表示需跳转/拒绝；返回 None 表示放行。
    """
    key_alias = alias or base_dir or dir_obj.alias
    # 超级管理员已登录 → 直接放行，无需目录密码或全局密码
    if session.get('admin'):
        return None
    if getattr(dir_obj, 'password', None):
        if not session.get(f'auth_{key_alias}'):
            if is_api:
                return jsonify({'error': 'Authentication required'}), 403
            return render_template('directory_password.html', alias=key_alias, pageMark=f'{key_alias}访问密码')
    else:
        if config.global_password and config.global_password.strip() and not session.get('auth'):
            if is_api:
                return jsonify({'error': 'Global password required'}), 403
            return render_template('global_password.html', alias='global', pageMark=f'全局密码')
    return None


# 清理函数 用于清理打包下载类路由函数生成的系统临时文件及过期分享链接
def cleanup_temp_files_and_expired_links():
    global cleanup_thread_running
    while cleanup_thread_running:
        # 清理 Windows 临时文件夹中生成的临时文件（ZIP 文件）
        if config.auto_cleanup:
            temp_dir = tempfile.gettempdir()
            now = time.time()
            for filename in os.listdir(temp_dir):
                # 只清理符合特定命名规则的临时文件
                if filename.startswith('file_share_') and filename.endswith('.zip'):
                    file_path = os.path.join(temp_dir, filename)
                    if os.path.isfile(file_path):
                        try:
                            # 如果文件存在超过一天，则删除
                            # if now - os.path.getmtime(file_path) > 24 * 60 * 60:
                            os.remove(file_path)
                            flask_app.logger.info(f"删除临时文件: {file_path}")
                        except PermissionError as e:
                            flask_app.logger.info(f"无法删除文件 {file_path}: 文件正在使用中。错误: {e}")
                        except Exception as e:
                            flask_app.logger.info(f"删除文件 {file_path} 时发生未知错误: {e}")

            # 清理过期的分享链接
            share_manager.remove_expired()
            flask_app.logger.info("清理了过期的分享链接")

            # 每小时运行一次清理任务，检查是否需要停止
            for _ in range(config.cleanup_time // 10):  # 统一config配时间间隔是否需要停止
                if not cleanup_thread_running:
                    return
                time.sleep(10)  # 每秒钟休眠一次，检查停止标志


# 清理函数 启动线程
def start_cleanup_thread():
    global cleanup_thread
    global cleanup_thread_running
    cleanup_thread_running = True  # 置停止标志
    cleanup_thread = threading.Thread(target=cleanup_temp_files_and_expired_links)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    flask_app.logger.info(f"清理线程启动，清理间隔{config.cleanup_time}秒")


# 清理函数 终止线程
def stop_cleanup_thread():
    global cleanup_thread_running
    cleanup_thread_running = False
    if cleanup_thread.is_alive():
        cleanup_thread.join()  # 等待线程终止
    flask_app.logger.info("清理线程已停止")


# 检查清理线程是否运行
def is_cleanup_running():
    global cleanup_thread_running
    return cleanup_thread_running


# 遍历static下指定目录的所有主题
def get_themes():
    themes = []
    bootswatch_dir = os.path.join(flask_app.static_folder, 'bootswatch')
    for filename in os.listdir(bootswatch_dir):
        if filename.endswith('.min.css'):
            theme_name = filename.replace('.min.css', '')
            theme_url = url_for('static', filename=f'bootswatch/{filename}')
            themes.append({'name': theme_name, 'url': theme_url})
    return themes


# 辅助函数 创建于修改预览编辑保存路由
def validate_file_path(filepath):
    """验证文件路径并返回完整路径"""
    base_dir = filepath.split('/')[0]
    dir_obj = get_dir_obj(base_dir)

    if not dir_obj:
        return '目录不存在', 404

    # 安全路径拼接，防止路径遍历
    try:
        full_path = safe_join_path(dir_obj.path, *filepath.split('/')[1:])
    except ValueError:
        return '非法的文件路径', 400

    if not os.path.exists(full_path):
        return '文件不存在', 404

    if not os.path.isfile(full_path):
        return '非法的文件路径', 400

    return full_path


def is_text_file(filepath, block_size=512):
    """检查是否为文本文件"""
    try:
        with open(filepath, 'rb') as f:
            block = f.read(block_size)
            return not bool(b'\x00' in block)
    except Exception:
        return False


def read_text_file(filepath):
    """读取文本文件内容"""
    encodings = ['utf-8', 'gbk', 'gb2312', 'ansi']
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise Exception('无法以支持的编码格式读取文件')


# 使用 Flask 的 context_processor 或 before_request 钩子来全局传递 themes 变量和页面设置
@flask_app.context_processor
def inject_global_vars():
    return {
        'themes': get_themes(),
        'page_title': config.page_title,
        'logo_name': config.logo_name,
        'logo_image_url': config.logo_image_url,
        'session_timeout_ms': config.session_timeout * 1000,
        'session_timeout_minutes': max(0, config.session_timeout // 60),
        'is_authenticated': bool(
            session.get('admin')
            or session.get('auth')
            or any(k.startswith('auth_') and not k.startswith('auth_time') for k in session)
            or any(k.startswith('dir_admin_') and not k.startswith('dir_admin_time') for k in session)
            or any(k.startswith('share_auth_') and not k.startswith('share_auth_time') for k in session)
        )
    }


# 在主应用入口处添加全局拦截
@flask_app.before_request
def check_blocked_ip():
    ip = request.remote_addr
    # 排除对静态文件的拦截
    if request.path.startswith('/static/'):
        return None
    if ip_limiter.is_blocked(ip):
        return render_template('ip_blocked.html',
                             remaining_time=ip_limiter.get_remaining_time(ip),
                             pageMark='访问受限')


@flask_app.before_request
def check_session_timeout():
    """会话空闲超时检查：超过 config.session_timeout 秒无活动则清除对应登录态"""
    if request.path.startswith('/static/'):
        return None
    timeout = getattr(config, 'session_timeout', 600)
    if not timeout or timeout <= 0:
        return None
    now = time.time()

    def expire(key):
        session.pop(key, None)

    # 超级管理员
    if session.get('admin'):
        if now - session.get('admin_time', now) > timeout:
            expire('admin')
            expire('admin_time')
        else:
            session['admin_time'] = now

    # 全局密码
    if session.get('auth'):
        if now - session.get('auth_time', now) > timeout:
            expire('auth')
            expire('auth_time')
        else:
            session['auth_time'] = now

    # 目录密码 / 目录管理员 / 分享密码（遍历 session 键）
    for key in list(session.keys()):
        if key.startswith('auth_') and not key.startswith('auth_time'):
            ts = session.get(f'auth_time_{key[5:]}', now)
            if now - ts > timeout:
                expire(key)
                expire(f'auth_time_{key[5:]}')
            else:
                session[f'auth_time_{key[5:]}'] = now
        elif key.startswith('dir_admin_') and not key.startswith('dir_admin_time'):
            ts = session.get(f'dir_admin_time_{key[10:]}', now)
            if now - ts > timeout:
                expire(key)
                expire(f'dir_admin_time_{key[10:]}')
            else:
                session[f'dir_admin_time_{key[10:]}'] = now
        elif key.startswith('share_auth_') and not key.startswith('share_auth_time'):
            ts = session.get(f'share_auth_time_{key[11:]}', now)
            if now - ts > timeout:
                expire(key)
                expire(f'share_auth_time_{key[11:]}')
            else:
                session[f'share_auth_time_{key[11:]}'] = now


@flask_app.route('/')
@check_auth_timestamp  # 添加装饰器 用于实时修改密码后的一个校验新密码
def index():
    # 检查全局密码验证
    if config.global_password and not session.get('auth') and not session.get('admin'):
        return render_template('global_password.html', alias='global', pageMark=f'全局密码')

    # 只返回必要的信息
    dirs = [
        {
            'alias': dir_obj.alias,
            'password': bool(dir_obj.password),
            'desc': dir_obj.desc
        }
        for dir_obj in config.shared_dirs.values()
    ]
    return render_template('index.html', dirs=dirs, pageMark=f'首页',
                           admin_totp_enabled=bool(config.admin_totp_secret),
                           admin_totp_only=bool(getattr(config, 'admin_totp_only', False)))


@flask_app.route('/check_password/<path:alias>', methods=['POST'])
@check_ip_limit
def check_password(alias):
    password = request.form.get('password')
    current_time = time.time()
    client_info = f"{request.remote_addr}"

    if alias == 'global':
        if password == config.global_password or password == config.admin_password:
            session['auth'] = True
            ip_limiter.reset(client_info)  # 登录成功后重置计数
            session['auth_time'] = current_time
            return '', 200
        else:
            # 记录失败次数
            ip_limiter.add_failed_attempt(client_info)

    else:
        dir_obj = get_dir_obj(alias)

        if dir_obj and password == dir_obj.password or password == config.admin_password:
            session[f'auth_{alias}'] = True
            ip_limiter.reset(client_info)  # 登录成功后重置计数
            session[f'auth_time_{alias}'] = current_time
            return '', 200
        else:
            # 记录失败次数
            ip_limiter.add_failed_attempt(client_info)

    return '', 403


@flask_app.route('/dir/<path:dirname>')
@check_auth_timestamp
def list_dir(dirname):
    base_dir = dirname.split('/')[0]

    # 查找目录对象（修复重复循环问题）
    dir_obj = get_dir_obj(base_dir)

    if not dir_obj:
        return render_template('error.html',
                               error_code=404,
                               message="找不到相关目录或文件", pageMark=f'找不到相关目录或文件'), 404

    # 校验访问权限（目录独立密码或全局密码兜底）
    access_resp = require_dir_access(dir_obj, base_dir=base_dir)
    if access_resp:
        return access_resp

    # 安全路径拼接，防止路径遍历
    sub_path = dirname.split('/')[1:]
    try:
        current_path = safe_join_path(dir_obj.path, *sub_path)
    except ValueError:
        return render_template('error.html',
                               error_code=400,
                               message="非法的路径访问", pageMark=f'非法的路径访问'), 400

    if not os.path.exists(current_path):
        return render_template('error.html',
                               error_code=404,
                               message="路径不存在或已被移除", pageMark=f'路径不存在或已被移除'), 404

    # Fixed navigation path building
    nav_path = []
    current = ""
    for part in dirname.split('/'):
        current += f"/{part}" if current else part
        # Find directory object by alias if it exists
        dir_info = get_dir_obj(part)
        nav_path.append({
            'name': part,
            'path': current,
            'alias': dir_info.alias if dir_info else part
        })

    items = []
    for item in sorted(os.listdir(current_path)):
        item_path = os.path.join(current_path, item)
        rel_path = os.path.join(dirname, item).replace('\\', '/')

        if os.path.isdir(item_path):
            items.append({
                'name': item,
                'is_dir': True,
                'path': rel_path
            })
        else:
            items.append({
                'name': item,
                'is_dir': False,
                'size': format_file_size(os.path.getsize(item_path)),
                'path': rel_path
            })

    return render_template('directory.html',
                           items=items,
                           nav_path=nav_path,
                           current_dir=base_dir,
                           current_path=dirname,
                           dir_obj=dir_obj, pageMark=f'{base_dir}目录浏览',
                           admin_totp_enabled=bool(config.admin_totp_secret),
                           admin_totp_only=bool(getattr(config, 'admin_totp_only', False)))


@flask_app.route('/api/search/<alias>')
@check_auth_timestamp
def search_files(alias):
    import time

    search_term = request.args.get('term', '').lower()
    max_results = int(request.args.get('limit', 100))  # 默认最多返回100个结果
    max_depth = int(request.args.get('depth', 5))  # 默认最大搜索深度5层
    timeout = int(request.args.get('timeout', 10))  # 默认超时10秒

    # 从config获取目录对象
    dir_obj = get_dir_obj(alias)

    if not dir_obj:
        return jsonify({'error': 'Directory not found'}), 404

    # 校验访问权限（目录独立密码或全局密码兜底）
    access_resp = require_dir_access(dir_obj, base_dir=alias, is_api=True)
    if access_resp:
        return access_resp

    if not search_term:
        return jsonify({'results': [], 'total': 0, 'limited': False, 'timeout': False})

    results = []
    start_time = time.time()

    try:
        # 使用受限的遍历逻辑
        for root, dirs, files in os.walk(dir_obj.path):
            # 检查超时
            if time.time() - start_time > timeout:
                flask_app.logger.warning(f"搜索超时: {alias}, 搜索词: {search_term}")
                break

            # 检查结果数量限制
            if len(results) >= max_results:
                break

            # 检查深度限制
            current_depth = root[len(dir_obj.path):].count(os.sep)
            if current_depth > max_depth:
                dirs[:] = []  # 不再深入子目录
                continue

            # 搜索目录
            for dir_name in dirs:
                if len(results) >= max_results:
                    break
                if search_term in dir_name.lower():
                    try:
                        rel_path = os.path.relpath(os.path.join(root, dir_name), dir_obj.path)
                        full_path = os.path.join(alias, rel_path).replace('\\', '/')
                        results.append({
                            'name': dir_name,
                            'is_dir': True,
                            'path': full_path
                        })
                    except (OSError, ValueError):
                        continue

            # 搜索文件
            for file_name in files:
                if len(results) >= max_results:
                    break
                if search_term in file_name.lower():
                    try:
                        file_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(file_path, dir_obj.path)
                        full_path = os.path.join(alias, rel_path).replace('\\', '/')
                        file_size = os.path.getsize(file_path)
                        results.append({
                            'name': file_name,
                            'is_dir': False,
                            'size': format_file_size(file_size),
                            'path': full_path
                        })
                    except (OSError, ValueError):
                        continue

    except Exception as e:
        flask_app.logger.error(f"搜索过程中发生错误: {e}")
        return jsonify({'error': 'Search failed'}), 500

    return jsonify({
        'results': results,
        'total': len(results),
        'limited': len(results) >= max_results,
        'timeout': time.time() - start_time > timeout
    })


@flask_app.route('/preview/<path:filepath>')
@check_auth_timestamp
def preview_file(filepath):
    base_dir = filepath.split('/')[0]
    dir_obj = get_dir_obj(base_dir)
    if not dir_obj:
        return '目录不存在', 404
    access_resp = require_dir_access(dir_obj, base_dir=base_dir, is_api=request.headers.get('X-Requested-With') == 'XMLHttpRequest')
    if access_resp:
        return access_resp

    # 验证和获取文件路径
    result = validate_file_path(filepath)
    if isinstance(result, tuple):
        return result if not request.is_xhr else jsonify({'error': result[0]}), result[1]
    full_path = result

    # 获取MIME类型
    mime_type = mimetypes.guess_type(full_path)[0]

    # 检查是否为AJAX请求
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # 对于不支持的文件类型，直接返回错误
    if not (mime_type and (
            mime_type.startswith(('image/', 'video/', 'audio/')) or mime_type == 'application/pdf') or is_text_file(
            full_path)):
        error_msg = '不支持预览此类型文件'
        return jsonify({'error': error_msg}) if is_ajax else (error_msg, 415)

    # 如果是AJAX请求(来自workspace.js),返回JSON格式
    if is_ajax:
        try:
            content = read_text_file(full_path)
            return jsonify({'content': content})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # 常规预览请求处理
    if mime_type:
        if mime_type.startswith(('image/', 'video/', 'audio/')) or mime_type == 'application/pdf':
            return send_file(
                full_path,
                mimetype=mime_type,
                as_attachment=False,
                conditional=True
            )

    # 文本文件处理
    try:
        return read_text_file(full_path)
    except Exception as e:
        return f'预览失败: {str(e)}', 500


def calculate_batch_size(items):    # 计算批量文件总大小
    total_size = 0

    for item in items:
        path = item['path']
        is_dir = item.get('is_dir', False)

        if is_dir:
            # 如果是目录，递归计算目录下所有文件大小
            for root, _, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.exists(file_path):
                        total_size += os.path.getsize(file_path)
        else:
            # 如果是文件，直接获取文件大小
            if os.path.exists(path):
                total_size += os.path.getsize(path)

    # 添加zip压缩文件的预估开销
    # 假设压缩率为0.9(根据实际情况调整)
    estimated_zip_size = int(total_size * 0.9)

    return estimated_zip_size

@flask_app.route('/api/batch-download', methods=['POST'])
@check_auth_timestamp
def batch_download():
    files = request.json.get('items', [])
    # 校验每个文件所属目录的访问权限
    checked_aliases = set()
    for file in files:
        base_dir = file['path'].split('/')[0]
        if base_dir in checked_aliases:
            continue
        dir_obj = get_dir_obj(base_dir)
        if dir_obj:
            access_resp = require_dir_access(dir_obj, base_dir=base_dir, is_api=True)
            if access_resp:
                return access_resp
        checked_aliases.add(base_dir)

    temp_zip = tempfile.NamedTemporaryFile(prefix='file_share_', suffix='.zip', delete=False)

    with zipfile.ZipFile(temp_zip.name, 'w') as zf:
        for file in files:
            base_dir = file['path'].split('/')[0]
            dir_obj = get_dir_obj(base_dir)
            if dir_obj:
                full_path = os.path.join(dir_obj.path, *file['path'].split('/')[1:])
                if os.path.exists(full_path):
                    zf.write(full_path, file['name'])

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 在 {base_dir} 打包下载了多个文件")
    response = send_file(
        temp_zip.name,
        mimetype='application/zip',
        as_attachment=True,
        download_name='download.zip',  # Changed from attachment_filename
        conditional=True
    )

    return response


@flask_app.route('/api/save-file/<path:filepath>', methods=['POST'])
def save_file(filepath):
    if not session.get('admin'):
        return jsonify({'error': '未授权访问'}), 403

    # 验证和获取文件路径
    result = validate_file_path(filepath)
    if isinstance(result, tuple):
        return jsonify({'error': result[0]}), result[1]
    full_path = result

    content = request.json.get('content')
    if content is None:
        return jsonify({'error': '无效的内容'}), 400

    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': f'保存失败: {str(e)}'}), 500


@flask_app.route('/download/<path:filepath>')
@check_auth_timestamp
def download(filepath):

    parts = filepath.split('/', 1)
    if len(parts) != 2:
        return "Invalid path", 400

    dirname, filename = parts

    dir_obj = get_dir_obj(dirname)

    if not dir_obj:
        flask_app.logger.error(f"Directory not found: {dirname}")
        return "Directory not found", 404

    # 检查目录访问权限（含全局密码兜底）
    access_resp = require_dir_access(dir_obj, base_dir=dirname)
    if access_resp:
        return access_resp

    # 安全路径拼接，防止路径遍历
    try:
        file_path = safe_join_path(dir_obj.path, filename)
    except ValueError:
        return "Invalid file path", 400

    if not os.path.isfile(file_path):
        flask_app.logger.error(f"File not found: {file_path}")
        return "File not found", 404

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 下载了{file_path}")

    content_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
    response = send_file(
        file_path,
        as_attachment=True,
        download_name=os.path.basename(filename),
        # os.path.basename(filename) 确保只使用文件名，而不包含路径分隔符。这可以防止路径被错误地包含在下载的文件名中
        conditional=True  # 启用断点续传支持
    )
    # 添加或覆盖任何你想要自定义的响应头 flask send_file自带了响应处理，无必要不需要自定义
    # response.headers['Content-Type'] = content_type
    # response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{quote(filename)}'

    return response


@flask_app.route('/api/upload/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def upload_file(alias):
    # 权限检查已由装饰器处理

    file = request.files.get('file')
    current_path = request.form.get('current_path', '')
    current_path = urllib.parse.unquote(current_path)

    # 获取分片信息
    chunk_number = request.form.get('chunk_index', 0)  # Changed from 'chunk'
    chunks = request.form.get('total_chunks', 1)  # Changed from 'chunks'
    filename = request.form.get('filename')  # Get original filename
    file_id = request.form.get('identifier')  # Match the frontend parameter name

    if not file_id:
        return "Missing file identifier", 400

    if not file:
        return "No file", 400

    # 校验分片参数，防止非法输入导致崩溃
    try:
        chunk_number = int(chunk_number)
        chunks = int(chunks)
    except (TypeError, ValueError):
        return "Invalid chunk parameters", 400

    if chunks < 1 or chunk_number < 0 or chunk_number >= chunks:
        return "Invalid chunk index", 400

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    # 处理目标路径
    path_parts = current_path.strip('/').split('/')
    if len(path_parts) > 1:
        sub_path = '/'.join(path_parts[2:])
        target_dir = os.path.join(dir_obj.path, sub_path)
        if not os.path.exists(target_dir):
            return "Target directory not found", 404
    else:
        target_dir = dir_obj.path

    # filename 可能为相对路径（文件夹上传场景，如 "docs/img/a.png"），逐段净化防穿越
    safe_rel = safe_relative_path(filename)
    if safe_rel is None:
        return "Invalid filename", 400
    final_path = os.path.join(target_dir, *safe_rel.split('/'))
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    # 如果是普通上传（非分片）
    if chunks == 1:
        file.save(final_path)
        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 上传文件: {safe_rel} 到了{target_dir}")
        return "Success", 200

    # 处理分片上传
    temp_dir = os.path.join(config.upload_temp_dir, file_id)
    os.makedirs(temp_dir, exist_ok=True)

    # 保存分片
    chunk_file = os.path.join(temp_dir, f"chunk_{chunk_number}")
    file.save(chunk_file)

    # 检查是否所有分片都已上传
    uploaded_chunks = len([f for f in os.listdir(temp_dir) if f.startswith('chunk_')])
    if uploaded_chunks == chunks:
        # 合并所有分片（流式写入，避免大文件整片读入内存）
        try:
            with open(final_path, 'wb') as target_file:
                for i in range(chunks):
                    chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                    with open(chunk_path, 'rb') as chunk:
                        shutil.copyfileobj(chunk, target_file, 1024 * 1024)

            # 清理临时文件
            shutil.rmtree(temp_dir, ignore_errors=True)

            client_info = get_client_info()
            flask_app.logger.info(f"{client_info} 上传文件: {safe_rel} 到了{target_dir}")
            return "Success", 200
        except Exception as e:
            flask_app.logger.error(f"合并分片失败: {filename}, 错误: {e}")
            return "Chunk merge failed", 500

    return jsonify({
        'uploaded_chunks': uploaded_chunks,
        'total_chunks': chunks
    })


def validate_folder_name(name):
    """验证文件夹名称，特别注意#号等URL敏感字符"""
    if not name.strip():
        return "文件夹名称不能为空"

    # 包含#号等URL敏感字符的完整检查
    invalid_chars = r'[<>:"|?*\\/#%&{}$!\'@+`=]'
    if re.search(invalid_chars, name):
        return "文件夹名称不能包含以下字符: < > : \" | ? * \\ / # % & { } $ ! ' @ + ` ="

    # 检查保留名称
    reserved_names = ['CON', 'PRN', 'AUX', 'NUL'] + [f'COM{i}' for i in range(1,10)] + [f'LPT{i}' for i in range(1,10)]
    if name.upper() in reserved_names:
        return "不能使用系统保留名称"

    # 检查长度
    if len(name) > 255:
        return "文件夹名称过长（最多255个字符）"

    # 检查是否以点开头
    if name.startswith('.'):
        return "文件夹名称不能以点开头"

    # 检查是否以点或空格结尾
    if name.endswith('.') or name.endswith(' '):
        return "文件夹名称不能以点或空格结尾"

    return None


@flask_app.route('/api/mkdir/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def make_directory(alias):
    # 权限检查已由装饰器处理

    current_path = request.form.get('current_path')
    folder_name = request.form.get('name')

    if not current_path or not folder_name:
        return "Missing required parameters", 400

    current_path = urllib.parse.unquote(current_path)
    folder_name = urllib.parse.unquote(folder_name)

    # 验证文件夹名称
    validation_error = validate_folder_name(folder_name)
    if validation_error:
        return validation_error, 400

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    # 从URL路径提取实际目录路径
    path_parts = current_path.strip('/').split('/')
    if len(path_parts) > 1:
        # 移除 'dir' 前缀并构建目标路径
        sub_path = '/'.join(path_parts[2:])
        target_dir = os.path.join(dir_obj.path, sub_path, folder_name)
        check_dir = os.path.join(dir_obj.path, sub_path)
    else:
        target_dir = os.path.join(dir_obj.path, folder_name)
        check_dir = os.path.join(dir_obj.path)
    # 验证目标路径是否存在
    if not os.path.exists(check_dir):
        return "Target directory not found", 404
    os.makedirs(target_dir, exist_ok=True)  # 验证目标路径是否存在 宽松模式，自动创建不存在的目录
    # 记录新建文件夹操作
    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 在 {check_dir} 新建文件夹: {folder_name}")
    return "Success", 200


@flask_app.route('/api/delete/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def delete_item(alias):
    # 权限检查已由装饰器处理

    name = request.form.get('name')
    current_path = request.form.get('current_path')
    if not current_path or not name:
        return "Missing required parameters", 400

    is_dir = request.form.get('is_dir') == 'true'
    recursive = request.form.get('recursive') == 'true'
    current_path = urllib.parse.unquote(current_path)
    name = urllib.parse.unquote(name)

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    # 构建目标路径
    path_parts = current_path.strip('/').split('/')
    sub_path = '/'.join(path_parts[2:]) if len(path_parts) > 1 else ''
    target_path = os.path.join(dir_obj.path, sub_path, name)

    try:
        if is_dir:
            if recursive:
                shutil.rmtree(target_path)
            else:
                os.rmdir(target_path)  # 只能删除空目录
            pre_name = "目录"
        else:
            os.remove(target_path)
            pre_name = "文件"

        client_info = get_client_info()
        op_path = os.path.join(dir_obj.path, sub_path)
        flask_app.logger.info(f"{client_info}在 {op_path} 删除了{pre_name}: {name}")
        return "Success", 200
    # except OSError as e:   # 返回详细错误写法
    #    return str(e), 400
    except OSError as e:  # 返回友好错误写法
        if "目录不是空的" in str(e):
            return "该文件夹不为空，请先清空文件夹内容", 400
        elif "找不到文件" in str(e):
            return "找不到要删除的项目", 404
        else:
            return "删除操作失败", 400


@flask_app.route('/api/batch-delete/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def batch_delete_items(alias):
    """批量删除文件/文件夹。items 传完整 path（alias/相对路径），支持搜索态跨目录选择。"""
    data = request.get_json(silent=True) or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'error': '未选择任何项目'}), 400

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return jsonify({'error': '目录不存在'}), 404

    failed = 0
    errors = []
    success = 0
    client_info = get_client_info()

    for item in items:
        item_path = item.get('path') or item.get('name')
        is_dir = bool(item.get('is_dir'))
        if not item_path:
            failed += 1
            errors.append('缺少项目路径')
            continue

        # item.path 形如 "alias/子目录/文件名"，去除 alias 前缀
        rel = item_path.strip('/')
        if rel.startswith(alias + '/'):
            rel = rel[len(alias) + 1:]
        else:
            # 搜索态返回的是 alias/xxx，若没有前缀则按原样处理
            pass

        safe_rel = safe_relative_path(rel)
        if safe_rel is None:
            failed += 1
            errors.append(f'{item_path}: 路径非法')
            continue

        full_path = os.path.join(dir_obj.path, safe_rel.replace('/', os.sep))
        if not full_path.startswith(os.path.abspath(dir_obj.path)):
            failed += 1
            errors.append(f'{item_path}: 路径越界')
            continue

        try:
            if is_dir:
                shutil.rmtree(full_path)  # 批量删除直接递归删除非空文件夹
                pre_name = '目录'
            else:
                os.remove(full_path)
                pre_name = '文件'
            success += 1
            flask_app.logger.info(f"{client_info} 批量删除了{pre_name}: {safe_rel}")
        except OSError as e:
            failed += 1
            if "目录不是空的" in str(e):
                errors.append(f'{item_path}: 文件夹不为空，请先清空内容')
            elif "找不到" in str(e) or "系统找不到" in str(e):
                errors.append(f'{item_path}: 找不到要删除的项目')
            else:
                errors.append(f'{item_path}: 删除失败')

    return jsonify({
        'success': success,
        'failed': failed,
        'errors': errors
    })


@flask_app.route('/api/rename/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def rename_item(alias):
    # 权限检查已由装饰器处理

    old_name = request.form.get('old_name')
    new_name = request.form.get('new_name')
    current_path = request.form.get('current_path')
    if not current_path or not old_name or not new_name:
        return "Missing required parameters", 400
    is_dir = request.form.get('is_dir') == 'true'
    current_path = urllib.parse.unquote(current_path)
    old_name = urllib.parse.unquote(old_name)
    new_name = urllib.parse.unquote(new_name)

    # 如果是重命名文件夹，验证新名称
    if is_dir:
        validation_error = validate_folder_name(new_name)
        if validation_error:
            return validation_error, 400

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    # 构建路径
    path_parts = current_path.strip('/').split('/')
    sub_path = '/'.join(path_parts[2:]) if len(path_parts) > 1 else ''
    old_path = os.path.join(dir_obj.path, sub_path, old_name)
    if not os.path.exists(old_path):
        return "Target directory not found", 404
    new_path = os.path.join(dir_obj.path, sub_path, new_name)

    try:
        if os.path.exists(new_path):
            return "目标名称已存在", 400
        os.rename(old_path, new_path)
        client_info = get_client_info()
        op_path = os.path.join(dir_obj.path, sub_path)
        flask_app.logger.info(f"{client_info} 在 {op_path} 重命名: {old_name} -> {new_name}")
        return "Success", 200
    except OSError as e:
        return str(e), 400


# 移动文件所需路由
@flask_app.route('/api/directories/<path:alias>')
@check_auth_timestamp
@check_directory_admin_permission
def get_directories(alias):
    """移动文件获取目录结构的端点"""
    # 权限检查已由装饰器处理

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    directories = []

    def scan_directories(path, relative_path=''):
        for item in os.scandir(path):
            if item.is_dir():
                dir_path = f"/dir/{alias}/{relative_path}/{item.name}".replace('//', '/')
                directories.append({
                    'name': f"/{relative_path}/{item.name}".replace('//', '/'),
                    'path': dir_path
                })
                scan_directories(item.path, f"{relative_path}/{item.name}".replace('//', '/'))

    scan_directories(dir_obj.path)
    return jsonify(directories)


# 移动文件所需路由
@flask_app.route('/api/move/<path:alias>', methods=['POST'])
@check_directory_admin_permission
def move_items(alias):
    # 权限检查已由装饰器处理

    data = request.json
    items = data.get('items', [])
    target_path = data.get('target_path', '')
    current_path = data.get('current_path', '')

    # 添加调试日志
    flask_app.logger.debug(f"Received data: {data}")
    flask_app.logger.debug(f"Items: {items}")

    if not items or not target_path:
        return "Missing required parameters", 400

    target_path = urllib.parse.unquote(target_path)
    current_path = urllib.parse.unquote(current_path)

    dir_obj = get_dir_obj(alias)
    if not dir_obj:
        return "Directory not found", 404

    # 修改路径处理方式
    path_parts = target_path.strip('/').split('/')
    target_subpath = path_parts[2:] if len(path_parts) > 2 else []
    target_dir = os.path.join(dir_obj.path, *target_subpath) if target_subpath else dir_obj.path

    if not os.path.exists(target_dir):
        return "Target directory not found", 404

    try:
        client_info = get_client_info()
        for item in items:
            # 获取文件名和路径
            item_name = item.get('name')
            item_path = item.get('path')

            # 处理name和path可能是列表的情况
            if isinstance(item_name, list):
                item_name = item_name[0]
            if isinstance(item_path, list):
                item_path = item_path[0]

            # 构建源路径
            source_parts = current_path.strip('/').split('/')
            source_subpath = '/'.join(source_parts[2:]) if len(source_parts) > 2 else ''
            source_path = os.path.join(dir_obj.path, source_subpath, item_name)

            # 构建目标路径
            dest_path = os.path.join(target_dir, item_name)

            # 检查目标路径是否存在
            if os.path.exists(dest_path):
                return f"File {item_name} already exists in target directory", 409

            # 检查源文件是否存在
            if not os.path.exists(source_path):
                return f"Source file {item_name} not found", 404

            # 执行移动操作
            shutil.move(source_path, dest_path)
            flask_app.logger.info(f"{client_info} 移动了{item_name} 从 {current_path} 到 {target_path}")

        return "Success", 200

    except Exception as e:
        flask_app.logger.error(f"移动文件错误: {str(e)}")
        return str(e), 500


def _start_totp_flow(scope, dirname=None):
    """记录待验证的TOTP会话，并跳转到对应的验证页。scope: 'admin' 或 'dir_admin'"""
    session['pending_2fa'] = {'scope': scope, 'dirname': dirname, 'time': time.time()}
    if scope == 'admin':
        return redirect(url_for('admin_2fa'))
    return redirect(url_for('dir_admin_2fa'))


def _verify_totp(secret, code):
    """校验TOTP验证码。secret为空时不校验。"""
    if not secret:
        return True
    try:
        totp = pyotp.TOTP(secret)
        return bool(code) and totp.verify(code)
    except Exception:
        return False


@flask_app.route('/admin/login', methods=['POST'])
@check_ip_limit
def admin_login():
    client_info = f"{request.remote_addr}"
    password = request.form.get('password', '')
    code = (request.form.get('code', '') or '').strip()
    totp_enabled = bool(config.admin_totp_secret)
    totp_only = bool(getattr(config, 'admin_totp_only', False))

    if totp_enabled and totp_only:
        # 仅凭验证码登录
        if _verify_totp(config.admin_totp_secret, code):
            session['admin'] = True
            session['admin_time'] = time.time()
            ip_limiter.reset(client_info)
            flask_app.logger.info(f"{client_info} 管理员TOTP免密登录成功")
            return redirect(request.referrer or url_for('index'))
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 管理员TOTP免密登录失败")
        return 'Invalid verification code', 401

    if password != config.admin_password:
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 管理员登录失败")
        return 'Invalid password', 401

    if totp_enabled:
        # 两步验证：需密码+验证码同时正确
        if _verify_totp(config.admin_totp_secret, code):
            session['admin'] = True
            session['admin_time'] = time.time()
            ip_limiter.reset(client_info)
            flask_app.logger.info(f"{client_info} 管理员登录成功(含TOTP)")
            return redirect(request.referrer or url_for('index'))
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 管理员TOTP验证失败")
        return 'Invalid verification code', 401

    # 密码正确且未启用TOTP，直接登录
    session['admin'] = True
    session['admin_time'] = time.time()
    ip_limiter.reset(client_info)  # 登录成功后重置计数
    flask_app.logger.info(f"{client_info} 管理员登录成功")
    return redirect(request.referrer or url_for('index'))  # 优先跳转到来源页面


@flask_app.route('/admin/logout')
def admin_logout():
    client_info = f"{request.remote_addr}"
    session.pop('admin', None)
    flask_app.logger.info(f"{client_info} 管理员退出登录")
    return redirect(url_for('index'))


@flask_app.route('/dir-admin/login', methods=['POST'])
@check_ip_limit
def dir_admin_login():
    """目录管理员登录"""
    client_info = f"{request.remote_addr}"
    password = request.form.get('password', '')
    dirname = request.form.get('dirname')
    code = (request.form.get('code', '') or '').strip()

    if not dirname:
        return 'Missing parameters', 400

    # 查找对应的目录配置
    dir_obj = get_dir_obj(dirname)

    if not dir_obj:
        return 'Directory not found', 404

    totp_secret = getattr(dir_obj, 'totp_secret', '')
    totp_only = bool(getattr(dir_obj, 'totp_only', False))

    if totp_secret and totp_only:
        # 仅凭验证码登录
        if _verify_totp(totp_secret, code):
            session[f'dir_admin_{dirname}'] = True
            session[f'dir_admin_time_{dirname}'] = time.time()
            ip_limiter.reset(client_info)
            flask_app.logger.info(f"{client_info} 目录管理员TOTP免密登录成功: {dirname}")
            return redirect(request.referrer or url_for('list_dir', dirname=dirname))
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 目录管理员TOTP免密登录失败: {dirname}")
        return 'Invalid verification code', 401

    # 检查密码
    if not (dir_obj.admin_password and (password == dir_obj.admin_password or password == config.admin_password)):
        # 记录失败次数
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 目录管理员登录失败: {dirname}")
        return 'Invalid password', 401

    if totp_secret:
        # 两步验证：需密码+验证码同时正确
        if _verify_totp(totp_secret, code):
            session[f'dir_admin_{dirname}'] = True
            session[f'dir_admin_time_{dirname}'] = time.time()
            ip_limiter.reset(client_info)  # 登录成功后重置计数
            flask_app.logger.info(f"{client_info} 目录管理员登录成功(含TOTP): {dirname}")
            return redirect(request.referrer or url_for('list_dir', dirname=dirname))
        ip_limiter.add_failed_attempt(client_info)
        flask_app.logger.warning(f"{client_info} 目录管理员TOTP验证失败: {dirname}")
        return 'Invalid verification code', 401

    session[f'dir_admin_{dirname}'] = True
    session[f'dir_admin_time_{dirname}'] = time.time()
    ip_limiter.reset(client_info)  # 登录成功后重置计数
    flask_app.logger.info(f"{client_info} 目录管理员登录成功: {dirname}")
    return redirect(request.referrer or url_for('list_dir', dirname=dirname))


@flask_app.route('/dir-admin/logout/<dirname>')
def dir_admin_logout(dirname):
    """目录管理员退出登录"""
    client_info = f"{request.remote_addr}"
    session.pop(f'dir_admin_{dirname}', None)
    flask_app.logger.info(f"{client_info} 目录管理员退出登录: {dirname}")
    return redirect(url_for('list_dir', dirname=dirname))


@flask_app.route('/admin/2fa', methods=['GET', 'POST'])
@check_ip_limit
def admin_2fa():
    """超级管理员TOTP两步验证"""
    client_info = f"{request.remote_addr}"
    pending = session.get('pending_2fa')
    if not pending or pending.get('scope') != 'admin':
        return render_template('error.html', error_code=401, message="请先输入管理密码", pageMark='两步验证'), 401

    if request.method == 'GET':
        return render_template('totp_verify.html', pageMark='两步验证',
                               scope='admin', dirname=None)

    code = request.form.get('code', '').strip()
    if _verify_totp(config.admin_totp_secret, code):
        session.pop('pending_2fa', None)
        session['admin'] = True
        session['admin_time'] = time.time()
        ip_limiter.reset(client_info)
        flask_app.logger.info(f"{client_info} 管理员TOTP验证成功")
        return redirect(request.referrer or url_for('index'))
    ip_limiter.add_failed_attempt(client_info)
    flask_app.logger.warning(f"{client_info} 管理员TOTP验证失败")
    return render_template('totp_verify.html', pageMark='两步验证',
                           scope='admin', dirname=None, error='验证码错误或已过期'), 401


@flask_app.route('/dir-admin/2fa', methods=['GET', 'POST'])
@check_ip_limit
def dir_admin_2fa():
    """目录管理员TOTP两步验证"""
    client_info = f"{request.remote_addr}"
    pending = session.get('pending_2fa')
    if not pending or pending.get('scope') != 'dir_admin':
        return render_template('error.html', error_code=401, message="请先输入目录管理密码", pageMark='两步验证'), 401

    dirname = pending.get('dirname')

    if request.method == 'GET':
        return render_template('totp_verify.html', pageMark='两步验证',
                               scope='dir_admin', dirname=dirname)

    code = request.form.get('code', '').strip()
    dir_obj = get_dir_obj(dirname)
    if dir_obj and _verify_totp(getattr(dir_obj, 'totp_secret', ''), code):
        session.pop('pending_2fa', None)
        session[f'dir_admin_{dirname}'] = True
        session[f'dir_admin_time_{dirname}'] = time.time()
        ip_limiter.reset(client_info)
        flask_app.logger.info(f"{client_info} 目录管理员TOTP验证成功: {dirname}")
        return redirect(request.referrer or url_for('list_dir', dirname=dirname))
    ip_limiter.add_failed_attempt(client_info)
    flask_app.logger.warning(f"{client_info} 目录管理员TOTP验证失败: {dirname}")
    return render_template('totp_verify.html', pageMark='两步验证',
                           scope='dir_admin', dirname=dirname, error='验证码错误或已过期'), 401


@flask_app.route('/api/directory', methods=['POST'])
def add_directory_api():
    if not session.get('admin'):
        return 'Unauthorized', 403

    data = request.form
    alias = data.get('alias', '').strip()
    path = data.get('path', '').strip()
    path = urllib.parse.unquote(path)
    # 验证目标路径是否存在
    if not os.path.exists(path):
        return "Target directory not found", 404

    if not alias:
        return 'Alias cannot be empty', 400

    secure_alias = secure_filename(alias)
    if not secure_alias:
        return 'Invalid alias name', 400

    client_info = f"{request.remote_addr}"
    dir_obj = ShareDirectory(
        path,
        secure_alias,
        data.get('password', ''),
        data.get('desc', ''),
        data.get('admin_password', '')  # 新增：处理目录管理密码
    )

    config.shared_dirs[dir_obj.name] = dir_obj
    config.save()
    flask_app.logger.info(f"{client_info} 添加了新共享目录: {dir_obj.alias} ({dir_obj.path})")
    return 'Success', 200


@flask_app.route('/static/logos/<filename>')
def serve_logo(filename):
    """专门处理logos目录的静态文件访问"""
    try:
        # 使用程序运行目录的logos文件夹
        logos_dir = os.path.join(get_app_path(), 'static', 'logos')
        file_path = os.path.join(logos_dir, filename)

        # 安全检查，防止路径遍历攻击
        if not os.path.abspath(file_path).startswith(os.path.abspath(logos_dir)):
            return "Access denied", 403

        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path)
        else:
            return "Logo not found", 404
    except Exception as e:
        flask_app.logger.error(f"Error serving logo {filename}: {e}")
        return "Error serving logo", 500


@flask_app.route('/api/upload-logo', methods=['POST'])
def upload_logo():
    """上传logo图片"""
    if not session.get('admin'):
        return jsonify({'error': '未授权访问'}), 403

    if 'logo' not in request.files:
        return jsonify({'error': '没有选择文件'}), 400

    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    # 检查文件类型
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}
    if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
        return jsonify({'error': '不支持的文件格式，请使用PNG、JPG、JPEG、GIF或BMP格式'}), 400

    try:
        # 生成唯一文件名
        import time
        filename = secure_filename(file.filename)
        name, ext = os.path.splitext(filename)
        timestamp = str(int(time.time()))
        new_filename = f"{name}_{timestamp}{ext}"

        # 使用配置中的logo目录（程序运行目录）
        logos_dir = config.logo_dir
        os.makedirs(logos_dir, exist_ok=True)

        # 保存文件
        file_path = os.path.join(logos_dir, new_filename)
        file.save(file_path)

        # 清理旧的logo文件
        from main import cleanup_old_logos
        cleanup_old_logos(logos_dir, new_filename)

        # 返回相对路径
        relative_path = f"logos/{new_filename}"
        return jsonify({'success': True, 'path': relative_path})

    except Exception as e:
        return jsonify({'error': f'上传失败: {str(e)}'}), 500


@flask_app.route('/api/page-settings', methods=['GET', 'POST'])
def page_settings():
    """页面设置API"""
    if not session.get('admin'):
        return jsonify({'error': '未授权访问'}), 403

    if request.method == 'GET':
        # 获取当前页面设置
        return jsonify({
            'page_title': config.page_title,
            'logo_name': config.logo_name,
            'logo_image_url': config.logo_image_url
        })

    elif request.method == 'POST':
        # 更新页面设置
        data = request.json

        page_title = data.get('page_title', '').strip()
        logo_name = data.get('logo_name', '').strip()
        logo_image_url = data.get('logo_image_url', '').strip()

        if not page_title:
            return jsonify({'error': '页面标题不能为空'}), 400

        if not logo_name:
            return jsonify({'error': 'Logo名称不能为空'}), 400

        # 如果logo_image_url是远程URL或为空，清理本地logo文件
        if not logo_image_url or logo_image_url.startswith(('http://', 'https://')):
            from main import cleanup_old_logos
            cleanup_old_logos(config.logo_dir)

        # 更新配置
        config.page_title = page_title
        config.logo_name = logo_name
        config.logo_image_url = logo_image_url

        # 保存配置
        config.save()

        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 更新了页面设置")

        return jsonify({'success': True, 'message': '页面设置已更新'})


@flask_app.route('/api/directory/<alias>', methods=['GET', 'PUT', 'DELETE'])
def manage_directory(alias):
    if not session.get('admin'):
        return 'Unauthorized', 403

    client_info = f"{request.remote_addr}"

    if request.method == 'GET':
        # 获取目录详细信息
        for name, dir_obj in config.shared_dirs.items():
            if dir_obj.alias == alias:
                return jsonify({
                    'alias': dir_obj.alias,
                    'password': dir_obj.password,
                    'admin_password': dir_obj.admin_password,
                    'desc': dir_obj.desc,
                    'path': dir_obj.path
                })
        return 'Directory not found', 404

    if request.method == 'DELETE':
        for name, dir_obj in config.shared_dirs.items():
            if dir_obj.alias == alias:
                path = dir_obj.path
                del config.shared_dirs[name]
                config.save()
                flask_app.logger.info(f"{client_info} 删除了目录: {alias} ({path})")
                token_to_remove = []
                for token, data in share_manager.share_links.items():   # 获取键（token）和值（data）
                    if data.alias == alias:
                        token_to_remove.append(token)  # 记录需要删除的键
                # for data in share_manager.share_links.values():     #.values() 返回字典中所有值的集合
                #     if data.alias == alias:
                #         token_to_remove.append(token)  # 记录需要删除的键
                # 遍历结束后删除键值对
                if token_to_remove:
                    for token in token_to_remove:
                        del share_manager.share_links[token]
                        flask_app.logger.info(f"自动删除{alias}对应的分享:  ({token})")
                    share_manager.save()
                return 'Success', 200

    elif request.method == 'PUT':
        data = request.form
        new_alias = data.get('alias', '').strip()

        if not new_alias:
            return 'Alias cannot be empty', 400

        secure_alias = secure_filename(new_alias)
        if not secure_alias:
            return 'Invalid alias name', 400

        for name, dir_obj in config.shared_dirs.items():
            if dir_obj.alias == alias:
                if dir_obj.password != data.get('password', ''):
                    password_change_timestamps['directories'][alias] = time.time()
                old_alias = dir_obj.alias
                old_password = "有密码" if dir_obj.password else "无密码"

                dir_obj.alias = secure_alias
                dir_obj.desc = data.get('desc', dir_obj.desc)
                dir_obj.password = data.get('password', '')
                dir_obj.admin_password = data.get('admin_password', '')  # 新增：处理目录管理密码
                new_password = "有密码" if dir_obj.password else "无密码"

                config.save()
                flask_app.logger.info(
                    f"{client_info} 修改了目录 {dir_obj.path}: {old_alias}->{dir_obj.alias}, {old_password}->{new_password}")
                # 处理同步修改分享链接里面的alias
                token_to_modify = []
                for token, data in share_manager.share_links.items():   # 获取键（token）和值（data）
                    if data.alias == alias:
                        token_to_modify.append(token)  # 记录需要修改的键
                if token_to_modify:
                    for token in token_to_modify:
                        share = share_manager.get_share_no_reload(token)
                        share.alias = secure_alias
                        flask_app.logger.info(f"自动修改 ({token}) 的新目录为 {secure_alias}")
                    share_manager.save()
                return 'Success', 200

    return 'Directory not found', 404


@flask_app.route('/api/settings', methods=['POST'])
def update_settings():
    if not session.get('admin'):
        return 'Unauthorized', 403

    data = request.form
    client_info = f"{request.remote_addr}"

    # 检查全局密码变更
    if config.global_password != data.get('global_password', ''):
        password_change_timestamps['global'] = time.time()

    # 只有当传入的管理员密码非空时才进行修改
    new_admin_password = data.get('admin_password', '')
    if new_admin_password and config.admin_password != new_admin_password:
        password_change_timestamps['admin'] = time.time()
        config.admin_password = new_admin_password

    config.global_password = data.get('global_password', '')

    # 超级管理员 TOTP 两步验证
    totp_enabled = data.get('admin_totp_enabled')
    if totp_enabled:
        totp_secret = (data.get('admin_totp_secret', '') or '').strip()
        if not totp_secret:
            totp_secret = pyotp.random_base32()
        config.admin_totp_secret = totp_secret
    else:
        config.admin_totp_secret = ''

    # 会话空闲超时（秒），0=禁用；非法值回退默认600
    try:
        config.session_timeout = max(0, int(data.get('session_timeout', config.session_timeout)))
    except (TypeError, ValueError):
        config.session_timeout = getattr(config, 'session_timeout', 600)

    config.save()

    flask_app.logger.info(f"{client_info} 更新了系统设置")
    return 'Success', 200


@flask_app.route('/api/totp', methods=['GET'])
def get_admin_totp():
    """返回超级管理员TOTP设置状态（需管理员登录）"""
    if not session.get('admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    secret = getattr(config, 'admin_totp_secret', '') or ''
    return jsonify({
        'enabled': bool(secret),
        'secret': secret,
        'link': f"https://2fa.it0731.cn/tok/{secret}" if secret else ''
    })


@flask_app.route('/api/create-share', methods=['POST'])
@check_auth_timestamp
def create_share():
    path = request.json.get('path')
    password = request.json.get('password')
    manage_code = request.json.get('manage_code')
    desc = request.json.get('desc')
    expire_days = request.json.get('expire_days', '7')

    base_dir = path.split('/')[0]
    dir_obj = get_dir_obj(base_dir)

    if not dir_obj:
        return jsonify({'error': '目录不存在'}), 404

    full_path = os.path.join(dir_obj.path, *path.split('/')[1:])
    name = os.path.basename(path)

    share = share_manager.create_share(full_path, dir_obj.alias, name, password, expire_days, manage_code, desc)  # 实例化调用
    # 假如我不进行share_manager = ShareManager()实例化，那么我就要在方法处给他加上装饰器@staticmethod
    # share = ShareManager.create_share(full_path, name, password, expire_days) #装饰器静态方法调用
    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 新建了分享 {share.token}")
    return jsonify({'share_url': f'/s/{share.token}'})


@flask_app.route('/s/<token>')
@flask_app.route('/s/<token>/<path:subpath>')
@check_auth_timestamp
def access_share(token, subpath=''):
    token = token.strip().split(' ')[0]   # 去掉传来参数空格后面的内容
    share = share_manager.get_share(token)
    if not share:
        return render_template('error.html', message='分享链接不存在', pageMark='分享链接不存在')

    if share.expire_time and share.expire_time < datetime.now():
        return render_template('error.html', message='分享链接已过期', pageMark='分享链接已过期')

    if share.password and not session.get(f'share_auth_{token}'):
        return render_template('share_password.html', token=token, pageMark='访问密码')

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 通过 {token} 访问了 {subpath}")

    return render_template('share_view.html',
                        pageMark=f'{share.name}-分享链接',
                         share=share,
                         token=token,
                         current_path=subpath,
                         contents=share_manager.list_directory_contents(share.path, subpath) if share.is_dir else None)


@flask_app.route('/s/<token>/verify', methods=['POST'])
@check_ip_limit
def verify_share(token):
    share = share_manager.get_share(token)
    client_info = f"{request.remote_addr}"

    if not share:
        return render_template('error.html', message='分享链接不存在', pageMark='分享链接不存在')

    if share.password == request.form.get('password') or config.admin_password == request.form.get('password'):
        session[f'share_auth_{token}'] = True
        ip_limiter.reset(client_info)  # 登录成功后重置计数
        # 设置认证时间为当前时间，确保大于密码修改时间戳
        session[f'share_auth_time_{token}'] = time.time()
        # 如果这个token还没有时间戳，初始化一个
        if f'share_{token}' not in password_change_timestamps['shares']:
            password_change_timestamps['shares'][token] = 0
        return redirect(url_for('access_share', token=token))

    # 记录失败次数
    ip_limiter.add_failed_attempt(client_info)
    return render_template('share_password.html', token=token, error='密码错误', pageMark='访问密码')


@flask_app.route('/api/manage_share/<token>/<manage_code>/<opcode>', methods=['POST'])
@check_auth_timestamp
@check_ip_limit
def manage_share(token, manage_code, opcode):
    # 获取 JSON 数据
    data = request.get_json()

    share = share_manager.get_share(token)
    client_info = f"{request.remote_addr}"
    if not share:
        return 'Share not found', 404

    if share.manage_code != manage_code and manage_code != config.admin_password:
        ip_limiter.add_failed_attempt(client_info)
        return '管理密码错误', 403

    ip_limiter.reset(client_info)

    if opcode == 'delete':
        del share_manager.share_links[token]
        share_manager.save()

    elif opcode == 'password':
        new_password = data.get('password')
        share.password = new_password
        password_change_timestamps['shares'][token] = time.time()
        share_manager.save()

    elif opcode == 'expire':
        expire_days = data.get('expire_days')
        share.expire_time = datetime.now() + timedelta(days=int(expire_days)) if int(expire_days) > 0 else None
        share_manager.save()

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 使用管理码操作了分享 {token}")

    return 'Success', 200


@flask_app.route('/s/<token>/file/<path:filepath>')
@check_auth_timestamp
def download_share_file(token, filepath):
    share = share_manager.get_share(token)
    if not share:
        return "分享不存在", 404

    if share.password and not session.get(f'share_auth_{token}'):
        return redirect(url_for('access_share', token=token))

    filepath = urllib.parse.unquote(filepath)
    file_path = os.path.join(share.path, filepath) if share.is_dir else share.path

    if not os.path.abspath(file_path).startswith(os.path.abspath(share.path if share.is_dir else os.path.dirname(share.path))):
        return "Access denied", 403

    if not os.path.isfile(file_path):
        return "文件不存在", 404

    filename = os.path.basename(file_path)
    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 下载了分享文件: {filename}")

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        conditional=True  # 启用断点续传支持
    )


@flask_app.route('/s/<token>/download')
@check_auth_timestamp
def download_share(token):
    share = share_manager.get_share(token)
    if not share:
        return "分享不存在", 404

    if share.password and not session.get(f'share_auth_{token}'):
        return redirect(url_for('access_share', token=token))

    if share.is_dir:
        temp_zip = share_manager.create_zip_from_dir(share.path)
        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 打包下载了整个目录 {share.name}")
        return send_file(
            temp_zip,
            as_attachment=True,
            download_name=f"{share.name}.zip",
            mimetype='application/zip',
            conditional=True
        )
    else:
        return send_file(
            share.path,
            as_attachment=True,
            download_name=share.name,
            conditional=True
        )


@flask_app.route('/s/<token>/batch-download', methods=['POST'])
@check_auth_timestamp
def share_batch_download(token):
    share = share_manager.get_share(token)
    if not share:
        return "分享不存在", 404

    if share.password and not session.get(f'share_auth_{token}'):
        return redirect(url_for('access_share', token=token))

    items = request.json.get('items', [])
    temp_zip = tempfile.NamedTemporaryFile(prefix='file_share_', suffix='.zip', delete=False)

    # 使用 ZIP_DEFLATED 压缩方式创建zip文件
    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            full_path = os.path.join(share.path, item['path'])
            if not os.path.exists(full_path):
                continue

            if item['is_dir']:
                base_name = item['name']
                for root, _, files in os.walk(full_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 计算相对路径作为zip内路径
                        rel_path = os.path.relpath(file_path, os.path.dirname(full_path))
                        arcname = os.path.join(base_name, rel_path)
                        zf.write(file_path, arcname)
            else:
                # 对于单个文件，直接使用文件名
                zf.write(full_path, item['name'])

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 打包下载了多个文件")

    return send_file(
        temp_zip.name,
        mimetype='application/zip',
        as_attachment=True,
        download_name='selected_files.zip',
        conditional=True
    )


@flask_app.route('/api/upload/status/<file_id>')
def check_upload_status(file_id):
    temp_dir = os.path.join(config.upload_temp_dir, file_id)
    uploaded_chunks = len(os.listdir(temp_dir)) if os.path.exists(temp_dir) else 0
    return jsonify({'uploaded_chunks': uploaded_chunks})


@flask_app.errorhandler(404)
def page_not_found(error):
    # error 是 werkzeug.exceptions.NotFound 对象
    # 直接使用固定的友好提示文本更合适
    message = '众里寻她千百度，蓦然回首，那页却在灯火阑珊处。'
    return render_template('error.html', message=message, pageMark='灯火阑珊'), 404  # 添加状态码404


@flask_app.route('/api/clear_session/<token>/<security_code>')
def clear_session(token, security_code):
    if security_code == config.security_code:
        # 只更新时间戳即可
        password_change_timestamps['shares'][token] = time.time()

        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 更新了分享 {token} 的认证时间戳")
        return 'success'
    return 'invalid security code'


# web管理私有分享链接相关
@flask_app.route('/share-manager')
@flask_app.route('/share-manager/<path>')
def share_manager_page(path=''):
    if not session.get('admin'):
        return redirect(url_for('index'))
    if path:
        print(path)
        return render_template('share_manager.html', share_path=path, pageMark='私有分享管理')
    return render_template('share_manager.html', pageMark='私有分享管理')


@flask_app.route('/api/shares')
def list_shares():
    if not session.get('admin'):
        return 'Unauthorized', 403

    token = request.args.get('token', '').strip()
    name = request.args.get('name', '').strip()
    date_start = request.args.get('date_start')
    date_end = request.args.get('date_end')
    share_path = request.args.get('share_path', '').strip()

    shares = []
    for share in share_manager.share_links.values():
        # Apply filters
        if share_path and share_path.lower() not in share.alias.lower():
            continue
        if token and token not in share.token:
            continue
        if name and name.lower() not in share.name.lower():
            continue
        if date_start:
            start_date = datetime.strptime(date_start, '%Y-%m-%d')
            if share.create_time < start_date:
                continue
        if date_end:
            end_date = datetime.strptime(date_end, '%Y-%m-%d') + timedelta(days=1)
            if share.create_time > end_date:
                continue

        shares.append({
            'token': share.token,
            'name': share.name,
            'size': share.size,
            'password': share.password,
            'manage_code': share.manage_code,
            'expire_time': share.expire_time.isoformat() if share.expire_time else None,
            'create_time': share.create_time.isoformat(),
            'desc': share.desc
        })

    return jsonify(shares)


@flask_app.route('/api/shares/<token>', methods=['PUT'])
def update_share(token):
    if not session.get('admin'):
        return 'Unauthorized', 403

    share = share_manager.get_share(token)
    if not share:
        return 'Share not found', 404

    data = request.json
    share.name = data['name']
    share.password = data['password'] or None
    share.manage_code = data['manage_code']
    share.desc = data['desc']

    if data['expire_time']:
        share.expire_time = datetime.fromisoformat(data['expire_time'])
    else:
        share.expire_time = None

    share_manager.save()
    return 'Success', 200


@flask_app.route('/api/shares/<token>', methods=['DELETE'])
def delete_share(token):
    if not session.get('admin'):
        return 'Unauthorized', 403

    del share_manager.share_links[token]

    share_manager.save()
    return 'Success', 200


@flask_app.route('/api/shares/batch-delete', methods=['POST'])
def batch_delete_shares():
    if not session.get('admin'):
        return 'Unauthorized', 403

    tokens = request.json.get('tokens', [])
    for token in tokens:
        if token in share_manager.share_links:
            del share_manager.share_links[token]

    share_manager.save()
    return 'Success', 200


@flask_app.route('/api/shares/clear-all', methods=['POST'])
def clear_all_shares():
    if not session.get('admin'):
        return 'Unauthorized', 403

    share_manager.share_links.clear()
    share_manager.save()
    return 'Success', 200


@flask_app.route('/api/shares/clear-expired', methods=['POST'])
def clear_expired_shares():
    if not session.get('admin'):
        return 'Unauthorized', 403

    share_manager.remove_expired()
    return 'Success', 200


# 模板过滤规则设置 "D:/tools/test" -> "tools" 或 "D:/" -> "D"
@flask_app.template_filter('extract_first_dir')
def extract_first_dir(path):
    # Replace backslashes with forward slashes and split by '/'
    parts = [p for p in path.replace('\\', '/').split('/') if p]

    # 如果有路径部分
    if parts:
        # 如果第一个部分包含冒号，表示这是一个驱动器号
        if ':' in parts[0]:
            # 如果有额外的部分，则返回驱动器号后的第一个目录
            if len(parts) > 1:
                return parts[1]
            # 如果没有额外的部分，则返回驱动器号的字母部分
            return parts[0][0]
        # 如果路径部分没有冒号，直接返回第一个部分
        return parts[0]

    # 返回空字符串如果没有路径部分
    return ''
