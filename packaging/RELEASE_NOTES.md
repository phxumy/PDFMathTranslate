## v1.9.11-codex.5.4 — 更新 Codex CLI 与多 PDF 顺序队列

内置 Codex CLI 从 `0.145.0` 更新至官方稳定版 `0.163.0`，解决旧 CLI 调用 `gpt-6-luna` 时返回 HTTP 400 的兼容问题。保持 `CODEX_BIN=codex` 即使用新版本，模型 ID 和推理强度仍由用户明确选择。

- 支持一次选择多个 PDF，按文件列表顺序逐个翻译。语言、页码范围、服务、模型和推理强度应用于整条队列；每份 PDF 内部仍遵守所选服务的并行设置。
- 显示当前文件、页码进度和队列状态，保留所有已完成文件的译文及双语下载。失败时暂停后续文件；“停止队列”停止当前任务并阻止下一份开始。
- 同一窗口中重试相同队列和设置会跳过已完成且文件仍存在的结果；修改文件或设置、删除输出、或选择 Ignore cache 会重新处理。跨重启可重新选择文件并复用翻译缓存。
- 对结构化的模型、参数或权限拒绝直接显示 HTTP 状态和原始原因，停止无效重试，避免只显示 `RetryError`。临时网络故障仍保留重试。
- 更新中英文模型 ID 说明，补充 GPT-6 Luna / Sol / GPT-6.1 Sol、有效推理强度、已退役型号及 CLI 版本区别。

CLI 官方压缩包固定 SHA-256：`901028b285614a9974f431f9d9f6534b3e5ed8d62eba4ba64f4762c00f040522`。验证覆盖 `gpt-6-luna + max`、真实 PDF 第一页队列、完成后重复运行不新增请求、停止/失败/恢复，以及安装与覆盖升级。

本次真实 PDF 验证范围为所选文件的第一页；不声称所有页已经验证。升级保留账户、配置、缓存和已有译文。原来显式填写其他 CLI 路径的用户仍使用该路径；改为 `codex` 才会选择包内版本。

This release bundles official stable Codex CLI 0.163.0 and adds an ordered multi-PDF queue with per-file progress, retained downloads and resume in the current session. Permanent structured model/access rejections now stop with their original reason.

## v1.9.11-codex.5.3 — 修复第六批中断、续传与正文漏译

修复 ChatGPT 连续翻译几批后反复提示“同一 PDF 的上一批尚未确认完成”的问题，以及 Nature 论文正文被误保留为英文的问题。

- ChatGPT 网页会卸载较早的消息节点。本版按完整的已验证消息 ID 历史核验当前连续后缀，不再要求网页节点数量一直增长；仍校验原聊天地址、完整请求、最后消息及回答归属，拒绝未知、插入、重复或乱序消息。
- 重试时若缓存改变了批次顺序，先取回原任务中未完成批次的回答，再处理新批次。失败恢复仍会停止发送，不重发未确认请求。
- PDF Form 对象中的翻译中断会直接停止当前页，保留最初错误，不再误启动后续批次。
- 修正 `HardingText` 等普通字体中的 `Text` 被误匹配为 `TeX` 数学字体，避免摘要和正文整体漏译。真正的公式字体仍保留。
- 编号机构地址不再误判为作者名单；紧凑脚注机构在原区域内自然换行，避免额外强制分行导致字体过小。

已使用用户报告问题的 Nature Physics 论文第一页实测：取回原第六批、在同一聊天中继续超过五轮、完成摘要与正文翻译并生成预览及 PDF；再次运行不新增翻译请求。另验证发布安装包的安装、覆盖升级及页面生成。本次实测范围为第一页，不声称全九页已经验证。

从 `.5.2` 可通过应用内更新或下载 EXE 覆盖安装。保留用户设置、缓存和原聊天记录。

This release handles virtualized ChatGPT conversation history, recovers pending batches before replanned work, propagates PDF Form translation failures, and fixes ordinary Text fonts being mistaken for TeX math fonts. Validation uses the reported paper's first page and a repeat run with no new submissions.

## v1.9.11-codex.5.2 — 修复首批回答读取与旧任务恢复

修复 `.5.1` 中连接和模型读取正常，但翻译时报“等待回答时原聊天发生变化”或“原聊天暂不可用且没有可恢复地址”的问题。

- 首次发送时，ChatGPT 会经过 `local-chatgpt:` 临时聊天地址，再分配正式地址。本版在原消息 ID、完整原文和消息顺序仍一致时接受这段正常转换，保存实际观察到的临时标识，并将其传递给同一 PDF 的后续批次。
- 正式地址下的实时回答节点可能仍使用临时标识。本版用已绑定的临时标识核验这些回答，继续保留正式会话地址、用户消息和回答归属校验。
- 对因旧版模型选择失败而留下的任务，仅在没有模型绑定、提交记录或历史消息时允许重新打开空白任务页。已经发送过的批次仍恢复原聊天，不会自动重发。
- 兼容实际网页中 `High/高`、`Medium/中`、`Instant/即时` 的标签变化。以完整 PDF 和设置身份查找唯一旧任务，保留原请求及缓存；存在多个已使用的别名任务时停止自动接管。
- 已结束但正文为空的回答会明确提示先在原聊天重新生成，不再一直等待。程序不会自行重新生成或重复发送。

验证包括真实 ChatGPT 连续两批翻译、同一聊天复用，以及两次恢复均不增加发送次数；另包含离线回归、安装与覆盖升级检查。

从 `.5.1` 可使用应用内更新，也可手动下载本页 EXE 覆盖安装。`.5` 用户仍需手动下载。升级保留账号、设置、原请求、翻译缓存和译文工作区。

This hotfix binds ChatGPT's provisional client conversation ID to its permanent URL, restores first-response capture and later batches, and preserves pending requests across observed effort-label translations. Completed empty replies are reported explicitly; recovery never resends or regenerates automatically.

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
