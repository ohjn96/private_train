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

# 저장소 루트 (이 파일은 desktop/build/ 에 있다)
ROOT_DIR = Path(__file__).resolve().parents[2]
# PyInstaller 중간 산출물 (desktop/build/.pyi)
PYI_DIR = Path(__file__).resolve().parent / '.pyi'

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
            f'{ROOT_DIR / "webui" / "templates"}{DATA_SEP}webui/templates',
            # webui/static 은 Tailwind 원본(css/input.css)을 빼고 담는다 (static_data() 참고)
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
        'collect_submodules': ['desktop', 'webui', 'core', 'korail2'],
    },
}

#: 번들에 넣지 않을 webui/static 아래 파일 (빌드 입력일 뿐 실행 중에는 쓰지 않는다)
STATIC_EXCLUDE = {'css/input.css'}


def static_data(static_dir: Path = ROOT_DIR / 'webui' / 'static') -> list[str]:
    """webui/static 을 --add-data 항목으로. STATIC_EXCLUDE 에 든 파일은 뺀다.

    --add-data 는 폴더 단위로만 넣을 수 있어, 제외할 파일이 든 폴더는 파일마다 따로 넣는다.
    """
    excluded = {static_dir / rel for rel in STATIC_EXCLUDE}
    entries = []

    def add(path: Path) -> None:
        dest = Path('webui/static') / path.relative_to(static_dir)
        if path.is_file():
            dest = dest.parent
        entries.append(f'{path}{DATA_SEP}{dest.as_posix()}')

    def walk(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            if path in excluded:
                continue
            if path.is_dir() and any(e.is_relative_to(path) for e in excluded):
                walk(path)
            else:
                add(path)

    walk(static_dir)
    return entries


def get_pyinstaller_cmd(config: dict) -> list[str]:
    """Generate PyInstaller command."""
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--clean',
        '--noconfirm',
        f'--name={config["name"]}-v{VERSION}',
        # 중간 산출물/spec 은 desktop/build/.pyi 아래로 모아 루트를 깨끗하게 유지
        f'--distpath={PYI_DIR / "dist"}',
        f'--workpath={PYI_DIR / "work"}',
        f'--specpath={PYI_DIR}',
    ]

    # Add data files
    for data in config.get('data', []) + static_data():
        cmd.append(f'--add-data={data}')

    # Add hidden imports
    for imp in config.get('hidden_imports', []):
        cmd.append(f'--hidden-import={imp}')

    # Collect whole packages (앱 패키지는 정적 분석으로 다 안 잡힐 수 있음)
    for pkg in config.get('collect_submodules', []):
        cmd.append(f'--collect-submodules={pkg}')

    # macOS 의 --argv-emulation 은 .app 번들(windowed) 전용이라 콘솔 앱에는 쓰지 않는다.
    # (기동 시 AppKit 이벤트 루프를 잠깐 도느라 늦어지기만 한다)

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
    built = PYI_DIR / 'dist' / f'{base_name}-v{VERSION}{suffix}'
    if not built.exists():
        raise FileNotFoundError(f'빌드 결과물을 찾을 수 없습니다: {built}')

    target = ROOT_DIR / built.name

    # 같은 플랫폼의 이전 버전 산출물만 정리 (예: Linux 빌드가 .exe 를 지우지 않도록).
    # 버전에 점이 들어가 Path.suffix 가 '.1' 처럼 잡히므로 확장자는 이름으로 판별한다.
    def _same_platform(name: str) -> bool:
        return name.endswith('.exe') if suffix == '.exe' else not name.endswith('.exe')

    for old in ROOT_DIR.glob(f'{base_name}*'):
        if old.is_file() and _same_platform(old.name) and old != target:
            old.unlink()
    shutil.move(str(built), target)

    # 중간 산출물 정리
    shutil.rmtree(PYI_DIR, ignore_errors=True)
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
