# 제3자 구성요소 고지 (Third-Party Notices)

이 프로젝트와 배포되는 실행 파일에는 아래의 오픈소스 구성요소가 포함되어 있습니다.
각 구성요소는 프로젝트 본체의 [LICENSE](LICENSE) 가 아니라 **각자의 라이선스**를 따릅니다.

## 저장소에 포함된 소스 (vendored)

| 구성요소 | 라이선스 | 저작권 | 원본 |
|---|---|---|---|
| `korail2/` | BSD | (c) 2014 Taehoon Kim | https://github.com/carpedm20/korail2 |
| `webui/static/css/app.css` (Tailwind CSS 3.4.17 로 만든 결과물, preflight 포함) | MIT | (c) Tailwind Labs, Inc. | https://tailwindcss.com |
| `webui/static/fonts/` IBM Plex Sans KR·IBM Plex Mono (woff2 조각) | SIL OFL 1.1 | (c) 2017 IBM Corp. (Reserved Font Name "Plex") | https://github.com/IBM/plex |
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
| IBM Plex Sans KR / IBM Plex Mono (앱에 포함, 위 표와 아래 전문 참고) | SIL OFL 1.1 |

## IBM Plex 글꼴 라이선스 전문 (SIL OFL 1.1)

`webui/static/fonts/` 의 IBM Plex Sans KR·IBM Plex Mono 에 적용됩니다.

```text
Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"

This Font Software is licensed under the SIL Open Font License, Version 1.1.

This license is copied below, and is also available with a FAQ at: http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```
