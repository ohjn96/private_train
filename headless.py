#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""헤드리스 예약 러너 진입점 (본체는 app/headless.py).

    python headless.py --dep 서울 --arr 부산 --date 20261003 --from 08:00
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.headless import main

if __name__ == '__main__':
    sys.exit(main())
