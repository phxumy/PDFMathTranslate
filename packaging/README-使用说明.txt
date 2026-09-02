PDFMathTranslate Codex 增强桌面版（非官方）
===============================================

适用系统：Windows 10/11 x64
启动程序：PDFMathTranslate-Codex.exe

一、第一次使用

1. 完整解压本 ZIP，不要直接在压缩包预览窗口中运行。
2. 如果要使用 Codex 翻译服务，双击 Login-Codex.cmd，在浏览器中完成官方登录。
3. 双击 PDFMathTranslate-Codex.exe。
4. 在 Service 中选择 Codex，上传 PDF 后点击 Translate。

本包已包含经过哈希校验的官方 Codex CLI 0.145.0 程序，无需单独安装 Python
或 Codex CLI。它不包含账号、套餐或免费额度、API Key 或任何人的登录凭据。
Codex 官方说明：https://developers.openai.com/codex/cli

二、包内文件

- PDFMathTranslate-Codex.exe：无终端窗口的桌面工作台。
- pdf2zh.exe：原命令行和浏览器 WebUI 启动器。
- Login-Codex.cmd：调用包内官方 Codex CLI 的登录脚本。
- codex-cli\：官方 Codex CLI 0.145.0 程序，不含登录数据。
- offline_assets_*.zip：离线版面模型资源。
- LICENSE：GNU AGPL-3.0 许可证。
- SOURCE-CODE.txt：这个二进制包对应的源码与提交。
- licenses\：Codex CLI、PyStand、Python 与离线资源的许可证和来源说明。

三、常见问题

- codex executable not found：确认完整解压，并检查应用旁边的
  codex-cli\x86_64-pc-windows-msvc\bin\codex.exe；目录缺失时重新下载 ZIP。
- Codex 未登录：再次双击 Login-Codex.cmd。
- 桌面窗口无法打开：安装 Microsoft Edge WebView2 Runtime。
  https://developer.microsoft.com/microsoft-edge/webview2/
- 提示缺少 VC++ 运行库：安装 Microsoft Visual C++ Redistributable x64。
  https://aka.ms/vs/17/release/vc_redist.x64.exe
- Windows SmartScreen 提示：本社区版目前没有付费代码签名。请先对照 Release
  页面的 SHA256SUMS.txt 校验下载文件，不要关闭防病毒软件。

四、隐私和说明

界面只绑定本机 127.0.0.1。PDF 解析主要在本机进行，但选择云翻译
服务后，需要翻译的文本会发送给相应服务商。Codex 后端会通过本机
包内 Codex CLI 使用当前 Windows 用户登录的 OpenAI/ChatGPT 账户。本程序不读取
或上传 ~/.codex/auth.json，也不会在发布包中携带任何登录凭据。

本项目是 PDFMathTranslate v1.9.11 的非官方 Codex 增强分支，不是
PDFMathTranslate 官方发布，也不是 OpenAI 官方产品或背书。

项目与更新：https://github.com/phxumy/PDFMathTranslate
问题反馈：https://github.com/phxumy/PDFMathTranslate/issues
