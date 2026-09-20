#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform build script for the KTX/SRT Train Reservation App.
"""
import os
import sys
import platform
import subprocess
from pathlib import Path

# Project root
ROOT_DIR = Path(__file__).parent.parent

# Platform-specific data separator for PyInstaller
DATA_SEP = ';' if platform.system() == 'Windows' else ':'

# Build configurations
BUILD_CONFIG = {
    'unified': {
        'script': 'main.py',
        'name': 'TrainReservationApp',
        'data': [
            f'app/templates{DATA_SEP}app/templates',
            f'app/static{DATA_SEP}app/static',
        ],
        'hidden_imports': [
            'flask', 'flask.sessions',
            'korail2',
            'requests', 'pycryptodome',
        ]
    },
    'ktx': {
        'script': 'ktx_main_web.py',
        'name': 'KTXReservationApp',
        'data': [f'static{DATA_SEP}static'],
        'hidden_imports': ['flask', 'flask.sessions'],
    }
}


def get_pyinstaller_cmd(config: dict) -> list[str]:
    """Generate PyInstaller command."""
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--clean',
        f'--name={config["name"]}',
    ]

    # Add data files
    for data in config.get('data', []):
        cmd.append(f'--add-data={data}')

    # Add hidden imports
    for imp in config.get('hidden_imports', []):
        cmd.append(f'--hidden-import={imp}')

    # Platform-specific options
    if platform.system() == 'Darwin':  # macOS
        cmd.append('--argv-emulation')

    cmd.append(config['script'])
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
        print(f"\n{'=' * 50}")
        print(f"Build successful!")
        print(f"Output: dist/{config['name']}")
        print(f"{'=' * 50}")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error: {e}")
        sys.exit(1)


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
