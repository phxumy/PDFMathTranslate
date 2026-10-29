# Windows EXE release

The `v*-codex.*` tag workflow builds a clean application from `uv.lock`, pinned Python/PyStand/Codex/Node archives and `browser_bridge/package-lock.json`. It runs Python and JavaScript tests, packages offline assets, checks for user data, and compiles an Inno Setup installer. Update `pdf2zh/version.py` to match the release tag before tagging.

The final release contains `PDFMathTranslate-Codex-Setup-win64.exe` and `SHA256SUMS.txt`. It is published only after the installer has installed into an isolated smoke-test directory and its CLI has started successfully. Inno Setup 6.7.3 is pinned by SHA-256 in the workflow.

For a prepared, clean staging directory on Windows:

```powershell
.\script\build-windows-installer.ps1 -ReleaseRoot C:\staging\PDFMathTranslate-Codex -ReleaseTag v1.9.11-codex.7 -Compiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Do not build a release from a user's working application folder. Copy only clean dependency packages, tracked project files, offline assets and required license files. Never bundle `.codex`, `auth.json`, environment files, translation caches, or PDFs.

Release tags may include one hotfix component, such as `v1.9.11-codex.5.1`. The build script maps the Codex revision and hotfix to the fourth Windows version component (`revision * 100 + hotfix`), so this tag has file version `1.9.11.501`. Hotfix values are limited to 0–99; every Windows component must fit 0–65535.

An isolated install can be tested with `/VERYSILENT /NORESTART /NOICONS /TESTMODE=1 /DIR="C:\isolated-test"`. Test mode skips shortcuts, Add/Remove Programs registration and automatic launch. Use a new empty directory, never a user's application directory. Normal installs use a stable AppId and the current-user Programs directory; upgrades reuse the existing installation location.

The desktop launcher keeps generated files under the existing user configuration directory. The installer does not delete user settings, Codex credentials, browser profiles or documents. `WAITFORPID` is an internal update argument: the installer waits for the calling application to exit before replacing files. No update is downloaded automatically, and every downloaded EXE must match the published SHA-256 and size before execution.

Inno Setup behavior: https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
Installer command-line options: https://jrsoftware.org/ishelp/topic_setupcmdline.htm
