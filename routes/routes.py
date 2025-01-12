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
    get_client_info, secure_filename_cn, ShareDirectory, password_change_timestamps
from share_links import ShareManager   # 这个文件被全部引入了main.py main.py已经引入了这个，所以注释
from firewall import IPLimiter

# 实例化 share_links/share_manager.py 里面的 ShareManager
share_manager = ShareManager()    # 这个文件被全部引入了main.py main.py已经引入了这个，所以注释
ip_limiter = IPLimiter()


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
        dir_config = next((d for d in config.shared_dirs.values() if getattr(d, 'alias', None) == dirname), None)

        # 检查目录是否需要认证
        if dir_config and getattr(dir_config, 'password', None):
            dir_auth_time = session.get(f'auth_time_{dirname}', 0)
            if dirname in password_change_timestamps['directories'] and dir_auth_time < password_change_timestamps[
                'directories'].get(dirname, 0):
                session.pop(f'auth_{dirname}', None)
                return render_template('directory_password.html', alias=dirname, pageMark=f'{dirname}访问密码')

        return f(*args, **kwargs)
    return decorated_function


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
    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == base_dir), None)

    if not dir_obj:
        return '目录不存在', 404

    full_path = os.path.join(dir_obj.path, *filepath.split('/')[1:])

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


# 使用 Flask 的 context_processor 或 before_request 钩子来全局传递 themes 变量
@flask_app.context_processor
def inject_themes():
    return {'themes': get_themes()}


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


@flask_app.route('/')
@check_auth_timestamp  # 添加装饰器 用于实时修改密码后的一个校验新密码
def index():
    # 检查全局密码验证
    if config.global_password and not session.get('auth'):
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
    return render_template('index.html', dirs=dirs, pageMark=f'首页')


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
        dir_obj = None
        for d in config.shared_dirs.values():
            if d.alias == alias:
                dir_obj = d
                break

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
    dir_obj = None
    base_dir = dirname.split('/')[0]
    # 添加详细日志
    # flask_app.logger.info(f"访问目录: {dirname}")
    # flask_app.logger.info(f"Session状态: {session}")
    for d in config.shared_dirs.values():
        if d.alias == base_dir:
            dir_obj = d
            break
    for d in config.shared_dirs.values():
        if d.alias == base_dir:
            dir_obj = d
            break

    # 添加详细打印
    # flask_app.logger.info(f"目录对象详情:")
    # flask_app.logger.info(f"类型: {type(dir_obj)}")
    # flask_app.logger.info(f"属性: {vars(dir_obj)}")
    # flask_app.logger.info(f"密码值: {dir_obj.password}")

    if not dir_obj:
        return render_template('error.html',
                               error_code=404,
                               message="找不到相关目录或文件", pageMark=f'找不到相关目录或文件'), 404

    if dir_obj.password and not session.get(f'auth_{base_dir}'):
        return render_template('directory_password.html', alias=base_dir, pageMark=f'{base_dir}访问密码')

    sub_path = dirname.split('/')[1:]
    current_path = os.path.join(dir_obj.path, *sub_path)

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
        dir_info = next((d for d in config.shared_dirs.values() if d.alias == part), None)
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
                           dir_obj=dir_obj, pageMark=f'{base_dir}目录浏览')


@flask_app.route('/api/search/<alias>')
@check_auth_timestamp
def search_files(alias):
    search_term = request.args.get('term', '').lower()

    # 从config获取目录对象
    dir_obj = None
    for d in config.shared_dirs.values():
        if d.alias == alias:
            dir_obj = d
            break

    if not dir_obj:
        return jsonify({'error': 'Directory not found'}), 404

    # 检查目录访问权限
    if dir_obj.password and not session.get(f'auth_{alias}'):
        return jsonify({'error': 'Authentication required'}), 403

    results = []
    # 使用和list_dir相同的遍历逻辑
    for root, dirs, files in os.walk(dir_obj.path):
        for dir_name in dirs:
            if search_term in dir_name.lower():
                rel_path = os.path.relpath(os.path.join(root, dir_name), dir_obj.path)
                full_path = os.path.join(alias, rel_path).replace('\\', '/')
                results.append({
                    'name': dir_name,
                    'is_dir': True,
                    'path': full_path
                })

        for file_name in files:
            if search_term in file_name.lower():
                rel_path = os.path.relpath(os.path.join(root, file_name), dir_obj.path)
                full_path = os.path.join(alias, rel_path).replace('\\', '/')
                results.append({
                    'name': file_name,
                    'is_dir': False,
                    'size': format_file_size(os.path.getsize(os.path.join(root, file_name))),
                    'path': full_path
                })

    return jsonify(results)


