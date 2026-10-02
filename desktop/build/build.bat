@echo off
REM 더블클릭 한 번으로 Windows EXE 빌드
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build.ps1" %*
echo.
pause
