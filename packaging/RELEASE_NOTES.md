## v1.9.11-codex.5 — 账号登录、并行翻译、实时预览与 EXE 更新

本版开始提供 Windows x64 EXE 安装程序：下载 `PDFMathTranslate-Codex-Setup-win64.exe` 并安装，无需手动解压运行。

- 在软件内查看 Codex 登录状态、发起官方账号登录，沿用现有登录凭据。
- 默认与空白推理强度改为 `low`，自动模型配置兼容旧版保存的 `none`，避免当前模型拒绝旧默认值。
- 增加可选 Codex Fast 档位和并行加速选择，并依据真实完成的翻译工作滚动估算剩余时间。Fast 取决于账号与模型支持，可能消耗更多额度；2 倍并行不承诺任何文档都能严格快 2 倍，实际仍受模型、网络及账号限速影响。
- 标准翻译时逐步预览已完成页面；完整任务完成后提供最终译文文件。BabelDOC 实验流程仍在结束后显示完整 PDF。
- 在软件内检查本仓库的新版本，点击后下载安装包，核验 SHA-256 与大小，再关闭窗口完成升级并重启。翻译过程中禁止安装更新。
- 安装器提供开始菜单、可选桌面快捷方式与卸载入口，覆盖升级保留用户设置、账号和译文工作区。
- 包含前期 PDF 排版、文字保留与 ChatGPT 浏览器桥接修复，以及桥接所需的 Node.js 和锁定依赖。

### 安装与升级

1. 下载并运行 `PDFMathTranslate-Codex-Setup-win64.exe`。
2. 打开软件，在 Codex 账号区域登录或查看已有登录状态。
3. 上传 PDF 并开始翻译。

旧 ZIP 用户可直接安装此版本。安装器不会删除旧解压目录中的文件；已有配置和账号继续沿用。新桌面版的译文工作区位于 `%USERPROFILE%\.config\PDFMathTranslate\workspace`，升级和卸载均不会清除该目录。

安装包包含 Python、Codex CLI 0.145.0、Node.js 24.19.0、锁定依赖及离线版面/OCR/字体资源；不包含账号、API Key、个人 PDF 或登录凭据。使用云服务仍需联网并消耗对应账号额度。此安装包未做代码签名，Windows 可能显示未知发布者；同页 `SHA256SUMS.txt` 提供完整性校验。

The Windows x64 EXE installer bundles the desktop runtime, official Codex CLI, Node.js browser bridge, and offline layout/font assets. This release adds in-app Codex account controls, adjustable concurrency, rolling ETA, incremental page preview, and explicit SHA-256-verified application updates. Upgrades preserve user settings and documents. This is an unofficial community edition.

Source and repository: https://github.com/phxumy/PDFMathTranslate
