# -*- coding: utf-8 -*-
"""데모 서버 화면을 폰(390x844@2x)·데스크톱(1280x900) 크기로 캡처하고 점검한다.

    python scripts/qa/screenshots.py --base http://127.0.0.1:5081 --out <폴더>

점검 (하나라도 걸리면 종료 코드 1):
  - 가로 넘침: document.documentElement.scrollWidth <= 화면 폭
  - 콘솔 JS 오류 / 페이지 오류 (외부 글꼴 로드 실패 같은 네트워크 오류는 경고로만)
참고 정보 (실패로 치지 않음, report.json 에 남김):
  - 44px 보다 작은 터치 대상 (폰 화면만)
  - 이모지가 들어간 보이는 글자
  - 팔레트 밖 색 (계산된 color/background/border)
  - 띄어쓰기 없는 짧은 낱말이 두 줄로 쪼개진 곳 (예: "미연/결")
"""
import argparse
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

CHROME = os.environ.get('QA_CHROME', '/usr/bin/google-chrome')
VIEWPORTS = {
    'mobile': dict(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True),
    'desktop': dict(viewport={'width': 1280, 'height': 900}, device_scale_factor=1),
}
# 팔레트 (base.html tailwind.config) + 흰색·투명
PALETTE = {'#f6f4f0', '#1a1714', '#c8102e', '#9e0c24', '#f4c7ce', '#6b645c', '#e6e1da', '#fbe9eb',
           '#0b6b3a', '#e3f4ea', '#5a544d', '#efece7', '#ffffff', '#fff7f8', '#d6d0c8', '#000000'}

