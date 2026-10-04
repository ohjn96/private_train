# 서명 키 안내

라이선스 시스템의 "도장"입니다. 이 문서 하나만 보면 무엇을 어디에 둬야 하는지 알 수 있게 정리했습니다.
전체 운영 방법은 [LICENSING.md](LICENSING.md)에 있습니다.

## 서명 키가 뭔가요

키는 **두 개가 한 쌍**입니다.

| | 개인키 (비밀) | 공개키 (공개) |
|---|---|---|
| 하는 일 | 라이선스·정책에 **도장을 찍음** | 도장이 진짜인지 **확인만** 함 |
| 위치 | `~/.config/private_train/license_signing_key.pem` (저장소 밖) | [licensing/public_key.py](../licensing/public_key.py) (exe 에 들어감) |
| 누가 가짐 | 나 + GitHub Secrets | 모든 사용자 |
| 잃어버리면 | 새로 발급·스위치 변경 불가 | 저장소에 있으니 문제없음 |
| 새면 | 아무나 라이선스를 만들고 스위치를 바꿀 수 있음 | 문제없음 (원래 공개용) |

앱은 공개 저장소에서 받은 정책(전체 ON / 허용한 것만 / 전체 OFF)·라이선스·철회 목록을
공개키로 검사합니다. **개인키로 찍은 것만 통과**하므로, 남이 가짜 정책 파일을 올려도 먹히지 않습니다.

알고리즘은 Ed25519 이고, 파일은 PEM 텍스트입니다 (`-----BEGIN PRIVATE KEY-----` 로 시작).

## 개인키가 필요한 일 / 필요 없는 일

| 개인키 필요 | 개인키 필요 없음 |
|---|---|
| 스위치 바꾸기 (`policy --mode on/allow/off`) | 앱 실행·빌드 |
| 라이선스 발급 (`issue`, `/approve`) | 현재 정책 보기 (`policy` 만) |
| 철회 (`revoke`, `/revoke`) | 테스트 |
| 자동 갱신 | |

GitHub Actions(버튼·이슈 댓글)는 **Secrets 에 등록한 개인키**를 씁니다.
그래서 Secrets 에만 제대로 들어 있으면, 평소엔 PC 에 개인키가 없어도 운영할 수 있습니다.

## 지금 상황

- 이 PC(Windows)에는 개인키가 **없습니다**.
- 공개키는 저장소에 있고, 지금 공개 저장소의 정책 파일이 이 공개키로 **검증됩니다**.
  → 짝이 되는 개인키가 어딘가에 살아 있다는 뜻입니다 (예전 PC, WSL, GitHub Secrets).

## 할 일

### 1. 예전 PC 에서 개인키 찾기

예전 PC(또는 WSL)에서:

```bash
ls ~/.config/private_train/
# license_signing_key.pem   ← 이게 개인키
# issued.jsonl              ← 발급 대장 (누구에게 언제 줬는지, 개인정보 포함)
```

찾았으면 **이 공개키와 짝이 맞는지** 확인합니다 (저장소 루트에서):

```bash
python -c "from Crypto.PublicKey import ECC; from licensing.public_key import PUBLIC_KEY_PEM as P; \
k=ECC.import_key(open('$HOME/.config/private_train/license_signing_key.pem').read()); \
print('짝 맞음' if k.public_key().export_key(format='PEM').strip()==P.strip() else '다른 키!')"
```

### 2. 이 PC 로 옮기기 (로컬에서 명령을 쓸 거라면)

두 파일을 아래 위치로 복사합니다. USB·암호 걸린 압축 등 **오프라인으로** 옮기세요.
메신저·메일·클라우드 공유 폴더(OneDrive 포함)는 피하세요.

```
C:\Users\ohjn9\.config\private_train\license_signing_key.pem
C:\Users\ohjn9\.config\private_train\issued.jsonl
```

다른 위치에 두려면 `LICENSE_ADMIN_DIR` 환경변수로 폴더를 지정합니다.

확인:

```powershell
python scripts/license_admin.py policy     # 현재 상태 보기 (키 없어도 됨)
python scripts/license_admin.py list       # 발급 내역 (대장이 있어야 보임)
```

### 3. GitHub Secrets 확인 (버튼·댓글로 운영할 거라면)

`ohjn96/train_macro` → Settings → Secrets and variables → Actions 에 아래가 있어야 합니다.

| 이름 | 값 |
|---|---|
| `LICENSE_SIGNING_KEY` | 개인키 파일 내용 전체 (BEGIN ~ END 줄까지) |
| `DIST_RELEASE_TOKEN` | 공개 저장소에 쓰는 토큰 ([SECRETS.md](../SECRETS.md)) |

Secrets 는 값을 다시 볼 수 없으므로, 있는지만 확인하고 짝이 맞는지는 실제로 한 번 돌려 봅니다:
Actions → "라이선스 검사 켜고 끄기" → Run workflow → `on` (지금과 같은 값이라 사용자에겐 영향 없음).
성공하면 키가 맞는 것이고, 공개 저장소 `license-policy.json` 의 seq 가 1 올라갑니다.

### 4. 백업

개인키는 **두 곳 이상**에 백업합니다 (예: 암호 걸린 USB + 비밀번호 관리자).
GitHub Secrets 는 꺼내 볼 수 없으므로 백업으로 치지 않습니다.

## 끝내 못 찾으면

새로 만들 수 있지만 **기존 라이선스가 전부 무효**가 됩니다 (새 공개키로는 옛 도장을 확인할 수 없으므로).

```bash
python scripts/license_admin.py keygen          # 새 개인키 + licensing/public_key.py 갱신
python scripts/license_admin.py policy --mode on   # 새 키로 정책 다시 서명
```

그 뒤에:

1. 바뀐 `licensing/public_key.py` 를 커밋하고 **exe 를 새로 빌드·배포** (옛 exe 는 새 정책을 못 읽어서
   마지막으로 받아 둔 정책에 머뭅니다)
2. GitHub Secrets 의 `LICENSE_SIGNING_KEY` 를 새 키로 교체
3. 공개 저장소에 새로 서명한 `license-policy.json` 푸시
4. 기존 사용자에게 재발급

지금 공개 정책이 "전체 ON" 이라 사용자들은 라이선스 없이도 쓰고 있으므로, 키를 바꿔도
당장 막히는 사람은 없습니다. 다만 옛 exe 는 새 키로 서명된 정책을 무시하므로
**옛 exe 에는 원격 스위치가 더 이상 먹히지 않습니다.** 그래서 찾을 수 있으면 찾는 게 낫습니다.

## 하지 말 것

- 개인키를 저장소에 커밋 (`*.pem` 은 커밋 전에 `git status` 로 확인)
- OneDrive 처럼 동기화되는 폴더에 두기 (이 저장소도 OneDrive 안에 있습니다)
- 유출이 의심되는데 그냥 두기 → 위 "끝내 못 찾으면" 절차로 교체
