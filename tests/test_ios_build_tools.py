# -*- coding: utf-8 -*-
"""iPhone 빌드 도구: 소스 동기화, 순수 파이썬 wheel, Xcode 프로젝트 보정, pyproject·워크플로 설정."""
import contextlib
import hashlib
import importlib.util
import io
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / 'mobile' / 'ios'


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_sources = load(IOS / 'sync_sources.py', 'ios_sync_sources')
pure = load(IOS / 'tools' / 'make_pure_markupsafe.py', 'ios_make_pure')
prepare = load(IOS / 'tools' / 'prepare_xcode_project.py', 'ios_prepare')

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


def make_fake_repo(base: Path) -> tuple[Path, Path]:
    repo = base / 'repo'
    for pkg in ('webui', 'core', 'korail2'):
        (repo / pkg / 'sub').mkdir(parents=True)
        (repo / pkg / '__init__.py').write_text(f'NAME = "{pkg}"\n', encoding='utf-8')
        (repo / pkg / 'sub' / 'mod.py').write_text('X = 1\n', encoding='utf-8')
        (repo / pkg / '__pycache__').mkdir()
        (repo / pkg / '__pycache__' / 'x.cpython-312.pyc').write_bytes(b'junk')
    (repo / 'webui' / 'templates').mkdir()
    (repo / 'webui' / 'templates' / 'base.html').write_text('<html>', encoding='utf-8')
    (repo / 'mobile' / 'shared').mkdir(parents=True)
    (repo / 'mobile' / 'shared' / 'mobile_runtime.py').write_text('RUNTIME = 1\n', encoding='utf-8')
    (repo / 'VERSION').write_text('4.5.6\n', encoding='utf-8')
    ios = repo / 'mobile' / 'ios'
    ios.mkdir(parents=True)
    (ios / 'pyproject.toml').write_text(
        '[tool.briefcase]\nproject_name = "X"\nversion = "0.0.1"\n\n'
        '[tool.briefcase.app.x]\nversion = "keep-second"\n', encoding='utf-8')
    return repo, ios


class SyncSourcesTest(unittest.TestCase):
    def test_copies_packages_runtime_and_version(self):
        with tempfile.TemporaryDirectory() as d:
            repo, ios = make_fake_repo(Path(d))
            # 원본에서 지운 파일이 복사본에 남아 있으면 안 된다
            (ios / 'src' / 'core').mkdir(parents=True)
            (ios / 'src' / 'core' / 'stale.py').write_text('old', encoding='utf-8')
            copied = sync_sources.sync(repo, ios)
            src = ios / 'src'
            self.assertEqual({p.name for p in copied}, {'webui', 'core', 'korail2', 'mobile_runtime.py'})
            for pkg in ('webui', 'core', 'korail2'):
                self.assertTrue((src / pkg / 'sub' / 'mod.py').is_file())
                self.assertFalse((src / pkg / '__pycache__').exists())
            self.assertFalse((src / 'core' / 'stale.py').exists())
            self.assertTrue((src / 'webui' / 'templates' / 'base.html').is_file())
            self.assertEqual((src / 'mobile_runtime.py').read_text(encoding='utf-8'), 'RUNTIME = 1\n')
            text = (ios / 'pyproject.toml').read_text(encoding='utf-8')
            self.assertIn('version = "4.5.6"', text)
            self.assertIn('version = "keep-second"', text)  # 첫 줄만 바꾼다
            # 두 번 돌려도 같다
            sync_sources.sync(repo, ios)
            self.assertFalse(sync_sources.write_version(ios / 'pyproject.toml', '4.5.6'))

    def test_missing_package_or_bad_version_fails(self):
        with tempfile.TemporaryDirectory() as d:
            repo, ios = make_fake_repo(Path(d))
            (repo / 'VERSION').write_text('v1.2 beta', encoding='utf-8')
            with self.assertRaises(ValueError):
                sync_sources.sync(repo, ios)
            (repo / 'VERSION').write_text('1.2.3', encoding='utf-8')
            (repo / 'korail2' / '__init__.py').unlink()
            with self.assertRaises(FileNotFoundError):
                sync_sources.sync(repo, ios)

    def test_real_repo_layout(self):
        """실제 저장소에 동기화할 패키지가 다 있고, pyproject 의 sources 와 맞는다."""
        for pkg in sync_sources.PACKAGES:
            self.assertTrue((ROOT / pkg / '__init__.py').is_file(), pkg)
        for rel in sync_sources.MODULES:
            self.assertTrue((ROOT / rel).is_file(), rel)
        text = (IOS / 'pyproject.toml').read_text(encoding='utf-8')
        expected = {f'src/{p}' for p in sync_sources.PACKAGES} | {f'src/{m}' for m in sync_sources.MODULES.values()}
        expected.add('src/trainreservation')
        sources = set(re.findall(r'"(src/[^"]+)"', text))
        self.assertEqual(sources, expected)
        # 동기화 대상은 git 에 올리지 않는다
        ignore = (IOS / '.gitignore').read_text(encoding='utf-8')
        for path in expected - {'src/trainreservation'}:
            self.assertIn('/' + path, ignore)
        self.assertIn('/wheels/', ignore)


