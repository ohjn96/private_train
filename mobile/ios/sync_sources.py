# -*- coding: utf-8 -*-
"""iPhone 앱에 실을 공통 파이썬 코드를 mobile/ios/src/ 로 복사한다 (복사본은 git 에 올리지 않음).

안드로이드의 `syncPythonSources`(app/build.gradle.kts) 와 같은 일을 한다:
- 저장소 루트의 webui/, core/, korail2/ → src/webui, src/core, src/korail2
- mobile/shared/mobile_runtime.py        → src/mobile_runtime.py
- VERSION 파일의 버전을 pyproject.toml 의 `version = "..."` 에 적는다
  (Briefcase 는 pyproject 의 버전으로 CFBundleShortVersionString 을 만든다)

`briefcase create/update/build` 전에 매번 돌린다:

    python mobile/ios/sync_sources.py
"""
import re
import shutil
import sys
from pathlib import Path

IOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = IOS_DIR.parent.parent

#: 저장소 루트 기준 복사할 패키지 (폴더)
PACKAGES = ('webui', 'core', 'korail2')
#: 저장소 루트 기준 복사할 단일 모듈 → src/ 에 둘 이름
MODULES = {'mobile/shared/mobile_runtime.py': 'mobile_runtime.py'}

_IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.DS_Store')
_VERSION_LINE = re.compile(r'^(version\s*=\s*)"[^"]*"', re.MULTILINE)


_SEMVER = re.compile(r'(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.(\d+))?')
_PEP440_STAGE = {'alpha': 'a', 'beta': 'b', 'rc': 'rc'}


def to_pep440(version: str) -> str:
    """VERSION(3.0.0-beta.0) → Briefcase 가 받는 PEP 440 (3.0.0b0)."""
    m = _SEMVER.fullmatch(version)
    if not m:
        raise ValueError(f'VERSION 파일 형식이 이상합니다: {version!r} (예: 3.0.0, 3.0.0-beta.0)')
    base, stage, n = m.groups()
    return base if not stage else f'{base}{_PEP440_STAGE[stage]}{n}'


def read_version(repo_root: Path) -> str:
    """VERSION 파일을 읽어 PEP 440 으로 돌려준다 (pyproject.toml 에 그대로 쓴다)."""
    return to_pep440((repo_root / 'VERSION').read_text(encoding='utf-8').strip())


def write_version(pyproject: Path, version: str) -> bool:
    """pyproject.toml 의 첫 `version = "..."` 줄을 바꾼다. 바뀌었으면 True."""
    text = pyproject.read_text(encoding='utf-8')
    new, count = _VERSION_LINE.subn(lambda m: f'{m.group(1)}"{version}"', text, count=1)
    if count != 1:
        raise ValueError(f'{pyproject} 에 version = "..." 줄이 없습니다')
    if new == text:
        return False
    pyproject.write_text(new, encoding='utf-8')
    return True


def sync(repo_root: Path = REPO_ROOT, ios_dir: Path = IOS_DIR) -> list[Path]:
    """복사하고 버전을 맞춘다. 복사한 대상 경로 목록을 돌려준다."""
    src = ios_dir / 'src'
    src.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in PACKAGES:
        origin = repo_root / name
        if not (origin / '__init__.py').is_file():
            raise FileNotFoundError(f'{origin} 패키지가 없습니다')
        target = src / name
        if target.exists():
            shutil.rmtree(target)  # 원본에서 지운 파일이 남지 않게 매번 새로
        shutil.copytree(origin, target, ignore=_IGNORE)
        copied.append(target)
    for rel, name in MODULES.items():
        target = src / name
        shutil.copy2(repo_root / rel, target)
        copied.append(target)
    write_version(ios_dir / 'pyproject.toml', read_version(repo_root))
    return copied


def main() -> int:
    copied = sync()
    for path in copied:
        print(f'synced {path.relative_to(IOS_DIR)}')
    print(f'version {read_version(REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
