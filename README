# Markdown content as a string
markdown_content = """
# Python 3.9 환경 설정 및 실행 가이드 (WSL - Ubuntu)

이 문서는 WSL(Windows Subsystem for Linux)에서 Ubuntu를 사용하여 Python 3.9 환경을 설정하고 프로그램을 실행하는 방법을 설명합니다.

---

## 1. 환경
- **운영체제**: Ubuntu (WSL)
- **Python 버전**: 3.9

---

## 2. Python 3.9 설치 및 우분투 패키지 설치


```bash
# 패키지 목록 업데이트
sudo apt update

# 필수 패키지 설치
sudo apt install software-properties-common -y

# Python 3.9용 PPA 추가
sudo add-apt-repository ppa:deadsnakes/ppa

# Python 3.9 설치
sudo apt install python3.9 python3.9-venv python3.9-distutils -y

sudo apt install pip

```

## 3. 가상환경 생성
```bash
python3.9 -m venv venv

```

## 4. 가상환경 활성화 및 패키지 설치치

```bash
source venv/bin/activate
pip install -r requirements.txt

```

## 5. 실행
```bash
# SRT 프로그램 실행
python srt_main_web.py

# KTX 프로그램 실행
python ktx_main_web.py
```


## APPENDIX exe 만들기기

```bash

pyinstaller --onefile --add-data "static;static" --hidden-import=flask --hidden-import=flask.sessions --name=SRTReservationApp srt_main_web.py
pyinstaller --onefile --add-data "static;static" --hidden-import=flask --hidden-import=flask.sessions --name=KTXReservationApp ktx_main_web.py

```
