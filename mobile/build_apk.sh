#!/bin/bash
# ============================================================
# Build APK Script (Linux / WSL / macOS)
# KTX/SRT Train Reservation Android APK Builder
# ============================================================
# 
# 사용법:
#   chmod +x mobile/build_apk.sh
#   ./mobile/build_apk.sh
#
# 요구사항:
#   - Python 3.11+
#   - Java 17 (OpenJDK)
#   - Android SDK (Buildozer가 자동 설치)
#   - Linux / WSL / macOS (Windows 직접 실행 불가)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_ROOT/_apk_build"

echo "============================================"
echo "  KTX/SRT Train Reservation APK Builder"
echo "============================================"
echo ""

# Check prerequisites
echo "[1/6] Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is required. Install Python 3.11+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PYTHON_VERSION"

if ! command -v java &> /dev/null; then
    echo "WARNING: Java not found. Installing OpenJDK 17..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y openjdk-17-jdk
    elif command -v brew &> /dev/null; then
        brew install openjdk@17
    else
        echo "ERROR: Please install Java 17 manually"
        exit 1
    fi
fi

JAVA_VERSION=$(java -version 2>&1 | head -1)
echo "  Java: $JAVA_VERSION"

# Install buildozer if not available
if ! command -v buildozer &> /dev/null; then
    echo ""
    echo "[2/6] Installing Buildozer..."
    pip install --upgrade buildozer cython==3.0.10
else
    echo ""
    echo "[2/6] Buildozer already installed"
fi

# Install system dependencies (Ubuntu/Debian)
echo ""
echo "[3/6] Checking system dependencies..."
if command -v apt-get &> /dev/null; then
    sudo apt-get install -y \
        build-essential git ffmpeg \
        libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
        libportmidi-dev libswscale-dev libavformat-dev libavcodec-dev \
        zlib1g-dev libgstreamer1.0-dev gstreamer1.0-plugins-base \
        libgstreamer-plugins-base1.0-dev libjpeg-dev libpng-dev libtiff-dev \
        libgl1-mesa-dev libgles2-mesa-dev autoconf automake libtool \
        pkg-config libffi-dev libssl-dev cmake unzip 2>/dev/null || true
fi

# Prepare build directory
echo ""
echo "[4/6] Preparing build directory..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Copy files
cp "$SCRIPT_DIR/main.py" "$BUILD_DIR/main.py"
cp -r "$PROJECT_ROOT/app" "$BUILD_DIR/app"
cp -r "$PROJECT_ROOT/korail2" "$BUILD_DIR/korail2"
cp -r "$PROJECT_ROOT/SRT" "$BUILD_DIR/SRT"
cp "$SCRIPT_DIR/buildozer.spec" "$BUILD_DIR/buildozer.spec"

# Clean pycache
find "$BUILD_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true

echo "  Build directory prepared at: $BUILD_DIR"

# Build APK
echo ""
echo "[5/6] Building APK (this may take 30-60 minutes on first run)..."
cd "$BUILD_DIR"
buildozer android debug

# Find and copy APK  
echo ""
echo "[6/6] Locating APK..."
mkdir -p "$PROJECT_ROOT/output"

APK_FILE=$(find "$BUILD_DIR" -name "*.apk" -type f | head -1)

if [ -n "$APK_FILE" ]; then
    cp "$APK_FILE" "$PROJECT_ROOT/output/TrainReservation-v2.0.0.apk"
    echo ""
    echo "============================================"
    echo "  BUILD SUCCESSFUL!"
    echo "  APK: $PROJECT_ROOT/output/TrainReservation-v2.0.0.apk"
    echo "  Size: $(du -h "$PROJECT_ROOT/output/TrainReservation-v2.0.0.apk" | cut -f1)"
    echo "============================================"
    echo ""
    echo "  APK를 안드로이드 폰에 전송하여 설치하세요."
    echo "  (설정 > 보안 > 알 수 없는 출처 허용 필요)"
else
    echo "ERROR: APK file not found!"
    echo "Check buildozer logs for errors."
    exit 1
fi
