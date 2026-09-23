# -*- coding: utf-8 -*-
"""안드로이드 앱 E2E 시나리오 (에뮬레이터, 디버그 빌드). android_e2e.sh 가 부른다.

코레일에는 붙지 않는다: 매크로는 디버그 빌드의 /__debug/fake_job(가짜 서비스)만 쓴다.

    python scripts/qa/android_e2e.py --apk <app-debug.apk> [--serial emulator-5560] [--out DIR]
                                     [--reboot] [--stall] [--no-install]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

PKG = 'com.ohjn96.trainreservation'
DEVICE_PORT = 17650
HOST_PORT = 17660
CDP_PORT = 9333
TOOLS = os.path.expanduser('~/.local/share/private_train-android')
ADB = os.environ.get('ADB', os.path.join(TOOLS, 'sdk/platform-tools/adb'))

results: list[tuple[str, bool, str]] = []
args = None


# ──────────────────────────────────────────────── adb / http 도우미

def adb(*cmd, check=False, timeout=120) -> str:
    p = subprocess.run([ADB, '-s', args.serial, *cmd], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f'adb {" ".join(cmd)}: {p.stderr.strip()}')
    return (p.stdout or '') + (p.stderr or '')


def sh(cmd: str, timeout=60) -> str:
    return adb('shell', cmd, timeout=timeout).strip()


def app_pid() -> str:
    return sh(f'pidof {PKG}').split()[0] if sh(f'pidof {PKG}') else ''


def token() -> str:
    xml = sh(f'run-as {PKG} cat shared_prefs/app.xml')
    m = re.search(r'name="server_token">([^<]+)<', xml)
    return m.group(1) if m else ''


def job_xml() -> str:
    return sh(f'run-as {PKG} cat shared_prefs/job.xml 2>/dev/null')


def http(path, cookie=None, follow=False, timeout=10):
    """(status, headers, body). 리다이렉트는 따라가지 않는다."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(f'http://127.0.0.1:{HOST_PORT}{path}')
    if cookie:
        req.add_header('Cookie', f'app_token={cookie}')
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode('utf-8', 'replace')
    except Exception as e:  # noqa: BLE001
        return 0, {}, f'{type(e).__name__}: {e}'


def health(tok):
    status, _, body = http('/__health', tok, timeout=5)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def forward():
    adb('forward', f'tcp:{HOST_PORT}', f'tcp:{DEVICE_PORT}')


def wait_for(cond, timeout, interval=1.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = cond()
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return None


def notifications() -> str:
    return sh('dumpsys notification --noredact', timeout=30)


def screencap(name):
    if not args.out:
        return ''
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f'android_{name}.png')
    with open(path, 'wb') as f:
        f.write(subprocess.run([ADB, '-s', args.serial, 'exec-out', 'screencap', '-p'],
                               capture_output=True, timeout=30).stdout)
    return path


def record(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def launch():
    sh(f'am start -n {PKG}/.MainActivity')


def stop_app():
    sh(f'am force-stop {PKG}')
    time.sleep(1)


def logcat_dump(name):
    if args.out:
        with open(os.path.join(args.out, f'logcat_{name}.txt'), 'w', encoding='utf-8') as f:
            f.write(adb('logcat', '-d', '-v', 'time', timeout=60))


def _cdp_call(ws_url: str, method: str, params: dict, timeout=10):
    """표준 라이브러리만으로 CDP 웹소켓에 요청 하나를 보내고 응답을 받는다."""
    import base64
    import socket
    import struct
    from urllib.parse import urlparse
    u = urlparse(ws_url)
    sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n'
                      f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('handshake')
            buf += chunk
        rest = buf.split(b'\r\n\r\n', 1)[1]
        payload = json.dumps({'id': 1, 'method': method, 'params': params}).encode()
        mask = os.urandom(4)
        n = len(payload)
        head = b'\x81' + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n)
                          if n < 65536 else bytes([0x80 | 127]) + struct.pack('>Q', n))
        sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

        def read(k):
            nonlocal rest
            while len(rest) < k:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError('closed')
                rest += chunk
            out, rest = rest[:k], rest[k:]
            return out
        message = b''
        while True:
            b1, b2 = read(2)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack('>H', read(2))[0]
            elif ln == 127:
                ln = struct.unpack('>Q', read(8))[0]
            data = read(ln)
            message += data
            if b1 & 0x80:
                obj = json.loads(message.decode('utf-8', 'replace'))
                message = b''
                if obj.get('id') == 1:
                    return obj
    finally:
        sock.close()


