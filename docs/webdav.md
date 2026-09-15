# WebDAV（可选功能）

> 默认关闭。启用后在**独立端口**提供 WebDAV 挂载（不占用现有 HTTP/HTTPS 服务端口），
> 支持 Windows 映射网络驱动器、macOS Finder、手机文件管理器（FE / Solid Explorer）等客户端。

## 启用方式

- **GUI**：勾选「启用 WebDAV」→ 设置端口（默认 8081）→ 点启动服务。后台服务运行中需重启服务使配置生效。
- **配置文件**：`share_config.json` 增加
  ```json
  "webdav_enabled": true,
  "webdav_port": 8081
  ```

> 修改任一密码后需重启服务使 WebDAV 认证生效（映射表在启动时构建）。

## 认证映射（HTTP Basic）

| 用户名 | 密码 | 权限 |
|---|---|---|
| `admin` | 超级管理员密码 | 全部目录 **读写** |
| `dir_<alias>` | 该目录**管理密码**（admin_password） | 该目录 **读写** |
| `guest` | 全局密码（未设置则不开放） | 全部目录 **只读** |
| `<alias>` | 该目录**访问密码**（password） | 该目录 **只读** |

- 目录无访问密码 → 该目录只读用户不存在（仅 admin / 全局 guest 可用）
- 未认证请求返回 401
- 写操作（PUT/MKCOL/DELETE/MOVE/COPY/PROPPATCH/LOCK/UNLOCK）由无写权限用户发起 → 403
- 写操作计入审计（`dav_write` 事件，含角色/IP/UA，见 `/stats`）

## 客户端挂载示例

**Windows 映射网络驱动器**
```
映射网络驱动器 → 文件夹: http://<服务器>:8081/<alias>
   或 \\<服务器>@8081\dav\<alias>
连接时输入用户名 admin / 管理密码（或 dir_<alias> / 目录管理密码）
```

**macOS Finder**
```
访达 → 前往 → 连接服务器 → http://<服务器>:8081/<alias>
```

**Linux davfs2**
```
sudo mount -t davfs http://<服务器>:8081/<alias> /mnt/dav -o username=guest
```

**curl 测试**
```bash
# 列目录（只读用户）
curl -u guest:<全局密码> -X PROPFIND -H "Depth: 1" http://127.0.0.1:8081/<alias>/

# 上传（读写用户）
curl -u admin:<管理密码> -T 本地文件 http://127.0.0.1:8081/<alias>/文件名
```

## 说明

- 独立端口默认 HTTP；如需公网建议套反向代理/隧道，或后续扩展 HTTPS（证书与现有 SSL 一致时可由用户自行选配）
- 只读用户通过 WebDAV 下载等价于网页下载权限，无新增风险；写权限仅限 admin / 目录管理员
- 实现基于 `wsgidav`（依赖已写入 requirements.txt），启动失败不影响主服务（默认关闭）
