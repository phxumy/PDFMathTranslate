This is the first downloadable Windows x64 package of the **unofficial PDFMathTranslate Codex Edition** community fork.

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
