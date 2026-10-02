#!/usr/bin/env bash
# 화면 CSS(webui/static/css/app.css)를 Tailwind 3.4.17 로 다시 만든다.
# 템플릿의 클래스나 tailwind.config.js / webui/static/css/input.css 를 바꾼 뒤 돌리고, app.css 를 같이 커밋한다.
#
#   scripts/build_css.sh            # node(npx)가 있으면 npx, 없으면 단독 실행 파일을 받아 쓴다
#   TAILWIND_BIN=/path/to/tailwindcss scripts/build_css.sh
set -euo pipefail
VERSION=3.4.17
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -n "${TAILWIND_BIN:-}" ]]; then
    TW=("$TAILWIND_BIN")
elif command -v npx >/dev/null 2>&1; then
    TW=(npx --yes "tailwindcss@$VERSION")
else
    # 단독 실행 파일 (node 필요 없음): 저장소 밖 캐시 폴더에 받는다
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64)  asset=tailwindcss-linux-x64 ;;
        Linux-aarch64) asset=tailwindcss-linux-arm64 ;;
        Darwin-arm64)  asset=tailwindcss-macos-arm64 ;;
        Darwin-x86_64) asset=tailwindcss-macos-x64 ;;
        MINGW*|MSYS*|CYGWIN*) asset=tailwindcss-windows-x64.exe ;;
        *) echo "이 플랫폼용 tailwindcss 실행 파일을 모릅니다. TAILWIND_BIN 을 지정하세요." >&2; exit 1 ;;
    esac
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/private_train/tailwindcss-$VERSION"
    mkdir -p "$cache"
    if [[ ! -x "$cache/$asset" ]]; then
        curl -fsSL -o "$cache/$asset" \
            "https://github.com/tailwindlabs/tailwindcss/releases/download/v$VERSION/$asset"
        chmod +x "$cache/$asset"
    fi
    TW=("$cache/$asset")
fi

"${TW[@]}" -c tailwind.config.js -i webui/static/css/input.css -o webui/static/css/app.css --minify
echo "만듦: webui/static/css/app.css ($(wc -c < webui/static/css/app.css) bytes)"
