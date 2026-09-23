# mobile/ — ③ 안드로이드 앱 (APK)

공통 웹 화면(`webui/`)과 예약 로직(`core/`)을 그대로 폰 안에 넣은 앱입니다. 예약 매크로가 **그 폰에서, 그 폰의 IP 로** 돕니다.
서버가 필요 없고, 화면·예약 로직·호출 간격 옵션은 데스크톱과 똑같습니다.

## 구조

```
앱 실행
 └ MainActivity ─ WebView ──────────► http://127.0.0.1:17650  (폰 안)
 └ ServerService (포그라운드 서비스)
     └ Chaquopy 파이썬 ─ android_main.start() ─ Flask 앱 (webui/, core/, korail2/)
                                                   └ 코레일 API (이 폰의 IP)
```

- **파이썬 코드는 복사본이 없습니다.** 빌드할 때 저장소 루트의 공통 모듈 `webui/`, `core/`, `korail2/` 를
  가져다 싣습니다 (`app/build.gradle.kts` 의 `syncPythonSources`).
- **앱을 내려도 계속 돕니다.** ServerService 가 포그라운드 서비스라 상단에 알림이 떠 있고,
  매크로가 도는 동안만 CPU·Wi-Fi 절전 방지 잠금을 쥡니다 (안 돌 땐 배터리를 안 씀).
- **알림**: 예약 성공·결제 결과·매크로 중단을 안드로이드 기본 알림으로 띄웁니다.
- **보안**: 서버는 127.0.0.1 에만 뜨고, 설치마다 만든 무작위 토큰을 WebView 쿠키로만 넘깁니다.
  같은 폰의 다른 앱이 붙으면 403. 코레일 비밀번호·카드는 앱 메모리에만 둡니다.

| 파일 | 역할 |
|---|---|
| `app/src/main/python/android_main.py` | 파이썬 진입점. Flask 를 띄우고 매크로 상태·알림을 Kotlin 에 알린다 |
| `.../ServerService.kt` | 포그라운드 서비스, 파이썬 시작, 절전 방지 잠금, 상단 알림·[종료] |
| `.../MainActivity.kt` | WebView, 알림 권한, 배터리 최적화 제외 안내 |
| `.../Bridge.kt` | 파이썬 → Kotlin 호출 입구 |
| `.../Notifications.kt` | 알림 채널, 예약 알림 |
| `requirements.txt` | 앱에 싣는 파이썬 패키지 |

## 친구에게 나눠줄 때 (설치 안내)

1. GitHub Releases 에서 `TrainReservationApp-v<버전>.apk` 받기
2. 설치할 때 "출처를 알 수 없는 앱" 허용
3. 처음 열면 뜨는 **알림 허용**, **배터리 최적화 제외** 둘 다 허용
4. 삼성폰은 추가로: 설정 → 배터리 → 백그라운드 사용 제한 → **절전 예외 앱**에 추가
   (안 하면 화면을 끄고 몇 시간 뒤 매크로가 멈출 수 있음)
5. 앱을 완전히 끄려면 상단 알림의 **[종료]**

업데이트는 새 APK 를 그냥 설치하면 됩니다 (같은 서명 키로 빌드했을 때만. 아래 참고).

## 빌드

### GitHub Actions (최종 빌드)

`.github/workflows/android.yml`. `v*` 태그를 푸시하면 APK 를 빌드해 Release 에 올리고,
Actions 탭에서 수동 실행도 됩니다.

**서명 키를 꼭 등록하세요.** 없으면 빌드마다 다른 디버그 키로 서명돼서, 업데이트할 때마다
앱을 지우고 다시 깔아야 합니다 (로그인·설정도 날아감). 저장소 Settings → Secrets → Actions:

| 시크릿 | 값 |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `base64 -w0 release.jks` 결과 |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 비밀번호 |
| `ANDROID_KEY_ALIAS` | 키 별칭 |
| `ANDROID_KEY_PASSWORD` | 키 비밀번호 |

키 만들기 (한 번만. **잃어버리면 기존 설치본을 업데이트할 수 없으니 백업**):

```bash
keytool -genkeypair -keystore release.jks -storetype PKCS12 -alias trainreservation \
  -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=TrainReservation, O=Personal, C=KR"
```

### 로컬 (테스트용)

JDK 17, Android SDK(platform 35, build-tools 35), Python 3.12 가 필요합니다.

```bash
cd mobile/android
echo "sdk.dir=$ANDROID_HOME" > local.properties
export CHAQUOPY_BUILD_PYTHON=/path/to/python3.12   # PATH 에 python3.12 가 있으면 생략
./gradlew assembleDebug        # → app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

디버그 빌드에만 코레일 없이 매크로 상태·알림을 시험하는 주소가 있습니다
(`/__debug/macro?state=on|off`, `/__debug/notify`, 토큰 쿠키 필요).

## 제약

- **iPhone 은 안 됩니다.** iOS 는 앱을 내리면 백그라운드 작업을 멈추고, 앱스토어 없이 설치도 어렵습니다.
  iPhone 은 서버 버전(`server/`)을 홈 화면 앱으로 쓰세요.
- Android 8.0 (API 26) 이상. 요즘 폰(arm64)과 에뮬레이터(x86_64)용만 싣습니다.
- 화면에 필요한 Tailwind·달력 스크립트를 인터넷(CDN)에서 받으므로 인터넷이 필요합니다
  (어차피 코레일 조회에도 필요).
