import mimetypes
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime, timedelta
from functools import wraps

from flask import session, render_template, request, send_file, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

from firewall import IPLimiter
from main import flask_app, config, format_file_size, get_client_info, secure_filename_cn, ShareDirectory, \
    password_change_timestamps
from share_links import ShareManager

share_manager = ShareManager()
ip_limiter = IPLimiter()


def check_ip_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.remote_addr

        if ip_limiter.is_blocked(ip):
            remaining_time = ip_limiter.get_remaining_time(ip)
            return render_template('ip_blocked.html',
                                   remaining_time=remaining_time,
                                   pageMark='访问受限')

        return f(*args, **kwargs)

    return decorated_function


def check_auth_timestamp(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):

        if config.global_password and config.global_password.strip():
            if request.path.startswith('/s/'):
                return f(*args, **kwargs)
            user_auth_time = session.get('auth_time', 0)
            if user_auth_time < password_change_timestamps['global']:
                session.pop('auth', None)
                return render_template('global_password.html', alias='global', pageMark=f'全局密码')

        token = kwargs.get('token')
        if token:
            share = share_manager.get_share(token)
            if share and share.password:
                share_auth_key = f'share_auth_{token}'
                share_auth_time = session.get(f'share_auth_time_{token}', 0)
                if share_auth_time < password_change_timestamps['shares'].get(token, float('inf')):
                    session.pop(share_auth_key, None)
                    return render_template('share_password.html', token=token, pageMark=f'分享密码')

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

        dir_config = next((d for d in config.shared_dirs.values() if getattr(d, 'alias', None) == dirname), None)

        if dir_config and getattr(dir_config, 'password', None):
            dir_auth_time = session.get(f'auth_time_{dirname}', 0)
            if dirname in password_change_timestamps['directories'] and dir_auth_time < password_change_timestamps[
                'directories'].get(dirname, 0):
                session.pop(f'auth_{dirname}', None)
                return render_template('directory_password.html', alias=dirname, pageMark=f'{dirname}访问密码')

        return f(*args, **kwargs)

    return decorated_function


