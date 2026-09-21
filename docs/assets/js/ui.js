// 페이지들이 공유하는 DOM 조각들.
import { ISSUES_URL, REPO_URL } from './config.js';

export const $ = (id) => document.getElementById(id);

/** 텍스트를 HTML 에 넣기 전에 항상 통과시킨다. */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

export function tag(text, kind = '') {
  return `<span class="tag ${kind}">${esc(text)}</span>`;
}

export function statusTag(row) {
  const kinds = { valid: 'ok', soon: 'warn', expired: 'dead', revoked: 'dead' };
  return tag(row.statusLabel, kinds[row.status] ?? '');
}

export function autoRenewTag(row) {
  if (row.autoRenew === null) return '';
  const label = row.renewingSoon ? `곧 갱신 +${row.autoRenew}일` : `자동 ${row.autoRenew}일`;
  return tag(label, 'auto');
}

/** 상단 바. 페이지마다 <body> 맨 앞에서 부른다. */
export function mountTopbar(current) {
  const pages = [
    ['index.html', '현황'],
    ['guide.html', '사용 안내'],
  ];
  const links = pages.map(([href, label]) => {
    const mark = href === current ? ' aria-current="page"' : '';
    return `<a href="${href}"${mark}>${label}</a>`;
  }).join('');

  document.body.insertAdjacentHTML('afterbegin', `
    <div class="topbar">
      <div class="wrap">
        <a class="brand" href="index.html">라이선스<span class="dot">.</span></a>
        <nav class="nav">
          ${links}
          <a href="${ISSUES_URL}" target="_blank" rel="noopener noreferrer">Issues ↗</a>
          <a href="${REPO_URL}" target="_blank" rel="noopener noreferrer">저장소 ↗</a>
        </nav>
      </div>
    </div>`);
}

export function showError(container, err) {
  container.innerHTML =
    `<div class="empty">불러오지 못했습니다.<br><small>${esc(err.message)}</small></div>`;
}
