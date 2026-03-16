"""
清理线程管理模块
用于统一管理清理线程的启动、停止和状态检查
"""
import threading
import time
import os
from loguru import logger

# 全局变量
cleanup_thread_running = False
cleanup_thread = None


def cleanup_temp_files():
    """清理临时文件的函数"""
    global cleanup_thread_running
    
    while cleanup_thread_running:
        try:
            # 这里需要导入config，但要避免循环导入
            from main import config
            
            temp_dir = config.upload_temp_dir
            if os.path.exists(temp_dir):
                current_time = time.time()
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            # 删除超过1小时的临时文件
                            if current_time - os.path.getmtime(file_path) > 3600:
                                os.remove(file_path)
                                logger.info(f"已清理临时文件: {file_path}")
                        except (OSError, FileNotFoundError):
                            continue
                    
                    # 删除空目录
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            if not os.listdir(dir_path):
                                os.rmdir(dir_path)
                                logger.info(f"已清理空目录: {dir_path}")
                        except (OSError, FileNotFoundError):
                            continue
            
            # 等待清理间隔时间
            time.sleep(config.cleanup_time)
            
        except Exception as e:
            logger.error(f"清理过程中发生错误: {e}")
            time.sleep(60)  # 出错时等待1分钟再重试


def start_cleanup_thread():
    """启动清理线程"""
    global cleanup_thread_running, cleanup_thread
    
    if cleanup_thread_running:
        logger.warning("清理线程已在运行")
        return
    
    cleanup_thread_running = True
    cleanup_thread = threading.Thread(target=cleanup_temp_files, daemon=True)
    cleanup_thread.start()
    logger.info("清理线程已启动")


def stop_cleanup_thread():
    """停止清理线程"""
    global cleanup_thread_running, cleanup_thread
    
    cleanup_thread_running = False
    if cleanup_thread and cleanup_thread.is_alive():
        cleanup_thread.join(timeout=5)  # 等待最多5秒
    logger.info("清理线程已停止")


def is_cleanup_running():
    """检查清理线程是否运行"""
    global cleanup_thread_running
    return cleanup_thread_running
