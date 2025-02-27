# -*- coding: utf-8 -*-
import time
import json
import os
# import logging  # 로깅 삭제
from flask import Flask, request, render_template_string, redirect, url_for, session, Response

from SRT.srt import SRT, SRTError, SRTNotLoggedInError
from SRT.train import SRTTrain
from SRT.constants import STATION_NAME
import sys

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# PyInstaller 패키징 경로 설정
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

static_path = os.path.join(application_path, 'static')

app = Flask(__name__, static_folder=static_path)

# secret_key 설정
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_secret_key")

STOP_MACRO = False

##################
# SRT 역 리스트 예시
##################
STATION_LIST = ["수서", "동탄", "지제", "대전", "오송", "부산", "광주송정", "목포"]

##############
# 1) 로그인 페이지 템플릿
##############
login_template = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>SRT 로그인</title>
  <style>.error { color: red; }</style>
</head>
<body>
  <h1>SRT 로그인</h1>
  <form method="POST">
    <label>아이디: </label><input type="text" name="srt_id" required><br><br>
    <label>비밀번호: </label><input type="password" name="srt_pw" required><br><br>
    <button type="submit">로그인</button>
  </form>
  {% if error_message %}
    <p class="error">{{ error_message }}</p>
  {% endif %}
</body>
</html>
"""

##############
# 2) 스케줄 페이지 템플릿
##############
schedule_template = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>SRT 스케줄</title>
</head>
<body>
  <h1>SRT 스케줄 폼</h1>
  <form method="POST">
    출발역:
    <select name="dep">
      {% for station in stations %}
        <option value="{{ station }}">{{ station }}</option>
      {% endfor %}
    </select><br><br>

    도착역:
    <select name="arr">
      {% for station in stations %}
        <option value="{{ station }}">{{ station }}</option>
      {% endfor %}
    </select><br><br>

    연도(YYYY):
    <select name="year">
      {% for y in [ 2025 ] %}
        <option value="{{ y }}">{{ y }}</option>
      {% endfor %}
    </select><br><br>

    월(MM):
    <select name="month">
      {% for m in range(1,13) %}
        <option value="{{ m }}">{{ m }}</option>
      {% endfor %}
    </select><br><br>

    일(DD):
    <select name="day">
      {% for d in range(1,32) %}
        <option value="{{ d }}">{{ d }}</option>
      {% endfor %}
    </select><br><br>

    시간(0~23):
    <select name="hour">
      {% for h in range(24) %}
        <option value="{{ h }}">{{ h }}</option>
      {% endfor %}
    </select><br><br>

    <button type="submit">검색하기</button>
  </form>
</body>
</html>
"""

