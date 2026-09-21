# 라이선스 발급 운영 가이드

발급자(저장소 주인)를 위한 문서입니다. 사용자용 안내는 앱의 `/license` 화면에 있습니다.

---

## 이게 무엇을 막고, 무엇을 못 막나

(아래는 검사를 `licensed` 로 켠 상태 기준입니다.)

| | |
|---|---|
| ✅ 막음 | 받은 exe 를 남에게 그대로 넘기는 것 — 머신 ID 가 달라 동작 안 함 |
| ✅ 막음 | 만료일·머신 ID 위조 — Ed25519 서명이 깨짐 |
| ✅ 막음 | 남의 라이선스 키 재사용 |
| ✅ 막음 | 시계를 되돌려 만료 회피 (마지막 관측 시각 기록) |
| ⚠️ 늦출 뿐 | 유출된 키 — 철회 목록에 올리면 최대 24시간 내 차단 |
| ❌ 못 막음 | exe 를 리버싱해 검사 자체를 건너뛰기 |
| ❌ 못 막음 | 공개된 소스에서 검사 코드를 지우고 직접 빌드 |

마지막 두 줄은 **어떤 오프라인 DRM 으로도 못 막습니다.** 이 저장소는 공개이므로
특히 마지막 항목이 열려 있다는 점을 전제로 쓰세요. 목표는 "무단 재배포된 exe 를
쓸모없게 만들고, 누가 쓰는지 통제한다" 입니다.

---

## 지금은 꺼져 있습니다

**기본 상태에서 라이선스 검사는 동작하지 않습니다.** 누구나 앱을 그냥 쓸 수 있습니다.
검사 기능은 전부 들어가 있고, 원격 스위치 하나로 켭니다.

```bash
python scripts/license_admin.py policy            # 현재 상태 보기
python scripts/license_admin.py policy --mode licensed   # 켜기
git add license-policy.json && git commit -m "chore: 라이선스 검사 켜기" && git push
```

| 모드 | 동작 |
|---|---|
| `open` (기본) | 검사 안 함. 누구나 사용 가능 |
| `licensed` | 라이선스가 있어야 사용 가능 |
| `blocked` | 전면 차단. 라이선스가 있어도 막힘 (긴급 정지용) |

앱은 **6시간마다, 그리고 로그인할 때마다** 이 파일을 확인합니다. 로그인은 어차피
인터넷이 필요한 시점이라, 스위치를 켜면 늦어도 다음 로그인 때 반영됩니다.
GitHub CDN 캐시 때문에 푸시 후 최대 5분쯤 더 걸립니다.

### 우회 방지

- 정책 파일도 **서명**돼 있어서, 남이 가짜 정책으로 남을 차단시킬 수 없습니다.
- 한 번 `licensed` 를 본 앱은 그 상태를 **기억**합니다. 인터넷을 끊어도 안 풀립니다.
- 정책에는 `seq` 번호가 붙고 앱은 **낮은 seq 를 거부**합니다. 옛날 `open` 정책을
  다시 들이밀어 잠금을 푸는 것도 안 됩니다.
- 되돌리려면 `--mode open` 을 다시 발행하면 됩니다 (seq 가 자동으로 올라갑니다).

### 오프라인 기본값 — 네트워크를 막아 피하지 못하게

원격 정책을 **한 번도** 받아보지 못한 설치는 기준이 없습니다. 그래서 빌드할 때의
기본값을 exe 에 박아둡니다 (`app/licensing/built_in_policy.py`).

```bash
python scripts/license_admin.py bake                    # 현재 상태 보기
python scripts/license_admin.py bake --mode licensed    # 정책을 못 받으면 잠기게
python scripts/license_admin.py bake --mode open        # 못 받으면 그냥 열리게
```

이 값과 공개 정책은 **따로 놉니다.** `policy --mode open` 으로 풀어도 박힌 값은
안 내려갑니다 (엄격해지는 방향으로만 자동으로 따라갑니다). 일부러 내리려면
`bake --mode open` 을 직접 쳐야 합니다.

권장 조합은 이렇습니다.

| | 값 | 효과 |
|---|---|---|
| 박힌 값 | `licensed` | 정책을 못 받는 설치는 잠김 |
| 공개 정책 | `open` | 인터넷 되는 정상 사용자는 제약 없음 |

