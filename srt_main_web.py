# -*- coding: utf-8 -*-
import time
import json
import os
import logging
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, session, Response

from SRT.srt import SRT, SRTError, SRTNotLoggedInError
from SRT.train import SRTTrain
from SRT.constants import STATION_NAME
from SRT.seat_type import SeatType  # ← enum 가져오기

from logging.handlers import RotatingFileHandler

# ── 로깅 세팅 ─────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
handler = RotatingFileHandler('srt_app.log', maxBytes=1_000_000, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(handler)

# ── Flask 앱 초기화 ───────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_secret_key")

STOP_MACRO = False

# ── 역 목록: constants.STATION_NAME 전체 값 사용 ─────────────────────────
STATION_LIST = sorted(STATION_NAME.values())

# ── 메인 템플릿 (로그인, 검색, 결과&예약 통합) ───────────────────────────
main_template = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>SRT 예약 서비스</title>
  <style>
    body { font-family: sans-serif; }
    .error { color: red; }
    table { border-collapse: collapse; width:100%; }
    th, td { border:1px solid #ddd; padding:6px; text-align:center; }
    th { background:#f2f2f2; }
    input[type=checkbox]{transform:scale(1.4);margin-right:6px;}
    tr.selected-row{background:#ffffcc;}
    .log-container{border:1px solid #ccc;height:200px;overflow-y:auto;margin:10px 0;padding:6px;}
    .log-line{margin:2px 0;font-size:0.9em;}
    .hidden{display:none;}
    .form-section{margin-bottom:20px;}
    .swap-btn{margin:0 6px;}
    .available{color:green;font-weight:bold;}
    .unavailable{color:red;font-weight:bold;}
    .sound-options{margin:10px 0;}
    .sound-options label{margin-right:15px;}
  </style>
</head>
<body>
  {% if not session.get('srt_id') %}
    <h1>SRT 로그인</h1>
    <form method="POST">
      아이디: <input type="text" name="srt_id" required><br><br>
      비밀번호: <input type="password" name="srt_pw" required><br><br>
      <button type="submit">로그인</button>
    </form>
    {% if error_message %}<p class="error">{{ error_message }}</p>{% endif %}
  {% else %}
    <form action="{{ url_for('logout') }}" method="POST" style="float:right;">
      <button type="submit">로그아웃</button>
    </form>
    <h1>SRT 검색 & 예약</h1>

    <div class="form-section">
      <form method="POST">
        출발역:
        <select name="dep">
          {% for st in stations %}
            <option value="{{ st }}"
              {% if form_data and form_data.dep==st %}
                selected
              {% elif not form_data and st=='수서' %}
                selected
              {% endif %}>{{ st }}</option>
          {% endfor %}
        </select>
        <button type="button" id="swapBtn" class="swap-btn">⇄</button>
        도착역:
        <select name="arr">
          {% for st in stations %}
            <option value="{{ st }}"
              {% if form_data and form_data.arr==st %}
                selected
              {% elif not form_data and st=='순천' %}
                selected
              {% endif %}>{{ st }}</option>
          {% endfor %}
        </select>
        날짜:
        <input type="date" name="date" value="{{ form_data.date if form_data else default_date }}" required>
        시간:
        <select name="time">
          {% for h in range(24) %}
            {% set hh = "%02d:00"|format(h) %}
            <option value="{{ hh }}"
              {% if form_data and form_data.time==hh %}
                selected
              {% elif not form_data and hh=='00:00' %}
                selected
              {% endif %}>{{ "%02d시"|format(h) }}</option>
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
            <option value="GENERAL_FIRST">일반석 우선</option>
            <option value="GENERAL_ONLY">일반석만</option>
            <option value="SPECIAL_FIRST">특실 우선</option>
            <option value="SPECIAL_ONLY">특실만</option>
          </select>
        </label>

        <table id="resultsTable">
          <thead><tr>
            <th>선택</th><th>열차명</th><th>출발</th><th>도착</th>
            <th>출발역</th><th>도착역</th>
            <th>특실 상태</th><th>일반실 상태</th>
          </tr></thead>
          <tbody>
            {% for train, idx in trains_with_index %}
            <tr>
              <td><input type="checkbox" name="train_indices[]" value="{{ idx }}"></td>
              <td>{{ train.train_name }}</td>
              <td>{{ train.dep_date[:4] }}-{{ train.dep_date[4:6] }}-{{ train.dep_date[6:8] }} {{ train.dep_time[:2] }}:{{ train.dep_time[2:4] }}</td>
              <td>{{ train.arr_date[:4] }}-{{ train.arr_date[4:6] }}-{{ train.arr_date[6:8] }} {{ train.arr_time[:2] }}:{{ train.arr_time[2:4] }}</td>
              <td>{{ train.dep_station_name }}</td>
              <td>{{ train.arr_station_name }}</td>
              <td class="{{ 'available' if train.special_seat_available else 'unavailable' }}">{{ '예약가능' if train.special_seat_available else '매진' }}</td>
              <td class="{{ 'available' if train.general_seat_available else 'unavailable' }}">{{ '예약가능' if train.general_seat_available else '매진' }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>

        <div class="sound-options">
          <label><input type="radio" name="sound_mode" value="once" checked> 소리 1회</label>
          <label><input type="radio" name="sound_mode" value="repeat"> 10초마다</label>
          <button type="button" id="ackBtn">ACK</button>
        </div>

        <button type="button" id="startBtn">예약시작</button>
      </form>

      <div id="logSection" class="hidden">
        <h2>예약 로그</h2>
        <div class="log-container" id="logContainer"></div>
        <button id="stopBtn">중지</button>
      </div>
      <audio id="successSound" src="{{ url_for('static', filename='success_sound.mp3') }}" preload="auto"></audio>

      <script>
        function log(txt){
          const p = document.createElement('p');
          p.className = 'log-line';
          p.textContent = `[${new Date().toLocaleTimeString()}] ${txt}`;
          const lc = document.getElementById('logContainer');
          lc.appendChild(p);
          lc.scrollTop = lc.scrollHeight;
        }

        // swap
        document.getElementById('swapBtn').onclick = () => {
          const d = document.querySelector('select[name="dep"]'),
                a = document.querySelector('select[name="arr"]'),
                tmp = d.value;
          d.value = a.value;
          a.value = tmp;
        };

        // row click → checkbox toggle + highlight
        document.querySelectorAll('#resultsTable tbody tr').forEach(row => {
          row.onclick = e => {
            if (e.target.type !== 'checkbox') {
              const cb = row.querySelector('input[type="checkbox"]');
              cb.checked = !cb.checked;
              row.classList.toggle('selected-row', cb.checked);
            }
          };
        });
        document.querySelectorAll('input[name="train_indices[]"]').forEach(cb => {
          cb.onchange = () => cb.closest('tr').classList.toggle('selected-row', cb.checked);
        });

        // sound / ack
        let soundMode = 'once', acked = false, repeatId = null;
        document.getElementsByName('sound_mode').forEach(r => r.onchange = () => {
          soundMode = r.value; acked = false;
          clearInterval(repeatId); repeatId = null;
          log(`[설정] 소리 모드 → ${soundMode}`);
        });
        document.getElementById('ackBtn').onclick = () => {
          acked = true; clearInterval(repeatId); repeatId = null;
          log('[ACK] 소리 중지');
        };

        // 예약 시작
        let es = null;
        document.getElementById('startBtn').onclick = async () => {
          acked = false; clearInterval(repeatId); repeatId = null;
          if (es) es.close();
          const sel = document.querySelectorAll('input[name="train_indices[]"]:checked');
          if (!sel.length) return;
          const fd = new FormData();
          fd.append('reserve_option', document.getElementById('reserveOption').value);
          sel.forEach(c => fd.append('train_indices[]', c.value));
          let r = await fetch('/reserve_select',{method:'POST',body:fd});
          if (!r.ok) { alert('준비 오류'); return; }
          document.getElementById('logSection').classList.remove('hidden');
          document.getElementById('logContainer').innerHTML = '';
          es = new EventSource('/start_reservation');
          es.onmessage = e => {
            log(e.data);
            if (e.data.includes('예약 성공')) {
              if (soundMode==='once' && !acked) {
                document.getElementById('successSound').play(); log('성공음');
                acked = true;
              } else if (soundMode==='repeat' && !acked) {
                document.getElementById('successSound').play(); log('성공음');
                if (!repeatId) repeatId = setInterval(()=>{
                  if (!acked) { document.getElementById('successSound').play(); log('성공음'); }
                }, 10000);
              }
            }
          };
          es.onerror = _ => es.close();
        };

        // 중지
        document.getElementById('stopBtn').onclick = async () => {
          if (es) es.close();
          await fetch('/stop_macro',{method:'POST'});
          log('[사용자] 중단 요청');
        };
      </script>
    {% endif %}
  {% endif %}
</body>
</html>
"""

# ── Flask 라우트 ─────────────────────────────────────────────────────────

@app.route("/", methods=["GET","POST"])
def main():
    error_message=None
    trains_with_index=None
    form_data=None
    default_date = datetime.now().strftime("%Y-%m-%d")

    # 로그인
    if request.method=="POST" and not session.get('srt_id'):
        sid, spw = request.form['srt_id'], request.form['srt_pw']
        cli = SRT(sid, spw, auto_login=False)
        try:
            cli.login(sid, spw)
            session['srt_id'], session['srt_pw'] = sid, spw
            logging.info(f"{sid} 로그인 성공")
            return redirect(url_for('main'))
        except Exception as e:
            error_message=str(e)
            logging.error(f"로그인 실패: {error_message}")

    # 검색
    if session.get('srt_id') and request.method=="POST":
        form_data = request.form
        dep, arr = form_data['dep'], form_data['arr']
        dt, tm   = form_data['date'], form_data['time']
        date_str = dt.replace('-','')
        time_str = f"{int(tm.split(':')[0]):02d}0000"
        cli = SRT(session['srt_id'], session['srt_pw'], auto_login=False)
        try:
            cli.login()
            ts = cli.search_train(dep, arr, date=date_str, time=time_str, available_only=False)
            results=[]
            for t in ts:
                results.append({
                    'train_name': t.train_name,
                    'dep_date': t.dep_date, 'dep_time': t.dep_time,
                    'arr_date': t.arr_date, 'arr_time': t.arr_time,
                    'dep_station_name': t.dep_station_name,
                    'arr_station_name': t.arr_station_name,
                    'special_seat_available': t.special_seat_available(),
                    'general_seat_available': t.general_seat_available(),
                    'train_number': t.train_number,
                    'run_date': t.dep_date,
                    'dep_station_code': t.dep_station_code,
                    'arr_station_code': t.arr_station_code
                })
            session['search_results'] = json.dumps(results, ensure_ascii=False)
            trains_with_index = list(zip(results, range(len(results))))
            logging.info(f"스케줄 검색: {len(results)}개")
        except Exception as e:
            error_message=str(e)
            logging.error(f"검색 오류: {error_message}")

    # 이전 검색 결과 유지
    if session.get('search_results') and not trains_with_index:
        try:
            lst = json.loads(session['search_results'])
            trains_with_index = list(zip(lst, range(len(lst))))
        except:
            trains_with_index=None

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
    idxs = request.form.getlist('train_indices[]')
    session['selected_indices'] = json.dumps(idxs)
    session['reserve_option'] = request.form.get('reserve_option','GENERAL_FIRST')
    logging.info(f"선택 인덱스: {idxs}")
    return "OK"

@app.route("/start_reservation", methods=["GET"])
def start_reservation():
    global STOP_MACRO
    STOP_MACRO = False

    sid = session.get('srt_id')
    spw = session.get('srt_pw')
    sr  = session.get('search_results')
    si  = session.get('selected_indices')
    opt_enum = SeatType[session.get('reserve_option', 'GENERAL_FIRST')]

    def sse():
        # 로그인/파라미터 체크
        if not sid or not spw:
            yield "data: 로그인 필요\n\n"; return
        if not sr or not si:
            yield "data: 예약할 데이터가 없습니다\n\n"; return

        cli = SRT(sid, spw, auto_login=False)
        cli.login()

        raw   = json.loads(sr)
        idxs  = json.loads(si)

        # active 리스트에 각 열차 객체와 상태 저장
        active = []
        for s in idxs:
            d = raw[int(s)]
            tr = SRTTrain({
                'stlbTrnClsfCd':'17','trnNo':d['train_number'],
                'dptDt':d['dep_date'],   'dptTm':d['dep_time'],
                'arvDt':d['arr_date'],   'arvTm':d['arr_time'],
                'dptRsStnCd':d['dep_station_code'],
                'arvRsStnCd':d['arr_station_code'],
                'gnrmRsvPsbStr':'예약가능' if d['general_seat_available'] else '매진',
                'sprmRsvPsbStr':'예약가능' if d['special_seat_available'] else '매진',
                'rsvWaitPsbCd':'0',
                'arvStnRunOrdr':'000','arvStnConsOrdr':'000',
                'dptStnRunOrdr':'000','dptStnConsOrdr':'000'
            })
            active.append({'train':tr, 'raw':d, 'attempts':0, 'done':False})

        yield "data: ▶ 예약 시작 (첫 성공 시 즉시 종료)\n\n"

        # 라운드 로빈 반복
        while not STOP_MACRO:
            all_done = True

            for info in active:
                if info['done']:
                    continue
                all_done = False

                tr = info['train']
                d  = info['raw']
                info['attempts'] += 1
                cnt = info['attempts']

                # 1) 최신 상태 조회
                try:
                    ups = cli.search_train(
                        d['dep_station_name'], d['arr_station_name'],
                        date=d['dep_date'], time=d['dep_time'],
                        available_only=False
                    )
                    cur = next((x for x in ups
                                if x.train_number==tr.train_number
                                and x.dep_time==tr.dep_time), None)
                except Exception as e:
                    yield f"data: [{tr.dep_time}] {tr.train_name} 검색 오류: {e}\n\n"
                    time.sleep(1)
                    continue

                # 2) 예약 가능 여부 & 시도
                if cur:
                    try:
                        cli.reserve(cur, special_seat=opt_enum)
                        yield f"data: [{tr.dep_time}] {tr.train_name} 예약 성공 ({cnt}회)\n\n"
                        # 첫 성공 시 전체 종료
                        yield "data: 첫 성공으로 전체 종료\n\n"
                        return
                    except SRTError as e:
                        msg = str(e)
                        if "잔여석없음" in msg:
                            yield f"data: [{tr.dep_time}] {tr.train_name} 매진({cnt}회)—잔여석없음, 1초 후 재시도\n\n"
                            time.sleep(1)
                            continue
                        else:
                            yield f"data: [{tr.dep_time}] {tr.train_name} 예약 오류: {msg}\n\n"
                            return
                    except SRTNotLoggedInError:
                        yield "data: 재로그인 필요\n\n"
                        return
                else:
                    yield f"data: [{tr.dep_time}] {tr.train_name} 매진 ({cnt}회)\n\n"

                time.sleep(1)

            if all_done:
                break

        # STOP_MACRO 되거나 모든 done
        if STOP_MACRO:
            yield "data: 예약이 중단되었습니다.\n\n"
        else:
            yield "data: 모든 열차 시도 완료—예약 실패\n\n"

    return Response(sse(), mimetype="text/event-stream")



@app.route("/stop_macro", methods=["POST"])
def stop_macro():
    global STOP_MACRO; STOP_MACRO=True
    logging.info("중단 요청")
    return "STOP_OK"

if __name__=="__main__":
    app.run(debug=True, threaded=True, port=5001)
