# -*- coding: utf-8 -*-
"""웹 푸시 알림 (서버 모드 전용).

폰 홈 화면에 추가한 앱(PWA)이 꺼져 있어도 예약 성공·결제·중단을 알린다.
iPhone 은 iOS 16.4 이상, 홈 화면 앱에서 알림을 허용해야 받는다.

- VAPID 키: 처음 한 번 만들어 ~/.train_reservation/vapid_private.pem (600) 에 둔다.
- 구독: 코레일 ID별로 ~/.train_reservation/push_subscriptions.json (600) 에 둔다.
  재시작해도 유지되고, 푸시 서버가 404/410 을 주면(앱 삭제·권한 해제) 지운다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from flask import Blueprint, current_app, jsonify, request
from py_vapid import Vapid, b64urlencode
from pywebpush import WebPushException, webpush

from app.utils.session_helper import current_user_id

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / '.train_reservation'
VAPID_KEY_PATH = DATA_DIR / 'vapid_private.pem'
SUBSCRIPTIONS_PATH = DATA_DIR / 'push_subscriptions.json'

#: 한 사람이 등록할 수 있는 기기 수 (폰·태블릿 등). 넘으면 오래된 것부터 버린다.
MAX_DEVICES_PER_USER = 5

#: 푸시 서버가 전달을 포기하기까지 기다리는 시간(초). 예약 성공은 늦게라도 알아야 한다.
TTL_SECONDS = 6 * 60 * 60


def _write_private(path: Path, data: bytes) -> None:
    """본인만 읽을 수 있게(600) 원자적으로 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def load_or_create_vapid(path: Path = VAPID_KEY_PATH) -> Vapid:
    if path.exists():
        return Vapid.from_file(str(path))
    vapid = Vapid()
    vapid.generate_keys()
    _write_private(path, vapid.private_pem())
    logger.info("VAPID 키를 새로 만들었습니다: %s", path)
    return vapid


class SubscriptionStore:
    """코레일 ID -> 구독(브라우저가 준 JSON) 목록. 파일에 저장한다."""

    def __init__(self, path: Path = SUBSCRIPTIONS_PATH):
        self._path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, list[dict]] = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        try:
            _write_private(self._path, json.dumps(self._data).encode('utf-8'))
        except OSError as e:
            logger.warning("푸시 구독 저장 실패: %s", e)

    def get(self, user_id: str) -> list[dict]:
        with self._lock:
            return [dict(sub) for sub in self._data.get(user_id, [])]

    def add(self, user_id: str, subscription: dict) -> None:
        endpoint = subscription['endpoint']
        with self._lock:
            # 같은 기기는 하나만. 다른 사람이 쓰던 기기라면 그 사람 목록에서도 뺀다.
            for owner in list(self._data):
                self._data[owner] = [s for s in self._data[owner] if s['endpoint'] != endpoint]
                if not self._data[owner]:
                    del self._data[owner]
            subs = self._data.setdefault(user_id, [])
            subs.append(subscription)
            del subs[:-MAX_DEVICES_PER_USER]
            self._save()

    def remove(self, user_id: str, endpoint: str) -> bool:
        with self._lock:
            before = self._data.get(user_id, [])
            after = [s for s in before if s['endpoint'] != endpoint]
            if len(after) == len(before):
                return False
            if after:
                self._data[user_id] = after
            else:
                self._data.pop(user_id, None)
            self._save()
            return True


class WebPusher:
    def __init__(self, vapid: Vapid, store: SubscriptionStore, subject: str):
        self._vapid = vapid
        self.store = store
        self._subject = subject

    @property
    def public_key(self) -> str:
        """브라우저 pushManager.subscribe 에 넘기는 applicationServerKey."""
        raw = self._vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return b64urlencode(raw)

    def send(self, user_id: str, title: str, body: str, url: str = '/') -> int:
        """user_id 의 모든 기기로 보낸다. 성공한 기기 수를 돌려준다."""
        payload = json.dumps({'title': title, 'body': body, 'url': url}, ensure_ascii=False)
        sent = 0
        for sub in self.store.get(user_id):
            try:
                webpush(
                    sub,
                    data=payload,
                    vapid_private_key=self._vapid,
                    # webpush 가 이 dict 에 aud(푸시 서버 주소)를 써 넣는다. 기기마다 푸시
                    # 서버가 다르므로(애플/구글) 매번 새로 만들어야 한다.
                    vapid_claims={'sub': self._subject},
                    ttl=TTL_SECONDS,
                    headers={'Urgency': 'high'},
                    timeout=10,
                )
                sent += 1
            except WebPushException as e:
                status = getattr(e.response, 'status_code', None)
                if status in (404, 410):
                    # 앱을 지웠거나 알림 권한을 끈 기기
                    self.store.remove(user_id, sub['endpoint'])
                else:
                    logger.warning("웹 푸시 실패 (%s): %s", status, e)
            except Exception as e:  # 네트워크 등
                logger.warning("웹 푸시 실패: %s", e)
        return sent

    def send_later(self, user_id: str, title: str, body: str) -> None:
        """매크로 스레드를 붙잡지 않도록 따로 보낸다."""
        threading.Thread(
            target=self.send, args=(user_id, title, body), daemon=True, name='webpush',
        ).start()


# ──────────────────────────────────────────────── 라우트

bp = Blueprint('push', __name__, url_prefix='/api/push')


def _pusher() -> WebPusher:
    return current_app.extensions['webpush']


@bp.before_request
def require_login():
    if not current_user_id():
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
    return None


@bp.route('/key')
def key():
    return jsonify({'publicKey': _pusher().public_key})


@bp.route('/status')
def status():
    endpoint = request.args.get('endpoint', '')
    subs = _pusher().store.get(current_user_id())
    return jsonify({
        'devices': len(subs),
        'this_device': any(s['endpoint'] == endpoint for s in subs),
    })


@bp.route('/subscribe', methods=['POST'])
def subscribe():
    sub = (request.get_json(silent=True) or {}).get('subscription') or {}
    keys = sub.get('keys') or {}
    endpoint = sub.get('endpoint', '')
    if not endpoint.startswith('https://') or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'success': False, 'message': '잘못된 구독 정보입니다.'}), 400
    _pusher().store.add(current_user_id(), {'endpoint': endpoint, 'keys': {
        'p256dh': keys['p256dh'], 'auth': keys['auth']}})
    return jsonify({'success': True})


@bp.route('/unsubscribe', methods=['POST'])
def unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get('endpoint', '')
    _pusher().store.remove(current_user_id(), endpoint)
    return jsonify({'success': True})


@bp.route('/test', methods=['POST'])
def test():
    sent = _pusher().send(current_user_id(), '🔔 테스트 알림', '예약 성공 알림이 이렇게 옵니다.')
    if sent:
        return jsonify({'success': True, 'message': f'{sent}개 기기로 보냈습니다.'})
    return jsonify({'success': False, 'message': '보낼 기기가 없거나 전송에 실패했습니다.'})


def init_push(app, pusher: WebPusher | None = None) -> WebPusher:
    """앱에 웹 푸시를 붙이고, 매크로 알림이 이리로 오게 한다."""
    from app.routes.reservation import add_macro_listener

    if pusher is None:
        subject = os.environ.get('VAPID_SUBJECT', 'https://github.com/ohjn96/private_train')
        pusher = WebPusher(load_or_create_vapid(), SubscriptionStore(), subject)
    app.extensions['webpush'] = pusher
    app.register_blueprint(bp)
    add_macro_listener(lambda owner, kind, title, body: pusher.send_later(owner, title, body))
    return pusher