이 앱은 코레일 API 를 쓰므로 어차피 인터넷이 필요합니다. 그래서 fail-closed 로 가도
정상 사용자가 잃는 건 없고, GitHub 만 골라 막아 검사를 피하는 길이 닫힙니다.
정책을 못 받아 잠긴 경우에는 "라이선스가 없습니다" 가 아니라
**"정책을 확인하지 못했습니다"** 라고 안내해서 원인을 구분할 수 있게 했습니다.

빌드(`build/build.py`)는 매번 박힌 모드를 유지한 채 seq 만 현재 정책에 맞춥니다.

### 남은 한계

- 공개 저장소이므로, 소스에서 검사 코드를 지우고 직접 빌드하는 것은 여전히 가능합니다.
- exe 를 리버싱해 검사를 건너뛰는 것도 막지 못합니다. 어떤 오프라인 DRM 도 마찬가지입니다.
- 공개키를 커밋하지 않은 빌드는 정책도 라이선스도 검증할 수 없어 **항상 open** 입니다.

---

## 두 가지 운영 방식

| | 수동 | 자동 (권장) |
|---|---|---|
| 요청 경로 | 이메일 | 앱의 [라이선스 요청하기] → GitHub Issue |
| 승인 | 터미널에서 `issue` 실행 | 이슈에 `/approve 30` 댓글 (**알림 메일에 답장해도 됨**) |
| 전달 | 키를 복사해 회신 | Actions 가 `licenses/` 에 커밋 → **앱이 자동 수령** |
| 개인키 위치 | 내 PC 에만 | 내 PC + GitHub Secrets |
| 필요한 것 | 없음 | 아래 시크릿 등록 1회 |

둘 다 동시에 쓸 수 있습니다. 자동 쪽을 쓰려면 아래 "자동 승인 설정"까지 마치세요.

---

## 최초 1회: 서명 키 만들기

```bash
python scripts/license_admin.py keygen
```

- **개인키** → `~/.config/private_train/license_signing_key.pem`
  저장소 바깥에 생깁니다. 암호화해서 백업하고, 절대 공유하지 마세요.
- **공개키** → `app/licensing/public_key.py` 에 자동으로 기록됩니다. 커밋하세요.

```bash
git add app/licensing/public_key.py
git commit -m "chore: 라이선스 공개키 등록"
```

> ⚠️ 공개키를 커밋하지 않고 빌드하면 **아무도 앱을 쓸 수 없습니다** (앱이 잠긴 채로 뜹니다).
> 실행 시 콘솔에 경고가 표시됩니다.
>
> ⚠️ 개인키를 잃어버리면 새 라이선스를 발급할 수 없습니다. 새로 만들면 기존에 발급한
> 라이선스가 **전부 무효**가 되고, 모든 사용자에게 다시 발급해줘야 합니다.

---

## 자동 승인 설정 (1회)

개인키를 GitHub 에 등록해야 Actions 가 대신 서명할 수 있습니다.

```bash
gh secret set LICENSE_SIGNING_KEY < ~/.config/private_train/license_signing_key.pem
```

웹에서 하려면 저장소 → Settings → Secrets and variables → Actions → New repository secret,
이름 `LICENSE_SIGNING_KEY`, 값은 PEM 파일 내용 전체.

> ⚠️ 개인키가 GitHub 에 올라갑니다. **계정 2FA 를 반드시 켜세요.** 저장소 쓰기 권한이
> 있는 사람은 꺼낼 수 있으므로, 협업자를 추가할 때 주의하세요. 유출되면 `keygen --force`
> 로 갈아엎으면 됩니다(기존 라이선스는 전부 무효가 되고 전원 재발급이 필요합니다).

### 실제 흐름

```
1. 사용자   앱에서 [라이선스 요청하기] → 머신 ID 가 채워진 Issue 가 열림
2. 나       GitHub 알림 메일 도착 → 그 메일에 "/approve 30" 이라고 답장
            (또는 이슈에 댓글, 또는 GitHub 모바일 앱에서)
3. Actions  댓글 작성자가 저장소 주인인지 확인 → 서명 → licenses/<머신ID>.key 커밋
            → 이슈에 결과 댓글 + 자동 닫기
4. 앱       라이선스 화면이 5초마다 자기 키를 확인 → 자동 활성화 → 앱으로 진입
```

