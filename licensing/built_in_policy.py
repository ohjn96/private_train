# -*- coding: utf-8 -*-
"""빌드 시점의 정책을 exe 에 구워 넣은 값.

왜 필요한가: 원격 정책 파일을 **한 번도** 받아보지 못한 설치는 기준이 없다.
이 값이 없으면 "인터넷을 막은 채 첫 실행"으로 검사를 통째로 건너뛸 수 있다.
빌드할 때의 license-policy.json 을 여기 적어두면, 그런 설치도 그 상태로 시작한다.

원격 정책은 이 값을 **덮어쓸 수 있다.** 단 seq 가 이 값 이상일 때만이라,
옛날 정책을 다시 들이밀어 푸는 것은 안 된다.

이 파일은 `python scripts/license_admin.py bake` 가 자동으로 고쳐 쓴다.
빌드(build/build.py)는 매번 이걸 먼저 실행하므로 손으로 건드릴 일은 없다.
"""

# license-policy.json 이 없던 시점에 빌드하면 'open' 이 된다.
BUILT_IN_MODE = "licensed"
BUILT_IN_SEQ = 2
BUILT_IN_MESSAGE = ""
