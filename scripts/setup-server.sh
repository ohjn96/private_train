#!/usr/bin/env bash
# 리눅스 서버(Oracle Cloud Always Free 등, Ubuntu 24.04)에 상주 서비스로 설치한다.
#   ./scripts/setup-server.sh               서버 버전 (여러 명이 웹으로, 알림은 웹 푸시)
#   ./scripts/setup-server.sh --headless    헤드리스 (혼자, 텔레그램으로만 조종)
#   ./scripts/setup-server.sh --tailscale   Tailscale + HTTPS 주소(https://<이름>.ts.net)로 폰에서 접속
#
# 여러 번 돌려도 된다. 설정 파일(/etc/train.env)이 이미 있으면 건드리지 않는다.
# 업데이트는 git pull 후 이 스크립트를 다시 돌리면 끝.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
RUN_USER="$(id -un)"
ENV_FILE=/etc/train.env
UNIT_FILE=/etc/systemd/system/train.service

HEADLESS=0
TAILSCALE=0
for arg in "$@"; do
    case "$arg" in
        --headless)  HEADLESS=1 ;;
        --tailscale) TAILSCALE=1 ;;
        *) echo "알 수 없는 옵션: $arg" >&2; exit 1 ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "!! root 말고 일반 계정(ubuntu 등)으로 실행하세요. 필요한 곳에서만 sudo 를 씁니다." >&2
    exit 1
fi

echo "==> 1/5 시스템 패키지"
sudo apt-get update -q
sudo apt-get install -y -q python3.12 python3.12-venv git

echo "==> 2/5 가상환경 + 의존성"
[[ -x venv/bin/python ]] || python3.12 -m venv venv
venv/bin/python -m pip install --upgrade pip -q
venv/bin/python -m pip install -r requirements-server.txt -q

echo "==> 3/5 설정 파일 ($ENV_FILE)"
if sudo test -f "$ENV_FILE"; then
    echo "    이미 있어서 그대로 둡니다. 고치려면: sudo nano $ENV_FILE"
    GATE_PW=""
else
    GATE_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
    # Tailscale 을 쓰면 앱은 이 서버 안(127.0.0.1)에만 뜨고, 밖에선 tailscale serve 의
    # HTTPS 주소로만 들어온다. PWA(홈 화면 추가)도 HTTPS 여야 동작한다.
    if [[ $TAILSCALE -eq 1 ]]; then BIND=127.0.0.1; SECURE=1; else BIND=0.0.0.0; SECURE=0; fi
    sudo install -m 600 -o root -g root /dev/null "$ENV_FILE"
    sudo tee "$ENV_FILE" >/dev/null <<EOF
# 열차 예약 서비스 설정. 바꾼 뒤엔: sudo systemctl restart train

# 웹에 들어올 때 묻는 접근 비밀번호 (가족·친구에게 알려줄 것)
APP_PASSWORD=$GATE_PW

# 서버에선 반드시 false. true 면 웹 디버거가 열려 원격으로 코드를 실행할 수 있다.
FLASK_DEBUG=false
NO_BROWSER=1

# 웹 푸시 발신자 표시 (mailto: 또는 https:)
#VAPID_SUBJECT=mailto:you@example.com
PORT=5050
HOST=$BIND
# HTTPS 뒤에서만 쓸 때 1 (세션 쿠키를 HTTPS 로만 보냄)
COOKIE_SECURE=$SECURE

# 헤드리스 모드(--headless)로 설치했다면 아래를 채운다
#KORAIL_ID=
#KORAIL_PW=
#TELEGRAM_BOT_TOKEN=
EOF
fi

echo "==> 4/5 systemd 서비스 ($UNIT_FILE)"
EXTRA_ENV=""
EXEC="$APP_DIR/venv/bin/python -m server"
if [[ $HEADLESS -eq 1 ]]; then
    EXTRA_ENV="Environment=HEADLESS=1"
    EXEC="$APP_DIR/venv/bin/python main.py"
fi
sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=Train Reservation App
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
$EXTRA_ENV
Environment=PYTHONUNBUFFERED=1
ExecStart=$EXEC
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable train >/dev/null
sudo systemctl restart train

if [[ $TAILSCALE -eq 1 ]]; then
    echo "==> 5/5 Tailscale"
    command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
    if ! tailscale ip -4 >/dev/null 2>&1; then
        echo "    아래 링크를 브라우저로 열어 로그인하세요."
        sudo tailscale up
    fi
    echo "    HTTPS 주소 연결 (tailscale serve → 127.0.0.1:5050)"
    echo "    'Serve is not enabled' 가 나오면 안내된 링크에서 HTTPS 를 켜 주세요."
    sudo tailscale serve --bg 5050
else
    echo "==> 5/5 Tailscale 건너뜀 (--tailscale 로 설치)"
fi

sleep 2
echo ""
echo "========================================"
if systemctl is-active --quiet train; then
    echo " ✅ 서비스 실행 중"
else
    echo " ❌ 서비스가 안 떠 있습니다. 로그: journalctl -u train -n 50"
fi
if [[ $HEADLESS -eq 0 ]]; then
    TS_NAME="$(tailscale status --json 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
    [[ -n "$TS_NAME" ]] && echo " 접속 주소: https://$TS_NAME  (Tailscale 켠 기기에서)"
    [[ -n "$GATE_PW" ]] && echo " 접근 비밀번호: $GATE_PW  (나중에 확인: sudo cat $ENV_FILE)"
else
    echo " 헤드리스 모드: $ENV_FILE 에 KORAIL_ID/PW, TELEGRAM_BOT_TOKEN 을 채운 뒤"
    echo "   sudo systemctl restart train"
fi
echo " 로그 보기: journalctl -u train -f"
echo "========================================"
echo ""
echo "⚠️  Oracle 보안 목록(Security List)에서 5050 포트는 열지 마세요."
echo "    Tailscale 로만 들어오는 게 안전합니다."