def cleanup_temp_files_and_expired_links():
    global cleanup_thread_running
    while cleanup_thread_running:

        if config.auto_cleanup:
            temp_dir = tempfile.gettempdir()
            now = time.time()
            for filename in os.listdir(temp_dir):

                if filename.startswith('file_share_') and filename.endswith('.zip'):
                    file_path = os.path.join(temp_dir, filename)
                    if os.path.isfile(file_path):
                        try:

                            os.remove(file_path)
                            flask_app.logger.info(f"删除临时文件: {file_path}")
                        except PermissionError as e:
                            flask_app.logger.info(f"无法删除文件 {file_path}: 文件正在使用中。错误: {e}")
                        except Exception as e:
                            flask_app.logger.info(f"删除文件 {file_path} 时发生未知错误: {e}")

            share_manager.remove_expired()
            flask_app.logger.info("清理了过期的分享链接")

            for _ in range(config.cleanup_time // 10):
                if not cleanup_thread_running:
                    return
                time.sleep(10)


def start_cleanup_thread():
    global cleanup_thread
    global cleanup_thread_running
    cleanup_thread_running = True
    cleanup_thread = threading.Thread(target=cleanup_temp_files_and_expired_links)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    flask_app.logger.info(f"清理线程启动，清理间隔{config.cleanup_time}秒")


def stop_cleanup_thread():
    global cleanup_thread_running
    cleanup_thread_running = False
    if cleanup_thread.is_alive():
        cleanup_thread.join()
    flask_app.logger.info("清理线程已停止")


def get_themes():
    themes = []
    bootswatch_dir = os.path.join(flask_app.static_folder, 'bootswatch')
    for filename in os.listdir(bootswatch_dir):
        if filename.endswith('.min.css'):
            theme_name = filename.replace('.min.css', '')
            theme_url = url_for('static', filename=f'bootswatch/{filename}')
            themes.append({'name': theme_name, 'url': theme_url})
    return themes


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


@flask_app.context_processor
def inject_themes():
    return {'themes': get_themes()}


@flask_app.before_request
def check_blocked_ip():
    ip = request.remote_addr

    if request.path.startswith('/static/'):
        return None
    if ip_limiter.is_blocked(ip):
        return render_template('ip_blocked.html',
                               remaining_time=ip_limiter.get_remaining_time(ip),
                               pageMark='访问受限')


@flask_app.route('/')
@check_auth_timestamp
def index():
    if config.global_password and not session.get('auth'):
        return render_template('global_password.html', alias='global', pageMark=f'全局密码')

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
            ip_limiter.reset(client_info)
            session['auth_time'] = current_time
            return '', 200
        else:

            ip_limiter.add_failed_attempt(client_info)

    else:
        dir_obj = None
        for d in config.shared_dirs.values():
            if d.alias == alias:
                dir_obj = d
                break

        if dir_obj and password == dir_obj.password or password == config.admin_password:
            session[f'auth_{alias}'] = True
            ip_limiter.reset(client_info)
            session[f'auth_time_{alias}'] = current_time
            return '', 200
        else:

            ip_limiter.add_failed_attempt(client_info)

    return '', 403


@flask_app.route('/dir/<path:dirname>')
@check_auth_timestamp
def list_dir(dirname):
    dir_obj = None
    base_dir = dirname.split('/')[0]

    for d in config.shared_dirs.values():
        if d.alias == base_dir:
            dir_obj = d
            break
    for d in config.shared_dirs.values():
        if d.alias == base_dir:
            dir_obj = d
            break

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

    nav_path = []
    current = ""
    for part in dirname.split('/'):
        current += f"/{part}" if current else part

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

    dir_obj = None
    for d in config.shared_dirs.values():
        if d.alias == alias:
            dir_obj = d
            break

    if not dir_obj:
        return jsonify({'error': 'Directory not found'}), 404

    if dir_obj.password and not session.get(f'auth_{alias}'):
        return jsonify({'error': 'Authentication required'}), 403

    results = []

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
    result = validate_file_path(filepath)
    if isinstance(result, tuple):
        return result if not request.is_xhr else jsonify({'error': result[0]}), result[1]
    full_path = result

    mime_type = mimetypes.guess_type(full_path)[0]

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not (mime_type and (
            mime_type.startswith(('image/', 'video/', 'audio/')) or mime_type == 'application/pdf') or is_text_file(
        full_path)):
        error_msg = '不支持预览此类型文件'
        return jsonify({'error': error_msg}) if is_ajax else (error_msg, 415)

    if is_ajax:
        try:
            content = read_text_file(full_path)
            return jsonify({'content': content})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    if mime_type:
        if mime_type.startswith(('image/', 'video/', 'audio/')) or mime_type == 'application/pdf':
            return send_file(
                full_path,
                mimetype=mime_type,
                as_attachment=False,
                conditional=True
            )

    try:
        return read_text_file(full_path)
    except Exception as e:
        return f'预览失败: {str(e)}', 500


def calculate_batch_size(items):
    total_size = 0

    for item in items:
        path = item['path']
        is_dir = item.get('is_dir', False)

        if is_dir:

            for root, _, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.exists(file_path):
                        total_size += os.path.getsize(file_path)
        else:

            if os.path.exists(path):
                total_size += os.path.getsize(path)

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
        download_name='download.zip',
        conditional=True
    )

    return response


@flask_app.route('/api/save-file/<path:filepath>', methods=['POST'])
def save_file(filepath):
    if not session.get('admin'):
        return jsonify({'error': '未授权访问'}), 403

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

        conditional=True
    )

    return response


@flask_app.route('/api/upload/<path:alias>', methods=['POST'])
def upload_file(alias):
    if not session.get('admin'):
        return "Unauthorized", 403

    file = request.files.get('file')
    current_path = request.form.get('current_path', '')
    current_path = urllib.parse.unquote(current_path)

    chunk_number = request.form.get('chunk_index', 0)
    chunks = request.form.get('total_chunks', 1)
    filename = request.form.get('filename')
    file_id = request.form.get('identifier')

    if not file_id:
        return "Missing file identifier", 400

    if not file:
        return "No file", 400

    dir_obj = next((d for d in config.shared_dirs.values() if d.alias == alias), None)
    if not dir_obj:
        return "Directory not found", 404

    path_parts = current_path.strip('/').split('/')
    if len(path_parts) > 1:
        sub_path = '/'.join(path_parts[2:])
        target_dir = os.path.join(dir_obj.path, sub_path)
        if not os.path.exists(target_dir):
            return "Target directory not found", 404
    else:
        target_dir = dir_obj.path

    filename = secure_filename_cn(filename)

    if int(chunks) == 1:
        file_path = os.path.join(target_dir, filename)
        file.save(file_path)
        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 上传文件: {filename} 到了{target_dir}")
        return "Success", 200

    temp_dir = os.path.join(config.upload_temp_dir, file_id)
    os.makedirs(temp_dir, exist_ok=True)

    chunk_file = os.path.join(temp_dir, f"chunk_{chunk_number}")
    file.save(chunk_file)

    uploaded_chunks = len(os.listdir(temp_dir))
    if uploaded_chunks == int(chunks):

        final_path = os.path.join(target_dir, filename)
        with open(final_path, 'wb') as target_file:
            for i in range(int(chunks)):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                with open(chunk_path, 'rb') as chunk:
                    target_file.write(chunk.read())

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

    path_parts = current_path.strip('/').split('/')
    if len(path_parts) > 1:

        sub_path = '/'.join(path_parts[2:])
        target_dir = os.path.join(dir_obj.path, sub_path, folder_name)
        check_dir = os.path.join(dir_obj.path, sub_path)
    else:
        target_dir = os.path.join(dir_obj.path, folder_name)
        check_dir = os.path.join(dir_obj.path)

    if not os.path.exists(check_dir):
        return "Target directory not found", 404
    os.makedirs(target_dir, exist_ok=True)

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

    path_parts = current_path.strip('/').split('/')
    sub_path = '/'.join(path_parts[2:]) if len(path_parts) > 1 else ''
    target_path = os.path.join(dir_obj.path, sub_path, name)

    try:
        if is_dir:
            os.rmdir(target_path)
            pre_name = "目录"
        else:
            os.remove(target_path)
            pre_name = "文件"

        client_info = get_client_info()
        op_path = os.path.join(dir_obj.path, sub_path)
        flask_app.logger.info(f"{client_info}在 {op_path} 删除了{pre_name}: {name}")
        return "Success", 200


    except OSError as e:
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


