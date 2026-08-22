# main.py 拆分：抽离纯工具模块 core_utils（阶段 A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务执行本计划。步骤使用 checkbox（`- [ ]`）追踪。

**Goal:** 把 `main.py`（3496 行）中 15 个**无内部耦合的纯函数**原样搬入新模块 `core_utils.py`，并让 `main.py` 与 `routes/routes.py` 改从该模块导入，在不改变任何行为的前提下把 `main.py` 减少约 350 行，并迈出打破 `main ⇄ routes` 循环依赖的第一步。

**Architecture:** 本轮只做"零风险拆第一层"：选中 15 个只依赖 stdlib / 外部库（flask.request、netifaces、pypinyin、werkzeug）的纯函数，它们内部**不引用** `Config`、`ShareDirectory`、`flask_app`、`_gui_config_sync_cb` 或任何 `main` 内符号，因此搬入独立模块后不会引入新的循环 import。`main.py` 顶部以 `from core_utils import *` 重新导入这些名字，所以 `main.py` 其余代码（GUI、服务、清理逻辑）无需改动；`routes/routes.py` 顶部与两处函数内 import 显式改指 `core_utils`。搬移采用**逐字原样复制**（禁止顺手优化或改名），行为由既有 37 项测试 + import 冒烟 + Flask 路由注册校验兜底。

**Tech Stack:** Python 3.8+，stdlib（os/re/socket/sys）、flask、netifaces、pypinyin、werkzeug；测试用 `venv_3.8\Scripts\python.exe -m pytest`。

**拆分总路线（阶段 B/C/D 为后续，独立成篇；本计划只交付阶段 A）：**

| 阶段 | 动作 | 目标文件 | 预计削减 main.py |
|---|---|---|---|
| **A（本计划）** | 抽纯工具函数 | `core_utils.py` | ~330 行 |
| B | 抽 Config / ShareDirectory / 全局状态（含 `notify_gui_config_saved`、`password_change_timestamps`）→ `app_state.py`；把 routes 的 `from main import ...` 改到 `app_state` | `app_state.py` | ~160 行 |
| C | 抽日志（`setup_service_logger`、`loguru_handler`）→ `logging_setup.py` | `logging_setup.py` | ~80 行 |
| D | 剩余 GUI/服务代码收敛（本轮不做，牵涉 FileShareService/FileShareApp 循环） | — | — |

> **执行前提（阶段 A 完成后进入 B 之前务必先做）：** 用一条端到端回归脚本把当前默认/分享/TOTP 行为"钉住"，再碰 B 的循环重构。A 是纯搬移（安全），B 才会动身上循环依赖点（风险高）。

---

## Task 0: 建立回归基线

**Files:**
- Modify:（无，只读）

- [ ] **Step 1: 记录当前基线测试通过数**

Run: `.\venv_3.8\Scripts\python.exe -m pytest tests -q`
Expected: 尾部为 `.... passed in ...`，且 `passed` 计数 ≥ 37（当前统计为 37）。**把这个确切数字记下来**，它是后续每一步的"行为不变"判据。

- [ ] **Step 2: 确认被抽 15 个函数在 main.py 当前行号（用于 Task 2 删除）**

Run:
```powershell
Select-String -Path main.py -Pattern '^def (get_app_path|get_path|show_password_toggle_enabled|get_optimal_threads|get_local_ip|get_global_ipv6|chinese_to_pinyin|validate_alias|cleanup_old_logos|secure_filename_cn|safe_relative_path|get_client_info|format_file_size|partial_download|send_file_generator)'
```
Expected: 15 条 `行号: def ...`，与本计划 Task 2 中的删除区间相匹配。

---

## Task 1: 新建 `core_utils.py`（逐字搬运 + import）

**Files:**
- Create: `core_utils.py`

> 原则：这是**原样搬移**。下面的 complete file 中，每个函数体都是从 `main.py` 逐字复制，**禁止**顺手改动"看起来能优化"的细节。唯一允许的改动是：把 `PINYIN_AVAILABLE` 这个模块全局常量改名为本模块内部的 `_PINYIN_AVAILABLE`（原量只在 `chinese_to_pinyin` 一处使用），以保持本模块自包含。

- [ ] **Step 1: 写入 `core_utils.py`**

