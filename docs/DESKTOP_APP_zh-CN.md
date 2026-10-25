# Windows 桌面工作台

本分支提供一个非官方 Windows 桌面外壳，用独立的 WebView2 窗口承载原 PDFMathTranslate WebUI。翻译核心和界面组件没有复制，因此原有翻译服务、语言、页码、缓存、字体、Prompt、BabelDOC 和 Codex 配置继续使用同一套代码。

> [!IMPORTANT]
>
> 这是基于 PDFMathTranslate v1.9.11 的社区修改版，不是 PDFMathTranslate
> 官方发布，也不是 OpenAI 官方产品或背书。公开 Release 内置固定版本的官方 Codex
> CLI 程序，但不包含账号、套餐额度、Codex 登录凭据、API Key、用户论文或翻译缓存。

## 普通用户：下载即用

### 1. 准备环境

- Windows 10/11 x64；
- 建议安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；
- 无需安装 Python，也无需单独安装 Codex CLI；`.6.1` 发布包已带经过哈希校验的官方稳定版 Codex CLI 0.163.0。

如果以后想改用系统里更新的 Codex CLI，可以参考 [OpenAI 官方 Codex CLI 页面](https://developers.openai.com/codex/cli)。OpenAI 当前提供的 Windows 安装命令是：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

安装完成后可在 PowerShell 运行 `codex --version`；若要让本程序使用这份系统安装，
在界面的 `CODEX_BIN` 中填写其完整路径。通常没有必要这样做。

无论使用内置还是系统安装的 Codex CLI，登录都属于当前 Windows 用户。不要把
`~/.codex/auth.json` 复制给别人或上传到任何网站。

发布包第一次使用无需打开 PowerShell；在软件内点击“登录 Codex”即可。系统安装的
OpenAI 官方 CLI 也可以直接运行：

```powershell
codex
```

### 2. 下载和启动

1. 打开[本项目最新 Release](https://github.com/phxumy/PDFMathTranslate/releases/latest)。
2. 下载并运行 `PDFMathTranslate-Codex-Setup-win64.exe`。安装包包含离线版面资源，无需手动解压。
3. 从开始菜单或桌面快捷方式启动 **PDFMathTranslate Codex Studio**。
4. 选择 `Codex` 即可查看登录状态；点击“登录 Codex”，在官方浏览器页面完成验证，状态会自动刷新。浏览器回调不可用时可改用设备验证码登录。
5. 上传 PDF，设置语言和页码后点击 `Translate`。模型、推理强度和 CLI 路径位于“模型与服务设置”。
6. 以后在“关于与更新”点击“检查更新”和“下载并安装更新”。程序先下载并校验，成功后退出并安装，保留账号、配置和缓存。

无需安装 Python。包内的 `pdf2zh.exe` 仍可用于原浏览器 WebUI 和命令行模式。

也可以直接使用这个固定下载地址：

[下载 Windows EXE 安装程序](https://github.com/phxumy/PDFMathTranslate/releases/latest/download/PDFMathTranslate-Codex-Setup-win64.exe)。旧 ZIP 用户首次升级到 .5 需下载安装程序；从 .5 开始支持应用内更新。

### 翻译速度、实时预览和估时

- **并行批次数**默认 4，可设 1–8；“双倍并行”将其乘以 2，最多 16。只有足够的待处理批次才能利用并发，遇到限流应降低。翻译质量校验与跨页衔接仍保留。
- **Codex Fast 模式**单独启用，使用官方加速服务，可能增加额度消耗，需账号与模型支持；它不等于固定两倍速。见[官方速度说明](https://learn.chatgpt.com/docs/agent-configuration/speed)。
- **标准翻译实时预览**按原文页码显示真正完成翻译和排版的页面；跨页段落可能需要读入下一页后才显示上一页。翻页按钮保持不变，未译页显示原文，已译页原位替换；完成后可下载完整 PDF。BabelDOC 实验流程目前在任务结束后显示 PDF。
- **时间估算**跳过首个冷启动样本，收集至少三个后续完成页的实际间隔，再按最近最多八页估算范围。缓存、并行、重试会体现在实际耗时里；样本不足或等待异常变长时显示“估算中”。字体处理、PDF 合并和保存单独显示阶段。
- 取消后已完成页和其余原文页仍可查看；用相同设置重试可利用已有翻译缓存。
- 完成后显示每篇和本次队列耗时。
- “翻译日志与本地文件管理”保留任务时间、进度、错误与缓存统计，提供原文/译文/双语下载及日志导出。缺失文件显示“已删除”；识别不到的作者、期刊或年份显示“未识别”。
- 清理前请保存需要的译文并勾选确认；翻译或更新期间禁止清理。旧版任务历史不强制迁移，BabelDOC 自有缓存不纳入标准缓存管理。

### 3. 校验下载文件

Release 同时发布 `SHA256SUMS.txt`。下载后可在安装程序所在目录运行：

```powershell
Get-FileHash ".\PDFMathTranslate-Codex-Setup-win64.exe" -Algorithm SHA256
```

输出应与 `SHA256SUMS.txt` 一致。由于社区版目前没有付费代码签名，Windows
SmartScreen 可能显示未知发布者；请先核对哈希和下载域名，不要为运行本程序而关闭
Microsoft Defender。

### 4. 常见启动问题

- `.6` 打开“翻译日志与本地文件管理”后提示 `Parameter interrupted is not a valid keyword argument`：升级到 `.6.1`。这是自动滚动回调与新版 Edge / WebView2 的兼容问题，不代表翻译中断，无需清空缓存。
- `codex executable not found`：检查安装目录中是否存在 `codex-cli\x86_64-pc-windows-msvc\bin\codex.exe`；若目录缺失，请重新运行安装程序修复。
- Codex 未登录：在软件内点击“登录 Codex”；“刷新登录状态”会读取官方 CLI 的登录结果。
- 窗口无法建立：安装 WebView2 Runtime 后重试。
- 提示缺少 VC++ 组件：安装微软官方 [Visual C++ Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
- 7860 端口已占用：桌面版会自动寻找后续空闲端口；只有显式指定固定端口时才会严格使用该端口。
- 翻译频繁限流或超时：先关闭“双倍并行”，把“并行批次数”设为 `1` 或 `2`。

## 界面特点

- 独立 Windows 窗口，不再自动打开 Chrome 或 Edge 浏览器。
- 启动页会立即出现，版面模型和本地服务在后台加载。
- 仅绑定 `127.0.0.1`，不会将翻译界面开放到局域网。
- 自动从 7860 开始选择可用端口，也支持通过 `--serverport` 指定固定端口。
- 允许下载翻译结果，并将外部链接交给系统默认浏览器。
- 关闭窗口时同步关闭 Gradio 服务，不留下后台进程。
- 若桌面组件启动失败，会在窗口中显示错误；原浏览器 WebUI 和命令行不受影响。
- 经典浏览器 WebUI 未指定端口时也会自动寻找 7860 之后的可用端口；显式 `--serverport` 仍严格使用指定端口。

## 从源码运行

Windows 10/11 建议安装 Microsoft Edge WebView2 Runtime。现代 Windows 11 通常已经自带。

```powershell
python -m pip install -e ".[desktop]"
pdf2zh --desktop
```

也可以使用专用入口：

```powershell
pdf2zh-desktop
```

调试桌面窗口：

```powershell
pdf2zh --desktop --debug
```

指定本机端口或自定义版面模型：

```powershell
pdf2zh --desktop --serverport 7868 --onnx "D:\models\doclayout.onnx"
```

## 与原程序的兼容关系

| 启动方式 | 行为 |
| --- | --- |
| `PDFMathTranslate-Codex.exe` | 启动独立桌面工作台，不显示终端窗口。 |
| `pdf2zh.exe` 双击 | 保留经典行为，启动浏览器 WebUI。 |
| `pdf2zh -i` | 保留浏览器 WebUI。 |
| `pdf2zh --desktop` | 从命令行启动桌面窗口。 |
| `pdf2zh document.pdf ...` | 保留所有原命令行翻译参数。 |

`--share`、`--authorized` 是对外提供 WebUI 时使用的选项，不应用于只绑定本机的桌面窗口。MCP、SSE、Flask、Celery 和 Docker 等服务端模式也继续通过原命令行运行。

选择 Codex 服务时，线程数会自动设为 `4`，输入框保持可编辑。默认设置会将原有翻译批次最多并行执行 4 个；批次数量和内容不变，所以总输入/输出量通常不会变成 4 倍，只会提高瞬时请求速率。并行度越高，越容易遇到账户限流，也会同时运行更多 `codex.exe` 进程，如遇限流或电脑负载过高，可以调低为 `1` 或 `2`。

桌面版会隐藏所有后台 Codex CLI 控制台窗口，翻译过程不会抢占桌面焦点。

## 目录、代码和扫描件

目录和图目录会按条目翻译，保留目标页码、缩进以及公式上下标，并根据译文长度重新绘制引导点。明确的多行等宽代码块会保留原始代码、注释和缩进，周围的说明文字仍参与翻译。这些版面处理适用于各翻译服务。

对于白底、未旋转、含整页扫描图和隐藏 OCR 文字层的 PDF，程序会清除已翻译文字行在底图中的墨迹，再排入译文；页眉页脚及未翻译区域保留原图，内嵌公式和引用标号按原图局部重排。此处理依赖已有 OCR 的位置和识别结果，不能自动修正识别错字，也不替无文字层的纯图片 PDF 执行 OCR。彩色背景、旋转或嵌套图片等未满足检测条件的文件不启用扫描底图清除。

## Codex CLI 自动发现

当 `CODEX_BIN` 填写 `codex` 或留作默认值时，程序会依次检查：

1. 便携包中与应用同目录的 `codex-cli/.../codex.exe`；
2. `CODEX_CLI_PATH` 环境变量；
3. 系统 `PATH` 返回的绝对 `.exe` 或 `.CMD` 路径；
4. Codex Desktop 的本机安装目录。

`.6` Release 会在第一项路径内置经过固定 SHA-256 校验的官方稳定版 Codex CLI 0.163.0，
所以通常保持 `CODEX_BIN=codex` 即可。包内只有 CLI 程序，不包含账户、登录状态或额度。

显式填写完整路径时始终优先使用该路径；路径不存在会直接报错，不会悄悄切换到另一份 Codex。便携版建议保持默认自动发现，或填写同目录内的完整 `codex.exe` 路径。

如果刚更新过桌面代码，请关闭并重新打开 `PDFMathTranslate-Codex.exe`，已经运行的窗口不会自动重新加载 Python 模块。

## Windows 打包

仓库的 `Publish Windows desktop release` 工作流会从 tag 在干净的 GitHub Windows
runner 上生成并发布 ZIP。两个启动器共享同一运行时和资源目录：

```text
PDFMathTranslate-Codex/
├─ PDFMathTranslate-Codex.exe   # 无终端桌面工作台
├─ pdf2zh.exe                   # 原命令行/浏览器启动器
├─ runtime/
├─ site-packages/
├─ codex-cli/                   # 固定版本的官方 Codex CLI 程序
├─ Login-Codex.cmd
└─ offline_assets_*.zip
```

构建流程严格使用 `uv.lock`，验证 Python、PyStand 和 Codex 下载文件的 SHA-256，使用
PyStand 的 x64 GUI 启动器生成桌面入口，并安装 `pdf2zh[desktop]`。桌面模式依赖
`pywebview 6.x`；在 Windows 上默认使用 Edge Chromium/WebView2。发布前会运行测试、
Black、启动器冒烟检查、离线资源内容检查和隐私文件扫描。

## 安全与隐私

- 内置 Codex CLI 程序不携带登录凭据；桌面外壳也不会读取或上传 Codex 的登录凭据。
- 界面和本地服务只绑定 `127.0.0.1`，不会主动开放到局域网。
- PDF 解析和重新排版主要在本机完成；选择云翻译服务后，需要翻译的文本会发送给相应服务商。
- Codex 翻译后端通过本机 `codex` 命令调用 OpenAI/Codex 服务，遵循用户自己的登录方式、账户政策和额度。
- 不要把 `~/.codex/auth.json`、用户论文、翻译缓存或 API Key 提交到仓库。
- 发布构建会在干净的 GitHub Windows runner 上从对应 tag 重新生成，并检查包内不包含论文、凭据、缓存或开发环境文件。