@flask_app.route('/admin/login', methods=['POST'])
@check_ip_limit
def admin_login():
    client_info = f"{request.remote_addr}"
    if request.form.get('password') == config.admin_password:
        session['admin'] = True
        ip_limiter.reset(client_info)
        flask_app.logger.info(f"{client_info} 管理员登录成功")
        return redirect(request.referrer or url_for('index'))

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
                for token, data in share_manager.share_links.items():
                    if data.alias == alias:
                        token_to_remove.append(token)

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

                token_to_modify = []
                for token, data in share_manager.share_links.items():
                    if data.alias == alias:
                        token_to_modify.append(token)
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

    if config.global_password != data.get('global_password', ''):
        password_change_timestamps['global'] = time.time()

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

    share = share_manager.create_share(full_path, dir_obj.alias, name, password, expire_days, manage_code, desc)

    client_info = get_client_info()
    flask_app.logger.info(f"{client_info} 新建了分享 {share.token}")
    return jsonify({'share_url': f'/s/{share.token}'})


@flask_app.route('/s/<token>')
@flask_app.route('/s/<token>/<path:subpath>')
@check_auth_timestamp
def access_share(token, subpath=''):
    token = token.strip().split(' ')[0]
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
                           contents=share_manager.list_directory_contents(share.path,
                                                                          subpath) if share.is_dir else None)


@flask_app.route('/s/<token>/verify', methods=['POST'])
@check_ip_limit
def verify_share(token):
    share = share_manager.get_share(token)
    client_info = f"{request.remote_addr}"

    if not share:
        return render_template('error.html', message='分享链接不存在', pageMark='分享链接不存在')

    if share.password == request.form.get('password') or config.admin_password == request.form.get('password'):
        session[f'share_auth_{token}'] = True
        ip_limiter.reset(client_info)

        session[f'share_auth_time_{token}'] = time.time()

        if f'share_{token}' not in password_change_timestamps['shares']:
            password_change_timestamps['shares'][token] = 0
        return redirect(url_for('access_share', token=token))

    ip_limiter.add_failed_attempt(client_info)
    return render_template('share_password.html', token=token, error='密码错误', pageMark='访问密码')


@flask_app.route('/api/manage_share/<token>/<manage_code>/<opcode>', methods=['POST'])
@check_auth_timestamp
@check_ip_limit
def manage_share(token, manage_code, opcode):
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

    if not os.path.abspath(file_path).startswith(
            os.path.abspath(share.path if share.is_dir else os.path.dirname(share.path))):
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
        conditional=True
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

                        rel_path = os.path.relpath(file_path, os.path.dirname(full_path))
                        arcname = os.path.join(base_name, rel_path)
                        zf.write(file_path, arcname)
            else:

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
    message = '众里寻她千百度，蓦然回首，那页却在灯火阑珊处。'
    return render_template('error.html', message=message, pageMark='灯火阑珊'), 404


@flask_app.route('/api/clear_session/<token>/<security_code>')
def clear_session(token, security_code):
    if security_code == config.security_code:
        password_change_timestamps['shares'][token] = time.time()

        client_info = get_client_info()
        flask_app.logger.info(f"{client_info} 更新了分享 {token} 的认证时间戳")
        return 'success'
    return 'invalid security code'


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


@flask_app.template_filter('extract_first_dir')
def extract_first_dir(path):
    parts = [p for p in path.replace('\\', '/').split('/') if p]

    if parts:

        if ':' in parts[0]:

            if len(parts) > 1:
                return parts[1]

            return parts[0][0]

        return parts[0]

    return ''