사용자는 복붙하지 않고, 나는 답장 한 줄만 씁니다.

### 현황 보기 — 터미널 없이

- **대시보드**: `https://ohjn96.github.io/private_train/` 에서 누가 언제까지 쓰는지
  표로 볼 수 있습니다 (휴대폰에서도). `.github/workflows/pages.yml` 이 `docs/` 가
  바뀔 때마다 알아서 배포하고, Pages 가 꺼져 있으면 켜기까지 합니다.
  **공개 페이지이니** 머신 ID 와 이름이 남에게 보인다는 점만 감안하세요.
  원치 않으면 그 워크플로를 지우고 Settings → Pages 에서 끄면 됩니다.

  | 페이지 | 내용 |
  |---|---|
  | `index.html` | 현황 표, 요약, 검사 ON/OFF 배너 |
  | `detail.html?id=<머신ID>` | 개별 라이선스 상세와 쓸 수 있는 명령 |
  | `guide.html` | 사용자·발급자용 안내 |

  빌드 단계가 없는 정적 ES 모듈입니다. 계산 로직은 `assets/js/model.js` 한 곳에 모여
  있고 `tests/test_dashboard_js.py` 가 실제로 실행해서 검증합니다
  (`pip install dukpy` 가 있어야 돌고, 없으면 건너뜁니다).
- **원본 데이터**: `licenses/index.json` 을 GitHub 웹에서 그냥 열어봐도 됩니다.
- **이력**: `licenses/` 의 커밋 히스토리가 발급·철회 기록입니다.

### 자동 갱신 — 한 번 켜두면 손 안 대도 됨

만료 3일 전에 알아서 연장합니다. 머신별로 켭니다 (기본은 꺼짐).

```
/autorenew        30일씩 자동 연장
/autorenew 90     90일씩
/autorenew off    끄기
```

CLI 로도 됩니다.

```bash
python scripts/license_admin.py autorenew --machine-id A1B2-... --days 30
python scripts/license_admin.py autorenew --machine-id A1B2-... --until 2027-01-01  # 이 날까지만
python scripts/license_admin.py autorenew --all      # 전부 자동 갱신
python scripts/license_admin.py autorenew            # 현재 설정 보기
```

- 설정은 `licenses/autorenew.json` 에 들어가고, 커밋해야 적용됩니다
  (댓글로 켰다면 Actions 가 알아서 커밋합니다).
- `license-renew.yml` 이 **매일 08:30 KST** 에 돌면서 3일 미만 남은 대상을 재발급합니다.
- **`/revoke` 하면 자동 갱신도 같이 꺼집니다.** 안 그러면 다음날 되살아나기 때문입니다.
- 자동 갱신 대상은 만료 알림을 보내지 않습니다 (알릴 이유가 없으므로).

자동 갱신을 켜면 그 사람은 사실상 무기한 사용자가 됩니다. 끊으려면 `/revoke` 나
`/autorenew off` 를 쓰거나, `--until` 로 기한을 미리 박아두세요.

### 만료 알림

`.github/workflows/license-expiry.yml` 이 **매일 09:00 (KST)** 에 돌면서 7일 안에
만료될 라이선스마다 이슈를 엽니다. GitHub 이 메일로 알려주고, **그 메일에
`/approve 30` 이라고 답장하면 바로 연장**됩니다.

같은 머신에 대해 이미 열린 알림 이슈가 있으면 다시 만들지 않습니다. 처리 후에는
이슈가 닫히므로, 다음 만료 때 새로 옵니다. 지금 당장 확인하려면 Actions 탭에서
수동 실행(Run workflow)하면 됩니다.

### 저장소 권한자는 자동 승인

`OWNER` / `MEMBER` / `COLLABORATOR` 가 요청 이슈를 열면 **댓글 없이 바로 90일이 발급**됩니다.
협업자를 추가해두면 그 사람은 알아서 쓰게 됩니다.

- 판정에 쓰는 `author_association` 은 GitHub 이 붙이는 값이라 요청자가 조작할 수 없습니다.
- `CONTRIBUTOR` 는 제외합니다 — PR 이 머지된 적 있을 뿐 쓰기 권한이 아닙니다.
- 이슈 **본문**에 `/approve` 를 적어두는 것은 무시됩니다. 명령은 댓글에서만 읽습니다.
- 기간은 `scripts/ci_approve.py` 의 `TRUSTED_DAYS` 로 바꿉니다.
- 협업자를 내보내도 이미 나간 라이선스는 살아 있습니다. 끊으려면 `/revoke` 를 쓰세요.

