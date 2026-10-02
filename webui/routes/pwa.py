# -*- coding: utf-8 -*-
"""홈 화면에 추가(PWA)용 manifest 와 서비스 워커.

서비스 워커는 사이트 전체(/)를 범위로 가져야 하므로 /static 아래가 아니라 루트에서 준다.
둘 다 브라우저가 쿠키 없이 가져가기도 해서 접근 비밀번호 관문에서 빠진다.
"""
from flask import Blueprint, Response, jsonify, url_for

bp = Blueprint('pwa', __name__)

#: 접근 비밀번호 없이도 열려야 하는 엔드포인트
PUBLIC_ENDPOINTS = {'pwa.manifest', 'pwa.service_worker'}

# 로그인·예약 화면은 늘 서버의 최신 상태여야 해서 아무것도 캐시하지 않는다.
# 서비스 워커는 설치(홈 화면 앱) 조건을 채우고, 웹 푸시(서버 모드)를 받는다.
SERVICE_WORKER_JS = """\
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// 서버가 보낸 알림을 띄운다. iOS 는 알림을 안 띄우면 구독을 끊으므로 늘 띄운다.
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  event.waitUntil(self.registration.showNotification(data.title || '열차 예약', {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/' },
  }));
});

// 알림을 누르면 열려 있는 앱으로 가고, 없으면 새로 연다.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const win of wins) {
      if ('focus' in win) { await win.focus(); return; }
    }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
"""


@bp.route('/manifest.webmanifest')
def manifest():
    icon = lambda name: url_for('static', filename=f'icons/{name}')  # noqa: E731
    resp = jsonify({
        'name': '열차 예약',
        'short_name': '열차예약',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#F9FAFB',
        'theme_color': '#F6F4F0',
        'lang': 'ko',
        'icons': [
            {'src': icon('icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': icon('icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': icon('icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    })
    resp.mimetype = 'application/manifest+json'
    return resp


@bp.route('/sw.js')
def service_worker():
    return Response(
        SERVICE_WORKER_JS,
        mimetype='text/javascript',
        # 서비스 워커 갱신이 늦지 않도록 캐시하지 않는다
        headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'},
    )
