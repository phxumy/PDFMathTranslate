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
echo Your login credentials are stored by Codex for your Windows account;
echo they are never included in this application package.
echo.
"%CODEX_EXE%" login
set "LOGIN_EXIT=%ERRORLEVEL%"
echo.
if not "%LOGIN_EXIT%"=="0" (
  echo Codex login ended with exit code %LOGIN_EXIT%.
) else (
  echo Codex login command finished successfully.
)
pause
exit /b %LOGIN_EXIT%

