# -*- coding: utf-8 -*-
"""core.stations 으로 옮겨졌다. 예전 import 경로가 계속 동작하도록 같은 모듈을 가리킨다."""
import sys

import core.stations

sys.modules[__name__] = core.stations
