# -*- coding: utf-8 -*-
"""路径遍历安全测试"""
import os

import pytest

from routes.routes import safe_join_path


def test_normal_join(tmp_path):
    p = safe_join_path(str(tmp_path), "sub", "file.txt")
    assert os.path.normcase(p).startswith(os.path.normcase(str(tmp_path)))


def test_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        safe_join_path(str(tmp_path), "..", "..", "etc", "passwd")


def test_absolute_path_rejected(tmp_path):
    with pytest.raises(ValueError):
        safe_join_path(str(tmp_path), os.path.abspath(tmp_path.parent.parent) + os.sep + "other")


def test_double_dot_rejected(tmp_path):
    with pytest.raises(ValueError):
        safe_join_path(str(tmp_path), "a", "../../../b")


def test_empty_join_returns_base(tmp_path):
    p = safe_join_path(str(tmp_path))
    assert os.path.normcase(p) == os.path.normcase(os.path.abspath(str(tmp_path)))