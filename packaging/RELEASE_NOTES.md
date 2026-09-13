## v1.9.11-codex.4 — PDF layout and text-preservation fixes

这是非官方 PDFMathTranslate Codex 增强版的 Windows x64 修复更新。

### 本次更新

- 修复白底扫描图加隐藏 OCR 文字层的 PDF 在中译英时叠字的问题，保留图片、页眉页脚及引用片段；原有英文摘要和关键词不重复翻译。
- 修复目录和图目录条目合并、引导点与页码错位，保留公式和上下标。
- 保护多行代码块的变量、注释、缩进和换行，周围说明文字继续翻译。
- 修复跨行斜体小节标题被误识别为公式，以及化学式后的方位词被误保护。
- 改善 Codex 对斜体书名、跨行书名和作者-年份格式学位论文题名的识别，保留作者、期刊和出版社信息。
- 保持 ORCID 作者名单逐行排列，修复图标与链接文字叠压。
- 增加内置 Unicode 字体后备，修复 `Ł/ł` 等作者姓名字符缺失。
- Codex 默认线程数恢复为 4，仍可手动调整。

扫描件处理依赖原有 OCR 识别质量；本次不会自动校正全部识别错字。升级程序不会自动改写之前生成的译文，旧文件需要用新版重新翻译。彩色背景、旋转和无 OCR 文字层等扫描件不适用本次白底扫描清除逻辑。

仍待后续处理：部分旧译文中的正文漏译需要重新生成；旧链接边框错位和断行姓名连字符尚未在本次全面修复。部分非 Codex 服务仍会保留斜体定理说明。

### 升级提示

完整解压到新目录后运行。若沿用的 `CODEX_BIN` 指向已经不存在的旧目录，将其改为 `codex`，由程序自动查找包内 CLI。

This Windows x64 update fixes OCR scan overlays, contents rows and page-number alignment, code-listing preservation, and wrapped italic subsection headings. Existing translated PDFs are not rewritten automatically; regenerate them with this version. OCR recognition errors remain a separate limitation.

### Download and start

1. Download `PDFMathTranslate-Codex-win64-with-assets.zip` and extract the whole ZIP.
2. Double-click `Login-Codex.cmd` once if you want to use the Codex translation service, then complete the official browser sign-in.
3. Double-click `PDFMathTranslate-Codex.exe`.

The package needs no Python installation and includes:

- the native Windows desktop shell and original command-line/WebUI launcher;
- the official Codex CLI 0.145.0 binary (no account, credentials, or included quota);
- BabelDOC offline layout/OCR/font assets;
- the exact locked Python runtime dependencies and required license notices.

Translation still requires internet access when using Codex or another cloud service and consumes that service's own account/API allowance. The archive is built from the exact public tag on a clean GitHub-hosted runner and excludes user PDFs, translation caches, API keys, and Codex login data.

This executable is not code-signed, so Windows may display an “Unknown publisher” warning. `SHA256SUMS.txt` is provided for integrity verification.

中文快速说明：完整解压 ZIP；使用 Codex 前双击 `Login-Codex.cmd` 登录；随后双击 `PDFMathTranslate-Codex.exe`。压缩包内含 Codex CLI 程序，但不含账号、套餐额度、API Key 或任何人的登录凭据。

Repository and source: https://github.com/phxumy/PDFMathTranslate