### 쓸 수 있는 명령

이슈 댓글(또는 알림 메일 답장)의 **첫 줄**에 적습니다.

| 명령 | 뜻 |
|---|---|
| `/approve` | 30일 발급 |
| `/approve 90` | 90일 발급 (1~3650 범위로 자름) |
| `/autorenew 30` | 만료 3일 전마다 30일씩 자동 연장 |
| `/autorenew off` | 자동 갱신 끄기 |
| `/revoke` | 그 PC 차단 (`revoked.json` 갱신 + `licenses/` 에서 삭제) |
| `/deny` | 거절하고 닫기 |

- **저장소 주인의 댓글만** 동작합니다. 남이 `/approve` 를 달아도 무시됩니다.
- 머신 ID 는 이슈 본문에서 `XXXX-XXXX-XXXX-XXXX` 형식으로만 인식합니다.
- 연장도 같습니다. 사용자가 다시 요청하고 `/approve` 하면 키가 교체되고, 앱이 알아서 받아갑니다.

### `licenses/` 를 공개해도 되는 이유

발급된 키는 머신 ID 에 묶여 있어서, 남이 파일을 받아가도 자기 PC 에서는 동작하지 않습니다.
철회 목록을 공개하는 것과 같은 이유입니다. 공개 이슈에 머신 ID(해시값)와 GitHub 아이디가
남는 것은 감수해야 합니다.

---

## 라이선스 발급 (수동)

사용자가 앱을 실행하면 라이선스 화면에 머신 ID 가 뜹니다. 그 값을 받아서:

```bash
python scripts/license_admin.py issue \
    --machine-id A1B2-C3D4-E5F6-7890 \
    --days 30 \
    --name "홍길동" \
    --email hong@example.com
```

출력된 `TRAIN1.....` 한 줄을 회신하면 됩니다. 사용자는 앱 화면에 붙여넣기만 하면 끝.

| 옵션 | 설명 |
|---|---|
| `--machine-id` | 필수. 사용자 화면의 16자리 값 |
| `--days` | 유효 기간. 기본 30 |
| `--name` | 발급 대상 메모 (토큰에 들어가고 앱 화면에 보임) |
| `--email` | 연락처. **대장에만 기록**되고 토큰에는 안 들어감 |
| `--note` | 토큰에 함께 넣을 짧은 메모 |

발급 내역은 `~/.config/private_train/issued.jsonl` 에 쌓입니다 (저장소 바깥, 개인정보 포함).

```bash
python scripts/license_admin.py list     # 발급 내역과 상태
```

앱이 자동으로 받아가게 하려면 `--publish` 를 붙입니다 (자동 승인은 이걸 알아서 씁니다).

```bash
python scripts/license_admin.py issue --machine-id A1B2-... --days 30 --publish
git add licenses && git commit -m "chore(license): approve" && git push
```

| 명령 | 하는 일 |
|---|---|
| `index` | `licenses/` 를 훑어 `licenses/index.json` 을 다시 씀 |
| `unpublish --machine-id X` | `licenses/` 에서 키 삭제 (**이미 등록된 PC 는 계속 동작**, 차단은 `revoke`) |
| `inspect <토큰>` | 토큰 내용 확인 |

---

## 철회 (키가 샜을 때)

```bash
python scripts/license_admin.py revoke --license-id 3f9a21c4      # 특정 키만
python scripts/license_admin.py revoke --machine-id A1B2-C3D4-E5F6-7890   # 그 PC 통째로
python scripts/license_admin.py revoke --license-id 3f9a21c4 --undo       # 해제
```

저장소 루트에 서명된 `revoked.json` 이 생기거나 갱신됩니다. **커밋하고 푸시해야 적용됩니다.**

```bash
git add revoked.json && git commit -m "chore: 라이선스 철회 목록 갱신" && git push
```

동작 방식:

