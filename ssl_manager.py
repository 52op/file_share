"""
SSL证书管理模块
用于自动下载、更新和管理SSL证书
"""
import os
import ssl
import zipfile
import requests
import threading
import time
from datetime import datetime, timedelta
from loguru import logger
from cryptography import x509
from cryptography.hazmat.backends import default_backend


class SSLCertificateManager:
    def __init__(self, config):
        self.config = config
        self.cert_check_thread = None
        self.cert_check_running = False
        
    def get_cert_download_url(self, domain=None, date=None):
        """生成证书下载URL"""
        if not domain:
            domain = self.config.ssl_domain
        if not date:
            date = datetime.now().strftime('%Y%m%d')
        
        if not self.config.cert_server_url or not domain:
            return None
            
        # 确保服务器地址以/结尾
        server_url = self.config.cert_server_url.rstrip('/')
        return f"{server_url}/{domain}_{date}.zip"
    
    def download_certificate(self, url=None, domain=None):
        """下载证书文件"""
        try:
            if not url:
                url = self.get_cert_download_url(domain)
            
            if not url:
                logger.error("无法生成证书下载URL")
                return False
            
            logger.info(f"开始下载证书: {url}")
            
            # 下载证书文件
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            # 保存到临时文件
            temp_zip_path = os.path.join(self.config.cert_dir, "temp_cert.zip")
            with open(temp_zip_path, 'wb') as f:
                f.write(response.content)
            
            # 解压证书文件
            success = self.extract_certificate(temp_zip_path)
            
            # 清理临时文件
            if os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)
            
            if success:
                logger.info("证书下载和解压成功")
                return True
            else:
                logger.error("证书解压失败")
                return False
                
        except requests.RequestException as e:
            logger.error(f"下载证书失败: {e}")
            return False
        except Exception as e:
            logger.error(f"处理证书时发生错误: {e}")
            return False
    
    def extract_certificate(self, zip_path):
        """解压证书文件"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # 备份现有证书
                self.backup_existing_certificates()
                
                # 解压到证书目录
                zip_ref.extractall(self.config.cert_dir)
                
                # 验证证书文件
                if self.validate_certificate_files():
                    logger.info("证书文件验证成功")
                    return True
                else:
                    logger.error("证书文件验证失败，恢复备份")
                    self.restore_certificate_backup()
                    return False
                    
        except zipfile.BadZipFile:
            logger.error("证书文件不是有效的ZIP格式")
            return False
        except Exception as e:
            logger.error(f"解压证书文件时发生错误: {e}")
            return False
    
    def backup_existing_certificates(self):
        """备份现有证书"""
        try:
            backup_dir = os.path.join(self.config.cert_dir, "backup")
            os.makedirs(backup_dir, exist_ok=True)
            
            cert_files = ['cert.pem', 'key.pem', 'fullchain.pem', 'privkey.pem']
            for cert_file in cert_files:
                cert_path = os.path.join(self.config.cert_dir, cert_file)
                if os.path.exists(cert_path):
                    backup_path = os.path.join(backup_dir, f"{cert_file}.backup")
                    with open(cert_path, 'rb') as src, open(backup_path, 'wb') as dst:
                        dst.write(src.read())
                        
        except Exception as e:
            logger.warning(f"备份证书文件时发生错误: {e}")
    
    def restore_certificate_backup(self):
        """恢复证书备份"""
        try:
            backup_dir = os.path.join(self.config.cert_dir, "backup")
            if not os.path.exists(backup_dir):
                return False
                
            cert_files = ['cert.pem', 'key.pem', 'fullchain.pem', 'privkey.pem']
            for cert_file in cert_files:
                backup_path = os.path.join(backup_dir, f"{cert_file}.backup")
                if os.path.exists(backup_path):
                    cert_path = os.path.join(self.config.cert_dir, cert_file)
                    with open(backup_path, 'rb') as src, open(cert_path, 'wb') as dst:
                        dst.write(src.read())
            
            return True
            
        except Exception as e:
            logger.error(f"恢复证书备份时发生错误: {e}")
            return False
    
    def validate_certificate_files(self):
        """验证证书文件的有效性"""
        try:
            cert_path = self.get_cert_file_path()
            key_path = self.get_key_file_path()
            
            if not cert_path or not key_path:
                return False
            
            # 验证证书文件可以正常读取
            with open(cert_path, 'rb') as f:
                cert_data = f.read()
                x509.load_pem_x509_certificate(cert_data, default_backend())
            
            # 验证私钥文件存在且可读
            if not os.path.exists(key_path):
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"验证证书文件时发生错误: {e}")
            return False
    
    def get_cert_file_path(self):
        """获取证书文件路径"""
        possible_names = ['cert.pem', 'fullchain.pem', 'certificate.pem']
        for name in possible_names:
            path = os.path.join(self.config.cert_dir, name)
            if os.path.exists(path):
                return path
        return None
    
    def get_key_file_path(self):
        """获取私钥文件路径"""
        possible_names = ['key.pem', 'privkey.pem', 'private.pem']
        for name in possible_names:
            path = os.path.join(self.config.cert_dir, name)
            if os.path.exists(path):
                return path
        return None
    
    def get_certificate_expiry_date(self):
        """获取证书到期时间"""
        try:
            cert_path = self.get_cert_file_path()
            if not cert_path:
                return None

            with open(cert_path, 'rb') as f:
                cert_data = f.read()
                cert = x509.load_pem_x509_certificate(cert_data, default_backend())

                # 使用新的UTC方法，如果不可用则回退到旧方法
                try:
                    return cert.not_valid_after_utc.replace(tzinfo=None)
                except AttributeError:
                    # 回退到旧方法（用于旧版本的cryptography库）
                    return cert.not_valid_after

        except Exception as e:
            logger.error(f"获取证书到期时间时发生错误: {e}")
            return None
    
    def is_certificate_expiring_soon(self, days_threshold=10):
        """检查证书是否即将到期"""
        try:
            expiry_date = self.get_certificate_expiry_date()
            if not expiry_date:
                return True  # 如果无法获取到期时间，认为需要更新
                
            days_until_expiry = (expiry_date - datetime.now()).days
            return days_until_expiry <= days_threshold
            
        except Exception as e:
            logger.error(f"检查证书到期时间时发生错误: {e}")
            return True
    
    def has_valid_certificate(self):
        """检查是否有有效的证书"""
        cert_path = self.get_cert_file_path()
        key_path = self.get_key_file_path()
        
        if not cert_path or not key_path:
            return False
            
        return self.validate_certificate_files()
    
    def start_certificate_monitor(self):
        """启动证书监控线程"""
        if self.cert_check_running:
            return
            
        self.cert_check_running = True
        self.cert_check_thread = threading.Thread(target=self._certificate_monitor_loop, daemon=True)
        self.cert_check_thread.start()
        logger.info("SSL证书监控线程已启动")
    
    def stop_certificate_monitor(self):
        """停止证书监控线程"""
        self.cert_check_running = False
        if self.cert_check_thread and self.cert_check_thread.is_alive():
            self.cert_check_thread.join(timeout=5)
        logger.info("SSL证书监控线程已停止")
    
    def _certificate_monitor_loop(self):
        """证书监控循环"""
        while self.cert_check_running:
            try:
                if self.config.ssl_enabled and self.config.ssl_domain and self.config.cert_server_url:
                    if not self.has_valid_certificate() or self.is_certificate_expiring_soon():
                        logger.info("检测到证书需要更新，开始自动更新...")
                        if self.download_certificate():
                            logger.info("证书自动更新成功")
                        else:
                            logger.error("证书自动更新失败")
                
                # 每天检查一次
                time.sleep(24 * 60 * 60)
                
            except Exception as e:
                logger.error(f"证书监控过程中发生错误: {e}")
                time.sleep(60 * 60)  # 出错时等待1小时再重试
