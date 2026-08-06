import base64
import os

from cryptography.fernet import Fernet

_PREFIX = "enc:"


class ConfigCrypto:
    """基于本地密钥文件的 Fernet 加解密。密钥保存在程序目录下的 config.key。

    目标仅是避免配置文件里一眼看出明文密码，不追求强安全。
    """

    def __init__(self, key_file="config.key", key_dir=None):
        self.key_file = os.path.join(key_dir, key_file) if key_dir else key_file
        self._fernet = None

    def _load_or_create_key(self):
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as f:
                key = f.read().strip()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as f:
                f.write(key)
        self._fernet = Fernet(key)

    def _ensure_fernet(self):
        if self._fernet is None:
            self._load_or_create_key()

    def encrypt(self, plaintext):
        """加密字符串，返回带 enc: 前缀的密文。空值原样返回。"""
        if not plaintext:
            return plaintext
        self._ensure_fernet()
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return _PREFIX + token.decode("ascii")

    def decrypt(self, value):
        """解密带 enc: 前缀的字符串。无前缀视为旧明文，原样返回。"""
        if not value:
            return value
        if not isinstance(value, str) or not value.startswith(_PREFIX):
            return value
        self._ensure_fernet()
        try:
            token = value[len(_PREFIX):].encode("ascii")
            return self._fernet.decrypt(token).decode("utf-8")
        except Exception:
            return ""


# 模块级单例，供 Config 和 ShareDirectory 使用
_crypto = None
_KEY_DIR = None


def set_key_dir(directory):
    """设置密钥文件所在目录（通常在程序运行目录）"""
    global _KEY_DIR
    _KEY_DIR = directory


def get_crypto():
    global _crypto
    if _crypto is None:
        _crypto = ConfigCrypto(key_dir=_KEY_DIR)
    return _crypto


def set_crypto(crypto):
    """测试用：替换加密器"""
    global _crypto
    _crypto = crypto
