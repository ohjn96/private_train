# -*- coding: utf-8 -*-
import time
import json
from flask import Flask, request, render_template_string, redirect, url_for, session, Response
from korail2.korail2 import Korail, Train, AdultPassenger, SoldOutError, NeedToLoginError

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"

###########################
# 전역 변수: 예약 중단 플래그
###########################
STOP_MACRO = False

###########################
# 역 리스트
###########################
STATION_LIST = [
    "서울", "용산", "광명", "천안아산", "오송", "대전", "김천(구미)", "신경주",
    "울산(통도사)", "부산", "공주", "익산", "정읍", "광주송정", "목포", "전주",
    "남원", "순천", "여천", "여수엑스포", "청량리", "양평", "원주", "제천",
    "단양", "풍기", "영주", "안동", "창원중앙", "창원", "마산", "진주", "홍성",
    "군산", "강릉", "만종", "둔내", "평창", "진부", "포항"
]

###########################
# (1) 로그인 페이지 템플릿
###########################
login_template = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>로그인</title>
  <style>.error { color: red; }</style>
</head>
<body>
  <h1>코레일 로그인</h1>
  <form method="POST">
    아이디: <input type="text" name="korail_id" required><br><br>
    비밀번호: <input type="password" name="korail_pw" required><br><br>
    <button type="submit">로그인</button>
  </form>
  {% if error_message %}
    <p class="error">{{ error_message }}</p>
  {% endif %}
</body>
</html>
"""

###########################
# (2) 스케줄 페이지 템플릿
###########################
schedule_template = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>기차 스케줄</title>
</head>
<body>
  <h1>기차 스케줄 폼</h1>
  <form method="POST">
    출발역:
    <select name="dep">
      {% for station in stations %}
        <option value="{{ station }}">{{ station }}</option>
      {% endfor %}
    </select>
    <br><br>
    도착역:
    <select name="arr">
      {% for station in stations %}
        <option value="{{ station }}">{{ station }}</option>
      {% endfor %}
    </select>
    <br><br>
    연도:
    <select name="year">
      {% for y in [2025] %}
        <option value="{{ y }}">{{ y }}</option>
      {% endfor %}
    </select>
    <br><br>
    월:
    <select name="month">
      {% for m in range(1,13) %}
        <option value="{{ m }}">{{ m }}</option>
      {% endfor %}
    </select>
    <br><br>
    일:
    <select name="day">
      {% for d in range(1,32) %}
        <option value="{{ d }}">{{ d }}</option>
      {% endfor %}
    </select>
    <br><br>
    시간(0~23):
    <select name="hour">
      {% for h in range(24) %}
        <option value="{{ h }}">{{ h }}</option>
      {% endfor %}
    </select>
    <br><br>
    <button type="submit">검색하기</button>
  </form>
</body>
</html>
"""

