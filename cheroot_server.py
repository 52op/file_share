"""
Cheroot服务器包装器
原生支持SSL的高性能WSGI服务器，完美替换Waitress
"""
import os
from loguru import logger


class CherootServer:
    """Cheroot服务器包装器"""
    
    def __init__(self, app, host='0.0.0.0', port=8080, ssl_cert=None, ssl_key=None, **kwargs):
        self.app = app
        self.host = host
        self.port = port
        self.ssl_cert = ssl_cert
        self.ssl_key = ssl_key
        self.kwargs = kwargs
        self.server = None
        self.is_ssl = bool(ssl_cert and ssl_key)
        self.start_callback = kwargs.get('start_callback', None)
        
    def create_server(self):
        """创建Cheroot服务器"""
        try:
            from cheroot.wsgi import Server as WSGIServer
            
            # 创建服务器
            self.server = WSGIServer(
                (self.host, self.port),
                self.app,
                numthreads=self.kwargs.get('threads', 10),
                max=self.kwargs.get('connection_limit', 1000),
                timeout=self.kwargs.get('channel_timeout', 300),
                shutdown_timeout=self.kwargs.get('shutdown_timeout', 5)
            )
            
            # 如果是SSL服务器，配置SSL
            if self.is_ssl:
                if not os.path.exists(self.ssl_cert) or not os.path.exists(self.ssl_key):
                    raise FileNotFoundError(f"SSL证书文件不存在: {self.ssl_cert} 或 {self.ssl_key}")
                
                from cheroot.ssl.builtin import BuiltinSSLAdapter
                self.server.ssl_adapter = BuiltinSSLAdapter(
                    certificate=self.ssl_cert,
                    private_key=self.ssl_key
                )
                logger.info(f"Cheroot SSL服务器已配置: {self.host}:{self.port}")
            else:
                logger.info(f"Cheroot HTTP服务器已配置: {self.host}:{self.port}")
            
            return True
            
        except Exception as e:
            logger.error(f"创建Cheroot服务器失败: {e}")
            return False
    
    def start(self):
        """启动服务器"""
        if not self.server:
            if not self.create_server():
                raise Exception("服务器创建失败")

        try:
            protocol = "HTTPS" if self.is_ssl else "HTTP"
            logger.info(f"启动Cheroot {protocol}服务器: {self.host}:{self.port}")
            self.server.start()

            # 调用启动成功回调
            if self.start_callback:
                try:
                    self.start_callback(self.host, self.port, self.is_ssl)
                except Exception as e:
                    logger.error(f"启动回调执行失败: {e}")

        except Exception as e:
            logger.error(f"Cheroot服务器启动失败: {e}")
            raise
    
    def run(self):
        """运行服务器（阻塞模式）"""
        self.start()
    
    def stop(self):
        """停止服务器"""
        if self.server:
            try:
                self.server.stop()
                protocol = "HTTPS" if self.is_ssl else "HTTP"
                logger.info(f"Cheroot {protocol}服务器已停止")
            except Exception as e:
                logger.error(f"停止Cheroot服务器时发生错误: {e}")
    
    def close(self):
        """关闭服务器（别名）"""
        self.stop()
    
    @property
    def ready(self):
        """检查服务器是否就绪"""
        return self.server and self.server.ready if self.server else False


def create_cheroot_server(app, host='0.0.0.0', port=8080, ssl_cert=None, ssl_key=None, **kwargs):
    """创建Cheroot服务器的便捷函数"""
    return CherootServer(
        app=app,
        host=host,
        port=port,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        **kwargs
    )


def create_cheroot_http_server(app, host='0.0.0.0', port=8080, **kwargs):
    """创建HTTP Cheroot服务器"""
    return create_cheroot_server(app, host, port, **kwargs)


def create_cheroot_https_server(app, host='0.0.0.0', port=443, cert_file=None, key_file=None, **kwargs):
    """创建HTTPS Cheroot服务器"""
    return create_cheroot_server(app, host, port, ssl_cert=cert_file, ssl_key=key_file, **kwargs)
