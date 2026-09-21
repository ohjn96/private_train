// 저장소의 JSON 파일들을 읽어온다.
//
// raw.githubusercontent.com 은 CDN 캐시(약 5분)가 걸려 있어서, 방금 푸시한 내용이
// 바로 안 보일 수 있다. 쿼리스트링과 no-store 로 최대한 우회하지만 완벽하지는 않다.
import { RAW_BASE } from './config.js';

async function getJSON(path) {
  try {
    const res = await fetch(`${RAW_BASE}/${path}?t=${Date.now()}`, { cache: 'no-store' });
    if (res.status === 404) return null;        // 아직 그 파일이 없다 (정상)
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (err) {
    if (err instanceof SyntaxError) throw new Error('JSON 형식이 올바르지 않습니다.');
    throw err;
  }
}

/** 대시보드가 필요한 것 전부. 하나가 없어도(404) 나머지는 살린다. */
export async function loadAll() {
  const [index, revocation, policy, autorenew] = await Promise.all([
    getJSON('licenses/index.json'),
    getJSON('revoked.json'),
    getJSON('license-policy.json'),
    getJSON('licenses/autorenew.json'),
  ]);

  return {
    licenses: Array.isArray(index?.licenses) ? index.licenses : [],
    revoked: new Set(Array.isArray(revocation?.revoked) ? revocation.revoked : []),
    policy: policy ?? null,
    autorenew: autorenew ?? null,
  };
}
