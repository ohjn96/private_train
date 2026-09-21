@echo off
REM 더블클릭으로 실행 (가상환경 자동 준비 + 앱 실행)
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
pause