def make_sdist(top: str, files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f'{top}/{name}')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class PureWheelTest(unittest.TestCase):
    def fake_markupsafe(self):
        return make_sdist('markupsafe-3.0.2', {
            'PKG-INFO': b'Metadata-Version: 2.1\nName: MarkupSafe\nVersion: 3.0.2\n',
            'LICENSE.txt': b'BSD',
            'src/markupsafe/__init__.py': (
                b'try:\n    from ._speedups import _escape_inner\n'
                b'except ImportError:\n    from ._native import _escape_inner\n'
                b'def escape(s):\n    return _escape_inner(str(s))\n'),
            'src/markupsafe/_native.py': b'def _escape_inner(s):\n    return s.replace("<", "&lt;")\n',
            'src/markupsafe/_speedups.c': b'/* C */',
            'src/markupsafe/_speedups.pyi': b'',
            'src/markupsafe/py.typed': b'',
            'tests/test_x.py': b'',
        })

    def test_builds_pure_wheel_without_c_sources(self):
        data = self.fake_markupsafe()
        spec = pure.Spec('markupsafe', '3.0.2', 'https://x/markupsafe-3.0.2.tar.gz',
                         hashlib.sha256(data).hexdigest(), 'src/markupsafe', 'LICENSE.txt')
        with tempfile.TemporaryDirectory() as d:
            wheel = pure.build_pure_wheel(spec, data, Path(d))
            self.assertEqual(wheel.name, 'markupsafe-3.0.2-py3-none-any.whl')
            with zipfile.ZipFile(wheel) as zf:
                names = set(zf.namelist())
                self.assertEqual(names, {
                    'markupsafe/__init__.py', 'markupsafe/_native.py', 'markupsafe/py.typed',
                    'markupsafe-3.0.2.dist-info/METADATA', 'markupsafe-3.0.2.dist-info/WHEEL',
                    'markupsafe-3.0.2.dist-info/RECORD', 'markupsafe-3.0.2.dist-info/licenses/LICENSE.txt',
                })
                self.assertIn('Tag: py3-none-any', zf.read('markupsafe-3.0.2.dist-info/WHEEL').decode())
                record = zf.read('markupsafe-3.0.2.dist-info/RECORD').decode()
                self.assertIn('markupsafe/_native.py,sha256=', record)
            # 설치한 것처럼 풀어서 import: _speedups 없이 _native 로 동작
            with zipfile.ZipFile(wheel) as zf:
                zf.extractall(Path(d) / 'site')
            out = subprocess.run(
                [sys.executable, '-I', '-c',
                 'import sys; sys.path.insert(0, sys.argv[1]); import markupsafe; '
                 'print(markupsafe.escape("<b>"), markupsafe.__file__)', str(Path(d) / 'site')],
                capture_output=True, text=True, check=True)
            self.assertIn('&lt;b>', out.stdout)
            self.assertIn(str(Path(d) / 'site'), out.stdout)
            # 같은 입력이면 같은 wheel (재현 가능)
            first = wheel.read_bytes()
            self.assertEqual(pure.build_pure_wheel(spec, data, Path(d)).read_bytes(), first)

    def test_hash_mismatch_is_rejected(self):
        spec = pure.Spec('markupsafe', '3.0.2', 'https://x/m.tar.gz', '0' * 64, 'src/markupsafe')
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            pure.build_pure_wheel(spec, self.fake_markupsafe(), Path(d))

    def test_pinned_specs(self):
        names = {s.name: s for s in pure.SPECS}
        self.assertEqual(set(names), {'markupsafe', 'pyaes'})
        req = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
        self.assertIn(f'MarkupSafe=={names["markupsafe"].version}', req)
        pyproject = (IOS / 'pyproject.toml').read_text(encoding='utf-8')
        for spec in pure.SPECS:
            self.assertRegex(spec.sha256, r'^[0-9a-f]{64}$')
            self.assertTrue(spec.url.endswith(f'{spec.name}-{spec.version}.tar.gz'))
            self.assertIn(f'=={spec.version}"', pyproject)
        self.assertNotRegex(pyproject.lower(), r'"pycryptodome')  # 요구 목록에 없다 (주석엔 있어도 됨)


