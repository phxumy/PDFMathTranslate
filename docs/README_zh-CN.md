<div align="center">

[English](../README.md) | 简体中文 | [繁體中文](README_zh-TW.md) | [日本語](README_ja-JP.md)

<img src="./images/banner.png" width="320px"  alt="PDF2ZH"/>  

<h2 id="title">PDFMathTranslate</h2>

<p>
  <!-- PyPI -->
  <a href="https://pypi.org/project/pdf2zh/">
    <img src="https://img.shields.io/pypi/v/pdf2zh"/></a>
  <a href="https://pepy.tech/projects/pdf2zh">
    <img src="https://static.pepy.tech/badge/pdf2zh"></a>
  <a href="https://hub.docker.com/repository/docker/byaidu/pdf2zh">
    <img src="https://img.shields.io/docker/pulls/byaidu/pdf2zh"></a>
  <!-- License -->
  <a href="../LICENSE">
    <img src="https://img.shields.io/github/license/phxumy/PDFMathTranslate"/></a>
  <a href="https://huggingface.co/spaces/reycn/PDFMathTranslate-Docker">
    <img src="https://img.shields.io/badge/%F0%9F%A4%97-Online%20Demo-FF9E0D"/></a>
  <a href="https://www.modelscope.cn/studios/AI-ModelScope/PDFMathTranslate">
    <img src="https://img.shields.io/badge/ModelScope-Demo-blue"></a>
  <a href="https://github.com/Byaidu/PDFMathTranslate/pulls">
    <img src="https://img.shields.io/badge/contributions-welcome-green"/></a>
  <a href="https://gitcode.com/Byaidu/PDFMathTranslate/overview">
    <img src="https://gitcode.com/Byaidu/PDFMathTranslate/star/badge.svg"></a>
  <a href="https://t.me/+Z9_SgnxmsmA5NzBl">
    <img src="https://img.shields.io/badge/Telegram-2CA5E0?style=flat-squeare&logo=telegram&logoColor=white"/></a>
</p>

<a href="https://trendshift.io/repositories/12424" target="_blank"><img src="https://trendshift.io/api/badge/repositories/12424" alt="Byaidu%2FPDFMathTranslate | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

> [!IMPORTANT]
>
> 当前 `desktop-app` 是基于经典版 v1.9.11 的**非官方 Codex 增强版**，
> 不是 PDFMathTranslate 官方发布，也不是 OpenAI 官方产品或背书。

## Windows 桌面版下载

适用于 **Windows 10/11 x64**，无需安装 Python。

