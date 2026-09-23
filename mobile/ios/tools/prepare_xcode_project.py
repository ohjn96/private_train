# -*- coding: utf-8 -*-
"""`briefcase create iOS` 뒤에 한 번 돌린다: Briefcase 가 만든 Xcode 프로젝트를 두 군데 고친다.

1. 홈 화면 이름(CFBundleDisplayName)을 "열차예약" 으로.
   formal_name 은 Xcode 타깃·실행 파일 이름이 되므로 ASCII(TrainReservation)로 두었다.
   (`briefcase build` 는 Info.plist 를 plistlib 로 다시 쓰지만 다른 키는 그대로 둔다)
2. 서명 없는(unsigned) 기기 빌드가 되게.
   파이썬 support package 의 utils.sh 가 표준 라이브러리 C 확장(.so)을 framework 로 바꾸면서
   `codesign --sign "$EXPANDED_CODE_SIGN_IDENTITY"` 를 부르는데, CODE_SIGNING_ALLOWED=NO 로
   빌드하면 이 값이 비어 있어 빌드가 실패한다. 비어 있으면 ad-hoc("-") 으로 서명하게 바꾼다.
   Sideloadly 가 설치할 때 사용자의 Apple ID 로 전부 다시 서명하므로 ad-hoc 서명은 상관없다.
   (서명 인증서가 있는 빌드에서는 값이 비어 있지 않으므로 동작이 바뀌지 않는다)

    python mobile/ios/tools/prepare_xcode_project.py
"""
import plistlib
import sys
from pathlib import Path

IOS_DIR = Path(__file__).resolve().parent.parent
BUNDLE = IOS_DIR / 'build' / 'trainreservation' / 'ios' / 'xcode'
DISPLAY_NAME = '열차예약'

SIGN_ORIGINAL = '--sign "$EXPANDED_CODE_SIGN_IDENTITY"'
SIGN_PATCHED = '--sign "${EXPANDED_CODE_SIGN_IDENTITY:--}"'


def set_display_name(bundle: Path, name: str = DISPLAY_NAME) -> Path:
    plists = sorted(p for p in bundle.glob('*/*-Info.plist') if p.parent.name != 'Support')
    if len(plists) != 1:
        raise FileNotFoundError(f'{bundle} 에서 앱 Info.plist 를 하나로 못 찾았습니다: {plists}')
    path = plists[0]
    with path.open('rb') as f:
        info = plistlib.load(f)
    info['CFBundleDisplayName'] = name
    with path.open('wb') as f:
        plistlib.dump(info, f)
    return path


def allow_unsigned_build(bundle: Path) -> Path:
    path = bundle / 'Support' / 'Python.xcframework' / 'build' / 'utils.sh'
    text = path.read_text(encoding='utf-8')
    if SIGN_PATCHED in text:
        return path
    if SIGN_ORIGINAL not in text:
        # support package 가 바뀌어 모양이 달라졌다. 기기 빌드가 실패하면 여기부터 확인.
        raise ValueError(f'{path} 에서 codesign 줄을 찾지 못했습니다 (support package 버전 확인)')
    path.write_text(text.replace(SIGN_ORIGINAL, SIGN_PATCHED), encoding='utf-8')
    return path


def main(argv=None) -> int:
    bundle = Path(argv[0]) if argv else BUNDLE
    print(f'patched {set_display_name(bundle)}')
    print(f'patched {allow_unsigned_build(bundle)}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
