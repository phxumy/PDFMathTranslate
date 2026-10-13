PDFMathTranslate Codex 增强桌面版（非官方）
===============================================

一、安装和启动
1. 在本项目 GitHub 发行页面下载 PDFMathTranslate-Codex-Setup-win64.exe。
2. 双击安装，默认只为当前 Windows 用户安装，无需单独安装 Python 或 Node.js。
   安装后可从开始菜单启动，也可选择创建桌面快捷方式。
3. 在软件里的 Codex 账号区域查看状态或发起登录，按提示完成官方账号验证。
   不再需要先找外部 CMD 文件登录；旧 Login-Codex.cmd 仅保留作兼容入口。
4. 上传 PDF 并开始翻译。已完成的页面会逐步显示，全部完成后可下载最终 PDF。

二、并行与预计时间
可选 Codex Fast 模式请求账号支持的快速服务档位，取决于模型与账号可用性，可能消耗更多额度。
“2 倍并行”提高同时处理的翻译任务数量，不保证任何文档都能严格缩短一半时间。
具体速度取决于模型、网络、段落长度、账号限速与文档布局。遇到限速时降低并行数。
预计剩余时间按照实际完成的翻译工作滚动估计，刚开始的少量样本可能不稳定。

三、检查与安装更新
在软件内点击检查更新；发现新版本后，明确点击下载并安装，软件才会下载。
只访问 phxumy/PDFMathTranslate 的 GitHub 正式发行，核对安装包大小与 SHA-256。
校验成功后关闭当前窗口并安装，完成后重新打开。翻译过程中禁止安装更新。
下载或校验失败不会运行安装包。浏览器模式请通过发行页面下载安装。

从旧 ZIP 升级时，运行本版 EXE 安装程序即可；旧解压目录和其中的译文不会删除。
账号和已有软件设置会沿用用户目录中的数据。可确认新版正常后自行处理旧目录。

四、文件位置与卸载
软件设置：%USERPROFILE%\.config\PDFMathTranslate
新桌面版翻译工作区：%USERPROFILE%\.config\PDFMathTranslate\workspace
Codex 登录保留在官方 CLI 原有的用户账号目录（或已配置的 CODEX_HOME）。
升级和卸载不删除这些用户数据。卸载入口在 Windows 设置的“已安装的应用”中。

五、发行内容
内含官方 Codex CLI 0.145.0、Node.js 24.19.0、锁定版本的运行依赖及离线版面/OCR/字体资源。
不包含任何人的账号、凭据、API Key、套餐或免费额度。云端翻译仍需网络并消耗服务额度。
此软件并非 OpenAI 或上游项目的官方发行。

适用 Windows 10/11 x64；桌面窗口需要 Microsoft Edge WebView2。
本社区安装包没有代码签名，Windows 可能提示“未知发布者”。请从本仓库发行页面下载，
可使用同页 SHA256SUMS.txt 核对文件完整性。缺少运行组件时从微软官网下载 WebView2
或 Visual C++ x64 Redistributable。

仓库：https://github.com/phxumy/PDFMathTranslate
发行：https://github.com/phxumy/PDFMathTranslate/releases/latest
反馈：https://github.com/phxumy/PDFMathTranslate/issues
