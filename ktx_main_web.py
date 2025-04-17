# -*- coding: utf-8 -*-
import time
import json
import urllib3
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, session, Response
from korail2.korail2 import Korail, Train, SoldOutError, NeedToLoginError

# HTTPS 경고 숨기기
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"

STOP_MACRO = False

STATION_LIST = [
    "서울","용산","광명","천안아산","오송","대전","김천(구미)","신경주",
    "울산(통도사)","부산","공주","익산","정읍","광주송정","목포","전주",
    "남원","순천","여천","여수엑스포","청량리","양평","원주","제천",
    "단양","풍기","영주","안동","창원중앙","창원","마산","진주","홍성",
    "군산","강릉","만종","둔내","평창","진부","포항","태화강"
]

main_template = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>코레일 예약 서비스</title>
  <style>
    .error { color: red; }
    table { border-collapse: collapse; width:100%; }
    th, td { border:1px solid #ddd; padding:6px; }
    th { background:#f2f2f2; }
    input[type=checkbox] { transform: scale(1.5); margin-right: 6px; }
    tr.selected-row { background-color: #ffffcc; }
    .log-container { border:1px solid #ccc; width:100%; height:300px; overflow-y:scroll; margin:10px 0; padding:6px; }
    .log-line { margin:2px 0; line-height:1.2; }
    .hidden { display:none; }
    .form-section { margin-bottom:20px; }
    .swap-btn { margin:0 10px; }
    .available { color:green; font-weight:bold; }
    .unavailable { color:red; font-weight:bold; }
    .sound-options { margin:10px 0; }
    .sound-options label { margin-right:15px; }
    #ackBtn { margin-left:20px; }
  </style>
</head>
<body>
  {% if not session.get('korail_id') %}
    <h1>코레일 로그인</h1>
    <form method="POST">
      아이디: <input type="text" name="korail_id" required><br><br>
      비밀번호: <input type="password" name="korail_pw" required><br><br>
      <button type="submit">로그인</button>
    </form>
    {% if error_message %}<p class="error">{{ error_message }}</p>{% endif %}
  {% else %}
    <form action="{{ url_for('logout') }}" method="POST" style="float:right;">
      <button type="submit">로그아웃</button>
    </form>
    <h1>열차 검색 & 예약</h1>

    <!-- 검색 폼 -->
    <div class="form-section">
      <form method="POST">
        출발역:
        <select name="dep">
          {% for s in stations %}
            <option value="{{ s }}"
              {% if form_data and form_data.dep==s %}selected{% elif not form_data and s=='용산' %}selected{% endif %}>
              {{ s }}
            </option>
          {% endfor %}
        </select>
        <button type="button" id="swapBtn" class="swap-btn">⇄</button>
        도착역:
        <select name="arr">
          {% for s in stations %}
            <option value="{{ s }}"
              {% if form_data and form_data.arr==s %}selected{% elif not form_data and s=='순천' %}selected{% endif %}>
              {{ s }}
            </option>
          {% endfor %}
        </select>
        날짜:
        <input type="date" name="date" value="{{ form_data.date if form_data else default_date }}" required>
        시간:
        <select name="time">
          {% for h in range(24) %}
            {% set hh = "%02d:00"|format(h) %}
            <option value="{{ hh }}"
              {% if form_data and form_data.time==hh %}selected{% elif not form_data and hh=='00:00' %}selected{% endif %}>
              {{ "%02d시"|format(h) }}
            </option>
          {% endfor %}
        </select>
        <button type="submit">검색하기</button>
      </form>
      {% if error_message %}<p class="error">{{ error_message }}</p>{% endif %}
    </div>

    {% if trains_with_index %}
      <h2>검색 결과</h2>
      <form id="reserveForm">
        <label>예약 옵션:
          <select id="reserveOption" name="reserve_option">
            <option value="GENERAL_FIRST">일반석 우선 예약 (경제성 우선)</option>
            <option value="GENERAL_ONLY">일반석만 예약 (가난뱅이)</option>
            <option value="SPECIAL_FIRST">특실 우선 예약 (편안함 우선)</option>
            <option value="SPECIAL_ONLY">특실만 예약 (부자 전용)</option>
          </select>
        </label>
        <table id="resultsTable">
          <thead><tr>
            <th>선택</th><th>열차종류</th><th>출발시간</th><th>도착시간</th>
            <th>출발역</th><th>도착역</th><th>좌석여부</th><th>가격</th>
            <th>특실</th><th>일반실</th>
          </tr></thead>
          <tbody>
            {% for train, idx in trains_with_index %}
            <tr>
              <td><input type="checkbox" name="train_indices[]" value="{{ idx }}"></td>
              <td>{{ train.train_type_name }}</td>
              <td>{{ train.dep_date[:4] }}-{{ train.dep_date[4:6] }}-{{ train.dep_date[6:8] }} {{ train.dep_time[:2] }}:{{ train.dep_time[2:4] }}</td>
              <td>{{ train.arr_date[:4] }}-{{ train.arr_date[4:6] }}-{{ train.arr_date[6:8] }} {{ train.arr_time[:2] }}:{{ train.arr_time[2:4] }}</td>
              <td>{{ train.dep_name }}</td>
              <td>{{ train.arr_name }}</td>
              <td class="{{ 'available' if train.reserve_possible=='Y' else 'unavailable' }}">
                {{ '예약가능' if train.reserve_possible=='Y' else '예약불가' }}
              </td>
              <td>{{ train.reserve_possible_name }}</td>
              <td>{% if train.special_seat=='11' %}예약가능{% elif train.special_seat=='13' %}매진{% else %}없음{% endif %}</td>
              <td>{% if train.general_seat=='11' %}예약가능{% elif train.general_seat=='13' %}매진{% else %}없음{% endif %}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>

        <!-- 소리 옵션 & ACK (결과 바로 아래) -->
        <div class="sound-options">
          <label><input type="radio" name="sound_mode" value="once" checked> 소리 1회</label>
          <label><input type="radio" name="sound_mode" value="repeat"> 10초마다 소리</label>
          <button type="button" id="ackBtn">ACK</button>
        </div>

        <button type="button" id="startBtn">예약하기</button>
      </form>

      <div id="logSection" class="hidden">
        <h2>예약 로그</h2>
        <div class="log-container" id="logContainer"></div>
        <button id="stopBtn">멈춤</button>
      </div>
      <audio id="successAudio" src="{{ url_for('static', filename='success_sound.mp3') }}" preload="auto"></audio>

      <script>
        function logEvent(text) {
          const p = document.createElement('p');
          p.className = 'log-line';
          p.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
          document.getElementById('logContainer').appendChild(p);
          document.getElementById('logContainer').scrollTop = document.getElementById('logContainer').scrollHeight;
        }

        // 스왑
        document.getElementById('swapBtn').onclick = () => {
          const d = document.querySelector('select[name="dep"]'), a = document.querySelector('select[name="arr"]');
          [d.value, a.value] = [a.value, d.value];
        };

        // 행 클릭으로 선택 토글 & 하이라이트
        document.querySelectorAll('#resultsTable tbody tr').forEach(row => {
          row.addEventListener('click', e => {
            if (e.target.type !== 'checkbox') {
              const cb = row.querySelector('input[type="checkbox"]');
              cb.checked = !cb.checked;
              row.classList.toggle('selected-row', cb.checked);
            }
          });
        });
        // 체크박스 직접 클릭 시에도 하이라이트
        document.querySelectorAll('input[name="train_indices[]"]').forEach(cb => {
          cb.addEventListener('change', () => {
            cb.closest('tr').classList.toggle('selected-row', cb.checked);
          });
        });

        const sb = document.getElementById('startBtn'),
              st = document.getElementById('stopBtn'),
              sa = document.getElementById('successAudio'),
              ackBtn = document.getElementById('ackBtn'),
              soundRadios = document.getElementsByName('sound_mode');
        let es = null, soundMode = 'once', acked = false, repeatId = null;

        soundRadios.forEach(r => r.onchange = () => {
          soundMode = r.value; acked = false;
          if (repeatId) clearInterval(repeatId), repeatId = null;
          logEvent(`소리 모드 변경: ${soundMode}`);
        });
        ackBtn.onclick = () => {
          acked = true;
          if (repeatId) clearInterval(repeatId), repeatId = null;
          logEvent('ACK 눌림: 소리 중지');
        };

        sb.onclick = async () => {
          acked = false;
          if (repeatId) clearInterval(repeatId), repeatId = null;
          if (es) es.close();

          const checked = document.querySelectorAll('input[name="train_indices[]"]:checked');
          if (!checked.length) return;
          const fd = new FormData();
          fd.append('reserve_option', document.getElementById('reserveOption').value);
          checked.forEach(c => fd.append('train_indices[]', c.value));

          const resp = await fetch('/reserve_select', { method:'POST', body:fd });
          if (!resp.ok) { alert('예약 준비 중 오류'); return; }

          document.getElementById('logSection').classList.remove('hidden');
          document.getElementById('logContainer').innerHTML = '';

          es = new EventSource('/start_reservation');
          es.onmessage = e => {
            logEvent(e.data);
            if (e.data.includes('예약 성공')) {
              if (soundMode==='once' && !acked) {
                sa.play(); logEvent('성공음 재생'); acked = true;
              } else if (soundMode==='repeat' && !acked) {
                sa.play(); logEvent('성공음 재생');
                if (!repeatId) repeatId = setInterval(() => {
                  if (!acked) { sa.play(); logEvent('성공음 재생'); }
                }, 10000);
              }
            }
          };
          es.onerror = () => es.close();
        };

        st.onclick = async () => {
          if (es) es.close();
          await fetch('/stop_macro',{method:'POST'});
          logEvent('예약 중단 요청');
        };
      </script>
    {% endif %}
  {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET","POST"])
def main():
    error_message = None
    trains_with_index = None
    form_data = None
    default_date = datetime.now().strftime("%Y-%m-%d")

    if request.method=="POST" and not session.get('korail_id'):
        uid, upw = request.form['korail_id'], request.form['korail_pw']
        kor = Korail(uid, upw, auto_login=False)
        if kor.login():
            session['korail_id'], session['korail_pw'] = uid, upw
            return redirect(url_for('main'))
        error_message = "로그인 실패: 아이디/비번 확인"

    if session.get('korail_id') and request.method=="POST":
        dep, arr = request.form['dep'], request.form['arr']
        dt, tm = request.form['date'], request.form['time']
        form_data = request.form
        date_str = dt.replace('-','')
        time_full = f"{int(tm.split(':')[0]):02d}0000"
        try:
            kor = Korail(session['korail_id'], session['korail_pw'], auto_login=False)
            kor.login()
            ts = kor.search_train(dep, arr, date=date_str, time=time_full, include_no_seats=True)
            results = [{
                'train_type': t.train_type,
                'train_type_name': t.train_type_name,
                'train_group': t.train_group,
                'train_no': t.train_no,
                'dep_name': t.dep_name,
                'dep_code': t.dep_code,
                'dep_date': t.dep_date,
                'dep_time': t.dep_time,
                'arr_name': t.arr_name,
                'arr_code': t.arr_code,
                'arr_date': t.arr_date,
                'arr_time': t.arr_time,
                'run_date': t.run_date,
                'reserve_possible': t.reserve_possible,
                'reserve_possible_name': t.reserve_possible_name,
                'special_seat': t.special_seat,
                'general_seat': t.general_seat
            } for t in ts]
            session['search_results'] = json.dumps(results, ensure_ascii=False)
            trains_with_index = list(zip(results, range(len(results))))
        except Exception as e:
            error_message = f"검색 오류: {e}"

    if session.get('search_results') and not trains_with_index:
        try:
            stored = json.loads(session['search_results'])
            trains_with_index = list(zip(stored, range(len(stored))))
        except:
            trains_with_index = None

    return render_template_string(
        main_template,
        error_message=error_message,
        stations=STATION_LIST,
        trains_with_index=trains_with_index,
        form_data=form_data,
        default_date=default_date
    )

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for('main'))

@app.route("/reserve_select", methods=["POST"])
def reserve_select():
    session['selected_indices'] = json.dumps(request.form.getlist('train_indices[]'))
    session['reserve_option'] = request.form.get('reserve_option')
    return "OK"

@app.route("/start_reservation", methods=["GET"])
def start_reservation():
    global STOP_MACRO
    STOP_MACRO = False

    uid = session.get('korail_id')
    upw = session.get('korail_pw')
    sr  = session.get('search_results')
    si  = session.get('selected_indices')
    opt = session.get('reserve_option', 'GENERAL_ONLY')

    def sse():
        # 1) 로그인/데이터 확인
        if not uid or not upw:
            yield "data: 로그인 정보 없음\n\n"
            return
        if not sr or not si:
            yield "data: 예약할 정보 없음\n\n"
            return

        # 2) Korail 객체 생성
        kor = Korail(uid, upw, auto_login=False)
        kor.login()

        # 3) JSON → 리스트, Train 객체로 래핑
        raw_list = json.loads(sr)
        idxs     = json.loads(si)
        active   = []
        for s in idxs:
            d = raw_list[int(s)]
            tr = Train({
                'h_trn_clsf_cd':   d['train_type'],
                'h_trn_clsf_nm':   d['train_type_name'],
                'h_trn_gp_cd':     d['train_group'],
                'h_trn_no':        d['train_no'],
                'h_dpt_rs_stn_nm': d['dep_name'],
                'h_dpt_rs_stn_cd': d['dep_code'],
                'h_dpt_dt':        d['dep_date'],
                'h_dpt_tm':        d['dep_time'],
                'h_arv_rs_stn_nm': d['arr_name'],
                'h_arv_rs_stn_cd': d['arr_code'],
                'h_arv_dt':        d['arr_date'],
                'h_arv_tm':        d['arr_time'],
                'h_run_dt':        d['run_date'],
                'h_rsv_psb_flg':   d['reserve_possible'],
                'h_rsv_psb_nm':    d['reserve_possible_name'],
                'h_spe_rsv_cd':    d['special_seat'],
                'h_gen_rsv_cd':    d['general_seat'],
            })
            active.append({'train': tr, 'raw': d, 'attempts': 0})

        # 4) 시작 메시지
        yield "data: ▶▶▶ 예약 시작 (한 사이클 당 1초씩, 첫 성공 시 즉시 종료)\n\n"

        # 5) 라운드로빈 반복
        while not STOP_MACRO:
            for info in active:
                if STOP_MACRO:
                    yield "data: 예약이 중단되었습니다.\n\n"
                    return

                tr = info['train']
                d  = info['raw']
                info['attempts'] += 1
                cnt = info['attempts']

                # 6) 최신 상태 가져오기
                try:
                    updated = kor.search_train(
                        dep=d['dep_name'],
                        arr=d['arr_name'],
                        date=d['dep_date'],
                        time=d['dep_time'],
                        include_no_seats=True
                    )
                    cur = next((x for x in updated
                                if x.train_type==tr.train_type
                                and x.train_no==tr.train_no
                                and x.dep_time==tr.dep_time), None)
                except Exception as e:
                    yield f"data: [{tr.train_type_name} {tr.dep_time}] 검색 오류: {e}\n\n"
                    time.sleep(1)
                    continue

                # 7) 예약 가능하면 시도
                if cur and cur.reserve_possible:
                    try:
                        kor.reserve(cur, option=opt)
                        yield f"data: [{tr.train_type_name} {tr.dep_time}] 예약 성공! (시도 {cnt}회)\n\n"
                        yield "data: 첫 성공으로 전체 예약 종료\n\n"
                        return
                    except SoldOutError:
                        yield f"data: [{tr.train_type_name} {tr.dep_time}] 매진 (시도 {cnt}회)\n\n"
                    except NeedToLoginError:
                        yield "data: NeedToLoginError: 재로그인 필요\n\n"
                        return
                    except Exception as e:
                        yield f"data: [{tr.train_type_name} {tr.dep_time}] 예약 오류: {e}\n\n"
                        return
                else:
                    yield f"data: [{tr.train_type_name} {tr.dep_time}] 매진 (시도 {cnt}회)\n\n"

                # 8) 다음 열차 전 1초 대기
                time.sleep(1)

            # 한 사이클 돌고도 성공 없으면 다시 처음부터…
            # (STOP_MACRO 체크는 while 조건에서)
        # STOP_MACRO == True 로 빠져나왔을 때
        yield "data: 예약이 중단되었습니다.\n\n"

    return Response(sse(), mimetype="text/event-stream")


@app.route("/stop_macro", methods=["POST"])
def stop_macro():
    global STOP_MACRO
    STOP_MACRO = True
    return "STOP_OK"
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=30000, debug=False)