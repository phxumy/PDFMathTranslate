@echo off
setlocal
title PDFMathTranslate - Codex Login
set "CODEX_EXE=%~dp0codex-cli\x86_64-pc-windows-msvc\bin\codex.exe"

if not exist "%CODEX_EXE%" (
  echo [ERROR] Bundled Codex CLI was not found:
  echo %CODEX_EXE%
  echo.
  echo Please download the complete release package again.
  pause
  exit /b 1
)

echo This opens the official Codex sign-in flow.
echo This login is private to PDFMathTranslate and separate from your personal Codex.
echo Credentials stay in your user profile, never in this application package.
echo.
"%~dp0runtime\python.exe" -c "import sys; sys.path.insert(0, sys.argv[1]); from pdf2zh.codex_runtime import login_bundled_cli; sys.exit(login_bundled_cli(sys.argv[2]))" "%~dp0site-packages" "%CODEX_EXE%"
set "LOGIN_EXIT=%ERRORLEVEL%"
echo.
if not "%LOGIN_EXIT%"=="0" (
  echo Codex login ended with exit code %LOGIN_EXIT%.
) else (
  echo Codex login command finished successfully.
)
pause
exit /b %LOGIN_EXIT%

