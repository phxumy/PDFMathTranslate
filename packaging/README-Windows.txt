PDFMathTranslate Codex Edition - Windows x64 portable package
=============================================================

QUICK START

1. Extract the entire ZIP to a normal writable folder. Do not run it inside
   the ZIP preview.
2. To use the Codex translation service, double-click Login-Codex.cmd once
   and complete the official sign-in in your browser.
3. Double-click PDFMathTranslate-Codex.exe.
4. Add a PDF, choose Codex (or another configured service), then translate.

No Python installation is required. This package includes the official Codex
CLI 0.145.0 program, but it does not include an account, subscription, free
quota, API key, or any user's login credentials. Translation through Codex is
online and uses the allowance of the account that signs in.

The included BabelDOC offline assets avoid downloading the layout/OCR/font
assets during first launch. They do not make cloud translation offline.

CODEX FIELDS (VERIFIED SEPTEMBER 5, 2026)

- CODEX_BIN: A fresh release normally shows `codex`; keep it unchanged. It
  means automatic discovery and selects the bundled CLI first. A full path
  inside the extracted release ending in
  codex-cli\x86_64-pc-windows-msvc\bin\codex.exe is also correct. To customize
  it, enter the full path to another codex.exe, not a directory. Blank is
  equivalent to `codex`.
- CODEX_PROFILE: Usually blank. Otherwise enter only a profile name such as
  `translation`, corresponding to $CODEX_HOME/translation.config.toml.
- CODEX_MODEL: Blank delegates to the CLI/account recommended model when no
  profile is selected; it does not inherit the Codex Desktop picker or user
  config.toml in this app's isolated fast path. Bundled CLI 0.145.0 normally
  recommends gpt-5.6-sol on the verification date, but this is not pinned.
- CODEX_REASONING_EFFORT: Blank is explicitly changed to `none`; it does not
  inherit any model, CLI, config, or profile default. Accepted input values are
  none, low, medium, high, xhigh, and max, subject to model support. Ultra is
  not accepted by this translation backend.
- CODEX_TIMEOUT: Request timeout in seconds; blank means 120.

Common exact model IDs are gpt-6-astra, gpt-5.6-sol, gpt-5.6-terra,
gpt-5.6-luna, gpt-5.5, gpt-5.4, gpt-5.4-mini, and
gpt-5.3-codex-spark. GPT-6 Astra is still rolling out by account and documents
low, medium, high, xhigh, and max effort; do not pair it with blank/none.
Invalid, retired, unavailable, or effort-incompatible selections fail instead
of silently switching models. CODEX_BIN is only a program path; it contains no
developer account, allowance, or credentials. Each user must sign in separately.

SECURITY AND PRIVACY

- The desktop interface is bound to 127.0.0.1 and is not exposed to your LAN.
- PDF text selected for translation is sent to the translation service you
  choose. Codex sends it through your signed-in Codex account.
- Never send anyone auth.json or the contents of your .codex directory.
- This package is built on a clean GitHub-hosted Windows runner from the exact
  public release tag. It intentionally excludes PDFs, caches, configuration,
  API keys, and login data.
- Verify the ZIP against SHA256SUMS.txt on the GitHub Release page if desired.

WINDOWS WARNINGS

The application is not code-signed, so Windows SmartScreen may show
"Unknown publisher". If you downloaded it from the official release page for
this fork and verified the checksum, use More info > Run anyway.

Windows 10/11 normally includes Microsoft Edge WebView2. If the window cannot
open, install the WebView2 Evergreen Runtime and the Microsoft Visual C++ x64
Redistributable from Microsoft's official website.

TROUBLESHOOTING

- "codex executable not found": make sure you extracted the full ZIP and the
  codex-cli folder is beside the EXE.
- Codex sign-in or quota errors: run Login-Codex.cmd again, or run the bundled
  codex.exe login status command in a terminal.
- A translation service may require its own API key. The program never ships
  third-party API credentials.
- Logs and translated files are created locally. Remove them yourself before
  sharing the application folder.

PROJECT AND SOURCE

Repository: https://github.com/phxumy/PDFMathTranslate
Releases:   https://github.com/phxumy/PDFMathTranslate/releases/latest
Issues:     https://github.com/phxumy/PDFMathTranslate/issues

The exact source tag and commit are recorded in BUILD-INFO.txt and
SOURCE-CODE.txt. Third-party licenses are under licenses/.

This is an unofficial community fork. It is not an official release of the
upstream PDFMathTranslate project or OpenAI.