def webview_eval(expr: str):
    """디버그 앱 WebView 에 CDP 로 붙어 JS 식을 평가한다 (디버그 빌드는 원격 디버깅이 켜져 있다)."""
    pid = app_pid()
    adb('forward', f'tcp:{CDP_PORT}', f'localabstract:webview_devtools_remote_{pid}')
    with urllib.request.urlopen(f'http://127.0.0.1:{CDP_PORT}/json', timeout=5) as r:
        targets = json.loads(r.read().decode())
    pages = [t for t in targets if t.get('type') == 'page' and '127.0.0.1' in t.get('url', '')]
    if not pages:
        return None
    res = _cdp_call(pages[0]['webSocketDebuggerUrl'], 'Runtime.evaluate',
                    {'expression': expr, 'returnByValue': True})
    return res.get('result', {}).get('result', {}).get('value')


# ──────────────────────────────────────────────── 시나리오

def s_launch(tok_holder):
    stop_app()
    sh(f'pm clear {PKG}')
    sh(f'pm grant {PKG} android.permission.POST_NOTIFICATIONS')
    sh(f'dumpsys deviceidle whitelist +{PKG}')  # 배터리 최적화 안내 창이 화면을 가리지 않게
    adb('logcat', '-c')
    launch()
    forward()
    tok = wait_for(token, 30)
    tok_holder['t'] = tok
    ok = wait_for(lambda: health(tok), 120, 2)
    record('앱 실행 + 파이썬 서버 기동', bool(ok) and app_pid(), f'pid={app_pid()} health={ok}')
    # WebView 가 화면(로그인)까지 띄웠는지
    title = wait_for(lambda: webview_eval('document.title'), 30, 2)
    shot = screencap('01_launch')
    record('WebView 가 앱 화면을 띄움', title and '열차' in title, f'title={title!r} {shot}')


def s_token(tok):
    st, _, body = http('/')
    record('토큰 없는 요청 → 403', st == 403, f'status={st}')
    st, _, _ = http('/', 'wrong-token')
    record('틀린 토큰 쿠키 → 403', st == 403, f'status={st}')
    st, _, _ = http('/__health', tok)
    record('올바른 토큰 쿠키 → 200', st == 200, f'status={st}')
    st, hdr, _ = http('/__auth?t=nope')
    record('/__auth 틀린 토큰 → 403', st == 403, f'status={st}')
    st, hdr, _ = http(f'/__auth?t={tok}')
    cookie = hdr.get('Set-Cookie', '')
    ok = st in (301, 302, 303, 307) and cookie.startswith(f'app_token={tok}') and 'HttpOnly' in cookie
    record('/__auth?t=<토큰> → 쿠키 app_token 심고 이동', ok, f'status={st} location={hdr.get("Location")} set-cookie={cookie[:24]}...{cookie[-40:]}')


def s_fake_job(tok):
    st, _, body = http('/__debug/fake_job', tok)
    running = wait_for(lambda: (health(tok) or {}).get('macro_running'), 60)
    record('가짜 매크로 시작 (/__debug/fake_job)', st == 200 and running, f'status={st} body={body.strip()[:60]} running={running}')
    xml = wait_for(lambda: (lambda x: x if 'name="data"' in x else None)(job_xml()), 20)
    xml = xml or job_xml()
    plain = [w for w in ('"password"', '"user_id"', 'debug', '서울', 'GENERAL_FIRST') if w in xml]
    record('작업이 암호화 저장됨 (job.xml 에 평문 없음)', 'name="data"' in xml and not plain,
           f'len={len(xml)} plaintext_hits={plain}')
    time.sleep(3)
    h = health(tok) or {}
    record('가짜 매크로가 조회를 계속함 (attempt 증가)', (h.get('attempt') or 0) > 0, f'health={h}')


