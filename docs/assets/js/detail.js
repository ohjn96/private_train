// 라이선스 상세 (detail.html?id=MACHINE-ID)
import { loadAll } from './api.js';
import { AUTORENEW_TRIGGER_DAYS, ISSUES_URL } from './config.js';
import { describe } from './model.js';
import { $, autoRenewTag, esc, mountTopbar, showError, statusTag } from './ui.js';

mountTopbar('index.html');

const machineId = (new URLSearchParams(location.search).get('id') || '').toUpperCase();

function renderMissing() {
  $('body').innerHTML = `<div class="empty">
    <code>${esc(machineId || '(머신 ID 없음)')}</code> 에 해당하는 라이선스가 없습니다.<br>
    <small>발급된 적이 없거나, 파일이 삭제되었습니다.</small></div>`;
}

function renderLicense(row) {
  $('machineId').textContent = row.machine_id;

  const auto = row.autoRenew === null
    ? '꺼짐 — 만료되면 이슈로 알림이 옵니다.'
    : `켜짐 — 만료 ${AUTORENEW_TRIGGER_DAYS}일 전에 ${row.autoRenew}일씩 연장됩니다.`;

  $('body').innerHTML = `
    <div class="card">
      <dl class="facts">
        <dt>상태</dt>      <dd>${statusTag(row)} ${autoRenewTag(row)}</dd>
        <dt>대상</dt>      <dd>${esc(row.name || '–')}</dd>
        <dt>머신 ID</dt>   <dd class="mono">${esc(row.machine_id)}</dd>
        <dt>라이선스 ID</dt><dd class="mono">${esc(row.license_id)}</dd>
        ${row.issuedOn ? `<dt>발급일</dt><dd>${esc(row.issuedOn)}</dd>` : ''}
        <dt>만료일</dt>    <dd>${esc(row.expiresOn)}
          ${row.inactive ? '' : ` <span class="seq">(${row.daysLeft}일 남음)</span>`}</dd>
        <dt>자동 갱신</dt> <dd>${esc(auto)}</dd>
      </dl>
    </div>

    <h2>이 라이선스에 할 수 있는 것</h2>
    <div class="card">
      <p class="sub">아래 명령을 이 머신의 이슈에 댓글로 남기면 됩니다.
         (알림 메일에 답장해도 똑같이 동작합니다.)</p>
      <pre class="cmd">/approve 30      30일 연장
/autorenew 30    만료 3일 전마다 자동으로 30일씩 연장
/autorenew off   자동 갱신 끄기
/revoke          이 PC 차단</pre>
      <p style="margin-top:12px">
        <a class="btn" target="_blank" rel="noopener noreferrer"
           href="${ISSUES_URL}?q=${encodeURIComponent(`is:issue ${machineId} in:title`)}"
           >이 머신의 이슈 찾기 ↗</a>
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
