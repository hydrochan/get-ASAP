// test_index_pages_mode.py 가 부르는 Node 드라이버: dashboard/index.html 의 인라인 스크립트에서 함수 원문을 뽑아
// 가짜 fetch·DOM 으로 실행한다(브라우저 없음). 사용: node pages_mode_driver.js <index.html> <시나리오>
// 시나리오가 통과하면 "OK" 를 출력하고 종료 코드 0, 실패하면 assert 메시지와 함께 1.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
const scenario = process.argv[3];

// ---------- index.html 에서 스크립트·함수 원문 추출 ----------
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const main = scripts.reduce((a, b) => (b.length > a.length ? b : a));   // 가장 큰 인라인 스크립트 = 메인 IIFE
const headScript = scripts.find(s => s.includes('ASAP_PAGES_MODE ='));

function fnSrc(name) {
  const m = new RegExp('(?:async )?function ' + name + '\\(').exec(main);
  if (!m) throw new Error('함수를 찾지 못함: ' + name);
  let i = main.indexOf('{', main.indexOf(')', m.index) );   // 매개변수 뒤의 본문 여는 중괄호
  let depth = 0;
  for (let j = i; j < main.length; j++) {
    if (main[j] === '{') depth++;
    else if (main[j] === '}' && --depth === 0) return main.slice(m.index, j + 1);
  }
  throw new Error('중괄호가 맞지 않음: ' + name);
}

const PAGES = 'https://demo-proj.pages.dev';
const API = 'https://get-asap.hydrochan.com';

function resp(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => (typeof body === 'string' ? JSON.parse(body) : body), text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) };
}
const csv = (id, month) => `id,title,journal,date,doi,gpt_reason\n${id},Paper ${id},J,${month}-01,,r\n`;
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }

// 환경을 만든다. routes: (url, opts) => 응답 | Promise (없으면 404)
function build({ pagesMode, routes, origin = PAGES, months = ['2026-11', '2026-10', '2026-09'], apiBase = API }) {
  const calls = [];
  const renders = [];
  const ctx = {
    fetch: (url, opts) => {
      calls.push({ url, opts });
      const r = routes(String(url), opts);
      return r === undefined ? Promise.resolve(resp('', 404)) : Promise.resolve(r);
    },
    location: { origin, hash: '', replaced: null, replace(u) { this.replaced = u; } },
    Papa: { parse(text) {
      const lines = text.trim().split('\n'); const h = lines[0].split(',');
      return { data: lines.slice(1).map(l => { const c = l.split(','); return Object.fromEntries(h.map((k, i) => [k, c[i]])); }) };
    } },
    localDateStr: () => '2026-11-05',
    getMonthRange: () => months,
    document: { getElementById: () => ({ value: String(months.length) }) },
    FIRST_STAGE_MONTHS: 2,
    scheduleRender: () => {}, flushRender: () => {},
    renderLoad: (ld) => renders.push([...ld.rows.keys()].sort().join(',')),
    syncLoadStatus: () => {}, updateLoadNotice: () => {},
    _safeUrl: u => u || '',
    console: { debug() {} },
  };
  const names = ['apiUrl', 'pubFetch', 'fetchOriginVersions', 'fetchPagesVersions', 'fetchVersions', 'fetchMonthRows',
                 'fetchMonths', 'reconcileWithOrigin', '_loadData', 'paperHref'];
  const body = `
    const API_BASE = ${JSON.stringify(apiBase)}; const PAGES_MODE = ${pagesMode};
    const PAGES_BASE_RE = /^https:\\/\\/[a-z0-9-]+\\.pages\\.dev$/;
    const { fetch, location, Papa, localDateStr, getMonthRange, document, FIRST_STAGE_MONTHS, scheduleRender, flushRender,
            renderLoad, syncLoadStatus, updateLoadNotice, _safeUrl, console } = ctx;
    let loadSeq = 1; let versionsPrefetch = null;
    ${names.map(fnSrc).join('\n')}
    return { apiUrl, pubFetch, fetchVersions, fetchMonthRows, reconcileWithOrigin, _loadData, paperHref, setSeq: v => { loadSeq = v; }, getSeq: () => loadSeq };
  `;
  const api = new Function('ctx', body)(ctx);
  const newLd = () => ({ seq: 1, versions: null, pages: null, months: [], total: 0, rows: new Map(), failed: new Set(), pending: new Set(), active: 1, retrying: false, rendered: false, dirty: false, timer: 0 });
  return { api, calls, renders, ctx, newLd };
}