- **推荐直接下载：** [`PDFMathTranslate-Codex-Setup-win64.exe`](https://github.com/phxumy/PDFMathTranslate/releases/latest/download/PDFMathTranslate-Codex-Setup-win64.exe)
- [查看最新 Release、版本说明和 SHA-256](https://github.com/phxumy/PDFMathTranslate/releases/latest)
- [桌面版完整安装和故障排查](./DESKTOP_APP_zh-CN.md)

一分钟上手：

1. 下载并运行上面的 EXE 安装包；已有版本可覆盖安装。
2. 打开软件，在 Codex 设置里登录自己的账户，或使用安装目录中的 `Login-Codex.cmd`。
3. 双击 `PDFMathTranslate-Codex.exe`。
4. 在 `Service` 中选择 `Codex`，上传 PDF 后开始翻译。

`.7.3` 发布包内置官方稳定版 **Codex CLI 0.166.0**，无需另装 Python 或 Codex CLI；但不包含
账号、套餐或免费额度、登录凭据、API Key、用户论文或翻译缓存。界面只绑定
`127.0.0.1`，选择云翻译服务后，待翻译文本仍会发送给对应服务商；Codex 后端会使用
你登录的账户调用 OpenAI/Codex 服务。账户和登录方式以 [OpenAI 官方 Codex CLI 说明](https://developers.openai.com/codex/cli)为准。

### 多个 PDF 按顺序翻译（`.5.4` 起）

一次选择多个 PDF，文件区列出的顺序就是翻译顺序；语言、页码范围、服务和模型设置应用于每个文件。点击“翻译 / 继续队列”后，软件一次处理一份 PDF，并显示当前文件、页码进度和队列表格。

完成的译文和双语 PDF 会持续列在“队列已完成文件”中。某份文件失败会暂停队列；点击“停止队列”会停止当前任务并阻止后续文件开始。在同一窗口中，用相同文件和设置再次点击翻译，会跳过仍可下载的已完成文件；勾选 Ignore cache 则重新处理。重启后的文件列表需要重新选择，已有翻译缓存仍可复用。

<a id="codex-fields"></a>

### Codex 各字段怎么填（核对日期：2026-10-28）

以下字段行为适用于桌面版 `.5` 至 `.7`。模型 ID、所调用的 CLI 版本、登录方式和账户权限共同决定请求能否执行。

| 字段 | 建议填写 | 留空时的实际行为 |
| --- | --- | --- |
| `CODEX_BIN` | `.7.3` 保持默认 `codex` 即使用包内稳定版 CLI `0.166.0`；自定义时填写其他 `codex.exe` 的完整路径。 | 留空等同于 `codex`，依次查找包内 CLI、`CODEX_CLI_PATH`、`PATH` 和 Codex Desktop。显式路径不存在时直接报错。 |
| `CODEX_PROFILE` | 一般留空；需要时填写所选 CLI 支持的 profile 名称。 | 不传 `--profile`。内置 CLI 的快速路径还会忽略用户配置和规则；兼容路径的配置解析由所选 CLI 决定。 |
| `CODEX_MODEL` | 填写下表中的精确 ID，或留空让 CLI 选择。 | 不传 `--model`，不固定到某一个模型。最终取决于所选 CLI、其可用配置和账户；不要据此推断它会跟随桌面应用当前选择的模型。 |
| `CODEX_REASONING_EFFORT` | 默认 `low`，也可填写所选模型支持的强度。 | 从 `.5` 起，空值明确转换为 `low`，并通过命令行覆盖 CLI/profile 的默认值。旧文档所写的“留空变成 `none`”已不适用。 |
| `CODEX_TIMEOUT` | 默认 `120` 秒；确实发生超时时，可为长段落增加等待时间。 | 使用 `120` 秒。HTTP 400 的模型拒绝不会通过延长超时解决。 |

`CODEX_MODEL` 和推理强度同时留空，表示**自动选模型 + `low`**。显式模型 ID 会原样传给 `codex exec --model`；本程序没有模型名称白名单，不会把未知 ID 偷换为其他模型。

从 `.7.2` 起，内置 Codex CLI 使用 `%USERPROFILE%\.config\PDFMathTranslate\codex` 中的独立凭据，即使 `CODEX_BIN` 显式填写内置程序路径也一样。登录、切换、退出、模型查询和翻译均使用该目录，不复制或修改桌面端和个人 CLI 的凭据，升级后需首次登录。指定用户自己的外部 CLI 时，沿用其 `CODEX_HOME` 和凭据存储配置。在“Codex 账号与剩余额度”中可查看邮箱、套餐、额度剩余百分比和重置时间；查询失败显示暂不可用，API Key 余额不由该接口提供。登录隔离不增加账号本身的额度。

<a id="codex-models"></a>

### Codex 模型 ID（核对日期：2026-10-28）

`gpt-6-luna` 是官方有效 ID，包含连字符；不要填写 `gpt6luna`、`luna` 或其他简称。下表列出官方模型，不表示旧安装包内置的 CLI 已能使用每一项。

| 官方名称 | `CODEX_MODEL` 精确值 | 说明 |
| --- | --- | --- |
| GPT-6 Luna | `gpt-6-luna` | 面向重复、批量任务；可用于翻译。官方支持 `none`、`low`、`medium`、`high`、`xhigh`、`max`，不支持 Ultra。 |
| GPT-6.1 Sol | `gpt-6.1-sol` | 当前 Sol 型号，适合复杂工作；需当前客户端及账户已开放。 |
| GPT-6.1 Terra | `gpt-6.1-terra` | 官方新型号；可直接填写精确 ID。2026-10-28 已从本机官方 CLI `0.163.0` 的模型目录读取到此项及 `low`、`medium`、`high`、`xhigh`、`max` 档位；这次为目录查询，未执行 Terra 翻译请求。 |
| GPT-6 Sol | `gpt-6-sol` | GPT-6 系列的前一款 Sol，按账户可用性选择。 |
| GPT-6 Astra | `gpt-6-astra` | 适合较难的推理工作；使用 `low` 至 `max`，不要显式填 `none`。 |
| GPT-5.6 Sol / Terra / Luna | `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna` | 上一代型号；是否仍可使用，以当前客户端、账户和官方公告为准。 |

型号与推理强度分别设置：`CODEX_MODEL=gpt-6-luna`、`CODEX_REASONING_EFFORT=max` 就是请求 GPT-6 Luna + Max。翻译后端接受 `none`、`low`、`medium`、`high`、`xhigh`、`max`，尚不接受 `ultra`。Luna 的官方能力见 [GPT-6 Luna 模型页](https://developers.openai.com/api/docs/models/gpt-6-luna)；Codex 入口与账户可用性见 [官方 Models 说明](https://learn.chatgpt.com/docs/models)。

Terra 可填写 `CODEX_MODEL=gpt-6.1-terra`，例如配合 `CODEX_REASONING_EFFORT=max`。[Terra API 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-terra)列出的 effort 为 `none`、`low`、`medium`、`high`、`xhigh`、`max`；Codex 客户端目录中的可选项可能不同。本机目录还为 Terra 列出了 `ultra`，但当前翻译后端不接受该档位，不能将 Codex 应用中的全部选项直接照搬。

从 `.7` 起，“模型与服务设置”中的模型和 effort 均为可手动填写的下拉框。启动或切换到 Codex 时自动读取目录，也可点击“刷新模型与推理强度”。选择目录中的模型会联动 effort 选项；手动填写的新型号不会被替换，读取失败也会保留当前值。官方 [`model/list` 接口](https://learn.chatgpt.com/docs/app-server)提供模型 ID、显示名、effort 和默认档位。目录可能来自客户端缓存或内置列表，不能当作账号调用权限的确认，详见[官方模型目录说明](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)。

旧型号不应继续列为当前推荐项：ChatGPT 登录下的 `gpt-5.4` / `gpt-5.4-mini` 已于 2026-08-31 退役，`gpt-5.3-codex-spark` 于 2026-09-14 退役，`gpt-5.5` 于 2026-10-14 退役。API Key 登录的可用范围和计费另按 API 规则处理，不能从订阅登录的结果推断。

### `.5.4` 内置 CLI 与新版模型

**`.5.4` 已将内置 CLI 更新为官方稳定版 `0.163.0`；保持 `CODEX_BIN=codex` 即可使用。** `.5.3` 及此前安装包捆绑的 CLI 为 `0.145.0`。官方 [2026-09-23 更新记录](https://learn.chatgpt.com/docs/changelog)记载 CLI **0.156.1** 加入 GPT-6 Sol/Luna 模型目录；应使用当前官方 CLI，并核对账户可用性。这个记录不代表任意账户仅升级到某个版本就一定获得权限。

2026-10-18 的同机短文本对照结果：

| 调用程序 | 模型 / 强度 | 结果 |
| --- | --- | --- |
| 安装包内 CLI `0.145.0` | `gpt-6-luna` / `max` | HTTP 400：`The 'gpt-6-luna' model is not supported when using Codex with a ChatGPT account.` |
| `.5.4` 使用的官方稳定版 CLI `0.163.0` | 同一账号、同一模型 / 强度 | 成功返回中文译文。 |

这证明该环境的问题与所调用的旧 CLI 有关，不能将报错解释为“GPT-6 Luna 普遍不支持 Codex 订阅登录”或“Luna 不支持 `max`”。表格是短文本兼容性对照；PDF 第一页及队列验证范围见发行说明。

升级及排查方法：

1. 优先覆盖安装 `.5.4`，`CODEX_BIN` 保持 `codex`。若仍使用 `.5.3` 或更早版本，可改填已安装新版 `codex.exe` 的完整路径，并用该路径执行 `--version` 确认。仅升级 `PATH` 中的 Codex 不会替换优先级更高的包内旧版本。
2. 保持 `CODEX_MODEL=gpt-6-luna`，按需要选择 `high` 或 `max`；先用短文本或第一页验证。不要为了绕过模型拒绝而把模型名改为简称。
3. 若当前 CLI 仍返回拒绝，继续核对登录账号、套餐、工作区权限和原始错误。旧版界面的 `RetryError` 只是重试异常的外层包装；`.5.4` 会直接展示结构化模型/权限拒绝并停止无效重试。

此处设置属于 **Codex 服务**。另一个 **ChatGPT 聊天服务** 从当前网页读取模型选项，不能照抄这里的 CLI 模型 ID。

科学 PDF 文档翻译及双语对照工具

- 📊 保留公式、图表、目录和注释 *([预览效果](#preview))*
- 🌐 支持 [多种语言](./ADVANCED.md#language) 和 [诸多翻译服务](./ADVANCED.md#services)
- 🤖 提供 [命令行工具](#usage)，[图形交互界面](#gui)，以及 [容器化部署](#docker)

Codex 增强版的问题请提交到[本 fork 的 GitHub Issues](https://github.com/phxumy/PDFMathTranslate/issues)；上游功能请前往 [PDFMathTranslate 官方仓库](https://github.com/PDFMathTranslate/PDFMathTranslate)。

有关如何贡献的详细信息，请查阅 [贡献指南](https://github.com/Byaidu/PDFMathTranslate/wiki/Contribution-Guide---%E8%B4%A1%E7%8C%AE%E6%8C%87%E5%8D%97)

<h2 id="updates">更新</h2>

- [2025 年 2 月 22 日] 更好的发布 CI 和精心打包的 windows-amd64 exe (由 [@awwaawwa](https://github.com/awwaawwa) 提供)
- [2024 年 12 月 24 日] 翻译器现在支持在 [Xinference](https://github.com/xorbitsai/inference) 上使用本地模型 _(由 [@imClumsyPanda](https://github.com/imClumsyPanda) 提供)_
- [2024 年 12 月 19 日] 现在支持非 PDF/A 文档，使用 `-cp` _(由 [@reycn](https://github.com/reycn) 提供)_
- [2024 年 12 月 13 日] 额外支持后端 _(由 [@YadominJinta](https://github.com/YadominJinta) 提供)_
- [2024 年 12 月 10 日] 翻译器现在支持 Azure 上的 OpenAI 模型 _(由 [@yidasanqian](https://github.com/yidasanqian) 提供)_

<h2 id="preview">预览</h2>
<div align="center">
<img src="./images/preview.gif" width="80%"/>
</div>

<h2 id="demo">在线演示 🌟</h2>

<h2 id="demo">在线服务 🌟</h2>

您可以通过以下演示尝试我们的应用程序：

- [公共免费服务](https://pdf2zh.com/) 在线使用，无需安装 _(推荐)_。
- [沉浸式翻译 - BabelDOC](https://app.immersivetranslate.com/babel-doc/) 每月免费 1000 页 _(推荐)_
- [在 HuggingFace 上托管的演示](https://huggingface.co/spaces/reycn/PDFMathTranslate-Docker)
- [在 ModelScope 上托管的演示](https://www.modelscope.cn/studios/AI-ModelScope/PDFMathTranslate) 无需安装。

请注意演示的计算资源有限，请避免滥用它们。
<h2 id="install">安装和使用</h2>

### 方法

针对不同的使用案例，我们提供不同的方法来使用我们的程序：

<details open>
  <summary>1. UV 安装</summary>

1. 安装 Python (3.10 <= 版本 <= 3.12)
2. 安装我们的包：

   ```bash
   pip install uv
   uv tool install --python 3.12 pdf2zh
   ```

3. 执行翻译，文件生成在 [当前工作目录](https://chatgpt.com/share/6745ed36-9acc-800e-8a90-59204bd13444)：

   ```bash
   pdf2zh document.pdf
   ```

</details>

<details>
  <summary>2. Windows exe</summary>

1. 从[本 fork 最新 Release](https://github.com/phxumy/PDFMathTranslate/releases/latest)下载 `PDFMathTranslate-Codex-Setup-win64.exe`。

2. 运行 EXE 安装；使用 Codex 前在软件中完成账户登录。

3. 双击 `PDFMathTranslate-Codex.exe`。包内的 `pdf2zh.exe` 仍用于经典浏览器 WebUI 和命令行。

压缩包已经包含固定版本的官方 Codex CLI 程序和 BabelDOC 离线资源，不包含任何人的账户或凭据。

</details>

<details>
  <summary id="gui">3. 图形用户界面</summary>
1. 安装 Python (3.10 <= 版本 <= 3.12)
2. 安装我们的包：

```bash
pip install pdf2zh
```

3. 在浏览器中开始使用：

   ```bash
   pdf2zh -i
   ```

4. 如果您的浏览器没有自动启动，请访问

   ```bash
   http://localhost:7860/
   ```

   <img src="./images/gui.gif" width="500"/>

有关更多详细信息，请参阅 [GUI 文档](./README_GUI.md)。

</details>

<details>
  <summary id="docker">4. Docker</summary>

1. 拉取并运行：

   ```bash
   docker pull byaidu/pdf2zh
   docker run -d -p 7860:7860 byaidu/pdf2zh
   ```

2. 在浏览器中打开：

   ```
   http://localhost:7860/
   ```

对于云服务上的 docker 部署：

<div>
<a href="https://www.heroku.com/deploy?template=https://github.com/Byaidu/PDFMathTranslate">
  <img src="https://www.herokucdn.com/deploy/button.svg" alt="部署" height="26"></a>
<a href="https://render.com/deploy">
  <img src="https://render.com/images/deploy-to-render-button.svg" alt="部署到 Koyeb" height="26"></a>
<a href="https://zeabur.com/templates/5FQIGX?referralCode=reycn">
  <img src="https://zeabur.com/button.svg" alt="在 Zeabur 上部署" height="26"></a>
<a href="https://template.sealos.io/deploy?templateName=pdf2zh">
  <img src="https://sealos.io/Deploy-on-Sealos.svg" alt="在 Sealos 上部署" height="26"></a>
<a href="https://app.koyeb.com/deploy?type=git&builder=buildpack&repository=github.com/Byaidu/PDFMathTranslate&branch=main&name=pdf-math-translate">
  <img src="https://www.koyeb.com/static/images/deploy/button.svg" alt="部署到 Koyeb" height="26"></a>
</div>

</details>

<details>
  <summary>5. Zotero 插件</summary>

有关更多细节，请参见 [Zotero PDF2zh](https://github.com/guaguastandup/zotero-pdf2zh)。

</details>

<details>
  <summary>6. 命令行</summary>

1. 已安装 Python（3.10 <= 版本 <= 3.12）
2. 安装我们的包：

   ```bash
   pip install pdf2zh
   ```

3. 执行翻译，文件生成在 [当前工作目录](https://chatgpt.com/share/6745ed36-9acc-800e-8a90-59204bd13444):

   ```bash
   pdf2zh document.pdf
   ```

</details>

> [!TIP]
>
> - 如果你使用 Windows 并在下载后无法打开文件，请安装 [vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe) 并重试。
>
> - 如果你无法访问 Docker Hub，请尝试在 [GitHub 容器注册中心](https://github.com/Byaidu/PDFMathTranslate/pkgs/container/pdfmathtranslate) 上使用该镜像。
> ```bash
> docker pull ghcr.io/byaidu/pdfmathtranslate
> docker run -d -p 7860:7860 ghcr.io/byaidu/pdfmathtranslate
> ```

### 无法安装？

当前程序在工作前需要一个 AI 模型 (`wybxc/DocLayout-YOLO-DocStructBench-onnx`)，一些用户由于网络问题无法下载。如果你在下载此模型时遇到问题，我们提供以下环境变量的解决方法：

```shell
set HF_ENDPOINT=https://hf-mirror.com
```

对于 PowerShell 用户：

```shell
$env:HF_ENDPOINT = https://hf-mirror.com
```

如果此解决方案对您无效或您遇到其他问题，请参阅 [常见问题解答](https://github.com/Byaidu/PDFMathTranslate/wiki#-faq--%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98)。


<h2 id="usage">高级选项</h2>

在命令行中执行翻译命令，在当前工作目录下生成译文文档 `example-mono.pdf` 和双语对照文档 `example-dual.pdf`，默认使用 Google 翻译服务，更多支持的服务在[这里](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#services))。

<img src="./images/cmd.explained.png" width="580px"  alt="cmd"/>  

在下表中，我们列出了所有高级选项供参考：

| 选项         | 功能                                                                                                          | 示例                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| files        | 本地文件                                                                                                     | `pdf2zh ~/local.pdf`                           |
| links        | 在线文件                                                                                                     | `pdf2zh http://arxiv.org/paper.pdf`            |
| `-i`         | [进入 GUI](#gui)                                                                                            | `pdf2zh -i`                                    |
| `-p`         | [部分文档翻译](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#partial)                | `pdf2zh example.pdf -p 1`                      |
| `-li`        | [源语言](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#languages)                    | `pdf2zh example.pdf -li en`                    |
| `-lo`        | [目标语言](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#languages)                  | `pdf2zh example.pdf -lo zh`                    |
| `-s`         | [翻译服务](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#services)                   | `pdf2zh example.pdf -s deepl`                  |
| `-t`         | [多线程](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#threads)                      | `pdf2zh example.pdf -t 1`                      |
| `-o`         | 输出目录                                                                                                     | `pdf2zh example.pdf -o output`                 |
| `-f`, `-c`   | [异常](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#exceptions)                     | `pdf2zh example.pdf -f "(MS.*)"`               |
| `-cp`        | 兼容模式                                                                                                     | `pdf2zh example.pdf --compatible`              |
| `--share`    | 公开链接                                                                                                     | `pdf2zh -i --share`                            |
| `--authorized` | [授权](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#auth)                         | `pdf2zh -i --authorized users.txt [auth.html]` |
| `--prompt`   | [自定义提示](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#prompt)                   | `pdf2zh --prompt [prompt.txt]`                 |
| `--onnx`     | [使用自定义 DocLayout-YOLO ONNX 模型]                                                                        | `pdf2zh --onnx [onnx/model/path]`              |
| `--serverport` | [使用自定义 WebUI 端口]                                                                                    | `pdf2zh --serverport 7860`                     |
| `--dir`      | [批量翻译]                                                                                                   | `pdf2zh --dir /path/to/translate/`             |
| `--config`   | [配置文件](https://github.com/Byaidu/PDFMathTranslate/blob/main/docs/ADVANCED.md#cofig)                       | `pdf2zh --config /path/to/config/config.json`  |
| `--serverport` | [自定义 gradio 服务器端口]                                                                                 | `pdf2zh --serverport 7860`                     |
| `--babeldoc`| 使用实验性后端 [BabelDOC](https://funstory-ai.github.io/BabelDOC/) 翻译 |`pdf2zh --babeldoc` -s openai example.pdf|

有关详细说明，请参阅我们的文档 [高级用法](./ADVANCED.md)，以获取每个选项的完整列表。

<h2 id="downstream">二次开发 (API)</h2>

当前的 pdf2zh API 暂时已弃用。API 将在 [pdf2zh 2.0](https://github.com/Byaidu/PDFMathTranslate/issues/586)发布后重新提供。对于需要程序化访问的用户，请使用[BabelDOC](https://github.com/funstory-ai/BabelDOC)的 `babeldoc.high_level.async_translate` 函数。

API 暂时弃用意味着：相关代码暂时不会被移除，但不会提供技术支持，也不会修复 bug。

<!-- 对于下游应用程序，请参阅我们的文档 [API 详细信息](./APIS.md)，以获取更多信息：
- [Python API](./APIS.md#api-python)，如何在其他 Python 程序中使用该程序
- [HTTP API](./APIS.md#api-http)，如何与已安装该程序的服务器进行通信 -->

<h2 id="todo">待办事项</h2>

- [ ] 使用基于 DocLayNet 的模型解析布局，[PaddleX](https://github.com/PaddlePaddle/PaddleX/blob/17cc27ac3842e7880ca4aad92358d3ef8555429a/paddlex/repo_apis/PaddleDetection_api/object_det/official_categories.py#L81)，[PaperMage](https://github.com/allenai/papermage/blob/9cd4bb48cbedab45d0f7a455711438f1632abebe/README.md?plain=1#L102)，[SAM2](https://github.com/facebookresearch/sam2)

- [ ] 修复页面旋转、目录、列表格式

- [ ] 修复旧论文中的像素公式

- [ ] 异步重试，除了 KeyboardInterrupt

- [ ] 针对西方语言的 Knuth–Plass 算法

- [ ] 支持非 PDF/A 文件

- [ ] [Zotero](https://github.com/zotero/zotero) 和 [Obsidian](https://github.com/obsidianmd/obsidian-releases) 的插件

<h2 id="acknowledgement">致谢</h2>

- [Immersive Translation](https://immersivetranslate.com) 为此项目的活跃贡献者提供每月的专业会员兑换码，详细信息请查看：[CONTRIBUTOR_REWARD.md](https://github.com/funstory-ai/BabelDOC/blob/main/docs/CONTRIBUTOR_REWARD.md)

- 文档合并：[PyMuPDF](https://github.com/pymupdf/PyMuPDF)

- 文档解析：[Pdfminer.six](https://github.com/pdfminer/pdfminer.six)

- 文档提取：[MinerU](https://github.com/opendatalab/MinerU)

- 文档预览：[Gradio PDF](https://github.com/freddyaboulton/gradio-pdf)

- 多线程翻译：[MathTranslate](https://github.com/SUSYUSTC/MathTranslate)

- 布局解析：[DocLayout-YOLO](https://github.com/opendatalab/DocLayout-YOLO)

- 文档标准：[PDF Explained](https://zxyle.github.io/PDF-Explained/)，[PDF Cheat Sheets](https://pdfa.org/resource/pdf-cheat-sheets/)

- 多语言字体：[Go Noto Universal](https://github.com/satbyy/go-noto-universal)

<h2 id="contrib">贡献者</h2>

<a href="https://github.com/Byaidu/PDFMathTranslate/graphs/contributors">
  <img src="https://opencollective.com/PDFMathTranslate/contributors.svg?width=890&button=false" />
</a>

![Alt](https://repobeats.axiom.co/api/embed/dfa7583da5332a11468d686fbd29b92320a6a869.svg "Repobeats analytics image")

<h2 id="star_hist">星标历史</h2>

<a href="https://star-history.com/#Byaidu/PDFMathTranslate&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date" />
   <img alt="星标历史图表" src="https://api.star-history.com/svg?repos=Byaidu/PDFMathTranslate&type=Date"/>
 </picture>
</a>
