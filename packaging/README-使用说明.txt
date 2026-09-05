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

二、Codex 选项怎么填（核对日期：2026-09-05）

- CODEX_BIN：全新解压后通常默认显示 codex，保持不动即可；它表示自动查找并
  优先使用本包内置的 CLI。若显示的是解压目录内以
  codex-cli\x86_64-pc-windows-msvc\bin\codex.exe 结尾的完整路径，也同样正确。
  自定义时应填另一个 codex.exe 的完整路径，不要只填目录；留空等同于 codex。
- CODEX_PROFILE：通常留空。需要时只填 profile 名，例如 translation 对应
  $CODEX_HOME/translation.config.toml，不要填文件路径。
- CODEX_MODEL：可留空，或填精确模型 ID。留空且未选 profile 时不会继承 Codex
  桌面应用或用户 config.toml，而是由 CLI/账户选择推荐模型；本包 CLI 0.145.0
  在核对日通常选择 gpt-5.6-sol，但未固定，以后可能改变。
- CODEX_REASONING_EFFORT：可填 none、low、medium、high、xhigh、max；留空会被
  本程序明确设为 none，不会继承模型、CLI 或 profile 默认值。具体模型可能只支持
  其中一部分，ultra 暂不支持。
- CODEX_TIMEOUT：单次请求超时秒数，留空为 120。

常用模型 ID：gpt-6-astra、gpt-5.6-sol、gpt-5.6-terra、gpt-5.6-luna、
gpt-5.5、gpt-5.4、gpt-5.4-mini、gpt-5.3-codex-spark。GPT-6 Astra 仍在按账户
逐步开放，官方支持 low、medium、high、xhigh、max；使用它时不要让 effort 留空
变成 none。模型 ID 拼错、已退役、账户无权限或强度不兼容都会报错，不会自动换模型。

CODEX_BIN 只是程序路径，不含开发者账号、额度或登录凭据；每位用户仍须运行
Login-Codex.cmd 登录自己的账户。

三、包内文件

- PDFMathTranslate-Codex.exe：无终端窗口的桌面工作台。
- pdf2zh.exe：原命令行和浏览器 WebUI 启动器。
- Login-Codex.cmd：调用包内官方 Codex CLI 的登录脚本。
- codex-cli\：官方 Codex CLI 0.145.0 程序，不含登录数据。
- offline_assets_*.zip：离线版面模型资源。
- LICENSE：GNU AGPL-3.0 许可证。
- SOURCE-CODE.txt：这个二进制包对应的源码与提交。
- licenses\：Codex CLI、PyStand、Python 与离线资源的许可证和来源说明。

四、常见问题

- codex executable not found：确认完整解压，并检查应用旁边的
  codex-cli\x86_64-pc-windows-msvc\bin\codex.exe；目录缺失时重新下载 ZIP。
- Codex 未登录：再次双击 Login-Codex.cmd。
- 桌面窗口无法打开：安装 Microsoft Edge WebView2 Runtime。
  https://developer.microsoft.com/microsoft-edge/webview2/
- 提示缺少 VC++ 运行库：安装 Microsoft Visual C++ Redistributable x64。
  https://aka.ms/vs/17/release/vc_redist.x64.exe
- Windows SmartScreen 提示：本社区版目前没有付费代码签名。请先对照 Release
  页面的 SHA256SUMS.txt 校验下载文件，不要关闭防病毒软件。

五、隐私和说明

界面只绑定本机 127.0.0.1。PDF 解析主要在本机进行，但选择云翻译
服务后，需要翻译的文本会发送给相应服务商。Codex 后端会通过本机
包内 Codex CLI 使用当前 Windows 用户登录的 OpenAI/ChatGPT 账户。本程序不读取
或上传 ~/.codex/auth.json，也不会在发布包中携带任何登录凭据。

本项目是 PDFMathTranslate v1.9.11 的非官方 Codex 增强分支，不是
PDFMathTranslate 官方发布，也不是 OpenAI 官方产品或背书。

项目与更新：https://github.com/phxumy/PDFMathTranslate
问题反馈：https://github.com/phxumy/PDFMathTranslate/issues
