## v1.9.11-codex.5.1 — 修复 ChatGPT 后台翻译标签页的推理强度选择

修复 ChatGPT 已连接、模型可正常读取，但点击翻译后提示“网页推理强度的位置与文字标签尚未同步”的问题。

- 新建 PDF 翻译标签页位于后台时，Chrome 可能忽略发给滑块的 CDP 方向键，导致档位一直停在 High，翻译在发送前被同步校验中止。本版在后台标签页内对已确认的推理强度菜单分发方向键和 Escape，完成档位读取、选择与菜单关闭。
- 保留实际滑块位置、文字标签、模型和聊天归属的校验；后台菜单操作不会切换前台标签页或操作其他输入框。
- 增加回归测试，覆盖后台 CDP 按键无效、档位恢复、菜单关闭、禁用及引用控件拒绝，以及前台草稿保持。
- 发布构建、Windows 安装器版本字段与更新检查支持 `.5.1` 形式的补丁版本号，并按数字比较后续版本。

**codex.5 用户请从本页手动下载 `PDFMathTranslate-Codex-Setup-win64.exe` 并覆盖安装。旧版更新器不识别 `.5.1` 版本号，不能依靠 codex.5 的“检查更新”完成这次升级。**

关闭正在运行的翻译软件，运行 EXE 安装到原位置，即可保留设置、账号、翻译缓存和译文工作区。启动新版后，在 ChatGPT 服务中连接已登录的 Chrome，刷新模型和推理强度，再对原 PDF 点击翻译。同页 `SHA256SUMS.txt` 提供安装包完整性校验。

This hotfix fixes reasoning-effort selection in background ChatGPT PDF tabs, where Chrome could drop CDP keyboard input despite a working browser connection. It also adds hotfix version support to release builds and the updater. Users of codex.5 must manually download and install this EXE; the older updater cannot parse the `.5.1` tag. Existing settings and documents are preserved.

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
