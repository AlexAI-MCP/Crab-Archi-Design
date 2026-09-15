/* Public runtime adapter. Drawing data stays in this tab's worker and IndexedDB. */
(() => {
  const worker = new Worker('/web_worker.js', { type: 'module' });
  const pending = new Map();
  let seq = 0;
  let failed = null;
  let ready = false;
  let initial = null;
  let persistTimer;
  const status = text => { const el = document.getElementById('webState'); if (el) el.textContent = text; };
  const download = (content, name, type) => {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement('a');
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };
  function request(route, body) {
    if (failed) return Promise.resolve({ status: 'error', error: failed });
    return new Promise(resolve => {
      const id = ++seq;
      pending.set(id, resolve);
      worker.postMessage({ id, request: { route, body } });
    });
  }
  worker.onmessage = ({ data }) => {
    pending.get(data.id)?.(data.result);
    pending.delete(data.id);
  };
  worker.onerror = event => {
    failed = event.message || '브라우저 편집 엔진을 불러오지 못했습니다. 새로고침해 주세요.';
    status('엔진 연결 실패');
    for (const resolve of pending.values()) resolve({ status: 'error', error: failed });
    pending.clear();
  };
  const database = () => new Promise((resolve, reject) => {
    const req = indexedDB.open('crab-archi-browser-v1', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('documents');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  async function storage(action, value) {
    const db = await database();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction('documents', action === 'get' ? 'readonly' : 'readwrite');
        const store = tx.objectStore('documents');
        const req = action === 'get' ? store.get('current') : store.put(value, 'current');
        tx.oncomplete = () => resolve(req.result);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
    } finally { db.close(); }
  }
  async function persist() {
    const doc = await request('/api/doc');
    if (doc.status !== 'ok') return;
    const handoff = await request('/api/design-request');
    try {
      await storage('put', { svg: doc.svg, name: doc.source_path,
        source_sha256: handoff.request?.source_sha256, messages: handoff.request?.messages || [] });
      status('이 브라우저에 저장됨');
    } catch (_) { status('자동저장 실패 · SVG 다운로드 필요'); }
  }
  async function initialize() {
    status('편집 엔진 준비 중…');
    const health = await request('/api/health');
    if (health.status !== 'ok') { status('엔진 연결 실패'); throw new Error(health.error); }
    let saved;
    try { saved = await storage('get'); } catch (_) { /* File downloads still work without storage. */ }
    if (saved?.svg) {
      const restored = await request('/api/restore', saved);
      if (restored.status !== 'ok') throw new Error(restored.error);
    } else {
      const response = await fetch('/sample.svg');
      if (!response.ok) throw new Error('Sample drawing could not be loaded');
      const loaded = await request('/api/open-text', { svg: await response.text(), name: 'sample.svg' });
      if (loaded.status !== 'ok') throw new Error(loaded.error);
    }
    ready = true; status('브라우저 편집 · Codex 미연결');
  }
  window.CrabBrowser = {
    async api(route, body) {
      if (!initial) initial = initialize();
      try { await initial; } catch (error) { return { status: 'error', error: String(error) }; }
      if (route === '/api/save') {
        const result = await request(route, body);
        if (result.status === 'ok') {
          download(result.svg, result.saved.replace(/\.svg$/i, '') + '-edited.svg', 'image/svg+xml');
          await persist();
        }
        return result;
      }
      if (route === '/api/3d/capture') {
        const raw = atob(body.image_data_url.split(',')[1]);
        download(Uint8Array.from(raw, char => char.charCodeAt(0)), 'camera-cut.png', 'image/png');
        download(JSON.stringify({ ...body, image_data_url: undefined, agent_connected: false }, null, 2), 'camera-cut.json', 'application/json');
        return { status: 'ok', capture_path: 'camera-cut.png', queued_to_agent: false };
      }
      const result = await request(route, body);
      if (body !== undefined && result.status === 'ok' && !route.includes('/session/')) {
        clearTimeout(persistTimer);
        persistTimer = setTimeout(persist, 500);
      }
      return result;
    },
    isReady: () => ready,
  };
  document.addEventListener('DOMContentLoaded', () => {
    const $ = id => document.getElementById(id);
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/web.css'; document.head.append(css);
    document.body.classList.add('public-canvas');
    const bar = document.createElement('div'); bar.className = 'web-topbar';
    bar.innerHTML = '<strong>Crab Archi Design</strong><span id="webState">편집 엔진 준비 중…</span><a href="https://opencrab.sh" target="_blank" rel="noopener">OpenCrab ↗</a>';
    document.body.prepend(bar);
    const notice = document.createElement('output'); notice.id = 'webNotice'; notice.setAttribute('aria-live', 'polite');
    document.querySelector('footer').append(notice);
    $('svgPath').hidden = true; $('btnOpen').hidden = true;
    $('btnBrowse').textContent = 'SVG 열기'; $('btnBrowse').onclick = () => $('filePick').click();
    $('btnSave').textContent = 'SVG 다운로드';
    $('btnDwg').hidden = true; $('btnDxf').hidden = true;
    $('prompt').placeholder = '선택한 공간의 변경 요청';
    $('btnSend').textContent = '요청 기록';
    $('btnRegen').textContent = '설계 요청 내보내기';
    $('btnRegen').title = 'Codex · OpenCrab용 JSON 다운로드';
    $('btnRegen').onclick = async () => {
      const text = $('prompt').value.trim();
      if (text) {
        const saved = await window.CrabBrowser.api('/api/message', { text });
        if (saved.status !== 'ok') { window.CrabCanvas.log(saved.error); return; }
        $('prompt').value = '';
      }
      const result = await request('/api/design-request');
      if (result.status !== 'ok') { window.CrabCanvas.log(result.error); return; }
      download(JSON.stringify(result.request, null, 2), 'design-request.json', 'application/json');
      window.CrabCanvas.log('설계 요청 JSON 다운로드 완료 · Codex/OpenCrab 실행은 아직 진행되지 않았습니다.');
    };
    $('btn3dSend').textContent = '컷 다운로드';
    const scale = document.createElement('button'); scale.textContent = '축척'; scale.title = 'SVG 1단위의 실제 길이(mm)';
    scale.onclick = async () => {
      const doc = await request('/api/doc');
      const value = window.prompt('SVG 1단위의 실제 길이 (mm)', String(doc.mm_per_unit || ''));
      if (value === null) return;
      const result = await window.CrabBrowser.api('/api/op', { op: 'set_scale', params: { mm_per_unit: Number(value) } });
      window.CrabCanvas.log(result.status === 'ok' ? `축척: ${result.mm_per_unit} mm/단위` : result.error);
    };
    $('btnSave').after(scale);
    const measure = document.createElement('button'); measure.textContent = '측정'; measure.title = '선택 요소의 길이·면적';
    measure.onclick = async () => {
      const result = await request('/api/measure', { cids: window.CrabCanvas.getSelectedCids() });
      window.CrabCanvas.log(result.status === 'ok' ? JSON.stringify(result.measurements, null, 2) : result.error);
    };
    scale.after(measure);
  });
})();
