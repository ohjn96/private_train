# -*- coding: utf-8 -*-
"""테스트 공용 도구."""
import time
from unittest import mock

from app.licensing import LicenseStatus
from app.licensing.token import License


def valid_license_patch():
    """라이선스 게이트를 '유효한 라이선스가 있는 상태'로 고정하는 patcher.

    게이트는 모든 요청을 막으므로, 게이트 자체를 검증하는 test_licensing.py 를
    제외한 라우트 테스트는 이걸 걸어야 한다.

        def setUpModule():
            global _license
            _license = valid_license_patch()
            _license.start()

        def tearDownModule():
            _license.stop()
    """
    now = int(time.time())
    status = LicenseStatus(
        valid=True,
        code='ok',
        license=License(
            license_id='test',
            machine_id='TEST-TEST-TEST-TEST',
            issued_at=now,
            expires_at=now + 365 * 86400,
            name='테스트',
            note='',
            data={},
        ),
    )
    return mock.patch('app.licensing.current_status', return_value=status)
