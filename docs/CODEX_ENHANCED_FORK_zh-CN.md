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

这些策略是保守启发式规则，复杂或扫描质量较差的 PDF 仍可能需要人工检查。

## 安装前提

1. 安装并登录 Codex CLI：

   ```powershell
   codex login
   codex --version
   ```

2. 使用 Python 3.10、3.11 或 3.12，在本分支源码目录安装：

   ```powershell
   python -m pip install -e .
   ```

本仓库只发布源码，不包含 `codex.exe`、Codex 登录凭据、模型资产、用户论文或翻译缓存。不要复制或上传 `~/.codex/auth.json`。

## WebUI 使用

启动 WebUI：

```powershell
pdf2zh -i
```

在 `Service` 中选择 `Codex`，然后设置：

| 选项 | 建议值 | 说明 |
| --- | --- | --- |
| `CODEX_BIN` | `codex`，或本机 `codex.exe` 的完整路径 | 默认值会自动检查便携包、`CODEX_CLI_PATH`、`PATH` 和 Codex Desktop；完整路径始终优先。 |
| `CODEX_PROFILE` | 留空 | 可选的 Codex CLI profile 名称；只有确实在 Codex 配置中创建了对应 profile 时才填写。 |
| `CODEX_MODEL` | 留空 | 留空时使用 Codex CLI 当前默认模型；也可以填写当前 CLI 支持的具体模型 ID。 |
| `CODEX_REASONING_EFFORT` | `none` | 可选值：`none`、`low`、`medium`、`high`、`xhigh`、`max`。翻译通常无需高推理强度。 |
| `CODEX_TIMEOUT` | `120` | 单次 Codex 请求超时秒数。长段落或较慢网络可适当增加。 |

选择 Codex 时，WebUI 会把 `number of threads` 默认设为 `1`，但输入框仍可编辑。该数值表示最多同时运行多少个相互独立的 Codex 请求；例如填写 `4`，程序会保留原有的批次划分和输出顺序，只把已经划分好的批次最多并行执行 4 个。并行不会把同一内容重复翻译，因此同一文档的总输入/输出量通常与单线程接近，但瞬时请求速率会提高，限流、超时或结果校验失败造成的重试可能带来少量额外消耗。建议先使用 `1` 或 `2`，确认账户速率和电脑负载稳定后再尝试 `4`。

Windows 桌面版会以隐藏窗口方式启动后台 Codex CLI；翻译期间不会为每个请求弹出控制台窗口。

## 命令行使用

PowerShell 示例：

```powershell
$env:CODEX_BIN = "codex"
$env:CODEX_REASONING_EFFORT = "none"
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

测试覆盖 Codex 命令构造、批处理与缓存、公式上下文、参考文献策略、作者与单位、斜体和旋转文字、重叠区域、双栏与跨页连续句等行为。

## 已知限制

- 这是基于经典版 v1.9.11 的实验性分支，与官方 2.x 架构不同。
- Codex CLI 必须支持本后端所需的 `codex exec` 参数；CLI 过旧时会在启动翻译器时明确报错。
- Codex 使用量由当前登录账户和 Codex 产品规则决定，本项目不能更改或绕过额度限制。
- PDF 版式恢复属于启发式处理，翻译完成后仍应检查参考文献、公式邻接标点、跨栏顺序和文本溢出。
