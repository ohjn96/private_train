// 현황 페이지 (index.html)
import { loadAll } from './api.js';
import { AUTORENEW_TRIGGER_DAYS } from './config.js';
import { describe, describePolicy, sortForDisplay, summarize } from './model.js';
import { $, autoRenewTag, esc, mountTopbar, showError, statusTag } from './ui.js';

mountTopbar('index.html');

function renderPolicy(policy) {
  const view = describePolicy(policy);
  const box = $('policy');
  box.className = `banner ${view.tone}`;
  const seq = view.seq === null ? '' : ` <span class="seq">seq ${view.seq}</span>`;
  const note = view.message ? `<p>${esc(view.message)}</p>` : '';
  box.innerHTML = `<div>${view.text}${seq}</div>${note}`;
}

function renderStats(counts) {
  $('statTotal').textContent = counts.total;
  $('statValid').textContent = counts.valid;
  $('statSoon').textContent = counts.soon;
  $('statInactive').textContent = counts.inactive;
  $('autoNote').textContent = counts.auto
    ? `${counts.auto}건이 자동 갱신 대상입니다 (만료 ${AUTORENEW_TRIGGER_DAYS}일 전에 연장).`
    : '자동 갱신으로 설정된 라이선스가 없습니다.';
}

function renderRows(rows) {
  const tbody = $('rows');

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="empty">
      아직 발급된 라이선스가 없습니다.</div></td></tr>`;
    return;
  }

  tbody.innerHTML = sortForDisplay(rows).map((row) => `
    <tr class="${row.inactive ? 'inactive' : ''}">
      <td><a class="mid mono" href="detail.html?id=${encodeURIComponent(row.machine_id)}"
             >${esc(row.machine_id)}</a></td>
      <td>${esc(row.name || '–')}</td>
      <td>${esc(row.expiresOn)}</td>
      <td class="num">${row.inactive ? '–' : `${row.daysLeft}일`}</td>
      <td>${statusTag(row)} ${autoRenewTag(row)}</td>
    </tr>`).join('');
}

export async function refresh() {
  const btn = $('refresh');
  btn.disabled = true;
  btn.textContent = '불러오는 중...';

  try {
    const data = await loadAll();
    const rows = data.licenses.map((item) =>
      describe(item, { revoked: data.revoked, autorenew: data.autorenew }));

    renderPolicy(data.policy);
    renderRows(rows);
    renderStats(summarize(rows));
    $('updated').textContent = `마지막 확인 ${new Date().toLocaleString('ko-KR')}`;
  } catch (err) {
    showError($('rows').parentElement.parentElement, err);
  } finally {
    btn.disabled = false;
    btn.textContent = '새로고침';
  }
}

$('refresh').addEventListener('click', refresh);
refresh();
