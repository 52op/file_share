# -*- coding: utf-8 -*-
"""Config 密码加密与旧明文迁移测试"""
import json

import pytest


def _make_config(tmp_path, admin_password=""):
    from main import Config
    cfg = Config()
    cfg.config_file = str(tmp_path / "share_config.json")
    cfg.shared_dirs = {}
    cfg.admin_password = admin_password
    return cfg


def _load_config(tmp_path):
    from main import Config
    cfg = Config()
    cfg.config_file = str(tmp_path / "share_config.json")
    cfg.load()
    return cfg


def test_save_never_writes_plaintext_default(tmp_path):
    """空 admin_password 时，配置文件中不得出现明文 'admin'。"""
    cfg = _make_config(tmp_path, admin_password="")
    cfg.save()
    raw = (tmp_path / "share_config.json").read_text(encoding="utf-8")
    assert '"admin_password": "admin"' not in raw
    assert '"admin_password": ""' in raw or '"admin_password": "enc:' in raw


def test_save_encrypts_nonempty_password(tmp_path):
    """非空 admin_password 保存后应为带 enc: 前缀的密文。"""
    cfg = _make_config(tmp_path, admin_password="secret123")
    cfg.save()
    raw = (tmp_path / "share_config.json").read_text(encoding="utf-8")
    assert '"admin_password": "enc:' in raw


def test_load_roundtrip_encrypted(tmp_path):
    """加密保存后 load 应还原原密码。"""
    cfg = _make_config(tmp_path, admin_password="secret123")
    cfg.save()
    loaded = _load_config(tmp_path)
    assert loaded.admin_password == "secret123"


def test_load_migrates_plaintext_to_encrypted(tmp_path):
    """旧版明文配置 load 后应自动迁移为加密格式。"""
    cfg_file = tmp_path / "share_config.json"
    plain_data = {
        "shared_dirs": {},
        "global_password": "oldglob",
        "admin_password": "oldadmin",
        "admin_totp_secret": "",
        "port": 12345,
    }
    cfg_file.write_text(
        json.dumps(plain_data, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = _load_config(tmp_path)
    assert cfg.admin_password == "oldadmin"
    assert cfg.global_password == "oldglob"
    # 迁移后磁盘上应为加密格式
    raw = cfg_file.read_text(encoding="utf-8")
    assert '"admin_password": "enc:' in raw
    assert '"global_password": "enc:' in raw