// publish_pages.py 가 staging 사본에 하는 치환과 같다(원본 표시 줄 -> true / API 출처)
const stagedHead = (apiBase) => headScript.replace('var ASAP_STATIC_FRONT = false;', 'var ASAP_STATIC_FRONT = true;').replace("var ASAP_API_BASE = '';", `var ASAP_API_BASE = '${apiBase}';`);
const isCsv = (u) => u.includes('/csv/') || u.includes('/api/csv/');
const scenarios = {
  async 'pages mode detection'() {
    // 원본(서버가 서빙하는) index.html 은 표시 줄이 false / '' - 어느 호스트에서 열어도(pages.dev 포함) 원본 모드
    assert.strictEqual((headScript.match(/var ASAP_STATIC_FRONT = false;/g) || []).length, 1);
    assert.strictEqual((headScript.match(/var ASAP_API_BASE = '';/g) || []).length, 1);
    const modeOf = (src, origin) => { const sb = { location: { origin } }; vm.runInNewContext(src, sb); return sb; };
    for (const origin of ['https://get-asap-data.pages.dev', 'https://get-asap.hydrochan.com', 'https://get-asap-api.hydrochan.com', 'http://localhost:8080', 'https://a.b.pages.dev']) {
      const sb = modeOf(headScript, origin);
      assert.strictEqual(sb.ASAP_PAGES_MODE, false, origin);
      assert.strictEqual(sb.ASAP_API_BASE, '', origin);
    }
    // staging 사본(표시 true)은 호스트와 무관하게 정적 화면 모드 + 주입된 API 출처
    for (const origin of ['https://get-asap-data.pages.dev', 'https://get-asap.hydrochan.com', 'https://front.example.com', 'http://localhost:8080']) {
      const sb = modeOf(stagedHead(API), origin);
      assert.strictEqual(sb.ASAP_PAGES_MODE, true, origin);
      assert.strictEqual(sb.ASAP_API_BASE, API, origin);
    }
    // 메인 스크립트는 head 전역을 그대로 쓴다(자체 호스트 판별 없음)
    assert.ok(main.includes('const PAGES_MODE = ASAP_PAGES_MODE;') && main.includes('const API_BASE = ASAP_API_BASE;'));
    assert.strictEqual(/pages\.dev/.test(headScript), false);
  },

  async 'marker true on a custom domain drives the pages flow with the injected api base'() {
    const API2 = 'https://get-asap-api.hydrochan.com';
    const sb = { location: { origin: 'https://front.example.com' } };
    vm.runInNewContext(stagedHead(API2), sb);
    const routes = (u) => {
      if (u === './versions.json') return resp({ '2026-10': 'v10' });
      if (u === API2 + '/api/versions') return resp({ months: { '2026-10': 'v10' }, pages: { base: PAGES, months: { '2026-10': 'v10' } } });
      if (u.startsWith(PAGES + '/csv/2026-10.v10.csv')) return resp(csv(10, '2026-10'));
    };
    const p = build({ pagesMode: sb.ASAP_PAGES_MODE, apiBase: sb.ASAP_API_BASE, origin: 'https://front.example.com', routes, months: ['2026-10'] });
    assert.strictEqual(p.api.apiUrl('/api/x'), API2 + '/api/x');
    assert.strictEqual(p.api.paperHref({ id: '42' }), API2 + '/api/link/42');
    await p.api.pubFetch('/api/metrics');
    assert.strictEqual(p.calls[0].url, API2 + '/api/metrics');
    assert.deepStrictEqual({ mode: p.calls[0].opts.mode, credentials: p.calls[0].opts.credentials }, { mode: 'cors', credentials: 'omit' });
    const ld = p.newLd();
    await p.api._loadData(ld);
    await new Promise(r => setTimeout(r, 20));
    const urls = p.calls.map(c => c.url);
    assert.ok(urls.includes('./versions.json'), urls.join('\n'));
    assert.ok(urls.includes(API2 + '/api/versions'), urls.join('\n'));
    assert.ok(urls.every(u => !u.startsWith(API + '/')), urls.join('\n'));   // 기본 출처로는 가지 않는다
  },

  async 'marker false on a pages dev like origin stays in origin mode'() {
    const sb = { location: { origin: PAGES } };
    vm.runInNewContext(headScript, sb);   // 표시가 false 면 pages.dev 에서 열어도 원본 모드
    const routes = (u) => {
      if (u === '/api/versions') return resp({ months: { '2026-10': 'v10' }, pages: null });
      if (u.startsWith('/api/csv/2026-10')) return resp(csv(10, '2026-10'));
    };
    const p = build({ pagesMode: sb.ASAP_PAGES_MODE, apiBase: sb.ASAP_API_BASE, origin: PAGES, routes, months: ['2026-10'] });
    assert.strictEqual(p.api.apiUrl('/api/x'), '/api/x');
    assert.strictEqual(p.api.paperHref({ id: '42' }), '/api/link/42');
    await p.api._loadData(p.newLd());
    await new Promise(r => setTimeout(r, 20));
    const urls = p.calls.map(c => c.url);
    assert.ok(urls.length > 0 && urls.every(u => u.startsWith('/api/')), urls.join('\n'));   // versions.json·절대 주소 없음
  },

  async 'apiUrl and pubFetch'() {
    const p = build({ pagesMode: true, routes: () => resp({}) });
    assert.strictEqual(p.api.apiUrl('/api/x?y=1'), API + '/api/x?y=1');
    await p.api.pubFetch('/api/metrics');
    assert.strictEqual(p.calls[0].url, API + '/api/metrics');
    assert.deepStrictEqual({ mode: p.calls[0].opts.mode, credentials: p.calls[0].opts.credentials }, { mode: 'cors', credentials: 'omit' });
    const o = build({ pagesMode: false, routes: () => resp({}), origin: API });
    assert.strictEqual(o.api.apiUrl('/api/x'), '/api/x');   // 원본 모드는 상대경로 그대로
    await o.api.pubFetch('/api/metrics');
    assert.strictEqual(o.calls[0].url, '/api/metrics');
    assert.strictEqual(o.calls[0].opts, undefined);
  },

  async 'paperHref absolute in pages mode'() {
    const p = build({ pagesMode: true, routes: () => resp({}) }).api;
    assert.strictEqual(p.paperHref({ id: '42' }), API + '/api/link/42');
    assert.strictEqual(p.paperHref({ id: 'a b' }), API + '/api/link/a%20b');
    assert.strictEqual(p.paperHref({ id: '42', doi: '10.1/x' }), 'https://doi.org/10.1/x');   // DOI 우선은 그대로
    assert.strictEqual(p.paperHref({}), '');
    const o = build({ pagesMode: false, routes: () => resp({}) }).api;
    assert.strictEqual(o.paperHref({ id: '42' }), '/api/link/42');
  },

  async 'first paint from versions.json then only changed months from origin'() {
    const originVer = deferred();
    const pagesVers = { '2026-10': 'v10', '2026-09': 'v09' };
    const routes = (u) => {
      if (u === './versions.json') return resp(pagesVers);
      if (u === API + '/api/versions') return originVer.promise.then(r => r);
      if (u.startsWith(PAGES + '/csv/2026-10.v10.csv')) return resp(csv(10, '2026-10'));
      if (u.startsWith(PAGES + '/csv/2026-09.v09.csv')) return resp(csv(9, '2026-09'));
      if (u.startsWith(API + '/api/csv/2026-09?fmt=slim&v=v09b')) return resp(csv(99, '2026-09'));
      if (u.startsWith(API + '/api/csv/2026-11?fmt=slim&v=v11')) return resp(csv(11, '2026-11'));
      return undefined;
    };
    const e = build({ pagesMode: true, routes });
    const ld = e.newLd();
    await e.api._loadData(ld);   // 원본 versions 가 아직 안 왔어도 끝난다(= LAX 를 기다리지 않는다)
    assert.deepStrictEqual(ld.months, ['2026-10', '2026-09']);   // 2026-11 은 versions.json 에 없다
    assert.deepStrictEqual(e.calls.filter(c => isCsv(c.url)).map(c => c.url.split('?')[0]).sort(),
      [PAGES + '/csv/2026-09.v09.csv', PAGES + '/csv/2026-10.v10.csv']);
    assert.ok(e.renders.length >= 1);
    assert.strictEqual(e.calls.some(c => c.url.includes('/api/session')), false);
    assert.strictEqual(ld.rows.get('2026-09')[0].id, '9');
    const rendersBefore = e.renders.length;

    originVer.resolve(resp({ months: { '2026-11': 'v11', '2026-10': 'v10', '2026-09': 'v09b' } }));
    await ld.reconcile;
    const originCsv = e.calls.filter(c => c.url.startsWith(API + '/api/csv/')).map(c => c.url).sort();
    assert.deepStrictEqual(originCsv, [API + '/api/csv/2026-09?fmt=slim&v=v09b', API + '/api/csv/2026-11?fmt=slim&v=v11']);   // 바뀐 달 + 새 달만, 2026-10 은 안 받음
    assert.strictEqual(ld.rows.get('2026-09')[0].id, '99');   // 그 달 행이 바뀜
    assert.strictEqual(ld.rows.get('2026-10')[0].id, '10');   // 같은 버전은 그대로
    assert.ok(ld.rows.has('2026-11'));
    assert.deepStrictEqual(ld.months, ['2026-11', '2026-10', '2026-09']);
    assert.strictEqual(ld.versions['2026-09'], 'v09b');
    assert.ok(e.renders.length > rendersBefore);   // 다시 그림
    const cors = e.calls.find(c => c.url === API + '/api/csv/2026-11?fmt=slim&v=v11').opts;
    assert.deepStrictEqual({ mode: cors.mode, credentials: cors.credentials }, { mode: 'cors', credentials: 'omit' });
  },

  async 'same versions means no origin csv fetch'() {
    const routes = (u) => {
      if (u === './versions.json') return resp({ '2026-10': 'v10', '2026-09': 'v09' });
      if (u === API + '/api/versions') return resp({ months: { '2026-10': 'v10', '2026-09': 'v09' } });
      if (u.includes('/csv/2026-10')) return resp(csv(10, '2026-10'));
      if (u.includes('/csv/2026-09')) return resp(csv(9, '2026-09'));
    };
    const e = build({ pagesMode: true, routes });
    const ld = e.newLd();
    await e.api._loadData(ld);
    const before = e.renders.length;
    await ld.reconcile;
    assert.strictEqual(e.calls.some(c => c.url.startsWith(API + '/api/csv/')), false);
    assert.strictEqual(e.renders.length, before);
  },

  async 'origin versions failure keeps pages data silently'() {
    for (const failure of ['reject', 'http500', 'badjson']) {
      const routes = (u) => {
        if (u === './versions.json') return resp({ '2026-10': 'v10' });
        if (u === API + '/api/versions') {
          if (failure === 'reject') return Promise.reject(new Error('net'));
          if (failure === 'http500') return resp('', 500);
          return resp({ nope: 1 });
        }
        if (u.includes('/csv/2026-10')) return resp(csv(10, '2026-10'));
      };
      const e = build({ pagesMode: true, routes });
      const ld = e.newLd();
      await e.api._loadData(ld);
      const before = e.renders.length;
      await ld.reconcile;   // 던지지 않는다
      assert.deepStrictEqual(ld.months, ['2026-10']);
      assert.strictEqual(ld.rows.get('2026-10')[0].id, '10');
      assert.strictEqual(e.renders.length, before);
      assert.strictEqual(ld.failed.size, 0);
    }
  },

  async 'origin month fetch failure keeps the pages rows'() {
    const routes = (u) => {
      if (u === './versions.json') return resp({ '2026-10': 'v10' });
      if (u === API + '/api/versions') return resp({ months: { '2026-10': 'v10b' } });
      if (u.includes('/csv/2026-10.v10.csv')) return resp(csv(10, '2026-10'));
      if (u.startsWith(API + '/api/csv/')) return resp('', 500);
    };
    const e = build({ pagesMode: true, routes });
    const ld = e.newLd();
    await e.api._loadData(ld);
    const before = e.renders.length;
    await ld.reconcile;
    assert.strictEqual(ld.rows.get('2026-10')[0].id, '10');
    assert.strictEqual(ld.versions['2026-10'], 'v10');   // 바꾸지 않음
    assert.strictEqual(ld.failed.size, 0);
    assert.strictEqual(e.renders.length, before);
  },

  async 'stale load is not touched by reconcile'() {
    const originVer = deferred();
    const routes = (u) => {
      if (u === './versions.json') return resp({ '2026-10': 'v10' });
      if (u === API + '/api/versions') return originVer.promise;
      if (u.includes('/csv/2026-10.v10.csv')) return resp(csv(10, '2026-10'));
      if (u.startsWith(API + '/api/csv/')) return resp(csv(77, '2026-10'));
    };
    const e = build({ pagesMode: true, routes });
    const ld = e.newLd();
    await e.api._loadData(ld);
    e.api.setSeq(2);   // 그 사이 새 로드(Period 변경·Refresh)가 시작됨
    originVer.resolve(resp({ months: { '2026-10': 'v10b' } }));
    await ld.reconcile;
    assert.strictEqual(e.calls.some(c => c.url.startsWith(API + '/api/csv/')), false);
    assert.strictEqual(ld.rows.get('2026-10')[0].id, '10');
  },

  async 'versions.json failure falls back to origin versions'() {
    const routes = (u) => {
      if (u === './versions.json') return resp('', 404);
      if (u === API + '/api/versions') return resp({ months: { '2026-10': 'v10' } });
      if (u.startsWith(API + '/api/csv/2026-10?fmt=slim&v=v10')) return resp(csv(10, '2026-10'));
    };
    const e = build({ pagesMode: true, routes });
    const ld = e.newLd();
    await e.api._loadData(ld);
    assert.strictEqual(ld.rows.get('2026-10')[0].id, '10');
    assert.strictEqual(ld.reconcile, undefined);   // 비교할 pages 첫 화면이 없었다
  },

  async 'origin mode never touches versions.json'() {
    const routes = (u) => {
      if (u === '/api/versions') return resp({ months: { '2026-10': 'v10' } });
      if (u.startsWith('/api/csv/2026-10?fmt=slim&v=v10')) return resp(csv(10, '2026-10'));
    };
    const e = build({ pagesMode: false, routes, origin: API });
    const ld = e.newLd();
    await e.api._loadData(ld);
    assert.strictEqual(e.calls.some(c => c.url.includes('versions.json')), false);
    assert.deepStrictEqual(e.calls.map(c => c.url), ['/api/versions', '/api/csv/2026-10?fmt=slim&v=v10']);
    assert.strictEqual(ld.reconcile, undefined);
  },

  // 세션 체크 + 방문 신호 + 관리자 로그인 해시
  async 'no session call and one visit beacon in pages mode'() {
    const startMarker = '// Pages 모드: 공개(게스트) 화면';
    const tail = main.slice(main.indexOf(startMarker), main.lastIndexOf('})();'));
    const run = (pagesMode) => {
      const calls = []; const log = [];
      new Function('apiUrl', 'fetch', 'PAGES_MODE', 'applyUserVisibility', 'loadData', 'syncLoginScreenFromHash',
        'currentUser', 'isAdmin', 'currentUserConfig', tail.replace(/\bcurrentUser = /g, 'void ').replace(/\bisAdmin = /g, 'void ').replace(/\bcurrentUserConfig = /g, 'void '))
        ((p) => (pagesMode ? API + p : p), (u, o) => { calls.push({ url: u, opts: o }); return Promise.resolve(resp({ ok: true, user: '' })); },
          pagesMode, () => log.push('visibility'), () => log.push('loadData'), () => log.push('sync'), '', false, {});
      return { calls, log };
    };
    const p = run(true);
    await new Promise(r => setTimeout(r, 10));
    assert.deepStrictEqual(p.calls.map(c => c.url), [API + '/api/visit']);   // /api/session 없음, 방문 신호 한 번
    const o = p.calls[0].opts;
    assert.deepStrictEqual({ method: o.method, mode: o.mode, credentials: o.credentials, keepalive: o.keepalive }, { method: 'POST', mode: 'cors', credentials: 'omit', keepalive: true });
    assert.strictEqual(o.headers, undefined);   // 사용자 헤더·본문 없음 -> preflight 없는 단순 요청
    assert.strictEqual(o.body, undefined);
    assert.deepStrictEqual(p.log, ['visibility', 'loadData', 'sync']);
    const q = run(false);
    await new Promise(r => setTimeout(r, 10));
    assert.deepStrictEqual(q.calls.map(c => c.url), ['/api/session']);   // 원본 모드는 예전 그대로, 방문 신호 없음
  },

  async 'admin login hash goes to origin in pages mode'() {
    const fn = fnSrc('showLoginScreenForAdminHash');
    const run = (pagesMode) => {
      const loc = { replaced: null, replace(u) { this.replaced = u; } };
      const el = { classList: { add() {}, remove() {} } };
      new Function('PAGES_MODE', 'API_BASE', 'ADMIN_LOGIN_HASH', 'location', 'document', 'dashboard', 'loginScreen',
        fn + '; showLoginScreenForAdminHash();')(pagesMode, API, '#admin-login', loc, { getElementById: () => el }, el, el);
      return loc.replaced;
    };
    assert.strictEqual(run(true), API + '/?stay=1#admin-login');
    assert.strictEqual(run(false), null);
    // 첫 페인트 전 동기 스크립트도 같은 곳으로 보낸다
    const sync = scripts.find(s => s.includes("location.hash === '#admin-login'") && s.includes('ASAP_PAGES_MODE'));
    const sb = { ASAP_PAGES_MODE: true, ASAP_API_BASE: API, location: { hash: '#admin-login', replaced: null, replace(u) { this.replaced = u; } },
      document: { getElementById: () => ({ classList: { add() {}, remove() {} } }) } };
    vm.runInNewContext(sync, sb);
    assert.strictEqual(sb.location.replaced, API + '/?stay=1#admin-login');
  },

  async 'inline scripts parse and no raw api fetch remains'() {
    assert.ok(scripts.length >= 3);
    for (const s of scripts) new vm.Script(s);   // 구문 오류가 있으면 던진다
    assert.strictEqual(/fetch\('\/api/.test(main), false);
    assert.strictEqual(/fetch\(`\/api/.test(main), false);
  },
};

(async () => {
  const fn = scenarios[scenario];
  if (!fn) { console.error('알 수 없는 시나리오: ' + scenario + ' / ' + Object.keys(scenarios).join(' | ')); process.exit(2); }
  try { await fn(); console.log('OK'); }
  catch (e) { console.error(e && e.stack || e); process.exit(1); }
})();
