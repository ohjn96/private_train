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
