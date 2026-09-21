#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform build script for the KTX/SRT Train Reservation App.
"""
import os
import sys
import platform
import shutil
import subprocess
from pathlib import Path

# Project root
ROOT_DIR = Path(__file__).parent.parent

# 버전 (VERSION 파일이 단일 출처)
VERSION = (ROOT_DIR / 'VERSION').read_text(encoding='utf-8').strip()

# Platform-specific data separator for PyInstaller
DATA_SEP = ';' if platform.system() == 'Windows' else ':'

# Build configurations
BUILD_CONFIG = {
    'unified': {
        'script': 'main.py',
        'name': 'TrainReservationApp',
        # --specpath 기준으로 상대경로가 풀리므로 원본 경로는 절대경로로 준다
        'data': [
            f'{ROOT_DIR / "app" / "templates"}{DATA_SEP}app/templates',
            f'{ROOT_DIR / "app" / "static"}{DATA_SEP}app/static',
            f'{ROOT_DIR / "VERSION"}{DATA_SEP}.',
            # 라이선스 고지: BSD 등 번들 구성요소는 바이너리 배포 시 고지문을 함께 제공해야 한다
            f'{ROOT_DIR / "LICENSE"}{DATA_SEP}.',
            f'{ROOT_DIR / "THIRD-PARTY-NOTICES.md"}{DATA_SEP}.',
            f'{ROOT_DIR / "korail2" / "LICENSE"}{DATA_SEP}korail2',
        ],
        'hidden_imports': [
            'flask', 'flask.sessions', 'jinja2',
            'korail2',
            'requests',
            # pycryptodome 은 배포 이름, 실제 모듈 이름은 Crypto
            'Crypto', 'Crypto.Cipher.AES', 'Crypto.Util.Padding',
        ],
        'collect_submodules': ['app', 'korail2'],
    },
    'ktx': {
        'script': 'ktx_main_web.py',
        'name': 'KTXReservationApp',
        'data': [f'{ROOT_DIR / "static"}{DATA_SEP}static'],
        'hidden_imports': ['flask', 'flask.sessions'],
    }
}


def get_pyinstaller_cmd(config: dict) -> list[str]:
    """Generate PyInstaller command."""
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--clean',
        '--noconfirm',
        f'--name={config["name"]}-v{VERSION}',
        # 중간 산출물/spec 은 build/.pyi 아래로 모아 루트를 깨끗하게 유지
        '--distpath=build/.pyi/dist',
        '--workpath=build/.pyi/work',
        '--specpath=build/.pyi',
    ]

    # Add data files
    for data in config.get('data', []):
        cmd.append(f'--add-data={data}')

    # Add hidden imports
    for imp in config.get('hidden_imports', []):
        cmd.append(f'--hidden-import={imp}')

    # Collect whole packages (앱 패키지는 정적 분석으로 다 안 잡힐 수 있음)
    for pkg in config.get('collect_submodules', []):
        cmd.append(f'--collect-submodules={pkg}')

    # Platform-specific options
    if platform.system() == 'Darwin':  # macOS
        cmd.append('--argv-emulation')

    cmd.append(str(ROOT_DIR / config['script']))
    return cmd


def build(app_name: str = 'unified'):
    """Run the build process."""
    if app_name not in BUILD_CONFIG:
        print(f"Error: Unknown app '{app_name}'")
        print(f"Available apps: {', '.join(BUILD_CONFIG.keys())}")
        sys.exit(1)

    config = BUILD_CONFIG[app_name]

    print(f"=" * 50)
    print(f"Building {config['name']}...")
    print(f"Platform: {platform.system()} ({platform.machine()})")
    print(f"Python: {sys.version}")
    print(f"=" * 50)

    # Change to project root
    os.chdir(ROOT_DIR)

    # Generate and run command
    cmd = get_pyinstaller_cmd(config)
    print(f"\nCommand: {' '.join(cmd)}\n")

    try:
        subprocess.run(cmd, check=True)
        artifact = move_to_root(config['name'])
        print(f"\n{'=' * 50}")
        print(f"Build successful!")
        print(f"Output: {artifact.name}")
        print(f"{'=' * 50}")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error: {e}")
        sys.exit(1)


def move_to_root(base_name: str) -> Path:
    """dist/ 결과물을 프로젝트 루트로 옮기고, 이전 버전 산출물은 지운다."""
    suffix = '.exe' if platform.system() == 'Windows' else ''
    built = ROOT_DIR / 'build' / '.pyi' / 'dist' / f'{base_name}-v{VERSION}{suffix}'
    if not built.exists():
        raise FileNotFoundError(f'빌드 결과물을 찾을 수 없습니다: {built}')

    target = ROOT_DIR / built.name

    # 같은 플랫폼의 이전 버전 산출물만 정리 (예: Linux 빌드가 .exe 를 지우지 않도록)
    for old in ROOT_DIR.glob(f'{base_name}*'):
        if old.is_file() and old.suffix == suffix and old != target:
            old.unlink()
    shutil.move(str(built), target)

    # 중간 산출물 정리
    shutil.rmtree(ROOT_DIR / 'build' / '.pyi', ignore_errors=True)
    return target


def build_all():
    """Build all apps."""
    for app_name in BUILD_CONFIG:
        build(app_name)


def main():
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Build Train Reservation App')
    parser.add_argument(
        'app',
        nargs='?',
        default='unified',
        choices=list(BUILD_CONFIG.keys()) + ['all'],
        help='App to build (default: unified)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available build targets'
    )

    args = parser.parse_args()

    if args.list:
        print("Available build targets:")
        for name, config in BUILD_CONFIG.items():
            print(f"  {name:10} - {config['name']}")
        return

    if args.app == 'all':
        build_all()
    else:
        build(args.app)


if __name__ == '__main__':
    main()