def s_kill9(tok):
    old = app_pid()
    sh(f'kill -9 {old}')
    new = wait_for(lambda: (lambda p: p if p and p != old else None)(app_pid()), 120, 1)
    forward()
    running = wait_for(lambda: (health(tok) or {}).get('macro_running'), 150, 2)
    note = wait_for(lambda: '다시 시작했어요' in notifications(), 30, 2)
    shot = screencap('02_after_kill9')
    record('kill -9 → 서비스 재시작 + 매크로 자동 재개', new and running and note,
           f'old_pid={old} new_pid={new} macro_running={running} 알림("다시 시작했어요")={bool(note)} {shot}')
    return new


def s_stop_macro(tok):
    # 서비스는 exported=false 라 루트 셸에서만 부를 수 있다 (알림 버튼과 같은 인텐트)
    out = sh(f'am start-foreground-service -n {PKG}/.ServerService -a {PKG}.STOP_MACRO')
    stopped = wait_for(lambda: (health(tok) or {}).get('macro_running') is False, 60, 1)
    cleared = wait_for(lambda: 'name="data"' not in job_xml(), 30, 1)
    alive = app_pid()
    record('STOP_MACRO 알림 동작 → 매크로 멈추고 작업 삭제', stopped and cleared and alive,
           f'am={out[:60]!r} macro_running=False:{bool(stopped)} job_cleared={bool(cleared)} pid={alive}')


def s_renderer_kill(tok):
    launch()  # 화면을 앞으로
    time.sleep(3)
    before = app_pid()
    ps = sh('ps -A -o PID,NAME')
    rpids = [line.split()[0] for line in ps.splitlines() if 'sandboxed_process' in line]
    if not rpids:
        record('렌더러 프로세스 종료 → 앱 생존', False, '렌더러 프로세스를 찾지 못함')
        return
    for p in rpids:
        sh(f'kill -9 {p}')
    time.sleep(6)
    after = app_pid()
    log = adb('logcat', '-d', '-s', 'MainActivity:W', timeout=30)
    gone = 'renderer gone' in log
    h = health(tok)
    title = wait_for(lambda: webview_eval('document.title'), 30, 2)
    shot = screencap('03_after_renderer_kill')
    record('렌더러 프로세스 종료 → 앱 생존 + 화면 다시 로드', before == after and gone and h and title,
           f'renderer={rpids} pid {before}->{after} logcat_renderer_gone={gone} health={bool(h)} title={title!r} {shot}')


def s_offline(tok):
    stop_app()
    sh('cmd connectivity airplane-mode enable')
    sh('svc wifi disable')
    sh('svc data disable')
    time.sleep(3)
    try:
        launch()
        forward()
        ok = wait_for(lambda: health(tok), 120, 2)
        info = wait_for(lambda: webview_eval(
            "(() => { const b = getComputedStyle(document.body); const btn = document.querySelector('button[type=submit]');"
            " return {title: document.title, tailwind: typeof tailwind !== 'undefined', bg: b.backgroundColor,"
            " btn: btn ? getComputedStyle(btn).backgroundColor : null,"
            " font: b.fontFamily, fontsLoaded: [...new Set([...document.fonts].filter(f => f.status === 'loaded').map(f => f.family))]} })()"), 40, 2)
        shot = screencap('04_offline')
        styled = bool(info) and info.get('tailwind') and info.get('bg') == 'rgb(246, 244, 240)' \
            and info.get('btn') == 'rgb(200, 16, 46)'
        record('오프라인 실행에도 화면 스타일 유지 (Tailwind 내장)', ok and styled, f'{info} {shot}')
    finally:
        sh('cmd connectivity airplane-mode disable')
        sh('svc wifi enable')
        sh('svc data enable')