- 앱이 `raw.githubusercontent.com` 에서 이 파일을 받아옵니다 (하루 1회 캐시).
- 목록 자체도 서명돼 있어서, 누가 가짜 목록을 끼워넣어 멀쩡한 사용자를 막을 수는 없습니다.
- **네트워크가 안 되면 막지 않습니다**(fail-open). 오프라인 사용을 보장하기 위한
  선택이며, 그 대가로 인터넷을 끊으면 철회를 미룰 수 있습니다.
- 반영까지 최대 24시간 걸립니다.
- 사용자가 `LICENSE_REVOCATION_URL` 환경변수로 조회 주소를 바꿔 철회를 피할 수는 있습니다.
  이미 자기 PC 를 통제하는 사람이라 어차피 막을 수 없는 범위이고, 그 변수는 테스트용입니다.
  철회가 정말 중요하면 만료 기간을 짧게(7~30일) 끊어 발급하는 쪽이 확실합니다.

---

## 배포 전 점검

```bash
# 0. 오프라인 기본값이 의도대로인지
python scripts/license_admin.py bake

# 1. 공개키가 들어있는지 (빈 문자열이면 안 됨)
grep 'PUBLIC_KEY_PEM = ' app/licensing/public_key.py

# 2. 개인키가 저장소에 없는지 (licenses/*.key 는 나와도 정상 — 발급된 라이선스)
git ls-files | grep -Ei 'pem|issued\.jsonl'   # 아무것도 안 나와야 정상

# 2-1. 자동 승인을 쓴다면 시크릿이 등록돼 있는지
gh secret list | grep LICENSE_SIGNING_KEY

# 3. 테스트
python -m unittest discover -s tests
```

---

## 사용자가 겪을 수 있는 상황

| 증상 | 원인 | 조치 |
|---|---|---|
| "다른 PC 앞으로 발급된 것입니다" | PC 를 바꿨거나 OS 재설치 | 새 머신 ID 로 재발급 |
| "라이선스가 만료되었습니다" | 기간 종료 | `issue` 로 새로 발급 |
| "시스템 시계가 과거로..." | 실제로 시계가 틀림 | 시간 동기화 후 재실행 |
| "철회되었습니다" | 발급자가 차단함 | 의도한 동작 |
| "공개키가 들어있지 않습니다" | 비공식 빌드 | 정식 exe 배포 |

머신 ID 는 OS 설치 고유값(Windows MachineGuid / Linux machine-id / macOS IOPlatformUUID)에서
뽑습니다. OS 를 재설치하면 바뀌므로 재발급이 필요합니다.

---

## 파일 배치

```
저장소 안 (공개)                     저장소 밖 (비공개)
├── app/licensing/                   ~/.config/private_train/
│   ├── public_key.py    공개키      ├── license_signing_key.pem   ← 개인키
│   ├── built_in_policy.py  오프라인 기본값 (빌드에 박힘)
│   ├── token.py         서명 형식   └── issued.jsonl              ← 발급 대장
│   ├── machine.py       머신 ID
│   ├── store.py         저장/상태
│   ├── revocation.py    철회 목록
│   └── guard.py         요청 차단
├── scripts/
│   ├── license_admin.py             발급 도구
│   ├── ci_approve.py                승인 댓글 해석 (Actions 용)
│   ├── ci_expiry.py                 만료 임박 스캔 (Actions 용)
│   └── ci_renew.py                  자동 갱신 대상 스캔 (Actions 용)
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   └── license-request.yml      요청 폼
│   └── workflows/
│       ├── license-approve.yml      승인 워크플로
│       ├── license-renew.yml        자동 갱신 (매일 08:30)
│       └── license-expiry.yml       만료 알림 (매일 09:00)
├── docs/                            GitHub Pages 사이트
│   ├── index.html                   현황 대시보드
│   ├── detail.html                  라이선스 상세
│   ├── guide.html                   사용 안내
│   └── assets/                      style.css, js/ (config·api·model·ui·페이지)
├── licenses/autorenew.json          자동 갱신 설정
├── licenses/<머신ID>.key             발급된 라이선스 (앱이 여기서 받아감)
├── license-policy.json              검사 ON/OFF 스위치 (없으면 OFF)
└── revoked.json                     서명된 철회 목록
```

사용자 PC 에는 `license.key` 와 `state.json` 이 OS 별 앱 데이터 폴더에 저장됩니다
(Windows `%APPDATA%\TrainReservation\`, macOS `~/Library/Application Support/TrainReservation/`,
Linux `~/.config/train-reservation/`).
