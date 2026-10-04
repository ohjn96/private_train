# 서명 키

라이선스·정책에 찍는 **도장**입니다. 두 개가 한 쌍입니다.

| | 위치 | 성격 |
|---|---|---|
| 개인키 | `~/.config/private_train/license_signing_key.pem` + GitHub Secret `LICENSE_SIGNING_KEY` | **비밀**. 도장을 찍음 |
| 공개키 | [licensing/public_key.py](../licensing/public_key.py) (exe 에 들어감) | 공개. 도장이 진짜인지 확인만 함 |

개인키가 있어야 스위치 변경(on/allow/off)·발급·철회를 할 수 있습니다. 앱 실행·빌드에는 필요 없습니다.

## 할 일

1. **예전 PC 에서 개인키 찾기** — `~/.config/private_train/license_signing_key.pem`
2. **이 PC 로 복사** — `C:\Users\ohjn9\.config\private_train\` 에 (`issued.jsonl` 도 같이).
   USB 등 오프라인으로 옮기고 OneDrive 폴더에는 두지 마세요.
3. **백업** — 두 곳 이상 (암호 걸린 USB, 비밀번호 관리자 등)
4. **GitHub Secret 확인** — Actions → "라이선스 검사 켜고 끄기" → `on` 실행. 성공하면 키가 맞습니다.

## 못 찾으면

`python scripts/license_admin.py keygen` 으로 새로 만들 수 있지만, **기존 라이선스가 전부 무효**가 되고
**이미 배포된 exe 에는 원격 스위치가 더 이상 듣지 않습니다**. 새 키로 다시 빌드·배포하고
Secret 도 교체해야 합니다.

## 절대 하지 말 것

- 개인키 커밋 (`*.pem` 은 `.gitignore` 에 있음)
- 메신저·메일·클라우드 동기화 폴더로 주고받기
