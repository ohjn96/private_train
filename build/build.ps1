<#
.SYNOPSIS
    Windows EXE 빌드 (가상환경 준비 -> PyInstaller -> 루트로 이동)

    실제 PyInstaller 옵션은 build/build.py 한 곳에만 있습니다.
    이 스크립트는 venv 준비 + 의존성 설치 + build.py 호출만 담당합니다.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build\build.ps1
    .\build\build.ps1 -SkipInstall
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall   # pip install 생략
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (Get-Content "$Root\VERSION" -Raw).Trim()
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Build TrainReservationApp v$Version" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1) 가상환경
$VenvPython = "$Root\venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> 가상환경 생성 (venv)" -ForegroundColor Yellow
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "!! Python 을 찾을 수 없습니다. Python 3.12+ 를 설치하세요." -ForegroundColor Red
        exit 1
    }
    & python -m venv venv
    if ($LASTEXITCODE -ne 0) { Write-Host "!! 가상환경 생성 실패" -ForegroundColor Red; exit 1 }
    $SkipInstall = $false
}
Write-Host "==> $(& $VenvPython --version)" -ForegroundColor Green

# 2) 의존성 (PyInstaller 포함)
if (-not $SkipInstall) {
    Write-Host "==> 의존성 설치 (requirements-windows.txt)" -ForegroundColor Yellow
    & $VenvPython -m pip install --upgrade pip -q
    & $VenvPython -m pip install -r requirements-windows.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "!! 의존성 설치 실패" -ForegroundColor Red; exit 1 }
}

# 3) 캐시 정리
Get-ChildItem -Path app,korail2 -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 4) 빌드 (옵션은 build/build.py 가 소유)
& $VenvPython build\build.py unified
if ($LASTEXITCODE -ne 0) { Write-Host "!! 빌드 실패" -ForegroundColor Red; exit 1 }

$exe = "$Root\TrainReservationApp-v$Version.exe"
if (-not (Test-Path $exe)) { Write-Host "!! 결과물을 찾을 수 없습니다: $exe" -ForegroundColor Red; exit 1 }
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

Write-Host ""
Write-Host "완료: TrainReservationApp-v$Version.exe ($sizeMb MB)" -ForegroundColor Green
Write-Host "실행: .\TrainReservationApp-v$Version.exe  ->  http://localhost:5050" -ForegroundColor Green