###########################
# (3) 검색테이블 + 예약 페이지(SSE 로그)
###########################
reserve_template = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>예약 페이지</title>
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
  <h1>검색 결과 & 예약</h1>

  <!-- 뒤로가기: /schedule -->
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
            <th>열차종류</th>
            <th>출발</th>
            <th>도착</th>
            <th>출발역</th>
            <th>도착역</th>
            <th>좌석여부</th>
            <th>가격</th>
          </tr>
        </thead>
        <tbody>
        {% for train, idx in trains_with_index %}
          <tr>
            <td><input type="checkbox" name="train_indices" value="{{ idx }}"></td>
            <td>{{ train.train_type_name }}</td>
            <td>{{ train.dep_date[0:4] }}-{{ train.dep_date[4:6] }}-{{ train.dep_date[6:8] }} {{ train.dep_time[0:2] }}:{{ train.dep_time[2:4] }}</td>
            <td>{{ train.arr_date[0:4] }}-{{ train.arr_date[4:6] }}-{{ train.arr_date[6:8] }} {{ train.arr_time[0:2] }}:{{ train.arr_time[2:4] }}</td>
            <td>{{ train.dep_name }}</td>
            <td>{{ train.arr_name }}</td>
            <td>{{ train.reserve_possible }}</td>
            <td>{{ train.reserve_possible_name }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <br>
      <button type="button" id="startBtn">예약하기</button>
    </form>
  {% else %}
    <p>검색 결과가 없습니다. /schedule 로 가서 검색하세요.</p>
  {% endif %}

  <!-- 로그 영역 (SSE) -->
  <div id="logSection" class="hidden">
    <h2>예약 로그</h2>
    <div class="log-container" id="logContainer"></div>
    <button id="stopBtn">멈춤</button>
  </div>

  <!-- 비디오 요소 추가 -->
  <video id="successVideo" src="{{ url_for('static', filename='success_sound.mp3') }}" preload="auto" style="display:none;"></video>

  <script>
    const reserveForm = document.getElementById("reserveForm");
    const startBtn = document.getElementById("startBtn");
    const logSection = document.getElementById("logSection");
    const logContainer = document.getElementById("logContainer");
    const stopBtn = document.getElementById("stopBtn");
    const successVideo = document.getElementById("successVideo"); // 비디오 요소 참조


    let evtSource = null;

    if(startBtn) {
      startBtn.addEventListener("click", async function() {
        // 체크된 값 확인
        const checked = reserveForm.querySelectorAll('input[name="train_indices"]:checked');
        if(checked.length === 0) {
          // 아무것도 선택안하면 무반응
          return; 
        }
        // 서버에 인덱스 전달
        const formData = new FormData();
        checked.forEach(cb => {
          formData.append("train_indices", cb.value);
        });
        // POST /reserve_select
        let resp = await fetch("/reserve_select", {
          method: "POST",
          body: formData
        });
        if(!resp.ok) {
          alert("예약 준비 중 오류 발생");
          return;
        }

        // SSE 시작
        logSection.classList.remove("hidden");
        logContainer.innerHTML = "";

        evtSource = new EventSource("/start_reservation");
        evtSource.onmessage = function(e) {
          const p = document.createElement("p");
          p.className = "log-line";
          p.textContent = e.data;
          logContainer.appendChild(p);
          logContainer.scrollTop = logContainer.scrollHeight;

          // 성공 메시지 감지 및 비디오 재생
          if (e.data.includes("예약 성공")) {
            successVideo.currentTime = 0; // 비디오의 시작점으로 되돌림
            successVideo.play();
          }
        };
        evtSource.onerror = function(e) {
          evtSource.close();
        };
      });
    }

    if(stopBtn) {
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
    """로그인 페이지"""
    error_message = None
    if request.method == "POST":
        user_id = request.form.get("korail_id")
        user_pw = request.form.get("korail_pw")

        korail = Korail(korail_id=user_id, korail_pw=user_pw, auto_login=False)
        success = korail.login()

        if success:
            session["korail_id"] = user_id
            session["korail_pw"] = user_pw
            return redirect(url_for("schedule"))  # 로그인 성공 후 스케줄 페이지로
        else:
            error_message = "로그인 실패. 아이디/비번 확인해주세요."

    return render_template_string(login_template, error_message=error_message)

@app.route("/schedule", methods=["GET", "POST"])
def schedule():
    """(2) 스케줄 폼 페이지
    GET => 폼만
    POST => 검색 실행 -> 세션에 저장 -> /reserve로 이동
    """
    if "korail_id" not in session:
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

        try:
            korail = Korail(session["korail_id"], session["korail_pw"], auto_login=False)
            korail.login()

            trains = korail.search_train(
                dep, arr, date=date_str, time=time_str, include_no_seats=True
            )


            # 검색 결과 JSON 저장
            train_list_for_session = []
            for t in trains:
                train_list_for_session.append({
                    "train_type": t.train_type,
                    "train_type_name": t.train_type_name,
                    "train_group": t.train_group,
                    "train_no": t.train_no,
                    "dep_name": t.dep_name,
                    "dep_code": t.dep_code,
                    "dep_date": t.dep_date,
                    "dep_time": t.dep_time,
                    "arr_name": t.arr_name,
                    "arr_code": t.arr_code,
                    "arr_date": t.arr_date,
                    "arr_time": t.arr_time,
                    "run_date": t.run_date,
                    "reserve_possible": t.reserve_possible,
                    "reserve_possible_name": t.reserve_possible_name,
                    "special_seat": t.special_seat,
                    "general_seat": t.general_seat,
                })
            session["search_results"] = json.dumps(train_list_for_session, ensure_ascii=False)
        except Exception as e:
            # 검색 실패 -> 세션에 빈 데이터
            session["search_results"] = "[]"
            print("검색오류:", e)

        return redirect(url_for("reserve_page"))

    # GET
    return render_template_string(schedule_template, stations=STATION_LIST)

@app.route("/reserve", methods=["GET"])
def reserve_page():
    """(3) 검색테이블 + 예약 페이지
    GET => 세션에 있는 검색 결과를 테이블로 표시
    """
    if "korail_id" not in session:
        return redirect(url_for("login"))

    error_message = None
    trains_with_index = None

    # 세션에서 검색 결과 가져오기
    search_results_json = session.get("search_results", "[]")

    try:
        train_list = json.loads(search_results_json)
        # Train 객체로 변환할 필요 없이, 보여줄 필드만 사용 -> 임시로 Fake Train
        # SSE 예약 시에는 start_reservation에서 Train(data)로 복원
        trains_with_index = list(zip(train_list, range(len(train_list))))
    except Exception as e:
        error_message = f"결과 파싱 오류: {str(e)}"

    return render_template_string(
        reserve_template,
        error_message=error_message,
        trains_with_index=trains_with_index
    )

@app.route("/reserve_select", methods=["POST"])
def reserve_select():
    """자바스크립트에서 체크된 인덱스를 받아 세션에 저장"""
    selected_indices = request.form.getlist("train_indices")
    session["selected_indices"] = json.dumps(selected_indices)
    return "OK"

@app.route("/start_reservation", methods=["GET"])
def start_reservation():
    """SSE 스트림으로 매크로 예약 진행"""
    global STOP_MACRO
    STOP_MACRO = False

    user_id = session.get("korail_id")
    user_pw = session.get("korail_pw")
    sr_json = session.get("search_results")
    si_json = session.get("selected_indices")

    def sse_stream():
        if not user_id or not user_pw:
            yield "data: 로그인 정보가 없습니다.\n\n"
            return

        korail = Korail(user_id, user_pw, auto_login=False)
        korail.login()

        if not sr_json or not si_json:
            yield "data: 예약할 열차 정보가 없습니다.\n\n"
            return

        train_data_list = json.loads(sr_json)
        selected_indices = json.loads(si_json)

        for idx_str in selected_indices:
            if STOP_MACRO:
                yield "data: 사용자에 의해 중단됨\n\n"
                return

            idx = int(idx_str)
            data = train_data_list[idx]
            fixed_data = {
                'h_trn_clsf_cd': data['train_type'],
                'h_trn_clsf_nm': data['train_type_name'],
                'h_trn_gp_cd': data['train_group'],
                'h_trn_no': data['train_no'],
                'h_dpt_rs_stn_nm': data['dep_name'],
                'h_dpt_rs_stn_cd': data['dep_code'],
                'h_dpt_dt': data['dep_date'],
                'h_dpt_tm': data['dep_time'],
                'h_arv_rs_stn_nm': data['arr_name'],
                'h_arv_rs_stn_cd': data['arr_code'],
                'h_arv_dt': data['arr_date'],
                'h_arv_tm': data['arr_time'],
                'h_run_dt': data['run_date'],

                'h_rsv_psb_flg': data['reserve_possible'],
                'h_rsv_psb_nm': data['reserve_possible_name'],
                'h_spe_rsv_cd': data['special_seat'],
                'h_gen_rsv_cd': data['general_seat'],
            }

            train_obj = Train(fixed_data)

            attempt_count = 0
            while True:
                if STOP_MACRO:
                    yield "data: 사용자에 의해 중단됨\n\n"
                    return

                attempt_count += 1
                try:
                    # 열차 정보를 다시 검색하여 최신 상태 확인
                    updated_trains = korail.search_train(
                        dep=data['dep_name'],
                        arr=data['arr_name'],
                        date=data['dep_date'],
                        time=data['dep_time'],
                        include_no_seats=True
                    )

                    # 현재 열차의 상태 확인
                    current_train = None
                    for t in updated_trains:
                        if (t.train_type == train_obj.train_type and
                            t.train_no == train_obj.train_no and
                            t.dep_time == train_obj.dep_time):
                            current_train = t
                            break

                    if not current_train:
                        yield f"data: [{train_obj.dep_time}] (시도 {attempt_count}회) 열차 정보 업데이트 실패, 재검색 중...\n\n"
                        time.sleep(1)
                        continue

                    if not current_train.reserve_possible:
                        yield f"data: [{train_obj.dep_time}] (시도 {attempt_count}회) 좌석 매진, 1초 후 재검색...\n\n"
                        time.sleep(1)
                        continue

                    # 예약 시도
                    seat = korail.reserve(current_train, option="GENERAL_FIRST")
                    tickets = korail.tickets()
                    yield f"data: [{train_obj.dep_time}] (시도 {attempt_count}회) 예약 성공!\n\n"
                    break

                except SoldOutError:
                    yield f"data: [{train_obj.dep_time}] (시도 {attempt_count}회) 매진, 1초 후 재검색\n\n"
                    time.sleep(1)
                except NeedToLoginError:
                    yield "data: NeedToLoginError: 재로그인 필요\n\n"
                    return
                except Exception as e:
                    yield f"data: 오류 발생: {str(e)}\n\n"
                    return

        yield "data: 모든 열차 예약 시도 완료\n\n"

        yield "data: 모든 열차 예약 시도 완료\n\n"

    return Response(sse_stream(), mimetype="text/event-stream")

@app.route("/stop_macro", methods=["POST"])
def stop_macro():
    """멈춤"""
    global STOP_MACRO
    STOP_MACRO = True
    return "STOP_OK"

if __name__ == "__main__":
    import webbrowser

    # Flask 서버를 백그라운드 스레드에서 실행하도록 수정
    from threading import Thread

    def open_browser():
        webbrowser.open("http://127.0.0.1:5000/")

    Thread(target=open_browser).start()

    app.run(debug=True, threaded=True, port=5000)
