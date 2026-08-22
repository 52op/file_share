# 项目安全加固与工程化改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复配置文件明文密码默认值漏洞、建立自动化测试基建、清理依赖清单、统一认证与路径校验公共层。

**Architecture:** 按四个独立任务依次推进，每任务独立可测试可提交。测试采用 Flask test_client + tkinter mock 的 conftest 基建，不触碰真实 GUI。

**Tech Stack:** Python 3.8、Flask 3.0.3（实际安装版本）、pytest、cryptography/Fernet、tkinter(测试时 mock)

---

## 背景事实（先确认再动手）

- `encryption.py` 的 `decrypt()`：无 `enc:` 前缀的值视为旧明文原样返回（encryption.py:42-46）。`encrypt()` 空值原样返回。
- `main.py:478-480` `Config.save()`：`admin_password` 为空时保存明文 `"admin"`（兜底值），非空时才加密——这是明文残留漏洞的根源。
- `main.py:522-524` `Config.load()`：`decrypt()` 对无前缀值原样返回，兼容旧明文。
- 权限校验核心在 `routes/routes.py` 的 `require_dir_access()`（routes.py:173）。
- 测试环境 `venv_3.8` 中 pytest **未安装**，需安装。
- 导入 main.py 会触发 tkinter/pystray/ttkbootstrap/tkinterdnd2 等依赖，需 conftest mock。
- `requirements.txt` 中 `Requests` 与 `requests` 重复。

---

## 文件结构

- 新增 `tests/conftest.py`：tkinter/GUI 依赖 mock + `flask_app` test_client fixture + 临时配置夹具。
- 新增 `tests/test_auth.py`：认证/权限核心测试。
- 新增 `tests/test_encryption.py`：Config save/load 加密与明文迁移测试。
- 新增 `tests/test_path_safety.py`：`safe_join_path` 路径遍历测试。
- 修改 `main.py:477-481`：修复 `admin_password` 空值兜底明文。
- 修改 `main.py`（新增迁移逻辑）：load 时检测明文旧配置并迁移为加密。
- 修改 `requirements.txt`：去重。
- 修改 `routes/routes.py`：将路径拼接与权限判断抽公共辅助函数（局部重构，保持行为不变）。

---

### Task 1: 修复 Config.save 空密码明文兜底漏洞

**Files:**
- Modify: `main.py:477-481`
- Test: `tests/test_encryption.py`

- [ ] **Step 1: 写失败测试（证明 save 对空密码写明文 "admin"）**

创建 `tests/test_encryption.py`：

```python
# -*- coding: utf-8 -*-
"""Config 密码加密与旧明文迁移测试"""
import os
import tempfile

import pytest

from encryption import get_crypto


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
    """空 admin_password 时，配置文件中不得出现明文 'admin' 或未加密字段。"""
    cfg = _make_config(tmp_path, admin_password="")
    cfg.save()
    raw = (tmp_path / "share_config.json").read_text(encoding="utf-8")
    # 不得出现无前缀的 admin_password 明文
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv_3.8\Scripts\python.exe -m pytest tests/test_encryption.py -v`
Expected: `test_save_never_writes_plaintext_default` FAIL（当前实现写明文 `"admin"`），其余两项 PASS。

- [ ] **Step 3: 修复 main.py**

将 `main.py:477-481` 中：

```python
"global_password": get_crypto().encrypt(self.global_password),
"admin_password": get_crypto().encrypt(self.admin_password)
if self.admin_password
else "admin",  # 修复：移除对全局config的引用
"admin_totp_secret": get_crypto().encrypt(self.admin_totp_secret),
```

改为：

