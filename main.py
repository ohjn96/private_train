#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""하위 호환 진입점. 실제 코드는 desktop/main.py 에 있다.

    python main.py              = python -m desktop
    python main.py --headless   = python -m desktop --headless

scripts/run.sh, exe 빌드(PyInstaller)가 이 파일로 들어온다.
"""
from desktop.main import main

if __name__ == '__main__':
    main()
