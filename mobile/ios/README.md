# mobile/ios/ — iPhone 앱 (.ipa)

안드로이드 앱과 같은 구조입니다. 공통 웹 화면(`webui/`)과 예약 로직(`core/`)을 폰 안의 127.0.0.1 에 띄우고
WebView 로 보여줍니다. 코레일 호출은 **그 아이폰에서, 그 아이폰의 IP 로** 나갑니다. 서버가 필요 없습니다.

> ⚠️ **아이폰은 앱이 화면에 떠 있는 동안에만 매크로가 돕니다.**
> iOS 는 뒤로 간 앱을 몇 초 안에 멈춥니다 (안드로이드처럼 백그라운드에서 계속 돌 방법이 없음).
> - 매크로가 도는 동안엔 **화면 자동 잠금이 꺼집니다** (끝나면 원래대로). 충전기에 꽂아 두세요.
> - 앱을 뒤로 보내거나 전원 버튼으로 화면을 끄면 매크로가 멈춥니다. 이때 "⏸ 예약 매크로가 멈춰요" 알림이 뜹니다.
>   앱을 다시 열면 이어서 돕니다.
> - 앱이 완전히 종료됐다가(메모리 정리 등) 다시 열리면, 하던 매크로를 저장된 내용으로 자동으로 다시 시작합니다.

## 구조

```
앱 실행 (BeeWare Toga)
 └ app.py ─ toga.WebView ───────────► http://127.0.0.1:<무작위 포트>  (폰 안, /__hello 로 확인한 곳만)
 └ ServerHost (파이썬 스레드)
     └ mobile_runtime.start() ─ Flask 앱 (webui/, core/, korail2/)
                                  └ 코레일 API (이 폰의 IP)
```

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | Briefcase 설정 (앱 이름·번들 ID·싣는 패키지·Info.plist) |
| `src/trainreservation/app.py` | 앱 입구. 서버를 무작위 포트로 띄우고, 확인되면 WebView 로 `/__auth?t=토큰` 을 연다. WebView 탐색 제한 |
| `src/trainreservation/ios_bridge.py` | `IOSBridge`(공통 런타임에 꽂는 연결부), 작업 파일 저장, 토큰, 서버 감시 — iOS API 없이 테스트 가능 |
| `src/trainreservation/ios_native.py` | iOS 호출 (rubicon-objc): 자동 잠금 끄기, 로컬 알림, 파일 보호 |
| `sync_sources.py` | 저장소 루트의 `webui/ core/ korail2/`, `mobile/shared/mobile_runtime.py` 를 `src/` 로 복사 + 버전 맞춤 |
| `tools/make_pure_markupsafe.py` | iOS 용 순수 파이썬 wheel (MarkupSafe, pyaes) → `wheels/` |
| `tools/prepare_xcode_project.py` | `briefcase create` 뒤 보정: 홈 화면 이름 "열차예약", 서명 없는 기기 빌드 허용 |

- **파이썬 코드는 복사본이 git 에 없습니다.** `sync_sources.py` 가 빌드 때 가져옵니다 (`src/webui` 등은 gitignore).
- **C 확장 문제**: Briefcase 는 iOS 패키지를 iOS 용 wheel 이나 순수 파이썬 wheel 로만 설치합니다.
  - `pycryptodome` 은 iOS wheel 이 없어 뺐습니다. 코레일 로그인 암호화는 `korail2/_aes.py` 가 순수 파이썬 `pyaes` 로 대신합니다 (결과는 바이트 단위로 같음, `tests/test_aes_backends.py`).
  - `MarkupSafe` 는 C 확장 wheel 만 있어서 sdist 에서 `.py` 만 골라 wheel 을 만듭니다 (`_speedups` 가 없으면 `_native` 로 동작). `pyaes` 는 PyPI 에 sdist 만 있어서 같은 도구로 wheel 을 만듭니다. 둘 다 sha256 을 고정해 받습니다.
