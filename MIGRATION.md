# 저장소 분리 — 이관 절차

앱 소스는 비공개로, 배포와 라이선스 인프라는 공개로 나눕니다.
**아래 순서대로** 하세요. 중간에 멈추면 기존 사용자가 잠길 수 있습니다.

```
ohjn96/private_train        (비공개)  소스 · 빌드
        │  태그 푸시 → exe 빌드 → 아래 Release 로
        ▼
ohjn96/train-reservation    (공개)    릴리스 · 요청 이슈 · 발급된 키 ·
                                      정책 · 철회 목록 · 현황판
```

준비된 공개 저장소 내용은 `~/workspace/train-reservation/` 에 이미 만들어 뒀습니다.

---

## 1. 공개 저장소 만들기

GitHub 에서 **ohjn96/train-reservation** 을 **Public** 으로 새로 만듭니다.
README·gitignore·라이선스는 **넣지 마세요** (이미 준비돼 있습니다).

```bash
cd ~/workspace/train-reservation
git add -A
git commit -m "feat: 배포·라이선스 인프라 저장소 분리"
git remote add origin https://github.com/ohjn96/train-reservation.git
git push -u origin release
```

기본 브랜치를 `release` 로 맞춥니다: Settings → Branches → Default branch.

## 2. 공개 저장소 설정

**시크릿** — Settings → Secrets and variables → Actions

| 이름 | 값 |
|---|---|
| `LICENSE_SIGNING_KEY` | `~/.config/private_train/license_signing_key.pem` 내용 전체 |

```
! clip.exe < ~/.config/private_train/license_signing_key.pem && echo "복사됨"
```

**라벨** — Issues → Labels 에서 `license-request` 를 만들어 둡니다.
(`license-expiry` 는 워크플로가 알아서 만듭니다.)

**Pages** — Settings → Pages → Source: **GitHub Actions**

## 3. 배포 토큰 만들기

비공개 저장소가 공개 저장소에 릴리스를 올릴 수 있어야 합니다.

1. https://github.com/settings/personal-access-tokens/new
2. Repository access → **Only select repositories** → `train-reservation`
3. Permissions → Repository permissions → **Contents: Read and write**
4. 만료일은 넉넉히 (만료되면 릴리스가 조용히 실패합니다)
5. 생성된 토큰을 복사

비공개 저장소(`private_train`) → Settings → Secrets → Actions 에 등록:

| 이름 | 값 |
|---|---|
| `DIST_RELEASE_TOKEN` | 방금 만든 PAT |

## 4. 공개 저장소가 살아있는지 확인

**이 단계를 건너뛰지 마세요.** 앱은 여기서 정책을 받아옵니다.

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  https://raw.githubusercontent.com/ohjn96/train-reservation/release/license-policy.json
```

`200` 이 나와야 합니다. `404` 면 아직 푸시가 안 됐거나 브랜치 이름이 다릅니다.

## 5. 비공개 저장소 푸시 후 private 전환

```bash
cd ~/workspace/private_train
git push origin release
```

그다음 Settings → General → 맨 아래 Danger Zone → **Change repository visibility**
→ Private.

> ⚠️ **4번이 `200` 이 아닌 상태에서 private 으로 바꾸면**, 앱이 정책을 못 받고
> exe 에 박힌 `licensed` 로 잠깁니다. 순서를 지키세요.

## 6. 확인

```bash
# 정책 · 현황판
curl -s -o /dev/null -w "policy  %{http_code}\n" \
  https://raw.githubusercontent.com/ohjn96/train-reservation/release/license-policy.json
curl -s -o /dev/null -w "pages   %{http_code}\n" https://ohjn96.github.io/train-reservation/

# 릴리스 (태그를 하나 밀어서)
cd ~/workspace/private_train && ./scripts/release.sh 2.4.0-rc.2
```

`train-reservation` 의 Releases 에 exe 가 올라오면 성공입니다.

---

## 옮겨간 것 / 남은 것

| 공개 (`train-reservation`) | 비공개 (`private_train`) |
|---|---|
| `licensekit.py` (토큰 형식·서명) | `app/` 전체 (앱 소스) |
| `public_key.py` | `app/licensing/` (검증·게이트) |
| `scripts/license_admin.py` 외 CI 스크립트 | `scripts/bake_policy.py` |
| `.github/workflows/license-*.yml`, `pages.yml` | `.github/workflows/release.yml` |
| `.github/ISSUE_TEMPLATE/` | `build/`, `tests/` |
| `docs/` (운영 가이드 + 현황판) | `korail2/`, `main.py` |
| `license-policy.json`, `revoked.json`, `licenses/` | |

**공개키는 양쪽에 같은 값**이 들어갑니다. 한쪽만 바꾸면 검증이 깨집니다.
토큰 형식도 `licensekit.py` 와 `app/licensing/token.py` 양쪽에 있습니다 —
형식을 고칠 일이 생기면 둘 다 고치고 `CURRENT_VERSION` 을 올리세요.

## 되돌리려면

공개 저장소를 지우고, `app/licensing/config.py` 의 `DIST_REPO` 를
`ohjn96/private_train` 으로 되돌린 뒤 비공개 저장소를 다시 public 으로
바꾸면 됩니다. 발급된 라이선스는 그대로 유효합니다 (키가 같으므로).
