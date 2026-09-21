# 한 방 실행 스크립트 (Windows)
#   .\run.ps1              가상환경 준비 + 앱 실행
#   .\run.ps1 -Reinstall   의존성 다시 설치 후 실행
#   .\run.ps1 -Port 8080   다른 포트로 실행
[CmdletBinding()]
param(
    [switch]$Reinstall,
    [int]$Port = 5050
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Train Reservation App - Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$VenvPython = ".\venv\Scripts\python.exe"
$Stamp      = ".\venv\.deps-installed"

# 1) 가상환경 준비
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> 가상환경이 없어 새로 만듭니다 (venv)" -ForegroundColor Yellow
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "!! Python 을 찾을 수 없습니다. Python 3.12+ 설치 후 다시 실행하세요." -ForegroundColor Red
        Write-Host "   https://www.python.org/downloads/  (설치 시 'Add python.exe to PATH' 체크)" -ForegroundColor Yellow
        exit 1
    }
    & python -m venv venv
    if ($LASTEXITCODE -ne 0) { Write-Host "!! 가상환경 생성 실패" -ForegroundColor Red; exit 1 }
    $Reinstall = $true
}
Write-Host "==> $(& $VenvPython --version) ($VenvPython)" -ForegroundColor Green

# 2) 의존성 설치 (최초 1회 또는 requirements 변경 시)
$needInstall = $Reinstall -or (-not (Test-Path $Stamp))
if (-not $needInstall) {
    $req = Get-Item ".\requirements.txt"
    if ($req.LastWriteTime -gt (Get-Item $Stamp).LastWriteTime) { $needInstall = $true }
}
if ($needInstall) {
    Write-Host "==> 의존성 설치 중... (requirements-windows.txt)" -ForegroundColor Yellow
    & $VenvPython -m pip install --upgrade pip -q
    & $VenvPython -m pip install -r requirements-windows.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "!! 의존성 설치 실패" -ForegroundColor Red; exit 1 }
    New-Item -ItemType File -Path $Stamp -Force | Out-Null
    Write-Host "==> 의존성 설치 완료" -ForegroundColor Green
}

# 3) 캐시 정리 (최신 코드 보장)
Get-ChildItem -Path app,korail2 -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 4) 실행
$env:PORT = "$Port"
Write-Host ""
Write-Host "🚄 http://localhost:$Port 에서 접속하세요 (종료: Ctrl+C)" -ForegroundColor Green
Write-Host ""
& $VenvPython main.py