- **보안**: 서버는 127.0.0.1 의 **무작위 포트**(운영체제가 고름)에만 뜨고, 설치마다 만든 무작위 토큰을 첫 주소 `/__auth?t=…` 로 WebView 쿠키에 넘깁니다. 같은 폰의 다른 앱이 붙으면 403.
  - 토큰을 보내기 전에 그 포트의 서버에 매번 새 무작위 nonce 로 `/__hello?nonce=` 를 불러 `HMAC-SHA256(토큰, nonce)` 가 맞는지 봅니다 (쿠키 없이, 리다이렉트 안 따라감). 맞지 않으면 토큰을 보내지 않습니다 — 다른 앱이 포트를 차지하고 우리 행세를 해도 토큰을 못 가져갑니다. `/__health` 도 이 확인을 거친 뒤에만 쿠키를 싣습니다.
  - WebView 는 확인한 `http://127.0.0.1:<포트>` 만 엽니다 (쿠키는 포트를 가리지 않으므로 다른 포트도 막음). 바깥 http(s) 링크는 사파리로 넘기고, 그 밖의 주소는 막습니다.
    toga 0.5.6 의 `on_navigation_starting` 은 허용(True)하면 그 주소를 GET 으로 다시 열어 POST(로그인 폼)를 깨뜨리므로, 판단만 쓰도록 handler 를 바꿔 끼웁니다 (`_drop_navigation_cleanup`, toga 를 올리면 다시 확인).
  - 확인을 마친 포트는 앱 데이터 폴더의 `server_port` 파일에 적습니다 (CI 스모크 테스트·진단용, 비밀 아님).
- **작업 저장**: 매크로를 시작하면 되살릴 작업(열차·좌석·코레일 로그인·카드)을 앱 데이터 폴더의 `macro_job.json` 에 원자적으로 씁니다.
  안드로이드는 Keystore 로 암호화하지만, 아이폰은 iOS 데이터 보호에 맡깁니다:
  `NSFileProtectionComplete`(폰이 잠겨 있으면 읽을 수 없음) + iCloud/PC 백업 제외. 둘 다 "최선" 이라 실패하면 로그만 남기고 저장은 합니다.
  매크로가 정상적으로 끝나면(성공·중단·로그인 거부) 지웁니다.
- **알림**: 예약 성공·결제·중단을 iOS 로컬 알림으로 띄웁니다. 처음 열 때 알림 권한을 묻습니다. 거절해도 웹 화면의 결과 배너는 그대로 뜹니다.

## 설치 (Windows PC + 무료 Apple ID + Sideloadly)

유료 개발자 계정 없이 **무료 Apple ID** 로 설치합니다. 대신 **7일마다 다시 서명**해야 합니다.