```python
"""纯工具函数集（从 main.py 抽离，阶段 A）。

这些函数不依赖 main.py 中的 Config / ShareDirectory / flask_app /
GUI 回调等状态，因此可独立存在，避免与 main.py 形成循环导入。
"""
import os
import re
import socket
import sys

import netifaces
from flask import request
from user_agents import parse

try:
    from pypinyin import Style, lazy_pinyin

    _PINYIN_AVAILABLE = True
except ImportError:
    _PINYIN_AVAILABLE = False


def get_app_path(tempdir=False):
    """获取应用程序路径 传True取临时文件夹路径"""
    if getattr(sys, "frozen", False):
        if tempdir:
            # 打包成单文件后程序运行生成的临时文件夹路径常用于取打包在EXE中的资源文件路径 如窗口图标等
            return sys._MEIPASS
        # 程序运行目录
        return os.path.dirname(os.path.abspath(sys.executable))

    else:
        # 开发环境路径
        return os.path.dirname(os.path.abspath(__file__))


def show_password_toggle_enabled():
    """程序目录存在 showpasswd 文件时启用「显隐密码」按钮；否则隐藏。"""
    return os.path.exists(os.path.join(get_app_path(), "showpasswd"))


def get_optimal_threads():
    """根据CPU核心计算最优线程数"""
    import multiprocessing

    cpu_count = multiprocessing.cpu_count()
    threads = cpu_count * 2

    # 设置线程  最小值 最大值
    min_threads = 4
    max_threads = 16

    return max(min_threads, min(threads, max_threads))


def get_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.normpath(os.path.join(base_path, relative_path))


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_global_ipv6():
    try:
        # 遍历所有网络接口
        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET6 in addrs:
                for addr in addrs[netifaces.AF_INET6]:
                    addr_ip = addr["addr"].split("%")[0]  # 去掉接口后缀
                    # 选择全局单播地址（2001: 或 240 开头），并排除临时地址
                    if (
                        addr_ip.startswith("2001:") or addr_ip.startswith("240")
                    ) and not addr.get("temporary", False):
                        return addr_ip
        return None
    except Exception:
        return "::1"


def chinese_to_pinyin(text):
    """将中文转换为拼音"""
    if not _PINYIN_AVAILABLE:
        # 如果没有pypinyin库，返回简单的处理
        return re.sub(r"[^\w]", "", text.lower())

    if not text:
        return ""

    # 使用pypinyin转换中文为拼音
    pinyin_list = lazy_pinyin(text, style=Style.NORMAL)
    # 连接拼音并移除非字母数字字符
    result = "".join(pinyin_list)
    # 只保留字母数字和下划线
    result = re.sub(r"[^\w]", "", result.lower())

    # 如果结果为空或以数字开头，添加前缀
    if not result or result[0].isdigit():
        result = "dir_" + result

    return result


def validate_alias(P):
    # 只允许字母、数字、下划线和连字符
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", P))


def cleanup_old_logos(logo_dir, current_logo_filename=None):
    """清理旧的logo文件，只保留当前使用的logo"""
    try:
        if not os.path.exists(logo_dir):
            return

        # 获取所有logo文件
        logo_files = []
        for file in os.listdir(logo_dir):
            if file.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                logo_files.append(file)

        # 删除除当前logo外的所有文件
        deleted_count = 0
        for file in logo_files:
            if current_logo_filename and file != current_logo_filename:
                try:
                    file_path = os.path.join(logo_dir, file)
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    print(f"删除旧logo文件失败 {file}: {e}")
            elif not current_logo_filename:
                # 如果没有当前logo，删除所有logo文件
                try:
                    file_path = os.path.join(logo_dir, file)
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    print(f"删除logo文件失败 {file}: {e}")

        if deleted_count > 0:
            print(f"已清理 {deleted_count} 个旧logo文件")

    except Exception as e:
        print(f"清理logo目录时发生错误: {e}")


def secure_filename_cn(filename):
    # 移除路径分隔符
    filename = filename.replace("/", "").replace("\\", "")
    # 移除其他危险字符
    filename = re.sub(r'[<>:"|?*]', "", filename)
    # 确保文件名不以点开头（隐藏文件）
    if filename.startswith("."):
        filename = "_" + filename
    return filename.strip()


def safe_relative_path(rel_path):
    """将前端传来的相对路径（可能含子目录）逐段净化，防止路径穿越。
    返回 POSIX 风格安全相对路径；非法输入返回 None。
    """
    if not rel_path:
        return None
    rel_path = rel_path.replace("\\", "/").strip("/")
    if not rel_path:
        return None
    parts = rel_path.split("/")
    safe_parts = []
    for p in parts:
        if p in ("", ".", ".."):
            return None
        cleaned = secure_filename_cn(p)
        if not cleaned:
            return None
        safe_parts.append(cleaned)
    return "/".join(safe_parts)


def get_client_info():
    user_agent_string = request.headers.get("User-Agent")
    user_agent = parse(user_agent_string)
    ip = request.remote_addr

    # Get detailed system and browser info
    os_info = f"{user_agent.os.family} {user_agent.os.version_string}".strip()
    browser_info = (
        f"{user_agent.browser.family} {user_agent.browser.version_string}".strip()
    )

    return f"IP:{ip} 系统:{os_info} 浏览器:{browser_info}"


def format_file_size(size_in_bytes):
    if size_in_bytes >= 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    elif size_in_bytes >= 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    else:
        return f"{size_in_bytes} B"


def partial_download(path, start, end):
    with open(path, "rb") as f:
        f.seek(start)
        chunk = 8192
        while True:
            read_size = min(chunk, end - f.tell() + 1)
            if read_size <= 0:
                break
            data = f.read(read_size)
            if not data:
                break
            yield data


def send_file_generator(path):
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            yield chunk
```

