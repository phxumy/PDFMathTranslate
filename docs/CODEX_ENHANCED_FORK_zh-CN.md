# Codex 增强版 v1.9.11

这是 PDFMathTranslate 经典版 v1.9.11 的非官方增强分支，主要用于通过本机已经登录的 Codex CLI 翻译科研论文。它不是 PDFMathTranslate 官方发布版本，也不是 2.x（PDFMathTranslate-next）的一部分。

Windows 独立窗口的安装和打包方式请参阅[桌面工作台说明](./DESKTOP_APP_zh-CN.md)。

## 来源与署名

- 上游项目：[PDFMathTranslate/PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate)
- 基础版本：[v1.9.11](https://github.com/PDFMathTranslate/PDFMathTranslate/releases/tag/v1.9.11)
- 初始 Codex CLI 后端来自社区贡献者 XiaKaiYang 的 [PR #1124](https://github.com/PDFMathTranslate/PDFMathTranslate/pull/1124)。本分支保留了该提交的原作者信息。
- 后续科研论文翻译、参考文献与版面修复由本 fork 继续开发。

本项目继续使用上游的 GNU Affero General Public License v3.0。分发修改版时请保留版权、许可证和对应源码。

## 主要改动

- 新增本机 Codex CLI 翻译后端，复用 Codex CLI 已有的 ChatGPT/Codex 登录状态，不要求在 PDFMathTranslate 中填写 OpenAI API Key。
- Codex 请求在临时目录中以只读沙箱、临时会话和结构化 JSON 输出运行。
- 批量翻译、超时重试、缓存隔离和公式占位符完整性校验。
- 保护公式、变量、引用标号、个人姓名、期刊名、年份、卷期页码、DOI、URL 等内容。
- Codex 后端仅翻译参考文献中可可靠识别的作品标题；作者、期刊、出版信息等保持原文。边界不确定时整条保持原样。其他翻译后端默认保持整条参考文献原文。
- 识别作者与单位，保护作者姓名，并恢复单位信息的合理换行。
- 改善斜体自然语言、旋转文字、公式边界标点和重叠版面区域的处理。
- 改善双栏跨栏、跨页连续句的阅读顺序，避免同一句被拆开或重复翻译。
- 图注、表题和表注在与图表区域重叠时仍会重新进入翻译管线；图内文字继续保持原样，不做 OCR 或重绘。
- 应译正文会执行英文残留、公式占位符、跨页边界和参考文献题名完整性校验；失败结果不会写入缓存。
- 中文译文会定点归一化兼容汉字，并按中文标点禁则、连续拉丁词和公式原子进行换行。

这些策略是保守启发式规则，复杂或扫描质量较差的 PDF 仍可能需要人工检查。

## 普通用户安装

Windows 10/11 x64 用户可以直接下载带离线版面资源的桌面包，无需安装 Python：

1. 从[本项目最新 Release](https://github.com/phxumy/PDFMathTranslate/releases/latest)下载 `PDFMathTranslate-Codex-win64-with-assets.zip`。
2. 完整解压；使用 Codex 前双击 `Login-Codex.cmd`，在浏览器中完成官方登录。
3. 双击 `PDFMathTranslate-Codex.exe`。

详细步骤、SHA-256 校验和故障排查参阅[桌面工作台说明](./DESKTOP_APP_zh-CN.md)。

发布包包含经过固定哈希校验的官方 Codex CLI 0.145.0 程序，但不包含账号、套餐额度、
Codex 登录凭据、用户论文、API Key 或翻译缓存。登录和账户规则以
[OpenAI 官方 Codex CLI 说明](https://developers.openai.com/codex/cli)为准。

## 从源码安装

1. 安装并登录 Codex CLI：

   ```powershell
   codex login
   codex --version
   ```

2. 使用 Python 3.10、3.11 或 3.12，在本分支源码目录安装：

   ```powershell
   python -m pip install -e .
   ```

源码安装也不会读取或分发 Codex 登录凭据。不要复制或上传 `~/.codex/auth.json`。

## WebUI 使用

启动 WebUI：

```powershell
pdf2zh -i
```

在 `Service` 中选择 `Codex`，再设置程序、模型和推理强度。字段规则及模型 ID 已于 **2026-10-18** 核对，完整表格、官方来源和同机 CLI 对照结果见 [简体中文 README 的 Codex 配置说明](./README_zh-CN.md#codex-fields)。

- `CODEX_MODEL` 使用精确 ID，例如 `gpt-6-luna`、`gpt-6.1-sol`、`gpt-6-sol` 或 `gpt-6-astra`，程序将它原样传给 CLI。留空则不传 `--model`，具体自动选择由所选 CLI 决定。
- GPT-6 Luna 支持 `max`。当前翻译后端允许 `none`、`low`、`medium`、`high`、`xhigh`、`max`；从 `.5` 起，推理强度留空使用 `low`，不是旧文档写的 `none`。模型自身的支持范围仍需核对。
- `.5.4` 内置官方稳定版 CLI `0.163.0`，`CODEX_BIN=codex` 或留空会优先选择它。旧 `.5.3` 包内仍为 `0.145.0`，可覆盖升级；若使用其他 CLI，填写完整路径并核对 `--version`。
- 同机对照中，旧 CLI 拒绝 `gpt-6-luna` / `max`，官方稳定版 `0.163.0` 使用相同账号和参数成功返回短文本译文。这不等于模型 ID 无效，也不等于所有账号已开放。请按完整说明核验当前 CLI 与登录权限。
- `CODEX_PROFILE` 一般留空；`CODEX_TIMEOUT` 默认 120 秒。`RetryError` 是外层重试提示，应读取内部错误；延长超时不能修复 HTTP 400 的模型拒绝。

`.5.4` 可一次选择多个 PDF，按列表顺序逐个处理；失败或停止后保留已完成下载，当前窗口重试同一队列会跳过已完成文件。完整操作见上述 README。

选择 Codex 时，WebUI 会把 `number of threads` 默认设为 `4`，输入框仍可编辑。该数值表示最多同时运行多少个相互独立的 Codex 请求；默认设置下，程序会保留原有的批次划分和输出顺序，只把已经划分好的批次最多并行执行 4 个。并行不会把同一内容重复翻译，因此同一文档的总输入/输出量通常与单线程接近，但瞬时请求速率会提高，限流、超时或结果校验失败造成的重试可能带来少量额外消耗。如遇账户限流或电脑负载过高，可以调低为 `1` 或 `2`。

Windows 桌面版会以隐藏窗口方式启动后台 Codex CLI；翻译期间不会为每个请求弹出控制台窗口。

## 命令行使用

PowerShell 示例：

```powershell
$env:CODEX_BIN = "codex"
$env:CODEX_REASONING_EFFORT = "low"
$env:CODEX_TIMEOUT = "120"
pdf2zh "D:\papers\example.pdf" -s codex -li en -lo zh -t 4
```

如需指定模型：

```powershell
$env:CODEX_MODEL = "你的 Codex CLI 当前支持的模型 ID"
```

若希望继续使用 Codex CLI 的当前默认模型，请删除该环境变量或保持为空：

```powershell
Remove-Item Env:CODEX_MODEL -ErrorAction SilentlyContinue
```

## 测试

本分支的回归测试使用 Python 标准库 `unittest`：

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```

测试覆盖 Codex 命令构造、批处理与缓存、公式上下文、参考文献策略、作者与单位、斜体和旋转文字、重叠区域、图表题注、中文换行、英文残留门禁、双栏正文与参考文献跨页连续句等行为。

## 已知限制

- 这是基于经典版 v1.9.11 的实验性分支，与官方 2.x 架构不同。
- Codex CLI 必须支持本后端所需的 `codex exec` 参数；CLI 过旧时会在启动翻译器时明确报错。
- Codex 使用量由当前登录账户和 Codex 产品规则决定，本项目不能更改或绕过额度限制。
- PDF 版式恢复属于启发式处理，翻译完成后仍应检查参考文献、公式邻接标点、跨栏顺序和文本溢出。
