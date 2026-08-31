# Windows 桌面工作台

本分支提供一个非官方 Windows 桌面外壳，用独立的 WebView2 窗口承载原 PDFMathTranslate WebUI。翻译核心和界面组件没有复制，因此原有翻译服务、语言、页码、缓存、字体、Prompt、BabelDOC 和 Codex 配置继续使用同一套代码。

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

选择 Codex 服务时，线程数会自动设为 `1` 并锁定，因为 Codex 后端按顺序执行翻译请求。切回其他服务后线程输入框会恢复可编辑。

## Codex CLI 自动发现

当 `CODEX_BIN` 填写 `codex` 或留作默认值时，程序会依次检查：

1. 便携包 `build/codex-cli/.../codex.exe`；
2. `CODEX_CLI_PATH` 环境变量；
3. 系统 `PATH` 返回的绝对 `.exe` 或 `.CMD` 路径；
4. Codex Desktop 的本机安装目录。

显式填写完整路径时始终优先使用该路径；路径不存在会直接报错，不会悄悄切换到另一份 Codex。便携版建议保持默认自动发现，或填写同目录内的完整 `codex.exe` 路径。

如果刚更新过桌面代码，请关闭并重新打开 `PDFMathTranslate-Codex.exe`，已经运行的窗口不会自动重新加载 Python 模块。

## Windows 打包

仓库的 `windows exe Release Workflow` 会生成两个共享同一运行时和资源目录的启动器：

```text
build/
├─ PDFMathTranslate-Codex.exe   # 无终端桌面工作台
├─ pdf2zh.exe                   # 原命令行/浏览器启动器
├─ runtime/
├─ site-packages/
└─ offline_assets_*.zip
```

构建流程使用 PyStand 的 x64 GUI 启动器生成桌面入口，并安装 `pdf2zh[desktop]`。桌面模式依赖 `pywebview 6.x`；在 Windows 上默认使用 Edge Chromium/WebView2。

## 安全与隐私

- 桌面外壳不会读取或上传 Codex 的登录凭据。
- PDF 和翻译结果仍由原 PDFMathTranslate 流程处理。
- Codex 翻译后端仍通过本机 `codex` 命令工作。
- 不要把 `~/.codex/auth.json`、用户论文、翻译缓存或 API Key 提交到仓库。