class PrepareXcodeProjectTest(unittest.TestCase):
    def make_bundle(self, base: Path) -> Path:
        bundle = base / 'xcode'
        (bundle / 'TrainReservation').mkdir(parents=True)
        with (bundle / 'TrainReservation' / 'TrainReservation-Info.plist').open('wb') as f:
            plistlib.dump({'CFBundleDisplayName': '${PRODUCT_NAME}', 'MainModule': 'trainreservation',
                           'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, f)
        utils = bundle / 'Support' / 'Python.xcframework' / 'build'
        utils.mkdir(parents=True)
        (utils / 'utils.sh').write_text(
            'echo "Signing..."\n'
            '/usr/bin/codesign --force --sign "$EXPANDED_CODE_SIGN_IDENTITY" ${OTHER_CODE_SIGN_FLAGS:-} -o runtime x\n',
            encoding='utf-8')
        return bundle

    def test_patches_display_name_and_codesign(self):
        with tempfile.TemporaryDirectory() as d:
            bundle = self.make_bundle(Path(d))
            with contextlib.redirect_stdout(io.StringIO()):
                prepare.main([str(bundle)])
                prepare.main([str(bundle)])  # 두 번 돌려도 괜찮다
            with (bundle / 'TrainReservation' / 'TrainReservation-Info.plist').open('rb') as f:
                info = plistlib.load(f)
            self.assertEqual(info['CFBundleDisplayName'], '열차예약')
            self.assertEqual(info['NSAppTransportSecurity'], {'NSAllowsLocalNetworking': True})
            script = (bundle / 'Support' / 'Python.xcframework' / 'build' / 'utils.sh').read_text()
            self.assertIn('--sign "${EXPANDED_CODE_SIGN_IDENTITY:--}"', script)
            # 고친 줄이 bash 에서 정말 "-" 로 풀리는지
            out = subprocess.run(['bash', '-c', 'EXPANDED_CODE_SIGN_IDENTITY=; echo "${EXPANDED_CODE_SIGN_IDENTITY:--}"'],
                                 capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout.strip(), '-')

    def test_unknown_support_package_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            bundle = self.make_bundle(Path(d))
            (bundle / 'Support' / 'Python.xcframework' / 'build' / 'utils.sh').write_text('echo nothing\n')
            with self.assertRaises(ValueError):
                prepare.allow_unsigned_build(bundle)


class ConfigTest(unittest.TestCase):
    @unittest.skipIf(tomllib is None, 'tomllib 은 Python 3.11+')
    def test_pyproject_parses(self):
        with (IOS / 'pyproject.toml').open('rb') as f:
            conf = tomllib.load(f)['tool']['briefcase']
        app = conf['app']['trainreservation']
        self.assertEqual(conf['bundle'], 'com.ohjn96')
        self.assertEqual(app['formal_name'], 'TrainReservation')
        self.assertEqual(app['iOS']['info']['NSAppTransportSecurity'], {'NSAllowsLocalNetworking': True})
        # 매크로 중 소리 없는 오디오로 백그라운드 유지 (Info.plist 키, entitlement 아님)
        self.assertEqual(app['iOS']['info']['UIBackgroundModes'], ['audio'])
        self.assertEqual(app['iOS']['requirement_installer_args'], ['--find-links', './wheels'])

    def test_workflow(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML 없음 (requirements.txt 에 있음)')
        wf = yaml.safe_load((ROOT / '.github' / 'workflows' / 'ios.yml').read_text(encoding='utf-8'))
        triggers = wf[True] if True in wf else wf['on']  # YAML 1.1 은 on 을 True 로 읽는다
        self.assertIn('workflow_dispatch', triggers)
        self.assertEqual(triggers['push']['tags'], ['v*'])
        job = wf['jobs']['build-ios']
        self.assertTrue(job['runs-on'].startswith('macos'))
        script = '\n'.join(str(s.get('run', '')) for s in job['steps'])
        for needle in ('sync_sources.py', 'make_pure_markupsafe.py', 'prepare_xcode_project.py',
                       'briefcase create iOS', 'briefcase build iOS', 'simctl install', 'simctl launch',
                       'CODE_SIGNING_ALLOWED=NO', '-sdk iphoneos', 'Payload', '.ipa', 'gh release upload'):
            self.assertIn(needle, script)
        # 앱은 무작위 포트(0)로 뜨고, CI 는 앱이 적은 포트 파일에서 포트를 읽는다 (이름이 같아야 한다)
        app_py = (IOS / 'src' / 'trainreservation' / 'app.py').read_text(encoding='utf-8')
        self.assertEqual(re.search(r'^PORT = (\d+)$', app_py, re.MULTILINE).group(1), '0')
        port_file = re.search(r"^PORT_FILE = '([^']+)'$", app_py, re.MULTILINE).group(1)
        self.assertEqual(wf['env']['PORT_FILE'], port_file)
        self.assertNotIn('APP_PORT', wf['env'])
        for needle in ('get_app_container', 'Documents/$PORT_FILE', '/__hello?nonce=', '/__health'):
            self.assertIn(needle, script)
        # .ipa 에 백그라운드 오디오 설정과 소리 없는 파일이 실렸는지 CI 가 확인한다
        for needle in ('UIBackgroundModes', '"audio"', 'trainreservation/resources/silence.wav'):
            self.assertIn(needle, script)

    def test_silence_file_is_real_silence(self):
        import wave
        path = IOS / 'src' / 'trainreservation' / 'resources' / 'silence.wav'
        native = (IOS / 'src' / 'trainreservation' / 'ios_native.py').read_text(encoding='utf-8')
        self.assertIn("'resources' / 'silence.wav'", native)  # 앱이 찾는 경로와 같다
        with wave.open(str(path), 'rb') as w:
            self.assertGreaterEqual(w.getnframes() / w.getframerate(), 0.5)  # 너무 짧으면 반복이 잦다
            frames = w.readframes(w.getnframes())
        self.assertTrue(frames)
        self.assertEqual(frames.count(0), len(frames))  # 샘플이 전부 0
        self.assertLess(path.stat().st_size, 64 * 1024)


if __name__ == '__main__':
    unittest.main()


class PrereleaseVersionTest(unittest.TestCase):
    """3.0.0-beta.0 같은 미리보기 버전: Briefcase 는 PEP 440, Info.plist 는 숫자만."""

    def test_to_pep440(self):
        import importlib.util
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location('sync_sources_v', root / 'mobile/ios/sync_sources.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.to_pep440('3.0.0'), '3.0.0')
        self.assertEqual(mod.to_pep440('3.0.0-beta.0'), '3.0.0b0')
        self.assertEqual(mod.to_pep440('3.1.2-rc.4'), '3.1.2rc4')
        self.assertEqual(mod.to_pep440('3.0.0-alpha.1'), '3.0.0a1')
        with self.assertRaises(ValueError):
            mod.to_pep440('3.0-beta')

    def test_plist_versions_are_numeric(self):
        import importlib.util
        import pathlib
        import plistlib
        import tempfile
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location('prep_v', root / 'mobile/ios/tools/prepare_xcode_project.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            app = pathlib.Path(d) / 'App'
            app.mkdir()
            plist = app / 'App-Info.plist'
            plist.write_bytes(plistlib.dumps({'CFBundleShortVersionString': '3.0.0b0',
                                              'CFBundleVersion': '3.0.0b0'}))
            mod.set_display_name(pathlib.Path(d))
            info = plistlib.loads(plist.read_bytes())
        self.assertEqual(info['CFBundleShortVersionString'], '3.0.0')
        self.assertEqual(info['CFBundleVersion'], '3.0.0')
