#!/usr/bin/env bash
# 버전을 올리고 태그를 밀어 GitHub Actions 의 Windows exe 빌드를 띄운다.
#   ./scripts/release.sh 2.0.1
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "사용법: ./scripts/release.sh <버전>   예) ./scripts/release.sh 2.0.1" >&2
    echo "현재 버전: $(cat VERSION)" >&2
    exit 1
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "!! 버전 형식은 X.Y.Z 여야 합니다 (입력: $VERSION)" >&2
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "!! 커밋되지 않은 변경이 있습니다. 먼저 정리하세요." >&2
    git status --short >&2
    exit 1
fi

echo "==> VERSION: $(cat VERSION) -> $VERSION"
echo "$VERSION" > VERSION
# package.json 의 version 도 함께 맞춘다
sed -i "s/^\(  \"version\": \"\)[^\"]*\"/\1$VERSION\"/" package.json

git add VERSION package.json
git commit -m "chore: bump version to $VERSION"
git tag "v$VERSION"
git push origin HEAD
git push origin "v$VERSION"

echo ""
echo "✅ v$VERSION 태그를 푸시했습니다."
echo "   GitHub Actions 가 Windows exe 를 빌드해 Release 에 첨부합니다:"
echo "   https://github.com/ohjn96/private_train/actions"
