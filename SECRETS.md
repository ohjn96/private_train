# 시크릿 배치

두 저장소가 서로를 부르므로 토큰이 양방향으로 필요합니다.

```
private_train (비공개)                    train-reservation (공개)
  LICENSE_SIGNING_KEY   서명 개인키
  DIST_RELEASE_TOKEN    ──────────────▶   Contents RW + Issues RW
                                          (릴리스 업로드, 산출물 푸시, 이슈 댓글)
  ◀──────────────────  RELAY_TOKEN        Contents RW
                                          (repository_dispatch 발송)
```

| 저장소 | 이름 | 값 | 권한 |
|---|---|---|---|
| private_train | `LICENSE_SIGNING_KEY` | `~/.config/private_train/license_signing_key.pem` 내용 | — |
| private_train | `DIST_RELEASE_TOKEN` | fine-grained PAT | `train-reservation` 에 **Contents: RW**, **Issues: RW** |
| train-reservation | `RELAY_TOKEN` | fine-grained PAT | `private_train` 에 **Contents: RW** |

`DIST_RELEASE_TOKEN` 은 이미 있지만 **Issues 권한을 추가**해야 합니다 —
승인 결과를 공개 이슈에 댓글로 달기 때문입니다.

```bash
# 서명 키 (이미 등록돼 있으면 생략)
gh secret set LICENSE_SIGNING_KEY --repo ohjn96/private_train \
  < ~/.config/private_train/license_signing_key.pem

# PAT 두 개는 https://github.com/settings/personal-access-tokens/new 에서 발급 후
gh secret set DIST_RELEASE_TOKEN --repo ohjn96/private_train
gh secret set RELAY_TOKEN        --repo ohjn96/train-reservation
```

## 토큰이 만료되면

조용히 실패합니다. 증상별로:

| 증상 | 의심 |
|---|---|
| 릴리스가 공개 저장소에 안 올라감 | `DIST_RELEASE_TOKEN` 만료 |
| `/approve` 를 달아도 아무 반응 없음 | `RELAY_TOKEN` 만료 |
| 발급은 되는데 이슈에 댓글이 안 달림 | `DIST_RELEASE_TOKEN` 에 Issues 권한 없음 |

Actions 탭에서 해당 워크플로 실행 기록을 보면 원인이 나옵니다.
