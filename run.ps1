# Quick Run Script for Train Reservation App
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Train Reservation App - Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if venv exists
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Error: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Please create venv first: python -m venv venv" -ForegroundColor Yellow
    exit 1
}

# Activate venv
Write-Host "✓ Activating virtual environment..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"

# Clear Python cache (실행 시 최신 코드 보장)
Write-Host "✓ Clearing Python cache..." -ForegroundColor Yellow
Get-ChildItem -Recurse -Path app,korail2 -Include *.pyc,__pycache__ -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "🚄 Starting application..." -ForegroundColor Green
Write-Host "💡 Tip: Press Ctrl+C to stop (cache will be cleaned automatically)" -ForegroundColor Cyan
Write-Host ""

# Run with venv python (프로그램 종료 시 자동 캐시 정리)
python main.py
