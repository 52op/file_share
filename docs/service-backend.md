# 后台服务（Windows 服务）启动方案说明

> 本文档供开发者 / AI 快速理解本项目「Windows 后台服务」的最终方案与历史坑位。
> 改动相关逻辑前请先读本文，避免重踩已验证的坑。

## 现状结论（一句话）

- **Windows 后台服务 = NSSM 包装器 + `--headless-server` 模式**：服务镜像由 NSSM（原生 C 小工具）担任，应用以普通进程运行，服务管理器正常启停、开机自启、崩溃自动重启，**任何 Windows 版本统一可用**。
- **不要试图让 Python 进程（pywin32）直接作服务镜像**：在 Server 2012 R2 上必然失败（见「为什么」），且在 PyInstaller 5.1+ 的 onefile 下也必然失败。

## 三种运行方式

| 方式 | 入口 | 说明 |
|---|---|---|
| GUI 前台 | 双击 `file_share.exe` | 在 GUI 进程内启动 Flask，需 GUI 保持打开 |
| Windows 服务（默认/推荐） | GUI 勾选「后台服务」→ 自动 NSSM 安装 | 服务管理器启停、开机自启、崩溃自动重启 |
| Linux | `file_share --headless-server` + systemd | 无 SCM 概念，天然丝滑（systemd 直接拉起进程） |

## 实现细节

- GUI `install_service()`（`main.py`）：
  - **优先 NSSM**：`nssm install FileShareService <exe> --headless-server`
    - 服务名保持 `FileShareService`，GUI 的启停 / 状态检测 / 开机自启逻辑全部兼容
    - 附带：`AppDirectory`、`Start=SERVICE_AUTO_START`、stdout/stderr 重定向到 `<主目录>/logs/`
  - **找不到 NSSM 时回退** `win32serviceutil.InstallService`（pywin32 直接服务，仅正常 Windows + PyInstaller 4.10 单文件可靠）
- NSSM 来源：`vendor/nssm.exe`（约 331KB，内嵌进单文件 / zip 包）；`_get_nssm_path()` 优先 exe 同目录 `nssm.exe`，其次从打包资源 `_MEIPASS` 释放到系统临时目录
- `--headless-server`（`run_headless_server()`）：不建 GUI、不走 SCM 握手，仅加载主配置并启动 HTTP/HTTPS 服务器后阻塞运行
- **主目录对齐**：服务进程统一使用主程序目录（`_SERVICE_MAIN_DIR` / `_resolve_service_main_dir()`），保证服务与 GUI 共用同一份 `share_config.json`、`config.key`、`logs/`，前端网页修改设置实时生效
- 卸载：`uninstall_service()` → `nssm remove FileShareService confirm` + `force_delete_service()` 兜底

## 为什么不能用 Python 直接作服务镜像（历史坑，勿重踩）

1. **PyInstaller onefile 是父/子双进程**：父进程（bootloader）负责解压，实际运行 Python 的是子进程。子进程调 `StartServiceCtrlDispatcher` 连接 SCM 的能力随 PyInstaller 版本变化：
   - **PyInstaller 4.10**：子进程能正常连接 SCM → onefile 可直接作服务（用户早期"30 多 MB 单文件能装服务"的真相）
   - **PyInstaller 5.1+**：onefile 架构调整，子进程无法连接 SCM → 服务启动报 **1063「无法连接服务控制器」→ 服务 1067**
2. **Server 2012 R2 额外坑**：本项目进程在该系统上无论 4.10 / 6.x / onedir 均 1063；但最小服务样例（minisvc）却能 RUNNING——属项目进程在该系统的特定握手兼容问题，未深究。NSSM 从握手层绕开，一劳永逸。
3. 因此：**服务握手一律交给原生宿主（NSSM），Python 只作业务子进程。**

## 构建要求（务必遵守）

- **PyInstaller 固定 4.10**：`requirements.txt` 已注明，CI（`.github/workflows/release.yml`）已对齐。
  ```bat
  pip install pyinstaller==4.10 pyinstaller-hooks-contrib==2022.15
  ```
- 构建命令：
  - 单文件：`pyinstaller main-onefile.spec --noconfirm` → `dist/file_share.exe`（内嵌 nssm）
  - zip/onedir：`pyinstaller main-zip.spec --noconfirm` → `dist/main/`（内嵌 nssm）
- **不要随意升级 PyInstaller 大版本**；如确需升级，请先验证「无 NSSM 回退路径」是否仍可用，或删除回退分支后只依赖 NSSM。

## 相关代码位置（main.py）

- `install_service` / `uninstall_service` / `_get_nssm_path` / `_run_nssm` —— NSSM 服务安装
- `run_headless_server` —— `--headless-server` 入口（服务/计划任务/systemd 共用）
- `FileShareService` —— pywin32 服务类（保留备用，非默认路径）
- `_SERVICE_MAIN_DIR` / `_resolve_service_main_dir` —— 服务主目录对齐（配置/密钥/日志与 GUI 一致）
- `_append_svc_diag` —— 服务启动诊断日志（`<exe 目录>/svc_diag.log`，定位启动失败用）
