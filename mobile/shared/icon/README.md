# 앱 아이콘

원본: `app-icon.svg` (1024×1024, 글자 없음). 딥블루 배경에 흰 열차 정면.

| 쓰임 | 색 |
|---|---|
| 배경 | `#1E4FD8` (딥블루, 흰색과 대비 6.6:1) |
| 열차 | `#FFFFFF` |
| 앞 유리·전조등 | 배경색으로 뚫음 |

앱 화면의 강조색(빨강)과는 따로다. 코레일 로고·색은 쓰지 않는다.

## 이 원본에서 뽑은 파일 (모두 알파 없는 꽉 찬 정사각형)

- Android: `mobile/android/app/src/main/res/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_launcher.png` (48·72·96·144·192)
- Android 8+ 적응형: `mipmap-anydpi-v26/ic_launcher{,_round}.xml` = 배경색 `@color/ic_launcher_background` + 앞면 `drawable/ic_launcher_foreground.xml` (같은 도형의 벡터, 안전 영역 66dp 안)
- 웹(PWA): `webui/static/icons/icon-192.png`, `icon-512.png` (maskable 겸용), `apple-touch-icon.png` (180)
- iOS (Briefcase `icon = "icons/trainreservation"`): `mobile/ios/icons/trainreservation-{20,29,40,58,60,76,80,87,120,152,167,180,1024}.png`,
  시작 화면용 `-640/-1280/-1920.png` (흰 바탕 가운데에 둥근 아이콘)

도형을 바꾸면 위 PNG 를 다시 뽑고, 적응형 앞면 벡터(`ic_launcher_foreground.xml`)의 경로도 같이 고친다.
PNG 는 헤드리스 Chrome(Playwright)으로 SVG 를 크기별로 찍은 뒤 RGB 로 저장했다.