##############
# 3) 검색 결과 & 예약 템플릿 (SSE 매크로)
##############
reserve_template = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>SRT 예약</title>
  <style>
    .error { color: red; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; }
    th { background-color: #f2f2f2; }

    .log-container {
      border: 1px solid #ccc;
      width: 100%;
      height: 300px;
      overflow-y: scroll;
      margin: 10px 0;
      padding: 8px;
    }
    .log-line { margin: 0; padding: 0; }
    .hidden { display: none; }
  </style>
</head>
<body>
  <h1>SRT 검색 결과 & 예약</h1>
  <button onclick="location.href='/schedule'">뒤로가기</button>

  {% if error_message %}
    <p class="error">{{ error_message }}</p>
  {% endif %}

  {% if trains_with_index %}
    <h2>검색 결과</h2>
    <form id="reserveForm">
      <table>
        <thead>
          <tr>
            <th>선택</th>
            <th>열차명</th>
            <th>출발일시</th>
            <th>도착일시</th>
            <th>출발역</th>
            <th>도착역</th>
            <th>좌석상태</th>
          </tr>
        </thead>
        <tbody>
        {% for train, idx in trains_with_index %}
          <tr>
            <td>
              <input type="checkbox" name="train_indices" value="{{ idx }}">
            </td>
            <td>{{ train.train_name }}</td>
            <td>{{ train.dep_date[0:4] }}-{{ train.dep_date[4:6] }}-{{ train.dep_date[6:8] }} {{ train.dep_time[0:2] }}:{{ train.dep_time[2:4] }}</td>
            <td>{{ train.arr_date[0:4] }}-{{ train.arr_date[4:6] }}-{{ train.arr_date[6:8] }} {{ train.arr_time[0:2] }}:{{ train.arr_time[2:4] }}</td>
            <td>{{ train.dep_station_name }}</td>
            <td>{{ train.arr_station_name }}</td>
            <td>{{ train.seat_avaiable }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <br>
      <button type="button" id="startBtn">예약하기</button>
    </form>
  {% else %}
    <p>검색 결과가 없습니다. /schedule 로 돌아가 다시 검색하세요.</p>
  {% endif %}

  <div id="logSection" class="hidden">
    <h2>예약 로그</h2>
    <div class="log-container" id="logContainer"></div>
    <button id="stopBtn">멈춤</button>
  </div>

  <!-- 오디오 요소 추가 -->
  <audio id="successSound" src="{{ url_for('static', filename='success_sound.mp3') }}" preload="auto" style="display:none;"></audio>

  <script>
    const reserveForm = document.getElementById("reserveForm");
    const startBtn = document.getElementById("startBtn");
    const logSection = document.getElementById("logSection");
    const logContainer = document.getElementById("logContainer");
    const stopBtn = document.getElementById("stopBtn");
    const successSound = document.getElementById("successSound");

    let evtSource = null;

    if (startBtn) {
      startBtn.addEventListener("click", async function() {
        const checked = reserveForm.querySelectorAll('input[name="train_indices"]:checked');
        if (checked.length === 0) {
          return;
        }
        const formData = new FormData();
        checked.forEach(cb => {
          formData.append("train_indices", cb.value);
        });
        let resp = await fetch("/reserve_select", {
          method: "POST",
          body: formData
        });
        if(!resp.ok) {
          alert("예약 준비 중 오류 발생");
          return;
        }
        logSection.classList.remove("hidden");
        logContainer.innerHTML = "";

        evtSource = new EventSource("/start_reservation");
        evtSource.onmessage = function(e) {
          const p = document.createElement("p");
          p.className = "log-line";
          p.textContent = e.data;
          logContainer.appendChild(p);
          logContainer.scrollTop = logContainer.scrollHeight;

          if (e.data.includes("예약 성공")) {
            successSound.currentTime = 0;
            successSound.play();
          }
        };
        evtSource.onerror = function(e) {
          evtSource.close();
        };
      });
    }

    if (stopBtn) {
      stopBtn.addEventListener("click", async function(){
        if(evtSource) {
          evtSource.close();
        }
        await fetch("/stop_macro", {method:"POST"});
      });
    }
  </script>
</body>
</html>
"""

###################################
# Flask 라우트
###################################
@app.route("/", methods=["GET", "POST"])
def login():
    error_message = None
    if request.method == "POST":
        srt_id = request.form.get("srt_id")
        srt_pw = request.form.get("srt_pw")
        srt_client = SRT(srt_id, srt_pw, auto_login=False, verbose=True)
        try:
            srt_client.login(srt_id, srt_pw)
            session["srt_id"] = srt_id
            session["srt_pw"] = srt_pw
            # 로그인 성공 후 스케줄 페이지로 이동
            return redirect(url_for("schedule"))
        except Exception as e:
            error_message = str(e)

    return render_template_string(login_template, error_message=error_message)

@app.route("/schedule", methods=["GET", "POST"])
def schedule():
    if "srt_id" not in session or "srt_pw" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        dep = request.form.get("dep")
        arr = request.form.get("arr")
        year = request.form.get("year")
        month = request.form.get("month")
        day = request.form.get("day")
        hour = request.form.get("hour")

        date_str = f"{year}{int(month):02d}{int(day):02d}"
        time_str = f"{int(hour):02d}0000"

        srt_id = session["srt_id"]
        srt_pw = session["srt_pw"]

        srt_client = SRT(srt_id, srt_pw, auto_login=False)
        try:
            srt_client.login(srt_id, srt_pw)
            if not srt_client.is_login:
                raise SRTNotLoggedInError("로그인되지 않았습니다. 로그인 후 다시 시도하세요.")

            trains = srt_client.search_train(dep, arr, date=date_str, time=time_str, available_only=False)
            train_list_for_session = []
            for t in trains:
                train_list_for_session.append({
                    "train_code": t.train_code,
                    "train_name": t.train_name,
                    "dep_date": t.dep_date,
                    "dep_time": t.dep_time,
                    "arr_date": t.arr_date,
                    "arr_time": t.arr_time,
                    "dep_station_name": t.dep_station_name,
                    "arr_station_name": t.arr_station_name,
                    "seat_avaiable": "예약가능" if t.general_seat_available() else "매진",
                    # 예약에 필요한 필드들
                    "train_number": t.train_number,
                    "run_date": t.dep_date, 
                    "dep_station_code": t.dep_station_code,
                    "arr_station_code": t.arr_station_code,
                    "dep_station_constitution_order": t.dep_station_constitution_order,
                    "arr_station_constitution_order": t.arr_station_constitution_order,
                    "dep_station_run_order": t.dep_station_run_order,
                    "arr_station_run_order": t.arr_station_run_order,
                    "special_seat_state": t.special_seat_state,
                    "general_seat_state": t.general_seat_state,
                })
            session["search_results"] = json.dumps(train_list_for_session, ensure_ascii=False)
        except Exception as e:
            session["search_results"] = "[]"

        return redirect(url_for("reserve_page"))

    return render_template_string(schedule_template, stations=STATION_LIST)

@app.route("/reserve", methods=["GET"])
def reserve_page():
    if "srt_id" not in session or "srt_pw" not in session:
        return redirect(url_for("login"))

    error_message = None
    trains_with_index = None

    search_results_json = session.get("search_results", "[]")
    try:
        train_list = json.loads(search_results_json)
        trains_with_index = list(zip(train_list, range(len(train_list))))
    except Exception as e:
        error_message = str(e)

    return render_template_string(
        reserve_template,
        error_message=error_message,
        trains_with_index=trains_with_index
    )

@app.route("/reserve_select", methods=["POST"])
def reserve_select():
    selected_indices = request.form.getlist("train_indices")
    session["selected_indices"] = json.dumps(selected_indices)
    return "OK"

@app.route("/start_reservation", methods=["GET"])
def start_reservation():
    global STOP_MACRO
    STOP_MACRO = False

    srt_id = session.get("srt_id")
    srt_pw = session.get("srt_pw")
    search_json = session.get("search_results")
    selected_json = session.get("selected_indices")

    def sse_stream():
        if not srt_id or not srt_pw:
            yield "data: 로그인 정보가 없습니다.\n\n"
            return

        srt_client = SRT(srt_id, srt_pw, auto_login=False)
        try:
            srt_client.login(srt_id, srt_pw)
        except Exception as e:
            yield f"data: 로그인 실패: {str(e)}\n\n"
            return

        if not search_json or not selected_json:
            yield "data: 예약할 열차 정보가 없습니다.\n\n"
            return

        try:
            train_data_list = json.loads(search_json)
            selected_indices = json.loads(selected_json)
        except json.JSONDecodeError as e:
            yield f"data: 예약할 열차 정보 파싱 오류: {str(e)}\n\n"
            return

        # 선택된 인덱스에 해당하는 열차 정보를 pending_trains 리스트에 저장 (라운드로빈 예약 시도)
        pending_trains = []
        for idx_str in selected_indices:
            try:
                idx = int(idx_str)
                data = train_data_list[idx]
            except (IndexError, ValueError) as e:
                yield f"data: 잘못된 열차 인덱스: {str(e)}\n\n"
                continue

            train_dict = {
                "stlbTrnClsfCd": "17",  # SRT=17
                "trnNo": data["train_number"],
                "runDt": data["run_date"],
                "dptRsStnCd": data["dep_station_code"],
                "dptDt": data["dep_date"],
                "dptTm": data["dep_time"],
                "arvRsStnCd": data["arr_station_code"],
                "arvDt": data["arr_date"],
                "arvTm": data["arr_time"],
                "gnrmRsvPsbStr": "예약가능" if data.get("general_seat_available") else "매진",
                "sprmRsvPsbStr": "예약가능" if data.get("special_seat_available") else "매진",
                "rsvWaitPsbCd": "9" if data.get("reserve_standby_available") else "0",
                "arvStnRunOrdr": data.get("arr_station_run_order", "000"),
                "arvStnConsOrdr": data.get("arr_station_constitution_order", "000"),
                "dptStnRunOrdr": data.get("dep_station_run_order", "000"),
                "dptStnConsOrdr": data.get("dep_station_constitution_order", "000"),
            }

            train_obj = SRTTrain(train_dict)
            pending_trains.append({
                "data": data,
                "train_obj": train_obj,
                "attempt_count": 0,
                "reserved": False,
            })

        if not pending_trains:
            yield "data: 선택된 예약 열차 정보가 없습니다.\n\n"
            return

        # 모든 선택된 열차에 대해 라운드 로빈 방식으로 예약 시도
        while any(not item["reserved"] for item in pending_trains):
            if STOP_MACRO:
                yield "data: 사용자에 의해 중단됨\n\n"
                return

            for item in pending_trains:
                if item["reserved"]:
                    continue

                item["attempt_count"] += 1
                try:
                    updated_trains = srt_client.search_train(
                        dep=item["data"]['dep_station_name'],
                        arr=item["data"]['arr_station_name'],
                        date=item["data"]['dep_date'],
                        time=item["data"]['dep_time'],
                        available_only=False
                    )

                    matching_trains = [
                        t for t in updated_trains
                        if (t.train_number == item["train_obj"].trnNo and
                            t.dep_time == item["train_obj"].dptTm and
                            t.run_date == item["train_obj"].runDt)
                    ]

                    if not matching_trains:
                        yield f"data: [{item['train_obj'].dptTm}] (시도 {item['attempt_count']}회) 열차 정보 업데이트 실패, 2초 후 재검색...\n\n"
                        continue

                    current_train = matching_trains[0]

                    if len(matching_trains) > 1:
                        yield f"data: [{item['train_obj'].dptTm}] 매칭된 열차가 여러 개 있습니다. 첫 번째 열차로 예약 시도합니다.\n\n"

                    if not current_train.general_seat_available():
                        yield f"data: [{item['train_obj'].dptTm}] (시도 {item['attempt_count']}회) 일반 좌석 매진, 2초 후 재검색...\n\n"
                        continue

                    # 예약 시도
                    reservation = srt_client.reserve(current_train)
                    yield f"data: [{current_train.dptTm}] (시도 {item['attempt_count']}회) 예약 성공!\n\n"
                    item["reserved"] = True

                except SRTError:
                    yield f"data: [{item['train_obj'].dptTm}] (시도 {item['attempt_count']}회) 매진, 5초 후 재검색\n\n"
                except SRTNotLoggedInError:
                    yield "data: NeedToLoginError: 재로그인 필요\n\n"
                    return
                except Exception as e:
                    yield f"data: 오류 발생: {str(e)}\n\n"
                    return
            time.sleep(2)

        yield "data: 모든 열차 예약 시도 완료\n\n"

    return Response(sse_stream(), mimetype="text/event-stream")

@app.route("/stop_macro", methods=["POST"])
def stop_macro():
    global STOP_MACRO
    STOP_MACRO = True
    return "STOP_OK"

if __name__ == "__main__":
    import webbrowser
    from threading import Thread

    def open_browser():
        webbrowser.open("http://127.0.0.1:5001/")

    Thread(target=open_browser).start()
    app.run(debug=True, threaded=True, port=5001)
