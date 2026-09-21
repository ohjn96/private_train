// 현황 (index.html)
import { loadAll } from './api.js';
import { AUTORENEW_TRIGGER_DAYS, NEW_REQUEST_URL } from './config.js';
import { describe, describePolicy, sortForDisplay, summarize } from './model.js';
import { $, autoRenewChip, esc, mountTopbar, showError, statusChip, validityRail } from './ui.js';

mountTopbar('index.html');

const POLICY_CLASS = { open: 'is-off', licensed: 'is-on', blocked: 'is-stop' };

function renderPolicy(policy) {
  const view = describePolicy(policy);
  const box = $('policy');
  box.className = `policy ${POLICY_CLASS[view.mode] ?? 'is-off'}`;
  box.innerHTML = [
    `<span>${view.text}</span>`,
    view.seq === null ? '' : `<span class="seq">seq ${view.seq}</span>`,
    view.message ? `<p class="note">${esc(view.message)}</p>` : '',
  ].join('');
}

function renderStats(counts) {
  $('statTotal').textContent = counts.total;
  $('statValid').textContent = counts.valid;
  $('statSoon').textContent = counts.soon;
  $('statInactive').textContent = counts.inactive;
  $('autoNote').textContent = counts.auto
    ? `${counts.auto}건은 만료 ${AUTORENEW_TRIGGER_DAYS}일 전에 자동으로 연장됩니다.`
    : '자동 갱신으로 설정된 라이선스는 없습니다.';
}

function emptyState() {
  return `<div class="empty">
    <h3>아직 발급된 라이선스가 없습니다</h3>
    <p>사용자가 앱에서 요청하고 승인하면 이 자리에 나타납니다.</p>
    <div class="flow">
      <div>
        <span class="n">1</span>
        <span class="t">사용자가 요청</span>
        <span class="d">앱의 [라이선스 요청하기] → 머신 ID 가 채워진 이슈가 열립니다.</span>
      </div>
      <div>
        <span class="n">2</span>
        <span class="t">승인</span>
        <span class="d">알림 메일에 <code>/approve 30</code> 이라고 답장하면 끝입니다.</span>
      </div>
      <div>
        <span class="n">3</span>
        <span class="t">앱이 자동 활성화</span>
        <span class="d">사용자는 복붙하지 않습니다. 반영까지 최대 5분.</span>
      </div>
    </div>
    <p style="margin-top:22px">
      <a class="btn" href="${NEW_REQUEST_URL}" target="_blank" rel="noopener noreferrer">
        요청 이슈 열기 ↗</a>
    </p>
  </div>`;
}

function renderRows(rows) {
  const table = $('rosterTable');
  const tbody = $('rows');

  if (!rows.length) {
    table.hidden = true;
    $('rosterEmpty').innerHTML = emptyState();
    $('rosterEmpty').hidden = false;
    return;
  }

  table.hidden = false;
  $('rosterEmpty').hidden = true;

  tbody.innerHTML = sortForDisplay(rows).map((row) => `
    <tr class="${row.inactive ? 'is-dormant' : ''}">
      <td data-label="상태"><span class="chips">${statusChip(row)}${autoRenewChip(row)}</span></td>
      <td data-label="머신 ID"><a class="mid"
          href="detail.html?id=${encodeURIComponent(row.machine_id)}">${esc(row.machine_id)}</a></td>
      <td data-label="대상">${esc(row.name || '–')}</td>
      <td data-label="유효기간">${validityRail(row)}</td>
      <td data-label="만료일"><span class="date">${esc(row.expiresOn)}</span></td>
      <td data-label="남은" class="num"><span class="left">${row.inactive ? '–' : `${row.daysLeft}일`}</span></td>
    </tr>`).join('');
}

export async function refresh() {
  const btn = $('refresh');
  btn.disabled = true;
  btn.textContent = '확인 중…';

  try {
    const data = await loadAll();
    const rows = data.licenses.map((item) =>
      describe(item, { revoked: data.revoked, autorenew: data.autorenew }));

    renderPolicy(data.policy);
    renderRows(rows);
    renderStats(summarize(rows));
    $('updated').textContent = new Date().toLocaleString('ko-KR', {
      dateStyle: 'medium', timeStyle: 'short',
    });
  } catch (err) {
    showError($('rosterEmpty'), err);
    $('rosterEmpty').hidden = false;
    $('rosterTable').hidden = true;
  } finally {
    btn.disabled = false;
    btn.textContent = '새로고침';
  }
}

$('refresh').addEventListener('click', refresh);
refresh();