def s_reboot(tok):
    http('/__debug/fake_job', tok)
    wait_for(lambda: (health(tok) or {}).get('macro_running'), 60)
    has_job = 'name="data"' in job_xml()
    adb('reboot')
    time.sleep(10)
    adb('wait-for-device', timeout=300)
    wait_for(lambda: sh('getprop sys.boot_completed') == '1', 300, 3)
    adb('root')
    time.sleep(3)
    adb('wait-for-device', timeout=120)
    pid = wait_for(app_pid, 240, 3)
    forward()
    running = wait_for(lambda: (health(tok) or {}).get('macro_running'), 240, 3)
    note = wait_for(lambda: '다시 시작했어요' in notifications(), 30, 2)
    record('재부팅 → 매크로 자동 재개 (--reboot)', has_job and pid and running and note,
           f'job_saved={has_job} pid={pid} macro_running={running} 알림={bool(note)}')
    sh(f'am start-foreground-service -n {PKG}/.ServerService -a {PKG}.STOP_MACRO')
    wait_for(lambda: (health(tok) or {}).get('macro_running') is False, 60)


def s_stall(tok):
    old = app_pid()
    http('/__debug/fake_job?stall=1', tok)
    wait_for(lambda: (health(tok) or {}).get('macro_running'), 60)
    started = time.time()
    # 헬스체크: 첫 90초 + 30초 간격, 180초 동안 조회가 없으면 프로세스를 다시 띄운다 → 약 3.5~5분
    new = wait_for(lambda: (lambda p: p if p and p != old else None)(app_pid()), 420, 5)
    took = round(time.time() - started)
    forward()
    running = wait_for(lambda: (health(tok) or {}).get('macro_running'), 150, 2)
    h = health(tok) or {}
    log = adb('logcat', '-d', '-s', 'ServerService:W', timeout=30)
    reason = next((line for line in log.splitlines() if 'restarting process' in line), '')
    record('조회 멈춤 → 워치독이 프로세스 재시작 후 재개 (--stall)', new and running and h.get('stalled_seconds', 999) < 60,
           f'pid {old}->{new} ({took}s) health={h} log={reason.strip()[-60:]!r}')
    sh(f'am start-foreground-service -n {PKG}/.ServerService -a {PKG}.STOP_MACRO')
    wait_for(lambda: (health(tok) or {}).get('macro_running') is False, 60)


def main():
    global args
    ap = argparse.ArgumentParser()
    ap.add_argument('--apk')
    ap.add_argument('--serial', default='emulator-5560')
    ap.add_argument('--out', default='')
    ap.add_argument('--reboot', action='store_true')
    ap.add_argument('--stall', action='store_true')
    ap.add_argument('--no-install', action='store_true')
    args = ap.parse_args()

    adb('root')
    time.sleep(2)
    adb('wait-for-device')
    if not args.no_install:
        out = adb('install', '-r', args.apk, timeout=300)
        record('APK 설치', 'Success' in out, out.strip().splitlines()[-1] if out.strip() else '')

    tok = {}
    steps = [
        ('launch', lambda: s_launch(tok)),
        ('token', lambda: s_token(tok['t'])),
        ('fake_job', lambda: s_fake_job(tok['t'])),
        ('kill9', lambda: s_kill9(tok['t'])),
        ('stop_macro', lambda: s_stop_macro(tok['t'])),
        ('renderer', lambda: s_renderer_kill(tok['t'])),
        ('offline', lambda: s_offline(tok['t'])),
    ]
    if args.stall:
        steps.append(('stall', lambda: s_stall(tok['t'])))
    if args.reboot:
        steps.append(('reboot', lambda: s_reboot(tok['t'])))
    for name, fn in steps:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            record(f'{name} (예외)', False, f'{type(e).__name__}: {e}')
            if name == 'launch':
                break
    logcat_dump('final')

    print('\n──── 안드로이드 E2E 요약')
    for name, ok, _ in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    failed = [r for r in results if not r[1]]
    print(f'  {len(results) - len(failed)}/{len(results)} 통과')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