```python
"global_password": get_crypto().encrypt(self.global_password),
"admin_password": get_crypto().encrypt(self.admin_password),
"admin_totp_secret": get_crypto().encrypt(self.admin_totp_secret),
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv_3.8\Scripts\python.exe -m pytest tests/test_encryption.py -v`
Expected: 3 项全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_encryption.py
git commit -m "fix: 空管理密码不再写明文admin兜底，全部密码字段统一加密"
```

---

### Task 2: 旧明文配置自动迁移为加密

**Files:**
- Modify: `main.py`（`Config.load` 附近，约 522 行后）
- Test: `tests/test_encryption.py`

- [ ] **Step 1: 写失败测试（旧明文配置 load 后应被加密重写）**

在 `tests/test_encryption.py` 追加：

```python
def test_load_migrates_plaintext_to_encrypted(tmp_path):
    """旧版明文配置 load 后应自动迁移为加密格式。"""
    cfg_file = tmp_path / "share_config.json"
    # 手工构造旧版明文配置
    plain_data = {
        "shared_dirs": {},
        "global_password": "oldglob",
        "admin_password": "oldadmin",
        "admin_totp_secret": "",
        "port": 12345,
    }
    cfg_file.write_text(
        __import__("json").dumps(plain_data, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = _load_config(tmp_path)
    assert cfg.admin_password == "oldadmin"
    assert cfg.global_password == "oldglob"
    # 迁移后磁盘上应为加密格式
    raw = cfg_file.read_text(encoding="utf-8")
    assert '"admin_password": "enc:' in raw
    assert '"global_password": "enc:' in raw
```

- [ ] **Step 2: 运行确认失败**

Run: `venv_3.8\Scripts\python.exe -m pytest tests/test_encryption.py::test_load_migrates_plaintext_to_encrypted -v`
Expected: FAIL（load 只读不写回）。

- [ ] **Step 3: 实现迁移逻辑**

在 `main.py` 的 `Config.load()` 方法末尾（约 529 行之后、文件返回前）追加：

```python
        # 旧版明文密码自动迁移为加密格式（仅在本次加载含明文密码时触发）
        raw_needle = open(self.config_file, encoding="utf-8").read()
        needs_migration = any(
            f'"{field}": "{value}"' in raw_needle and not f'"{field}": "enc:' in raw_needle
            for field, value in [
                ("global_password", self.global_password),
                ("admin_password", self.admin_password),
                ("admin_totp_secret", self.admin_totp_secret),
            ]
            if value
        )
        if needs_migration:
            self.save()
```

> 注意：用字段值子串判断可能误判，更稳妥的做法是直接检查原始 JSON 中该字段值是否以 `enc:` 开头。实现时应解析原始 JSON：

```python
        import json as _json
        with open(self.config_file, encoding="utf-8") as _f:
            _raw_data = _json.load(_f)
        _migrate = False
        for _field in ("global_password", "admin_password", "admin_totp_secret"):
            _val = _raw_data.get(_field)
            if _val and not str(_val).startswith("enc:"):
                _migrate = True
                break
        if _migrate:
            self.save()
```

- [ ] **Step 4: 运行全部加密测试确认通过**

Run: `venv_3.8\Scripts\python.exe -m pytest tests/test_encryption.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_encryption.py
git commit -m "feat: 加载旧明文配置时自动迁移为加密存储"
```

---

### Task 3: 搭建 pytest 测试基建并编写核心认证测试

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_auth.py`
- Create: `tests/test_path_safety.py`
- Modify: `requirements.txt`（追加 pytest）

- [ ] **Step 1: 创建 conftest.py**

创建 `tests/conftest.py`，内容包含 tkinter/GUI mock（复用现有临时脚本模板）+ fixtures：

```python
# -*- coding: utf-8 -*-
"""pytest 全局夹具：mock 掉 GUI 相关依赖，提供 Flask test_client。"""
import os
import sys
import types
import tempfile

import pytest


class _W:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return self._n
    def _n(self, *a, **k): return self
    def pack(self, *a, **k): pass
    def pack_forget(self, *a, **k): pass
    def grid(self, *a, **k): pass
    def set(self, *a, **k): pass
    def get(self, *a, **k): return ''
    def insert(self, *a, **k): pass
    def configure(self, *a, **k): pass
    def config(self, *a, **k): pass
    def bind(self, *a, **k): pass
    def after(self, *a, **k): return 0
    def delete(self, *a, **k): pass
    def destroy(self, *a, **k): pass
    def update(self, *a, **k): pass
    def update_idletasks(self, *a, **k): pass


class _T:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _W()
    def withdraw(self): pass
    def mainloop(self): pass


class _V:
    def __init__(self, *a, **k): pass
    def set(self, *a, **k): pass
    def get(self, *a, **k): return ''


class _MB:
    @staticmethod
    def showinfo(*a, **k): pass
    @staticmethod
    def showwarning(*a, **k): pass
    @staticmethod
    def showerror(*a, **k): pass
    @staticmethod
    def askyesno(*a, **k): return False
    @staticmethod
    def askokcancel(*a, **k): return False


def _install_mocks():
    import tkinter as _tk
    _tk.Tk = _T
    _tk.BooleanVar = _V
    _tk.StringVar = _V
    _tk.IntVar = _V
    _tk.DoubleVar = _V
    _tk.Toplevel = _W
    _tk.Frame = _W
    _tk.Label = _W
    _tk.Button = _W
    _tk.Entry = _W
    _tk.Checkbutton = _W
    _tk.Radiobutton = _W
    _tk.ScrolledText = _W
    _tk.Canvas = _W
    _tk.Menu = _W
    _tk.Menubutton = _W
    _tk.PanedWindow = _W
    _tk.Scrollbar = _W
    _tk.Listbox = _W
    _tk.Text = _W
    _tk.LabelFrame = _W
    _tk.messagebox = _MB
    _tk.filedialog = types.ModuleType('tkinter.filedialog')
    _tk.filedialog.askdirectory = lambda *a, **k: ''
    _tk.filedialog.askopenfilename = lambda *a, **k: ''
    _tk.filedialog.askopenfilenames = lambda *a, **k: []

    class _M(types.ModuleType):
        def __getattr__(self, n): return _W

    for mn in ['pystray', 'tkinterdnd2', 'tkinterdnd2.DND', 'ttkbootstrap',
               'ttkbootstrap.constants', 'ttkbootstrap.scrolled']:
        if mn in sys.modules:
            continue
        m = _M(mn)
        m.__all__ = []
        sys.modules[mn] = m

    ttk = sys.modules['ttkbootstrap']
    c = sys.modules['ttkbootstrap.constants']
    for x in ['LEFT', 'RIGHT', 'TOP', 'BOTTOM', 'X', 'Y', 'BOTH', 'N', 'S', 'E', 'W', 'CENTER', 'VERTICAL']:
        setattr(c, x, x)
    ttk.constants = c

    s = sys.modules['ttkbootstrap.scrolled']
    s.__all__ = []
    ttk.scrolled = s

    if not sys.modules.get('servicemanager'):
        sm = types.ModuleType('servicemanager')
        for a in ['PYS_SERVICE', 'StartServiceCtrlDispatcher', 'LogInfoMsg']:
            setattr(sm, a, lambda *a, **k: 0)
        sys.modules['servicemanager'] = sm


_install_mocks()

# 导入被测模块（在 mock 之后）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main
from main import ShareDirectory
from routes import routes


@pytest.fixture()
def app(tmp_path):
    """每个测试独立的 Flask 应用与临时配置。"""
    cfg = main.config
    cfg.config_file = str(tmp_path / "share_config.json")
    share_root = tmp_path / "share"
    share_root.mkdir(exist_ok=True)
    cfg.shared_dirs.clear()
    cfg.global_password = ""
    cfg.admin_password = "admin"
    cfg.admin_totp_secret = ""
    cfg.admin_totp_only = False
    (share_root / "f.txt").write_text("x", encoding="utf-8")
    cfg.shared_dirs["pub"] = ShareDirectory(str(share_root), alias="pub",
                                            password="", desc="", admin_password="")
    cfg.shared_dirs["locked"] = ShareDirectory(str(share_root), alias="locked",
                                               password="dirpass", desc="", admin_password="")
    main.flask_app.config["TESTING"] = True
    return main.flask_app


@pytest.fixture()
def client(app):
    return app.test_client()
```

- [ ] **Step 2: 写核心认证测试 test_auth.py**

创建 `tests/test_auth.py`：

```python
# -*- coding: utf-8 -*-
"""认证与目录访问权限测试"""
import time


def _has_password_form(body):
    return 'id="passwordForm"' in body


def test_unauthenticated_locked_dir_requires_password(client):
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert _has_password_form(resp.get_data(as_text=True))


def test_admin_bypasses_dir_password(client):
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_unauthenticated_global_password_dir(client):
    from main import config
    config.global_password = "globpass"
    resp = client.get("/dir/pub")
    assert resp.status_code == 200
    assert _has_password_form(resp.get_data(as_text=True))


def test_admin_bypasses_global_password(client):
    from main import config
    config.global_password = "globpass"
    with client.session_transaction() as s:
        s["admin"] = True
        s["admin_time"] = time.time()
    resp = client.get("/dir/pub")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_dir_password_login_sets_auth_session(client):
    resp = client.post("/check_password/locked", data={"password": "dirpass"})
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert s.get("auth_locked") is True
    resp = client.get("/dir/locked")
    assert resp.status_code == 200
    assert not _has_password_form(resp.get_data(as_text=True))


def test_admin_login_without_totp(client):
    resp = client.post("/admin/login", data={"password": "admin"})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get("admin") is True


def test_dir_admin_login(client):
    from main import config
    config.shared_dirs["locked"].admin_password = "dadmin"
    resp = client.post("/dir-admin/login", data={"password": "dadmin", "dirname": "locked"})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get("dir_admin_locked") is True
```

- [ ] **Step 3: 写路径安全测试 test_path_safety.py**

创建 `tests/test_path_safety.py`：

```python
# -*- coding: utf-8 -*-
"""路径遍历安全测试"""
import pytest

from routes.routes import safe_join_path


def test_normal_join():
    p = safe_join_path(r"C:\share\root", "sub", "file.txt")
    assert os.path.normcase(p).startswith(os.path.normcase(r"C:\share\root"))


def test_traversal_rejected():
    with pytest.raises(ValueError):
        safe_join_path(r"C:\share\root", "..", "..", "etc", "passwd")


def test_absolute_path_rejected():
    with pytest.raises(ValueError):
        safe_join_path(r"C:\share\root", r"C:\Windows\system32")


def test_double_dot_rejected():
    with pytest.raises(ValueError):
        safe_join_path(r"C:\share\root", "a", "../../../b")


import os
```

- [ ] **Step 4: 安装 pytest 并运行全部测试**

Run:
```powershell
venv_3.8\Scripts\python.exe -m pip install pytest
venv_3.8\Scripts\python.exe -m pytest tests/ -v
```
Expected: 全部 PASS。若 `safe_join_path` 对某些用例行为与预期不符，按实际语义修正测试断言（先确认函数实现，见 routes.py:36-60 附近）。

- [ ] **Step 5: 提交**

```bash
git add tests/ requirements.txt
git commit -m "test: 新增pytest测试基建与认证/加密/路径安全核心测试"
```

---

### Task 4: requirements.txt 去重

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 移除重复项**

将 `requirements.txt` 中：

```
requests==2.32.3
...
Requests==2.32.3
```

改为只保留一条 `requests==2.32.3`（pip 包名统一小写）。

同时核对：若 `python-docx`、`python-pptx`、`Pillow`、`cryptography` 等实际未被导入的包存在，标注是否可移除（保持谨慎，仅去重，不删除可能使用的包）。

- [ ] **Step 2: 验证安装元数据仍可解析**

Run: `venv_3.8\Scripts\python.exe -m pip check`
Expected: `No broken requirements found.`

- [ ] **Step 3: 提交**

```bash
git add requirements.txt
git commit -m "chore: 清理requirements.txt重复依赖项"
```

---

### Task 5: 统一路径拼接与权限判断公共层（局部重构）

**Files:**
- Modify: `routes/routes.py`
- Test: `tests/test_path_safety.py`、`tests/test_auth.py`

- [ ] **Step 1: 抽取公共函数（保持行为不变）**

在 `routes/routes.py` 的 `safe_join_path` 旁新增（若尚未存在）：

```python
def get_dir_obj(alias):
    """按别名查找目录对象。"""
    for d in config.shared_dirs.values():
        if d.alias == alias:
            return d
    return None
```

并在 `list_dir`、`download`、`check_password`、`search`、`batch_download` 中把重复的 `for d in config.shared_dirs.values(): if d.alias == alias` 循环替换为 `get_dir_obj(alias)`。

- [ ] **Step 2: 运行全部测试确认无回归**

Run: `venv_3.8\Scripts\python.exe -m pytest tests/ -v`
Expected: 全部 PASS。

- [ ] **Step 3: 提交**

```bash
git add routes/routes.py
git commit -m "refactor: 抽取get_dir_obj公共查找函数，消除重复遍历"
```

---

## 执行顺序与风险

1. Task 1/2 修复加密漏洞（最高优先，独立可测）。
2. Task 3 测试基建（Task 1/2 的测试已内含）。
3. Task 4 依赖去重（低风险）。
4. Task 5 局部重构（行为不变，靠测试兜底）。

**风险点：**
- 导入 main.py 的 mock 方案依赖 tkinter 内部结构，若失败可参考 `C:\Users\letvar\AppData\Local\Temp\opencode\` 下此前验证过的 mock 模板（已删除，需按本计划重建）。
- 迁移逻辑中 `self.save()` 会写全量配置，确保 load 时所有必需字段已初始化（Config.__init__ 已保证）。
- `safe_join_path` 对绝对路径的处理需先读实现再定断言。

## 自检清单

- [ ] Task 1 测试证明空密码不再写明文 "admin"
- [ ] Task 2 旧明文配置自动迁移
- [ ] Task 3 认证/加密/路径测试全绿
- [ ] Task 4 pip check 通过
- [ ] Task 5 全测试回归通过
- [ ] 每任务独立 commit
