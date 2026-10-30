PDFMathTranslate Codex Edition - Windows x64 installer
======================================================

INSTALL AND START
1. Download PDFMathTranslate-Codex-Setup-win64.exe from this project's release.
2. Run the installer. It installs for the current Windows user, adds a Start
   menu shortcut, and optionally adds a desktop shortcut. No Python or Node.js
   installation is required.
3. Open PDFMathTranslate Codex. In the Codex account panel, check your account
   status or start sign-in. Complete the official sign-in page when it opens.
4. Select one or more PDFs. The queue translates them in displayed order.
   Completed translation and bilingual downloads remain available after a
   later failure or Stop. Retry the same queue/settings in this session to
   skip completed outputs; Ignore cache requests a new run.
5. Original navigation remains available during translation. Completed pages
   replace their original page in the preview; elapsed times remain after completion.
6. Translation history stores task events, errors, progress and cache statistics.
   Download originals/results or export a log. Missing local files show as deleted.
   Save needed translations before confirming local-file or standard-cache cleanup.
   Cleanup is disabled during translation/update. Legacy history is not imported;
   BabelDOC's own cache is outside this standard-cache manager.

UPDATES
Use Check for updates in the application, then explicitly click Download and
install. The application checks only https://github.com/phxumy/PDFMathTranslate,
downloads its official EXE attachment, verifies SHA-256 and the expected size,
then closes and upgrades. Updates cannot install while translation is running.
Use Cancel download to stop before installer handoff; a cancelled download can
be retried. The cancellation button is hidden once installation begins.
The app reopens after a successful in-app upgrade. A failed download or hash
check never runs the installer. Browser mode links to the release page.

Existing ZIP installations can install this release into its default directory.
The installer does not remove the old extracted folder or its translated PDFs.
Existing application settings are retained. The bundled CLI requires a fresh
sign-in when upgrading to codex.7.2; personal Codex credentials are not copied.

YOUR FILES
Settings: %USERPROFILE%\.config\PDFMathTranslate
New desktop translation workspace:
  %USERPROFILE%\.config\PDFMathTranslate\workspace
Bundled Codex sign-in: %USERPROFILE%\.config\PDFMathTranslate\codex
The bundled CLI uses private file credentials for login, logout, account/model
queries and translation. Personal Codex credentials are not read or changed.
A user-specified external CLI retains its own CODEX_HOME and credential store.
The account panel shows email, plan, remaining quota percentages and reset times
when available. API-key billing balances are not available through this API.
Separate sign-ins to the same account still consume that account's quota.
Upgrades and uninstallation leave these user directories untouched.

TRANSLATION SPEED
The optional Codex Fast mode requests the account's faster service tier. It
depends on model/account availability and can consume more account allowance.
The 2x concurrency preset increases parallel translation work; actual throughput
still depends on the document, selected model, network, and account limits.
It does not promise exactly twice the speed. ETA is a rolling estimate based
on completed translation work and becomes more useful after enough work ends.
An account with tighter rate limits may work better with lower concurrency.

WHAT IS INCLUDED
- The Windows desktop shell and command-line/browser launchers.
- Official stable Codex CLI 0.166.0, Node.js 24.19.0, and locked browser bridge packages.
- Python and pinned dependencies, plus offline layout/OCR/font assets.
- Source references and third-party license notices.
No account, allowance, API key, credentials, or personal documents are bundled.
Cloud translation requires internet and consumes the selected service's quota.

WINDOWS
Supports Windows 10/11 x64. Microsoft Edge WebView2 is required for the desktop
window; normally it is already present. If needed, obtain WebView2 and the
Visual C++ x64 Redistributable from Microsoft. This community release is not
code-signed; Windows may display an unknown-publisher warning. SHA256SUMS.txt
on the same release page can be used to verify the downloaded installer.

Repository: https://github.com/phxumy/PDFMathTranslate
Releases: https://github.com/phxumy/PDFMathTranslate/releases/latest
Issues: https://github.com/phxumy/PDFMathTranslate/issues
This is an unofficial community fork, not an OpenAI or upstream release.
