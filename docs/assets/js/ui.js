// 페이지들이 공유하는 DOM 조각들.
import { ISSUES_URL, REPO_URL } from './config.js';

export const $ = (id) => document.getElementById(id);

/** 텍스트를 HTML 에 넣기 전에 항상 통과시킨다. */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

/** 상태 칩. 색만으로 읽히지 않도록 글자를 항상 함께 낸다. */
export function chip(text, kind = 'off') {
  return `<span class="chip ${kind}">${esc(text)}</span>`;
}

const STATUS_KIND = { valid: 'ok', soon: 'warn', expired: 'off', revoked: 'crit' };

export function statusChip(row) {
  return chip(row.statusLabel, STATUS_KIND[row.status] ?? 'off');
}

export function autoRenewChip(row) {
  if (row.autoRenew === null) return '';
  return chip(row.renewingSoon ? `곧 갱신 +${row.autoRenew}일` : `자동 ${row.autoRenew}일`, 'auto');
}

/** 유효기간 레일 — 발급 기간 중 남은 비율을 형태로 보여준다. */
export function validityRail(row) {
  if (row.inactive || row.remaining === null) {
    return '<div class="railbar is-dormant" style="--fill:100%"></div>';
  }
  const pct = Math.max(2, Math.round(row.remaining * 100));
  const kind = row.status === 'soon' ? ' is-warn' : '';
  return `<div class="railbar${kind}" style="--fill:${pct}%"
               role="img" aria-label="유효기간 ${pct}% 남음"></div>`;
}

/** 상단 바. 페이지마다 <body> 맨 앞에서 부른다. */
export function mountTopbar(current) {
  const pages = [['index.html', '현황'], ['guide.html', '안내']];
  const links = pages.map(([href, label]) =>
    `<a href="${href}"${href === current ? ' aria-current="page"' : ''}>${label}</a>`).join('');

  document.body.insertAdjacentHTML('afterbegin', `
    <header class="topbar">
      <div class="wrap">
        <a class="brand" href="index.html">라이선스 콘솔</a>
        <nav class="nav">
          ${links}
          <a href="${ISSUES_URL}" target="_blank" rel="noopener noreferrer">요청함 ↗</a>
          <a href="${REPO_URL}" target="_blank" rel="noopener noreferrer">저장소 ↗</a>
        </nav>
      </div>
    </header>`);
}

export function showError(container, err) {
  container.innerHTML = `<div class="empty">
    <h3>불러오지 못했습니다</h3>
    <p>${esc(err.message)}</p>
  </div>`;
}