@flask_app.route('/preview/<path:filepath>')
@check_auth_timestamp
def preview_file(filepath):
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
    temp_zip = tempfile.NamedTemporaryFile(prefix='file_share_', suffix='.zip', delete=False)

    with zipfile.ZipFile(temp_zip.name, 'w') as zf:
        for file in files:
            base_dir = file['path'].split('/')[0]
            dir_obj = next((d for d in config.shared_dirs.values() if d.alias == base_dir), None)
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

    dir_obj = None
    for d in config.shared_dirs.values():
        if d.alias == dirname:
            dir_obj = d
            break

    if not dir_obj:
        flask_app.logger.error(f"Directory not found: {dirname}")
        return "Directory not found", 404

    file_path = os.path.join(dir_obj.path, filename)

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
def upload_file(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

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

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
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

    filename = secure_filename_cn(filename)

    # 如果是普通上传（非分片）
    if int(chunks) == 1:
        file_path = os.path.join(target_dir, filename)
        file.save(file_path)
        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 上传文件: {filename} 到了{target_dir}")
        return "Success", 200

    # 处理分片上传
    temp_dir = os.path.join(config.upload_temp_dir, file_id)
    os.makedirs(temp_dir, exist_ok=True)

    # 保存分片
    chunk_file = os.path.join(temp_dir, f"chunk_{chunk_number}")
    file.save(chunk_file)

    # 检查是否所有分片都已上传
    uploaded_chunks = len(os.listdir(temp_dir))
    if uploaded_chunks == int(chunks):
        # 合并所有分片
        final_path = os.path.join(target_dir, filename)
        with open(final_path, 'wb') as target_file:
            for i in range(int(chunks)):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                with open(chunk_path, 'rb') as chunk:
                    target_file.write(chunk.read())

        # 清理临时文件
        shutil.rmtree(temp_dir)

        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 上传文件: {filename} 到了{target_dir}")
        return "Success", 200

    return jsonify({
        'uploaded_chunks': uploaded_chunks,
        'total_chunks': chunks
    })


@flask_app.route('/api/mkdir/<path:alias>', methods=['POST'])
def make_directory(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

    current_path = request.form.get('current_path')
    folder_name = request.form.get('name')

    if not current_path or not folder_name:
        return "Missing required parameters", 400

    current_path = urllib.parse.unquote(current_path)
    folder_name = urllib.parse.unquote(folder_name)

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
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
def delete_item(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

    name = request.form.get('name')
    current_path = request.form.get('current_path')
    if not current_path or not name:
        return "Missing required parameters", 400

    is_dir = request.form.get('is_dir') == 'true'
    current_path = urllib.parse.unquote(current_path)
    name = urllib.parse.unquote(name)

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
    if not dir_obj:
        return "Directory not found", 404

    # 构建目标路径
    path_parts = current_path.strip('/').split('/')
    sub_path = '/'.join(path_parts[2:]) if len(path_parts) > 1 else ''
    target_path = os.path.join(dir_obj.path, sub_path, name)

    try:
        if is_dir:
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


@flask_app.route('/api/rename/<path:alias>', methods=['POST'])
def rename_item(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

    old_name = request.form.get('old_name')
    new_name = request.form.get('new_name')
    current_path = request.form.get('current_path')
    if not current_path or not old_name or not new_name:
        return "Missing required parameters", 400
    is_dir = request.form.get('is_dir') == 'true'
    current_path = urllib.parse.unquote(current_path)
    old_name = urllib.parse.unquote(old_name)
    new_name = urllib.parse.unquote(new_name)

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
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
def get_directories(alias):
    """移动文件获取目录结构的端点"""
    if not session.get('admin'):
        return "Unauthorized", 403

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
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
def move_items(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

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

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
    if not dir_obj:
        return "Directory not found", 404

    # 修改路径处理方式
    path_parts = target_path.strip('/').split('/')
    target_subpath = path_parts[2:] if len(path_parts) > 2 else []
    target_dir = os.path.join(dir_obj.path, *target_subpath) if target_subpath else dir_obj.path

    if not os.path.exists(target_dir):
        return "Target directory not found", 404

    try:
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

        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 移动了文件从 {current_path} 到 {target_path}")
        return "Success", 200

    except Exception as e:
        flask_app.logger.error(f"Error moving files: {str(e)}")
        return str(e), 500


@flask_app.route('/admin/login', methods=['POST'])
@check_ip_limit
def admin_login():
    client_info = f"{request.remote_addr}"
    if request.form.get('password') == config.admin_password:
        session['admin'] = True
        ip_limiter.reset(client_info)  # 登录成功后重置计数
        flask_app.logger.info(f"{client_info} 管理员登录成功")
        return redirect(request.referrer or url_for('index'))  # 优先跳转到来源页面

    # 记录失败次数
    ip_limiter.add_failed_attempt(client_info)
    flask_app.logger.warning(f"{client_info} 管理员登录失败")
    return 'Invalid password', 401


@flask_app.route('/admin/logout')
def admin_logout():
    client_info = f"{request.remote_addr}"
    session.pop('admin', None)
    flask_app.logger.info(f"{client_info} 管理员退出登录")
    return redirect(url_for('index'))


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
        data['password'],
        data['desc']
    )

    config.shared_dirs[dir_obj.name] = dir_obj
    config.save()
    flask_app.logger.info(f"{client_info} 添加了新共享目录: {dir_obj.alias} ({dir_obj.path})")
    return 'Success', 200


@flask_app.route('/api/directory/<alias>', methods=['PUT', 'DELETE'])
def manage_directory(alias):
    if not session.get('admin'):
        return 'Unauthorized', 403

    client_info = f"{request.remote_addr}"

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
    config.save()

    flask_app.logger.info(f"{client_info} 更新了系统设置")
    return 'Success', 200


@flask_app.route('/api/create-share', methods=['POST'])
@check_auth_timestamp
def create_share():
    path = request.json.get('path')
    password = request.json.get('password')
    manage_code = request.json.get('manage_code')
    desc = request.json.get('desc')
    expire_days = request.json.get('expire_days', '7')

    base_dir = path.split('/')[0]
    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == base_dir), None)

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
    temp_dir = os.path.join(config.temp_path, file_id)
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
