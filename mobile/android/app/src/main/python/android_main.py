# -*- coding: utf-8 -*-
"""안드로이드 앱의 파이썬 입구 (Chaquopy).

실제 동작은 공통 런타임(mobile/shared/mobile_runtime.py)에 있다. 여기선 Kotlin 의
Bridge(알림·절전 방지·Keystore 저장)를 꽂아 주기만 한다. ServerService 가 부른다.
"""
import mobile_runtime


def _use_android_bridge() -> None:
    from java import jclass
    mobile_runtime.set_bridge(jclass('com.ohjn96.trainreservation.Bridge'))


def start(files_dir: str, port: int, token: str, version: str, debug: bool = False) -> None:
    _use_android_bridge()
    mobile_runtime.start(files_dir, port, token, version, debug)


def resume_job(job_json: str) -> None:
    _use_android_bridge()
    mobile_runtime.resume_job(job_json)


def stop_macro() -> None:
    mobile_runtime.stop_macro()
