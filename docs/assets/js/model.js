// 순수 계산만 한다 — DOM 도 fetch 도 여기 없다.
import { SOON_DAYS, AUTORENEW_TRIGGER_DAYS } from './config.js';

export const STATUS = {
  VALID: 'valid',
  SOON: 'soon',
  EXPIRED: 'expired',
  REVOKED: 'revoked',
};

export const STATUS_LABEL = {
  [STATUS.VALID]: '유효',
  [STATUS.SOON]: '곧 만료',
  [STATUS.EXPIRED]: '만료',
  [STATUS.REVOKED]: '철회됨',
};

/** 남은 일수. 오늘 만료면 1, 이미 지났으면 0 이하. */
export function daysLeft(expiresAt, now = Date.now()) {
  return Math.ceil((expiresAt * 1000 - now) / 86400000);
}

export function formatDate(epochSeconds) {
  return new Date(epochSeconds * 1000).toISOString().slice(0, 10);
}

/** autorenew.json 을 보고 이 머신이 자동 갱신 대상인지. scripts/license_admin.py 와 같은 규칙. */
export function autoRenewDays(machineId, settings) {
  if (!settings) return null;

  const machines = settings.machines ?? {};
  let entry = machines[machineId];

  if (entry === undefined) {
    if (!settings.all) return null;
    entry = {};
  }
  if (entry.off) return null;

  if (entry.until) {
    const deadline = Date.parse(`${entry.until}T23:59:59Z`);
    if (!Number.isNaN(deadline) && Date.now() > deadline) return null;
  }

  const days = Number(entry.days ?? settings.default_days ?? 30);
  return Number.isFinite(days) ? Math.max(1, days) : 30;
}

/** 라이선스 한 건을 화면에 필요한 형태로 정리한다. */
export function describe(license, { revoked, autorenew, now = Date.now() } = {}) {
  const isRevoked = revoked?.has(license.license_id) || revoked?.has(license.machine_id);
  const left = daysLeft(license.expires_at, now);
  const renewDays = autoRenewDays(license.machine_id, autorenew);

  let status;
  if (isRevoked) status = STATUS.REVOKED;
  else if (left <= 0) status = STATUS.EXPIRED;
  else if (left <= SOON_DAYS) status = STATUS.SOON;
  else status = STATUS.VALID;

  return {
    ...license,
    status,
    statusLabel: STATUS_LABEL[status],
    remaining: remainingFraction(license, now),
    daysLeft: left,
    expiresOn: formatDate(license.expires_at),
    issuedOn: license.issued_at ? formatDate(license.issued_at) : null,
    autoRenew: !isRevoked && renewDays !== null ? renewDays : null,
    // 자동 갱신 대상이면서 임계값 안쪽 → 다음 실행 때 갱신된다
    renewingSoon: !isRevoked && renewDays !== null && left <= AUTORENEW_TRIGGER_DAYS && left > 0,
    inactive: status === STATUS.EXPIRED || status === STATUS.REVOKED,
  };
}

/** 발급 기간 중 얼마나 남았는지 0~1. 유효기간 레일에 쓴다.
 *  발급일을 모르면(구버전 index.json) null. */
export function remainingFraction(license, now = Date.now()) {
  const { issued_at: issued, expires_at: expires } = license;
  if (!issued || !expires || expires <= issued) return null;
  const total = expires - issued;
  const left = expires - now / 1000;
  return Math.max(0, Math.min(1, left / total));
}

/** 만료가 임박한 순으로. 비활성(만료·철회)은 뒤로 보낸다. */
export function sortForDisplay(rows) {
  return rows.slice().sort((a, b) => {
    if (a.inactive !== b.inactive) return a.inactive ? 1 : -1;
    return a.expires_at - b.expires_at;
  });
}

export function summarize(rows) {
  const counts = { total: rows.length, valid: 0, soon: 0, inactive: 0, auto: 0 };
  for (const row of rows) {
    if (row.status === STATUS.VALID) counts.valid++;
    else if (row.status === STATUS.SOON) counts.soon++;
    else counts.inactive++;
    if (row.autoRenew !== null) counts.auto++;
  }
  return counts;
}

/** license-policy.json → 배너에 쓸 내용. 파일이 없으면 open 으로 본다. */
export function describePolicy(policy) {
  const mode = policy?.mode ?? 'open';
  const TEXT = {
    open: '라이선스 검사 <strong>꺼짐</strong> — 누구나 앱을 쓸 수 있습니다.',
    licensed: '라이선스 검사 <strong>켜짐</strong> — 허가된 PC 에서만 동작합니다.',
    blocked: '<strong>전면 차단</strong> 중 — 라이선스가 있어도 막힙니다.',
  };
  return {
    mode,
    text: TEXT[mode] ?? TEXT.open,
    seq: policy?.seq ?? null,
    message: policy?.message ?? '',
  };
}
