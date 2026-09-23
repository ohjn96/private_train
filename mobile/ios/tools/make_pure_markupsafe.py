# -*- coding: utf-8 -*-
"""iPhone 빌드용 순수 파이썬 wheel 을 만든다 (mobile/ios/wheels/, git 에는 올리지 않음).

Briefcase 는 iOS 패키지를 `--only-binary=:all:` 로, iOS 플랫폼 태그를 붙여 설치한다.
그래서 PyPI 에 iOS wheel 도, 순수 파이썬(py3-none-any) wheel 도 없는 패키지는 설치가 안 된다.

- MarkupSafe 3.0.x : C 확장(_speedups) wheel 만 있다. 하지만 _speedups 가 없으면
  순수 파이썬 `_native` 로 자동으로 넘어가므로, sdist 에서 .py 만 골라 py3-none-any wheel 로 싼다.
  (Jinja2 가 MarkupSafe 를 요구하므로 이게 없으면 Flask 가 안 깔린다)
- pyaes 1.6.1      : 원래 순수 파이썬인데 PyPI 에 sdist 만 있다. 그대로 wheel 로 싼다.
  (korail2/_aes.py 가 pycryptodome 이 없을 때 쓴다. pycryptodome 도 iOS wheel 이 없다)

pyproject.toml 의 `requirement_installer_args = ["--find-links", "./wheels"]` 로 pip 가 이 폴더를 본다.

    python mobile/ios/tools/make_pure_markupsafe.py            # PyPI 에서 받아 만든다 (sha256 확인)
    python mobile/ios/tools/make_pure_markupsafe.py --sdist-dir DIR   # 미리 받아 둔 sdist 로
"""
import argparse
import base64
import hashlib
import io
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

IOS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = IOS_DIR / 'wheels'


class Spec:
    def __init__(self, name, version, url, sha256, package_dir, license_file=None):
        self.name = name              # 배포 이름 (wheel 파일명·dist-info 에 쓴다)
        self.version = version
        self.url = url
        self.sha256 = sha256
        self.package_dir = package_dir  # sdist 안의 패키지 폴더 (최상위 폴더 기준)
        self.license_file = license_file

    @property
    def sdist_name(self):
        return self.url.rsplit('/', 1)[-1]

    @property
    def wheel_name(self):
        return f'{self.name}-{self.version}-py3-none-any.whl'


#: requirements.txt(MarkupSafe==3.0.2) 와 같은 버전. 올릴 땐 url·sha256 을 PyPI 에서 함께 바꾼다.
SPECS = [
    Spec(
        'markupsafe', '3.0.2',
        'https://files.pythonhosted.org/packages/b2/97/5d42485e71dfc078108a86d6de8fa46db44a1a9295e89c5d6d4a06e23a62/markupsafe-3.0.2.tar.gz',
        'ee55d3edf80167e48ea11a923c7386f4669df67d7994554387f84e7d8b0a2bf0',
        'src/markupsafe', license_file='LICENSE.txt',
    ),
    Spec(
        'pyaes', '1.6.1',
        'https://files.pythonhosted.org/packages/44/66/2c17bae31c906613795711fc78045c285048168919ace2220daa372c7d72/pyaes-1.6.1.tar.gz',
        '02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f',
        'pyaes', license_file='LICENSE.txt',
    ),
]

#: 패키지 폴더에서 wheel 에 넣는 파일. C 소스(.c)·타입 스텁(.pyi)은 뺀다.
KEEP_SUFFIXES = ('.py', '.typed')


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
    return f'sha256={digest}'


def build_pure_wheel(spec: Spec, sdist_bytes: bytes, out_dir: Path) -> Path:
    """sdist(tar.gz) 에서 순수 파이썬 파일만 골라 py3-none-any wheel 을 만든다."""
    if spec.sha256 and hashlib.sha256(sdist_bytes).hexdigest() != spec.sha256:
        raise ValueError(f'{spec.sdist_name}: sha256 이 맞지 않습니다')

    files: dict[str, bytes] = {}
    pkg_info = None
    license_text = None
    with tarfile.open(fileobj=io.BytesIO(sdist_bytes), mode='r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split('/', 1)
            if len(parts) != 2:
                continue
            rel = parts[1]  # 최상위 "<name>-<version>/" 를 뗀 경로
            if rel == 'PKG-INFO':
                pkg_info = tar.extractfile(member).read()
            elif spec.license_file and rel == spec.license_file:
                license_text = tar.extractfile(member).read()
            elif rel.startswith(spec.package_dir + '/') and rel.endswith(KEEP_SUFFIXES):
                inner = rel[len(spec.package_dir) + 1:]
                if '__pycache__' in inner.split('/'):
                    continue
                top = spec.package_dir.rsplit('/', 1)[-1]
                files[f'{top}/{inner}'] = tar.extractfile(member).read()

    if pkg_info is None:
        raise ValueError(f'{spec.sdist_name}: PKG-INFO 가 없습니다')
    if not any(name.endswith('/__init__.py') for name in files):
        raise ValueError(f'{spec.sdist_name}: {spec.package_dir}/__init__.py 가 없습니다')

    dist_info = f'{spec.name}-{spec.version}.dist-info'
    files[f'{dist_info}/METADATA'] = pkg_info
    files[f'{dist_info}/WHEEL'] = (
        'Wheel-Version: 1.0\n'
        'Generator: make_pure_markupsafe (train-reservation)\n'
        'Root-Is-Purelib: true\n'
        'Tag: py3-none-any\n'
    ).encode()
    if license_text is not None:
        files[f'{dist_info}/licenses/{spec.license_file}'] = license_text

    record_name = f'{dist_info}/RECORD'
    record_lines = [f'{name},{_record_hash(data)},{len(data)}' for name, data in sorted(files.items())]
    record_lines.append(f'{record_name},,')
    files[record_name] = ('\n'.join(record_lines) + '\n').encode()

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / spec.wheel_name
    tmp = target.with_suffix('.tmp')
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):  # RECORD 는 이름순으로 dist-info 끝쪽에 들어간다
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))  # 재현 가능한 빌드
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    tmp.replace(target)
    return target


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - 고정된 PyPI 주소
        return resp.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT, help='wheel 을 둘 폴더')
    parser.add_argument('--sdist-dir', type=Path, help='미리 받아 둔 sdist 가 있는 폴더 (없으면 PyPI 에서 받음)')
    args = parser.parse_args(argv)

    for spec in SPECS:
        if args.sdist_dir:
            data = (args.sdist_dir / spec.sdist_name).read_bytes()
        else:
            data = _download(spec.url)
        path = build_pure_wheel(spec, data, args.out)
        print(f'built {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
