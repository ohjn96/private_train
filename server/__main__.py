# -*- coding: utf-8 -*-
"""python -m server — 서버 버전을 waitress 로 띄운다.

비밀번호·카드 정보를 프로세스 메모리에 두므로 반드시 프로세스 하나로 돌린다
(요청은 스레드 여러 개로 동시에 처리한다).
"""
import logging
import os

from waitress import serve

from webui.version import get_version
from server import create_server_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5050))
    # 열어 둔 화면마다 실시간 로그(SSE) 연결이 스레드 하나씩 쓴다
    threads = int(os.environ.get('SERVER_THREADS', 16))

    if not os.environ.get('APP_PASSWORD'):
        logging.warning('APP_PASSWORD 가 비어 있습니다. 외부에 열 거라면 꼭 설정하세요.')

    app = create_server_app()
    print(f"🚄 Train Reservation Server v{get_version()}  http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=threads, ident='train')


if __name__ == '__main__':
    main()