AUDIT_JS = r'''() => {
  const vw = window.innerWidth;
  const hex = c => {
    const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    if (m[4] !== undefined && parseFloat(m[4]) === 0) return null;
    return '#' + [m[1], m[2], m[3]].map(v => (+v).toString(16).padStart(2, '0')).join('');
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0;
  };
  const small = [], colors = {}, emoji = [], overflow = [], brokenWords = [];
  const emojiRe = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2705}\u{274C}]/u;
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const tag = el.tagName.toLowerCase();
    const name = tag + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
    if (r.right > vw + 1 && ![...el.children].some(c => c.getBoundingClientRect().right > vw + 1)) {
      overflow.push(`${name} right=${Math.round(r.right)}`);
    }
    // 누를 수 있는 것 (라벨은 짝 입력칸이 따로 잡히므로 뺀다). 부모가 44px 이상인 누름 영역이면 통과
    const interactive = ['a', 'button', 'select', 'textarea'].includes(tag) || el.getAttribute('role') === 'button' || el.onclick
      || (tag === 'input' && !['hidden', 'checkbox', 'radio'].includes(el.type));
    if (interactive) {
      const hit = el.closest('label') || el.parentElement;
      const hr = hit ? hit.getBoundingClientRect() : r;
      const tooSmall = (r.width < 44 || r.height < 44) && !(hit && hit.tagName === 'LABEL' && hr.width >= 44 && hr.height >= 44);
      if (tooSmall) {
        small.push(`${name} ${Math.round(r.width)}x${Math.round(r.height)} "${(el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 20)}"`);
      }
    }
    for (const p of ['color', 'backgroundColor', 'borderTopColor']) {
      if (p === 'borderTopColor' && parseFloat(s.borderTopWidth) === 0) continue;
      const h = hex(s[p]);
      if (h) (colors[h] = colors[h] || []).length < 3 && colors[h].push(name);
    }
    // 띄어쓰기 없는 짧은 낱말(예: "미연결")이 두 줄로 쪼개졌는지: 글자 영역의 줄 수를 센다
    for (const n of el.childNodes) {
      const t = n.nodeType === 3 ? n.textContent.trim() : '';
      if (!t || t.length > 12 || /\s/.test(t)) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const tops = new Set([...range.getClientRects()].filter(q => q.width > 0).map(q => Math.round(q.top)));
      if (tops.size > 1) brokenWords.push(`${name}: "${t}" ${tops.size}줄`);
    }
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && emojiRe.test(n.textContent)) emoji.push(`${name}: ${n.textContent.trim().slice(0, 40)}`);
    }
  }
  return {scrollWidth: document.documentElement.scrollWidth, innerWidth: vw, small, colors, emoji, brokenWords, overflow: overflow.slice(0, 20)};
}'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:5081')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    base = args.base.rstrip('/')
    report, failures = {}, []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
        for vp_name, vp in VIEWPORTS.items():
            ctx = browser.new_context(**vp, locale='ko-KR')
            page = ctx.new_page()
            errors = []
            page.on('console', lambda m, e=errors: m.type == 'error' and e.append(('console', m.text)))
            page.on('pageerror', lambda ex, e=errors: e.append(('pageerror', str(ex))))

            def shot(name, full=False, settle=900):
                page.wait_for_timeout(settle)
                path = os.path.join(args.out, f'{vp_name}_{name}.png')
                page.screenshot(path=path, full_page=full)
                audit = page.evaluate(AUDIT_JS)
                off = {c: els for c, els in audit['colors'].items() if c not in PALETTE}
                real_errors = [e for e in errors if not re.search(r'fonts\.(googleapis|gstatic)|ERR_|Failed to load resource', e[1])]
                warn_errors = [e for e in errors if e not in real_errors]
                entry = {
                    'file': path, 'scrollWidth': audit['scrollWidth'], 'viewport': audit['innerWidth'],
                    'overflow': audit['overflow'], 'js_errors': real_errors, 'net_warnings': warn_errors,
                    'small_targets': audit['small'] if vp_name == 'mobile' else [],
                    'emoji': audit['emoji'], 'off_palette': off, 'broken_words': audit['brokenWords'],
                }
                report[f'{vp_name}_{name}'] = entry
                ok = audit['scrollWidth'] <= audit['innerWidth'] and not real_errors
                if not ok:
                    failures.append(f'{vp_name}_{name}')
                print(f"{'PASS' if ok else 'FAIL'}  {vp_name}_{name}  scrollWidth={audit['scrollWidth']}/{audit['innerWidth']}"
                      f"  js_errors={len(real_errors)}  small={len(entry['small_targets'])}  emoji={len(audit['emoji'])}"
                      f"  off_palette={len(off)}  broken_words={len(audit['brokenWords'])}")
                errors.clear()

            page.goto(base + '/demo_reset')
            ctx.clear_cookies()
            page.goto(base + '/login')
            shot('01_login')

            page.goto(base + '/demo')
            shot('02_search', settle=1500)
            cards = page.locator('.train-row:visible')
            if cards.count() >= 3:
                cards.nth(1).click()
                cards.nth(2).click()
            page.evaluate("(document.querySelector('.train-row.selected') || document.body).scrollIntoView({block:'center'})")
            shot('03_results_selected')
            shot('03b_results_full', full=True)

            page.goto(base + '/demo_run')
            shot('04_running', settle=3500)
            page.goto(base + '/demo_reset')

            for state in ('success_paid', 'success_payfail', 'success_nopay', 'stopped', 'gave_up', 'crash'):
                page.evaluate("try { localStorage.removeItem('dismissedResult') } catch (e) {}")
                page.goto(base + '/demo_last/' + state)
                page.wait_for_timeout(2500)  # 상태 폴링
                visible = page.evaluate("!document.getElementById('lastResult')?.classList.contains('hidden')")
                if not visible:
                    failures.append(f'{vp_name}_last_{state} (카드 안 보임)')
                    print(f'FAIL  {vp_name}_last_{state}  마지막 결과 카드가 보이지 않음')
                page.evaluate("document.getElementById('lastResult')?.scrollIntoView({block:'start'}); window.scrollBy(0,-16)")
                shot(f'05_last_{state}', settle=300)
            page.goto(base + '/demo_reset')

            # 예약 성공 배너: 도는 중 화면의 로그 스트림으로 실제 이벤트를 흘린다 (/demo_success)
            for name in ('pay_running', 'pay_done', 'pay_failed', 'pay_pending'):
                page.goto(base + '/demo_reset')
                page.evaluate("try { localStorage.removeItem('dismissedSuccess') } catch (e) {}")
                page.goto(base + '/demo_run')
                page.wait_for_timeout(2500)  # 상태 폴링 → 스트림 연결
                page.evaluate(f"fetch('/demo_success/{name}')")
                try:
                    page.wait_for_selector('#successBanner:not(.hidden)', timeout=8000)
                except Exception:  # noqa: BLE001
                    failures.append(f'{vp_name}_banner_{name} (배너 안 보임)')
                    print(f'FAIL  {vp_name}_banner_{name}  성공 배너가 보이지 않음')
                page.wait_for_timeout(1200)
                page.evaluate("window.scrollTo(0,0)")
                shot(f'06_banner_{name}', settle=500)
            page.goto(base + '/demo_reset')
            ctx.close()
        browser.close()

    with open(os.path.join(args.out, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n{len(report)} 장 캡처, 실패 {len(failures)}: {failures}")
    print(f"자세한 점검 결과: {os.path.join(args.out, 'report.json')}")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