1. **.ipa 받기**: GitHub [Releases](https://github.com/ohjn96/private_train/releases) 에서
   `TrainReservationApp-v<버전>.ipa` (또는 Actions 탭 → *Build iOS App* 실행 결과의 Artifacts).
2. **PC 준비**
   - [iTunes](https://www.apple.com/itunes/) 를 **Microsoft Store 가 아닌 Apple 홈페이지 판**으로 설치 (Sideloadly 가 기기 드라이버를 씀).
   - [Sideloadly](https://sideloadly.io/) 설치.
3. **아이폰 연결**: USB 로 연결 → 아이폰에서 "이 컴퓨터를 신뢰" → 암호 입력.
4. **Sideloadly 로 설치**
   - 왼쪽에 .ipa 를 끌어다 놓기 → *Apple account* 에 Apple ID 입력 → **Start** → Apple ID 암호(2단계 인증 코드) 입력.
   - 부계정 Apple ID 를 써도 됩니다. 암호는 Sideloadly 가 Apple 서버에 로그인하는 데만 씁니다.
5. **개발자 모드 켜기** (iOS 16 이상, 처음 한 번)
   설정 → 개인정보 보호 및 보안 → **개발자 모드** 켜기 → 재시동 → "켜기" 확인.
   (메뉴가 안 보이면 Sideloadly 로 한 번 설치를 시도한 뒤 다시 보세요.)
6. **개발자 신뢰** (처음 한 번)
   설정 → 일반 → **VPN 및 기기 관리** → 내 Apple ID → **신뢰**.
7. 앱 "열차예약" 실행 → **알림 허용**.

### 7일 만료와 자동 갱신

무료 Apple ID 로 서명한 앱은 **7일 뒤 열리지 않습니다** (데이터는 남음). 다시 서명하면 됩니다:

- **수동**: 같은 .ipa 를 Sideloadly 로 다시 설치 (지우지 말고 덮어쓰기 → 로그인·설정 유지).
- **자동**: Sideloadly 설정에서 **Automatic app refresh** 를 켜 두면, PC 가 켜져 있고 아이폰이 같은 Wi-Fi 에 있을 때
  (처음에 iTunes 에서 "Wi-Fi 로 이 iPhone 과 동기화" 를 켜야 함) 만료 전에 알아서 다시 서명합니다.
- 무료 Apple ID 는 동시에 **앱 3개**까지, **일주일에 새 앱 ID 10개**까지만 됩니다.

업데이트: 새 버전 .ipa 를 같은 Apple ID 로 Sideloadly 에 넣고 설치하면 덮어씁니다.

## 쓰는 법

1. 앱을 열고 코레일 로그인 → 열차 조회 → 매크로 시작 (데스크톱·안드로이드와 같은 화면).
2. 매크로가 도는 동안엔 **앱을 화면에 띄워 두세요.** 자동 잠금은 꺼져 있습니다. 충전기에 꽂아 두는 걸 권합니다.
3. 예약에 성공하면 알림 + 웹 화면 결과 카드. 결제 기한 안에 결제를 확인하세요.

알아둘 점:
- 성공 소리(웹 화면의 효과음)는 iOS WebView 의 자동 재생 제한 때문에 안 날 수 있습니다. 알림 소리는 납니다.
- 전원 버튼으로 화면을 끄면 멈춥니다 (자동 잠금만 꺼 두는 것이지, 잠금 상태에서 돌 수는 없음).

## 빌드

### GitHub Actions (최종 빌드)

`.github/workflows/ios.yml`. `v*` 태그를 푸시하면 .ipa 를 빌드해 Release 에 올리고, Actions 탭에서 수동 실행도 됩니다.
Mac 이 없어도 됩니다 (macOS 러너에서 빌드).

1. 공통 코드 동기화 + 순수 파이썬 wheel
2. `briefcase create iOS` → `tools/prepare_xcode_project.py`
3. `briefcase build iOS` (시뮬레이터) → **스모크 테스트**: 시뮬레이터에 설치·실행하고,
   `xcrun simctl get_app_container … data` 의 `Documents/server_port` 에서 포트를 읽어 러너에서
   `/login` 이 **403** 인지(서버가 떴고 토큰 검사가 켜짐), `/__hello` HMAC 이 맞고 토큰 쿠키로 `/__health` 가 200 인지 확인.
   스크린샷·앱 로그는 `ios-simulator-smoke` 아티팩트.
4. `xcodebuild -sdk iphoneos -configuration Release CODE_SIGNING_ALLOWED=NO` → `Payload/TrainReservation.app` 을 zip 해서 `TrainReservationApp-v<버전>.ipa`

서명은 하지 않습니다. Sideloadly 가 설치할 때 사용자의 Apple ID 로 서명합니다.

### Mac 에서 (테스트용)

Xcode, Python 3.12 가 필요합니다.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install briefcase==0.4.4
python mobile/ios/sync_sources.py
python mobile/ios/tools/make_pure_markupsafe.py
cd mobile/ios
briefcase create iOS
python tools/prepare_xcode_project.py
briefcase run iOS                 # 시뮬레이터에서 실행
# 코드를 고친 뒤: python sync_sources.py && briefcase update iOS && briefcase run iOS
```

### 리눅스/윈도우에서 확인할 수 있는 것

`python -m unittest discover -s tests` 의 `test_ios_bridge.py`, `test_ios_build_tools.py` 가
작업 파일 저장·토큰·bridge·서버 소켓 재개·앱 배선(가짜 toga)·동기화·wheel 빌더를 확인합니다.
iOS API(rubicon) 부분은 CI 시뮬레이터와 실기기에서만 확인됩니다.

## 알려진 위험 / 확인이 필요한 것

- **실기기 동작은 CI 가 확인하지 못합니다.** CI 는 시뮬레이터에서 서버가 뜨는지까지만 봅니다.
  알림 배너(앱이 앞에 있을 때), 자동 잠금 끄기, 파일 보호는 실기기에서 한 번 확인해 주세요.
- **알림 delegate (rubicon)**: `UNUserNotificationCenterDelegate` 를 파이썬 클래스로 구현했습니다.
  실패하면 로그만 남기고 알림 없이 동작합니다 (웹 화면 배너는 그대로).
- **소켓 회수**: iOS 는 멈춰 있던 앱의 듣는 소켓을 회수할 수 있습니다 (Apple TN2277).
  앱이 다시 앞으로 오면 `/__hello` + `/__health` 로 확인하고, 응답이 없으면 같은 Flask 앱으로 소켓만 새 무작위 포트에
  다시 열고, 그 포트를 다시 확인한 뒤 WebView 를 새 주소로 엽니다.
- **서명 없는 기기 빌드**: support package 의 `utils.sh` 가 표준 라이브러리 C 확장을 서명하는 줄을 ad-hoc 서명으로 바꿔 둡니다.
  Briefcase/support package 버전을 올리면 `prepare_xcode_project.py` 가 그 줄을 못 찾고 실패합니다 (일부러 크게 실패).
