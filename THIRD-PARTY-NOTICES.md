# 제3자 구성요소 고지 (Third-Party Notices)

이 프로젝트와 배포되는 실행 파일에는 아래의 오픈소스 구성요소가 포함되어 있습니다.
각 구성요소는 프로젝트 본체의 [LICENSE](LICENSE) 가 아니라 **각자의 라이선스**를 따릅니다.

## 저장소에 포함된 소스 (vendored)

| 구성요소 | 라이선스 | 저작권 | 원본 |
|---|---|---|---|
| `korail2/` | BSD | (c) 2014 Taehoon Kim | https://github.com/carpedm20/korail2 |
| `webui/static/vendor/tailwindcss.js` (3.4.17) | MIT | (c) Tailwind Labs, Inc. | https://tailwindcss.com |
| `webui/static/vendor/flatpickr*` (4.6.13) | MIT | (c) Gregory Petrosyan | https://github.com/flatpickr/flatpickr |

전문: [`korail2/LICENSE`](korail2/LICENSE)

## 실행 파일(exe)에 번들되는 의존성

PyInstaller 로 빌드한 실행 파일에는 `requirements.txt` 의 패키지가 포함됩니다.

| 패키지 | 라이선스 |
|---|---|
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click, blinker | BSD-3-Clause |
| requests, urllib3, idna, charset-normalizer | Apache-2.0 / MIT / BSD-3-Clause |
| certifi | MPL-2.0 |
| pycryptodome | BSD-2-Clause / Public Domain |
| PyYAML, colorama, packaging, zipp, importlib_metadata | MIT / BSD / Apache-2.0 |
| PyInstaller (빌드 도구) | GPL-2.0 with bootloader exception |
| altgraph | MIT |

각 라이선스 전문은 설치된 패키지의 `*.dist-info/` 에서 확인할 수 있습니다:

```bash
python -m pip install pip-licenses && pip-licenses --format=markdown --with-license-file
```

> PyInstaller 의 GPL 은 **부트로더 예외(bootloader exception)** 가 있어, 이 예외에 따라
> 빌드된 실행 파일 자체는 GPL 을 따를 필요가 없습니다.

## UI

| 구성요소 | 라이선스 |
|---|---|
| Tailwind CSS (앱에 포함, 위 표 참고) | MIT |
| flatpickr (앱에 포함, 위 표 참고) | MIT |
| IBM Plex Sans KR / IBM Plex Mono (Google Fonts 에서 불러옴) | SIL OFL 1.1 |