- [ ] **Step 2: 验证可独立导入**

Run: `.\venv_3.8\Scripts\python.exe -c "import core_utils as c; print(len([n for n in dir(c) if not n.startswith('_')]))"`
Expected: 打印一个 ≥ 15 的整数，且无 ImportError/Traceback。

- [ ] **Step 3: Commit（仅新增文件）**

```bash
git add core_utils.py
git commit -m "refactor: 抽出 core_utils 纯函数模块(阶段A)"
```

---

## Task 2: `main.py` 删除已迁移函数，改为 `from core_utils import *`

**Files:**
- Modify: `main.py`（删除若干函数定义 + 顶部插入一行 import）
- Test: 使用 Task 0 的基线全量测试

> 关键原则：**逐字搬走，不留副本**。删除后必须验证这些名字仍能从 `core_utils` 拿回同名函数（故用 `import *`）。

- [ ] **Step 1: 删除 pypinyin 探测块与 15 个函数定义**

删除以下**仅含这些定义的行区间**改为新的下划线内空行（保留其后其它内容）：

在 `main.py` 中删除：
1. 第 54–59 行（`try: from pypinyin ...` 探测块）
2. `get_app_path` 定义（原 L62–73）
3. `show_password_toggle_enabled`（76–78）
4. `get_optimal_threads`（151–162）
5. `get_path`（204–210）
6. `get_local_ip`（213–222）
7. `get_global_ipv6`（225–240）
8. `chinese_to_pinyin`（243–263）
9. `validate_alias`（266–268）
10. `cleanup_old_logos`（271–306）
11. `secure_filename_cn`（309–317）
12. `safe_relative_path`（320–338）
13. `get_client_info`（341–353）
14. `format_file_size`（589–595）
15. `partial_download`（598–609）
16. `send_file_generator`（612–618）

> 每个删除项务必核对函数边界（`def` 到下一个顶层 `def`/`class`/注释行之前）。不要删动相邻的 `show_password_toggle_enabled` 下方 `_loguru_initialized`、`setup_service_logger` 等**不在清单内**的内容。

- [ ] **Step 2: 顶部插入 `from core_utils import *`**

在 `main.py` 顶部 `from encryption import get_crypto, set_key_dir`（当前 L48）之后新增一行：

```python
from core_utils import *
```

> 位置选择：放在 L48 之后、`# Cheroot服务器` 注释（L50）之前。此时 `core_utils` 已是全量可用，`import *` 会把上述 15 个名字注入 `main` 命名空间，供后续 `main.py` 内部代码使用。

- [ ] **Step 3: 确认 `import main` 无异常**

Run: `.\venv_3.8\Scripts\python.exe -c "import main; print(main.flask_app is not None)"`
Expected: 打印 `True`，且无 `ImportError` / `NameError`。（`main.py` 在此后因 `L626 from routes import *` 而完整注册所有路由。）

- [ ] **Step 4: 增加一个保真冒烟测试**

将以下内容写入 `tests/test_core_utils_smoke.py`（断言 core_utils 与 main 导出同名函数且可调用，防止"改名/改签名"的隐性漂移）：

```python
import core_utils
import main

NAMES = [
    "get_app_path", "get_path", "show_password_toggle_enabled",
    "get_optimal_threads", "get_local_ip", "get_global_ipv6",
    "chinese_to_pinyin", "validate_alias", "cleanup_old_logos",
    "secure_filename_cn", "safe_relative_path", "get_client_info",
    "format_file_size", "partial_download", "send_file_generator",
]


def test_core_utils_exported_to_main():
    for name in NAMES:
        assert hasattr(core_utils, name), f"core_utils 缺少 {name}"
        assert callable(getattr(core_utils, name))
        assert hasattr(main, name), f"main 未导出 {name}"


def test_pure_behaviors_unchanged():
    assert core_utils.format_file_size(1024) == "1.00 KB"
    assert core_utils.format_file_size(5 * 1024 * 1024) == "5.00 MB"
    assert core_utils.validate_alias("a-b_1") is True
    assert core_utils.validate_alias("a b") is False
    assert core_utils.secure_filename_cn("../a/b") == "_a.b"
    assert core_utils.safe_relative_path("../etc") is None
    assert core_utils.safe_relative_path("dir/sub/file.txt") == "dir/sub/file.txt"
```

