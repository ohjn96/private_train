# -*- coding: utf-8 -*-
"""core.rate_limit 으로 옮겨졌다. 예전 import 경로가 계속 동작하도록 같은 모듈을 가리킨다."""
import sys

import core.rate_limit

sys.modules[__name__] = core.rate_limit
