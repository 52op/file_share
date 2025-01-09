# file_share - HTTP 文件分享服务器
![软件界面](https://github.com/52op/file_share/blob/master/preview_1.png "软件界面")

file_share 是一个基于 Python Flask 和 Waitress 的轻量级 HTTP 文件分享工具，支持通过浏览器快速访问和下载共享的文件。它不仅可以作为前台窗口服务运行，还可以安装为 Windows 系统服务，实现开机自启动。无论是局域网内的文件共享，还是临时搭建一个文件下载服务器，file_share 都能轻松应对。

## 功能特性

- **简单易用**：只需选择要共享的文件夹，设置访问密码（可选），启动服务后即可通过浏览器访问。

- **多线程支持**：基于 Waitress 的多线程服务器，适合生产环境使用。

- **IPv4 和 IPv6 双栈支持**：无论是传统的 IPv4 网络还是新一代的 IPv6 网络，file_share 都能完美支持。

- **密码保护**：可以为每个共享目录设置独立的访问密码，确保文件安全。

- **管理密码**：设置一个管理密码，该密码在前端 WEB 页面中任何需要输入密码的地方都可以使用。

- **分享链接生成**：在 WEB 页面中，你可以把当前文件或目录生成一个分享链接，支持加密分享链接，就像百度网盘一样。

- **自动清理**：支持自动清理用户打包下载产生的临时文件和过期的共享链接，避免磁盘空间浪费。

- **系统服务支持**：可以将 file_share 安装为 Windows 系统服务，实现开机自启动，无需手动操作。

- **日志记录**：详细的日志记录功能，方便排查问题和监控服务状态。

## 使用场景

- **局域网文件共享**：在办公室或家庭局域网内快速共享文件，无需借助第三方工具。

- **临时文件服务器**：在需要临时搭建文件下载服务器时，file_share 可以快速部署并提供服务。

- **跨设备文件传输**：在不同设备之间传输文件，尤其是当设备之间无法直接连接时，file_share 可以作为一个中转站。

## 快速开始

### 1. 下载与运行
#### a. 直接使用打包好的
你可以从 [Release 页面](https://github.com/52op/file_share/releases) 下载 file_share 的打包版本，解压后直接运行 `file_share.exe`。

#### b.自己编译
下载压缩包，解压
```
# 安装依赖 可能不全，因为不是使用pip 生成的requirements ，提示少什么 自己pip install 什么 
pip install -r requirements.txt
# 打包成单文件
pyinstaller main-onefile.spec
# 打包成非单文件，方便改前端模板
pyinstaller main-zip.spec
```


### 2. 添加共享目录

1. 运行 `file_share.exe`，程序启动后会显示一个简洁的界面。

2. 点击“添加目录”，选择你要共享的文件夹，设置显示名称和访问密码（可选）,也可以直接拖曳文件夹到程序窗口。

3. 你可以添加多个共享目录。
![软件界面](https://github.com/52op/file_share/blob/master/preview_2.png "软件界面")
### 3. 启动服务

1. 点击“启动服务”按钮，file_share 会显示一个访问链接。

2. 在浏览器中输入该链接，访问共享的文件。

### 4. 管理共享文件

在 WEB 页面中，你可以浏览共享的文件，下载文件，甚至生成分享链接。如果设置了管理密码，你还可以通过管理密码进行更多操作。

### 5. 安装为系统服务

如果你希望 file_share 在后台运行，并且开机自启动，可以点击“安装为系统服务”按钮。安装完成后，file_share 将以 Windows 服务的形式运行，无需手动启动。

## 技术细节

file_share 基于 Python 开发，使用了以下技术栈：

- **Flask**：轻量级的 Web 框架，用于处理 HTTP 请求和响应。

- **Waitress**：生产级的 WSGI 服务器，支持多线程，适合高并发场景。

- **Tkinter**：Python 的标准 GUI 库，用于构建程序界面。

- **TTKBootstrap**：基于 Tkinter 的现代化主题库，使界面更加美观。
![前端WEB界面](https://github.com/52op/file_share/blob/master/preview_3.png "前端WEB界面")
![图片预览](https://github.com/52op/file_share/blob/master/preview_4.png "图片预览")
![在线代码类文档预览编辑](https://github.com/52op/file_share/blob/master/preview_5.png "在线代码类文档预览编辑")
## 未来计划

- 没有计划

## 贡献与反馈

如果你有任何问题或建议，欢迎通过邮件（[letvar@qq.com](mailto:letvar@qq.com)）与我联系。也欢迎提交 Issue 或 Pull Request，帮助改进 file_share。

## 许可证

file_share 采用 [MIT 许可证](#)，你可以自由使用、修改和分发它。