- [ ] **Step 5: 跑全量回归**

Run: `.\venv_3.8\Scripts\python.exe -m pytest tests -q`
Expected: **通过数与 Task 0 记录一致（≥37）**，且 `test_core_utils_smoke.py` 的 2 项也通过。

- [ ] **Step 6: 校验路由全量注册（冒烟）**

Run: `.\venv_3.8\Scripts\python.exe -c "import main; print(len(list(main.flask_app.url_map.iter_rules())))"`
Expected: 打印一个 ≥1 的路由数且无异常（应与此前注册过的所有 `@flask_app.route` 一致，无 ImportError）。

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_core_utils_smoke.py
git commit -m "refactor: main.py 移除已迁 core_utils 的函数并按需导入(阶段A)"
```

---

## Task 3: 更新 `routes/routes.py` 的 import 指向 `core_utils`

**Files:**
- Modify: `routes/routes.py`
- Test: 复用 Task 2 的全量回归 + Task 2 新增冒烟

- [ ] **Step 1: 顶部 import 拆成两组**

把 `routes/routes.py` 第 26–27 行：
```python
from main import flask_app, config, format_file_size, partial_download, send_file_generator, \
    get_client_info, secure_filename_cn, safe_relative_path, ShareDirectory, password_change_timestamps, get_app_path
```
替换为：
```python
from main import flask_app, config, ShareDirectory, password_change_timestamps
from core_utils import (
    get_app_path,
    format_file_size,
    partial_download,
    send_file_generator,
    get_client_info,
    secure_filename_cn,
    safe_relative_path,
)
```

- [ ] **Step 2: 函数内两处 `from main import cleanup_old_logos` 改指 core_utils**

`routes/routes.py` 第 1546 行与第 1587 行（两个函数内部）：
```python
    from main import cleanup_old_logos
```
替换为：
```python
    from core_utils import cleanup_old_logos
```

- [ ] **Step 3: 全量回归**

Run: `.\venv_3.8\Scripts\python.exe -m pytest tests -q`
Expected: 通过数 ≥ Task 0 基线，无 `ImportError`（尤其确认 routes 能正常 `import main` 且路由注册）。

- [ ] **Step 4: Commit**

```bash
git add routes/routes.py
git commit -m "refactor: routes 改从 core_utils 导入纯函数(阶段A)"
```

---

## Task 4: 人工验收（不可自动化部分，需手动）

**Files:** 无（仅手动操作）

- [ ] **Step 1: 启动 GUI 正常进入主界面**（`.\venv_3.8\Scripts\python.exe main.py`），确认无报错、目录列表正常展示。
- [ ] **Step 2: 启动 Web 服务后访问首页 + 一个 /api 端点**（如 `GET /api/diretories` 或 `GET /` 200），确认纯函数仍正常工作。
- [ ] **Step 3: 提交文档口径（可选）**，package 用 `main-onefile.spec` 构建一次确认 `core_utils.py` 被 PyInstaller 正确包含（该模块会被 main 顶部 import，自动纳入）。

---

## Self-Review（执行前自查清单）

请使用下方 checklist 复核本计划后，再交付执行：

- **是否每个 symbol 都已迁移并路由到新模块？** 15 个函数全部在 Task 1 `core_utils.py` 中，且 Task 3 显式把 `routes` 的引用改为 `core_utils`。
- **是否有未迁移函数仍被 routes 引用、且在 main.py 中被错误删除？** 被 `routes` 引用的还有 `ShareDirectory`、`password_change_timestamps`、`config`、`flask_app`——它们**不**在阶段 A 迁移范围，仍留在 main.py，因此 Task 3 顶部 import 保留 `from main import ...` 这几项。已在 Task 3 Step 1 中保留。
- **`PINYIN_AVAILABLE` 是否已被安全替换？** 是，`chinese_to_pinyin` 内部改引用 `_PINYIN_AVAILABLE`，探测块随迁进 `core_utils.py`；已在 Task 0/1 中覆盖。
- **import 循环是否有新增？** 无：`core_utils` 不依赖 main / routes；`main` 顶部 `from core_utils import *`（粗粒度，把符号注入本模块命名空间）。唯一需注意的既有循环 `main ⇄ routes` 保持现状，A 阶段刻意不动它。

---

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-08-07-split-main-core-utils.md`。两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派独立 subagent，任务间两次评审，迭代快。
**2. Inline Execution** — 本会话内用 executing-plans 逐 Task 执行，带检查点评审。