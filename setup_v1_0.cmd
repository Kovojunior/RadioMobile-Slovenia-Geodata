@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_v1_0.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo Priprava V1.0 se je koncala z napako. Koda: %RC%
)
exit /b %RC%
