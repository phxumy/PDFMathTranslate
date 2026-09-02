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
- 无需安装 Python，也无需单独安装 Codex CLI；发布包已带经过哈希校验的官方 Codex CLI 0.145.0。

如果以后想改用系统里更新的 Codex CLI，可以参考 [OpenAI 官方 Codex CLI 页面](https://developers.openai.com/codex/cli)。OpenAI 当前提供的 Windows 安装命令是：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

安装完成后可在 PowerShell 运行 `codex --version`；若要让本程序使用这份系统安装，
在界面的 `CODEX_BIN` 中填写其完整路径。通常没有必要这样做。

无论使用内置还是系统安装的 Codex CLI，登录都属于当前 Windows 用户。不要把
`~/.codex/auth.json` 复制给别人或上传到任何网站。

发布包第一次使用无需打开 PowerShell；直接按下一节双击登录脚本即可。系统安装的
OpenAI 官方 CLI 也可以直接运行：

```powershell
codex
```

### 2. 下载和启动

1. 打开[本项目最新 Release](https://github.com/phxumy/PDFMathTranslate/releases/latest)。
2. 下载推荐文件 `PDFMathTranslate-Codex-win64-with-assets.zip`。该版本已经包含版面模型资源，首次启动不必再下载模型。
3. 完整解压 ZIP 到普通文件夹；不要直接在压缩包预览窗口中双击程序。
4. 如果使用 Codex，先双击 `Login-Codex.cmd`，在浏览器中完成官方登录。使用其他翻译服务可跳过。
5. 双击 `PDFMathTranslate-Codex.exe`。
6. 在 `Service` 中选择 `Codex`，上传 PDF，设置语言和页码后点击 `Translate`。

无需安装 Python。包内的 `pdf2zh.exe` 仍可用于原浏览器 WebUI 和命令行模式。

也可以直接使用这个固定下载地址：

<https://github.com/phxumy/PDFMathTranslate/releases/latest/download/PDFMathTranslate-Codex-win64-with-assets.zip>

### 3. 校验下载文件

Release 同时发布 `SHA256SUMS.txt`。下载后可在 ZIP 所在目录运行：

```powershell
Get-FileHash ".\PDFMathTranslate-Codex-win64-with-assets.zip" -Algorithm SHA256
```

输出应与 `SHA256SUMS.txt` 一致。由于社区版目前没有付费代码签名，Windows
SmartScreen 可能显示未知发布者；请先核对哈希和下载域名，不要为运行本程序而关闭
Microsoft Defender。

### 4. 常见启动问题

- `codex executable not found`：确认完整解压，并检查应用旁边是否存在 `codex-cli\x86_64-pc-windows-msvc\bin\codex.exe`；若目录缺失，请重新下载完整 ZIP。
- Codex 未登录：双击 `Login-Codex.cmd`；也可运行包内 `codex.exe login status` 检查状态。
- 窗口无法建立：安装 WebView2 Runtime 后重试。
- 提示缺少 VC++ 组件：安装微软官方 [Visual C++ Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
- 7860 端口已占用：桌面版会自动寻找后续空闲端口；只有显式指定固定端口时才会严格使用该端口。
- 翻译频繁限流或超时：先把 `number of threads` 设为 `1` 或 `2`。

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

选择 Codex 服务时，线程数会自动设为 `1`，但输入框保持可编辑。填写 `4` 表示将原有翻译批次最多并行执行 4 个；批次数量和内容不变，所以总输入/输出量通常不会变成 4 倍，只会提高瞬时请求速率。并行度越高，越容易遇到账户限流，也会同时运行更多 `codex.exe` 进程，建议从 `1` 或 `2` 开始。

桌面版会隐藏所有后台 Codex CLI 控制台窗口，翻译过程不会抢占桌面焦点。

## Codex CLI 自动发现

当 `CODEX_BIN` 填写 `codex` 或留作默认值时，程序会依次检查：

1. 便携包中与应用同目录的 `codex-cli/.../codex.exe`；
2. `CODEX_CLI_PATH` 环境变量；
3. 系统 `PATH` 返回的绝对 `.exe` 或 `.CMD` 路径；
4. Codex Desktop 的本机安装目录。

当前公开 Release 会在第一项路径内置经过固定 SHA-256 校验的官方 Codex CLI 0.145.0，
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
