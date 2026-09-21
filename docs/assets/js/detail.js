// 라이선스 상세 (detail.html?id=MACHINE-ID)
import { loadAll } from './api.js';
import { AUTORENEW_TRIGGER_DAYS, ISSUES_URL } from './config.js';
import { describe } from './model.js';
import { $, autoRenewChip, esc, mountTopbar, showError, statusChip, validityRail } from './ui.js';

mountTopbar('index.html');

const machineId = (new URLSearchParams(location.search).get('id') || '').toUpperCase();

function renderMissing() {
  $('body').innerHTML = `<div class="panel empty">
    <h3>해당 라이선스가 없습니다</h3>
    <p><code>${esc(machineId || '머신 ID 없음')}</code> 앞으로 발급된 기록을 찾지 못했습니다.
       발급된 적이 없거나, 파일이 삭제되었습니다.</p>
  </div>`;
}

function renderLicense(row) {
  document.title = `${row.machine_id} — 라이선스`;
  $('eyebrow').textContent = '라이선스 상세';

  const auto = row.autoRenew === null
    ? '꺼짐 · 만료되면 이슈로 알림이 옵니다'
    : `켜짐 · 만료 ${AUTORENEW_TRIGGER_DAYS}일 전에 ${row.autoRenew}일씩 연장`;

  const search = encodeURIComponent(`is:issue ${row.machine_id} in:title`);

  $('body').innerHTML = `
    <div class="panel ticket">
      <div class="stub">
        <div class="who">${esc(row.name || '이름 없음')}</div>
        <div class="id">${esc(row.machine_id)}</div>
        <div class="chips" style="margin-top:11px">${statusChip(row)}${autoRenewChip(row)}</div>
      </div>
      <div class="body">
        <dl class="facts">
          <dt>유효기간</dt>
          <dd>${validityRail(row)}</dd>

          <dt>만료일</dt>
          <dd class="mono">${esc(row.expiresOn)}${
            row.inactive ? '' : ` <span style="color:var(--ink-3)">· ${row.daysLeft}일 남음</span>`}</dd>

          ${row.issuedOn ? `<dt>발급일</dt><dd class="mono">${esc(row.issuedOn)}</dd>` : ''}

          <dt>라이선스 ID</dt>
          <dd class="mono">${esc(row.license_id)}</dd>

          <dt>자동 갱신</dt>
          <dd>${esc(auto)}</dd>
        </dl>
      </div>
    </div>

    <h2>이 머신에 쓸 수 있는 명령</h2>
    <div class="panel panel-pad">
      <p class="sub" style="margin:0 0 12px">
        이 머신의 이슈에 댓글로 남기세요. 알림 메일에 답장해도 똑같이 동작합니다.</p>
      <pre class="cmd"><b>/approve 30</b>      30일 연장
<b>/autorenew 30</b>    만료 ${AUTORENEW_TRIGGER_DAYS}일 전마다 30일씩 자동 연장
<b>/autorenew off</b>   자동 갱신 끄기
<b>/revoke</b>          이 PC 차단</pre>
      <p style="margin:14px 0 0">
        <a class="btn" target="_blank" rel="noopener noreferrer"
           href="${ISSUES_URL}?q=${search}">이 머신의 이슈 찾기 ↗</a>
      </p>
    </div>`;
}

async function load() {
  try {
    const data = await loadAll();
    const found = data.licenses.find((item) => item.machine_id === machineId);
    if (!found) { renderMissing(); return; }
    renderLicense(describe(found, { revoked: data.revoked, autorenew: data.autorenew }));
  } catch (err) {
    showError($('body'), err);
  }
}

load();
