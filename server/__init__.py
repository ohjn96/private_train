# -*- coding: utf-8 -*-
"""여러 명이 같이 쓰는 서버 버전.

화면과 예약 로직은 데스크톱 앱(app/, core/)과 같은 것을 쓰고, 여기엔 서버에만 필요한
것만 둔다: 운영용 WSGI 서버(waitress)로 띄우기, 웹 푸시 알림.

    python -m server
"""
from webui import create_app


def create_server_app(pusher=None):
    """서버 모드 앱. 매크로는 한 번에 한 명만, 알림은 웹 푸시."""
    from server.push import init_push

    app = create_app(server_mode=True)
    init_push(app, pusher)
    return app
