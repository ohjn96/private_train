# mobile/ — ③ 폰 앱 (안드로이드 · iPhone)

두 앱 모두 공통 웹 화면(`webui/`)과 예약 로직(`core/`)을 폰 안의 127.0.0.1 에 띄우고
WebView 로 보여준다. 코레일 호출은 그 폰에서, 그 폰의 IP 로 나간다.

```
mobile/
├─ shared/mobile_runtime.py   공통: 서버 시작, 작업 저장·자동 재개, 오류 시 재시작, 헬스체크
├─ android/                   안드로이드 (Chaquopy + 포그라운드 서비스) — android/README.md
└─ ios/                       iPhone (BeeWare) — 앱을 화면에 띄워 둔 동안만 동작
```

플랫폼별 차이는 `mobile_runtime.set_bridge()` 로 꽂는 연결부(알림, 절전 방지, 작업 암호화 저장)뿐이다.

## iPhone (`ios/`)

BeeWare(Briefcase + Toga) 로 만든 앱. 파이썬이 앱 안에서 같은 공통 런타임을 띄우고 WebView 로 보여준다.
**앱이 화면에 떠 있는 동안에만 돈다** (iOS 제약). 매크로가 도는 동안엔 자동 잠금을 끈다.
배포는 무료 Apple ID + Sideloadly (7일마다 다시 서명). 설치·빌드는 [ios/README.md](ios/README.md).

| | 안드로이드 | iPhone |
|---|---|---|
| 앱을 내려도 계속 | ✅ 포그라운드 서비스 | ❌ 화면에 떠 있을 때만 (자동 잠금 끔) |
| 작업 저장 | Keystore 암호화 | iOS 데이터 보호(NSFileProtectionComplete) + 백업 제외 |
| 알림 | 안드로이드 알림 | iOS 로컬 알림 |
| 코레일 암호화 | pycryptodome | 순수 파이썬 pyaes (C 확장을 못 실음) |
| 배포 | .apk (Release) | 서명 없는 .ipa (Release) → Sideloadly |
