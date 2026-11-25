/* Seedr Movie Manager — front-end logic.
   Talks to Python through window.pywebview.api.* and receives events via window.__emit(). */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const el = {
    email: $('#email'), password: $('#password'), pwToggle: $('#pwToggle'), pwIcon: $('#pwIcon'),
    remember: $('#remember'), accountBadge: $('#accountBadge'), connectBtn: $('#connectBtn'),
    search: $('#search'), searchBtn: $('#searchBtn'),
    variant: $('#variant'), variantBtn: $('#variantBtn'), variantText: $('#variantText'), variantList: $('#variantList'),
    folder: $('#folder'), browseBtn: $('#browseBtn'),
    processBtn: $('#processBtn'), processText: $('#processText'), gateText: $('#gateText'),
    readyPill: $('#readyPill'), readyPillText: $('#readyPillText'),
    transferBadge: $('#transferBadge'), msTitle: $('#msTitle'), msSub: $('#msSub'),
    pct: $('#pct'), fill: $('#fill'),
    log: $('#log'), logGuide: $('#logGuide'), hintText: $('#hintText'), waitDot: $('#waitDot'),
    toasts: $('#toasts'),
    stats: $('#stats'), statSpeed: $('#statSpeed'), statEta: $('#statEta'), statDone: $('#statDone'),
    controls: $('#controls'), pauseBtn: $('#pauseBtn'), pauseIcon: $('#pauseIcon'), pauseText: $('#pauseText'),
    openMgrBtn: $('#openMgrBtn'), openMgrText: $('#openMgrText'), cancelBtn: $('#cancelBtn'),
    useManager: $('#useManager'), managerPath: $('#managerPath'), managerBtn: $('#managerBtn'),
    handoff: $('#handoff'), handoffTitle: $('#handoffTitle'), clearBtn: $('#clearBtn'),
  };

  /* ---------------- formatting ---------------- */
  function fmtBytes(n) {
    if (!n || n < 0) return '0 MB';
    const mb = n / 1048576;
    return mb >= 1024 ? (mb / 1024).toFixed(2) + ' GB' : mb.toFixed(mb >= 100 ? 0 : 1) + ' MB';
  }
  function fmtSpeed(bps) {
    if (!bps || bps < 1) return '—';
    const mb = bps / 1048576;
    return mb >= 1 ? mb.toFixed(2) + ' MB/s' : (bps / 1024).toFixed(0) + ' KB/s';
  }
  function fmtEta(sec) {
    if (sec == null || !isFinite(sec)) return '—';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + 'm ' + String(s).padStart(2, '0') + 's';
    const h = Math.floor(m / 60);
    return h + 'h ' + String(m % 60).padStart(2, '0') + 'm';
  }

  const state = {
    busy: false,
    searching: false,
    items: [],          // [{title, size_gb, seeds}]
    selected: -1,
    stage: 'select',    // select | ready | sync | download | done
    statusDetail: '',
    logCount: 0,
    accountState: 'idle',
    stageName: '',
    paused: false,
    manager: { name: '', path: '' },
  };

  /* ---------------- backend bridge ---------------- */
  async function call(name, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[name] !== 'function') throw new Error('The app backend is not ready yet.');
    return api[name](...args);
  }

  /* ---------------- helpers ---------------- */
  function toast(kind, text, ms = 5000) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.dataset.kind = kind;
    t.textContent = text;
    el.toasts.appendChild(t);
    setTimeout(() => {
      t.classList.add('is-leaving');
      setTimeout(() => t.remove(), 220);
    }, ms);
  }

  function addLog(text, level = 'info') {
    const row = document.createElement('div');
    row.className = 'log-row';
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.dataset.level = level;
    chip.textContent = level === 'error' ? 'ERROR' : level === 'warn' ? 'WARN' : 'SYSTEM';
    const msg = document.createElement('span');
    msg.className = 'log-text';
    msg.textContent = text;
    row.append(chip, msg);
    el.log.appendChild(row);
    while (el.log.children.length > 200) el.log.removeChild(el.log.firstChild);
    el.log.scrollTop = el.log.scrollHeight;
    state.logCount += 1;
    if (state.logCount === 1) el.logGuide.hidden = true;
  }

  const hasCreds = () => el.email.value.trim() !== '' && el.password.value !== '';

  /* ---------------- UI state ---------------- */
  function setAccount(kind, message) {
    state.accountState = kind;
    const b = el.accountBadge;
    if (kind === 'connected') { b.textContent = 'Connected'; b.dataset.kind = 'ok'; }
    else if (kind === 'connecting') { b.textContent = 'Connecting…'; b.dataset.kind = 'idle'; }
    else if (kind === 'failed') { b.textContent = 'Login failed'; b.dataset.kind = 'error'; }
    else { b.textContent = 'Not connected'; b.dataset.kind = 'idle'; }
    el.connectBtn.disabled = kind === 'connecting' || state.busy;
    el.connectBtn.textContent = kind === 'connecting' ? 'Connecting…' : 'Connect account';
    updateGate();
  }

  function setPill(kind, text) {
    el.readyPill.dataset.state = kind;
    el.readyPillText.textContent = text;
  }

  function setTransferBadge(kind, text) {
    el.transferBadge.textContent = text;
    el.transferBadge.dataset.kind = kind;
  }

  function setStage(stage) {
    state.stage = stage;
    const map = {
      select:   ['active', 'pending', 'pending'],
      ready:    ['done',   'pending', 'pending'],
      sync:     ['done',   'active',  'pending'],
      download: ['done',   'done',    'active'],
      done:     ['done',   'done',    'done'],
      handoff:  ['done',   'done',    'done'],
    };
    const states = map[stage] || map.select;
    $$('.step').forEach((s, i) => { s.dataset.state = states[i]; });
  }

  function stageFromSelection() { setStage(state.selected >= 0 ? 'ready' : 'select'); }

  function setProgress(pct) {
    const p = Math.max(0, Math.min(100, pct));
    el.fill.style.width = p + '%';
    el.pct.textContent = Math.round(p) + '%';
  }

  function updateSelectionCard() {
    const item = state.items[state.selected];
    if (!item) {
      el.msTitle.textContent = 'No movie selected';
      el.msSub.textContent = state.statusDetail || 'Your next watch starts with a search.';
      return;
    }
    el.msTitle.textContent = item.title;
    el.msSub.textContent = state.statusDetail || `${item.size_gb} GB · ${item.seeds} seeds`;
  }

  function updateGate() {
    const ready = !state.busy && hasCreds() && state.selected >= 0 && el.folder.value.trim() !== '';
    el.processBtn.disabled = !ready;
    if (state.busy) {
      el.gateText.textContent = 'Transfer in progress. Keep the app open until it finishes.';
    } else if (!hasCreds()) {
      el.gateText.textContent = 'Connect your account and select a movie to continue.';
    } else if (state.selected < 0) {
      el.gateText.textContent = 'Search for a movie and choose a version to continue.';
    } else if (!el.folder.value.trim()) {
      el.gateText.textContent = 'Choose a folder to save your movie.';
    } else {
      el.gateText.textContent = 'All set. Sync with Seedr and download your movie.';
    }
    el.searchBtn.disabled = state.searching || state.busy;
    el.searchBtn.textContent = state.searching ? 'Searching…' : 'Search YTS';
    el.browseBtn.disabled = state.busy;
    el.connectBtn.disabled = state.accountState === 'connecting' || state.busy;
    el.processText.textContent = state.busy ? 'Working…' : 'Sync & download movie';
  }

  /* ---------------- dropdown ---------------- */
  function renderVariants() {
    el.variantList.innerHTML = '';
    state.items.forEach((it, idx) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.dataset.index = String(idx);
      li.setAttribute('aria-selected', idx === state.selected ? 'true' : 'false');
      const name = document.createElement('span');
      name.textContent = it.title;
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = `${it.size_gb} GB · ${it.seeds} seeds`;
      li.append(name, meta);
      el.variantList.appendChild(li);
    });
  }

  function setVariantText(text, placeholder) {
    el.variantText.textContent = text;
    el.variantText.classList.toggle('is-placeholder', !!placeholder);
  }

  function openDropdown(open) {
    if (open && (el.variantBtn.disabled || !state.items.length)) return;
    el.variantList.hidden = !open;
    el.variantBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    el.variantBtn.classList.toggle('is-open', open);
  }

  async function chooseVariant(idx) {
    const it = state.items[idx];
    if (!it) return;
    state.selected = idx;
    state.statusDetail = '';
    setVariantText(`${it.title} · ${it.size_gb} GB · ${it.seeds} seeds`, false);
    $$('#variantList li').forEach((li, i) => li.setAttribute('aria-selected', i === idx ? 'true' : 'false'));
    try { await call('select_variant', idx); } catch (e) { toast('error', e.message); }
    updateSelectionCard();
    if (!state.busy) { stageFromSelection(); setProgress(0); setTransferBadge('idle', 'Idle'); }
    updateGate();
  }

  /* ---------------- actions ---------------- */
  async function onConnect() {
    const email = el.email.value.trim();
    const pw = el.password.value;
    if (!email || !pw) { toast('warn', 'Enter your Seedr email and password first.'); return; }
    setAccount('connecting');
    try {
      await call('connect', email, pw, el.remember.checked);
    } catch (e) {
      setAccount('failed');
      toast('error', e.message);
    }
  }

  async function onSearch() {
    const q = el.search.value.trim();
    if (!q) { toast('warn', 'Please enter a movie name to search.'); el.search.focus(); return; }
    if (state.searching || state.busy) return;
    state.searching = true;
    state.items = []; state.selected = -1;
    renderVariants();
    el.variantBtn.disabled = true;
    setVariantText('Searching…', true);
    updateSelectionCard();
    stageFromSelection();
    updateGate();
    try {
      await call('search', q);
    } catch (e) {
      state.searching = false;
      setVariantText('Search for a movie to see available versions', true);
      toast('error', e.message);
      updateGate();
    }
  }

  async function onBrowse() {
    try {
      const f = await call('browse_folder', el.folder.value);
      if (f) { el.folder.value = f; updateGate(); }
    } catch (e) { toast('error', e.message); }
  }

  async function onProcess() {
    if (el.processBtn.disabled) return;
    try {
      const res = await call('start_process', el.email.value.trim(), el.password.value,
        el.remember.checked, el.folder.value.trim(), el.useManager.checked);
      if (res && res.ok === false) toast('error', res.error || 'Could not start the transfer.', 8000);
      else resetTransferView();
    } catch (e) { toast('error', e.message); }
  }

  /* ---- transfer controls ---- */
  function resetTransferView() {
    state.paused = false;
    el.handoff.hidden = true;
    el.statSpeed.textContent = '—'; el.statEta.textContent = '—'; el.statDone.textContent = '—';
    setProgress(0);
    renderPause();
  }

  function renderPause() {
    el.pauseText.textContent = state.paused ? 'Resume' : 'Pause';
    el.pauseIcon.innerHTML = `<use href="#${state.paused ? 'i-play' : 'i-pause'}"/>`;
    $$('.step').forEach((s) => s.querySelector('.step-dot').classList.toggle('is-paused', state.paused && s.dataset.state === 'active'));
  }

  function updateControls() {
    const live = state.busy;
    const dl = state.stageName === 'download';
    el.controls.hidden = !live;
    el.stats.hidden = !(live && dl);
    el.pauseBtn.disabled = !dl;
    el.openMgrBtn.disabled = !dl;
    el.cancelBtn.disabled = !live;
  }

  async function onPause() {
    try {
      const res = state.paused ? await call('resume') : await call('pause');
      if (res && res.ok === false && res.error) toast('warn', res.error);
    } catch (e) { toast('error', e.message); }
  }

  let cancelArmed = null;
  async function onCancel() {
    const label = el.cancelBtn.querySelector('span');
    if (!cancelArmed) {                       // first click: ask for confirmation in place
      label.textContent = 'Click again to cancel';
      cancelArmed = setTimeout(() => { cancelArmed = null; label.textContent = 'Cancel'; }, 4000);
      return;
    }
    clearTimeout(cancelArmed); cancelArmed = null; label.textContent = 'Cancel';
    try { await call('cancel'); } catch (e) { toast('error', e.message); }
  }

  /* ---- download manager (any program the user picks) ---- */
  function renderManager() {
    const m = state.manager;
    el.managerPath.value = m.path || '';
    el.managerPath.title = m.path || '';
    el.openMgrText.textContent = m.name ? `Open in ${m.name}` : 'Open in download manager';
  }

  async function pickManager() {
    const picked = await call('choose_manager');
    if (!picked) return false;
    state.manager = { name: picked.name, path: picked.path };
    renderManager();
    return true;
  }

  // Make sure a manager is chosen; opens the file picker when none is saved. Returns true when ready.
  async function ensureManager() {
    const res = await call('set_use_manager', true);
    if (res && res.ok) return true;
    toast('info', "Select your download manager's program file (for example IDMan.exe).", 7000);
    if (!(await pickManager())) return false;
    const again = await call('set_use_manager', true);
    return !!(again && again.ok);
  }

  async function onChooseManager() {
    try {
      if (await pickManager() && el.useManager.checked) await call('set_use_manager', true);
    } catch (e) { toast('error', e.message); }
  }

  async function onOpenManager() {
    try {
      if (!(await ensureManager())) return;
      const res = await call('open_in_manager');
      if (res && res.ok === false && res.error) toast('warn', res.error);
      else toast('info', `Opening the download in ${state.manager.name || 'your download manager'}…`);
    } catch (e) { toast('error', e.message); }
  }

  async function onUseManagerChange() {
    try {
      if (el.useManager.checked) {
        if (!(await ensureManager())) el.useManager.checked = false;
      } else {
        await call('set_use_manager', false);
      }
    } catch (e) { el.useManager.checked = false; toast('error', e.message); }
  }

  async function onClearStorage() {
    el.clearBtn.disabled = true;
    try {
      const res = await call('clear_seedr_storage', el.email.value.trim(), el.password.value);
      if (res && res.ok === false) { toast('error', res.error || 'Could not clear Seedr storage.'); el.clearBtn.disabled = false; }
    } catch (e) { toast('error', e.message); el.clearBtn.disabled = false; }
  }

  function onNewDownload() {
    if (state.busy) { toast('warn', 'A transfer is running. Wait for it to finish first.'); return; }
    el.search.value = '';
    state.items = []; state.selected = -1; state.statusDetail = '';
    renderVariants();
    el.variantBtn.disabled = true;
    setVariantText('Search for a movie to see available versions', true);
    openDropdown(false);
    setProgress(0);
    setTransferBadge('idle', 'Idle');
    setPill('ready', 'Ready to start');
    el.hintText.textContent = 'Transfer updates will appear here.';
    updateSelectionCard();
    stageFromSelection();
    updateGate();
    $('#sec-search').scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.search.focus();
  }

  /* ---------------- events from Python ---------------- */
  const handlers = {
    log(ev) { addLog(ev.text, ev.level || 'info'); },

    toast(ev) { toast(ev.kind || 'info', ev.text); },

    account(ev) {
      setAccount(ev.state);
      if (ev.state === 'failed' && ev.message) toast('error', ev.message);
      if (ev.state === 'connected') toast('info', 'Connected to Seedr.');
    },

    search_done(ev) {
      state.searching = false;
      state.items = ev.items || [];
      renderVariants();
      if (state.items.length) {
        el.variantBtn.disabled = false;
        chooseVariant(0);
      } else {
        el.variantBtn.disabled = true;
        setVariantText('No versions found under the size limit', true);
        state.selected = -1;
        updateSelectionCard();
        stageFromSelection();
      }
      updateGate();
    },

    busy(ev) {
      state.busy = !!ev.busy;
      if (!state.busy) { state.stageName = ''; state.paused = false; renderPause(); }
      updateGate();
      updateControls();
      el.waitDot.classList.toggle('is-live', state.busy);
    },

    stage(ev) {
      state.stageName = ev.stage;
      setStage(ev.stage);
      updateControls();
      if (ev.stage === 'sync') { setPill('working', 'Syncing with Seedr'); setTransferBadge('ok', 'Working'); }
      if (ev.stage === 'download') { setPill('working', 'Downloading'); setTransferBadge('ok', 'Working'); }
      if (ev.stage === 'handoff') { setPill('ready', 'Sent to download manager'); setTransferBadge('ok', 'Sent'); setProgress(100); }
      if (ev.stage === 'done') { setPill('ready', 'Completed'); setTransferBadge('ok', 'Done'); setProgress(100); }
    },

    paused(ev) {
      state.paused = !!ev.paused;
      renderPause();
      if (state.paused) {
        const lost = ev.reason === 'connection';
        setPill('warn', lost ? 'Connection lost' : 'Paused');
        setTransferBadge(lost ? 'error' : 'idle', lost ? 'Offline' : 'Paused');
        el.waitDot.classList.remove('is-live');
        el.statSpeed.textContent = '—'; el.statEta.textContent = '—';
      } else {
        setPill('working', 'Downloading');
        setTransferBadge('ok', 'Working');
        el.waitDot.classList.add('is-live');
      }
    },

    handoff_cleared() {
      el.handoff.hidden = true;
      el.clearBtn.disabled = false;
      toast('info', 'Seedr storage cleared.');
    },

    status(ev) {
      state.statusDetail = ev.detail || '';
      el.hintText.textContent = ev.detail ? `${ev.title} — ${ev.detail}` : (ev.title || '');
      updateSelectionCard();
    },

    progress(ev) {
      const p = ev.kind === 'download' ? 50 + ev.pct / 2 : ev.kind === 'sync' ? ev.pct / 2 : ev.pct;
      setProgress(p);
      if (ev.kind === 'download') {
        el.statSpeed.textContent = state.paused ? '—' : fmtSpeed(ev.speed);
        el.statEta.textContent = state.paused ? '—' : (ev.eta == null ? 'Calculating…' : fmtEta(ev.eta));
        el.statDone.textContent = ev.total ? `${fmtBytes(ev.done)} / ${fmtBytes(ev.total)}` : fmtBytes(ev.done);
        state.statusDetail = ev.total ? `Downloading ${Math.round(ev.pct)}% · ${fmtBytes(ev.done)} of ${fmtBytes(ev.total)}` : '';
        updateSelectionCard();
      } else if (ev.detail) {
        state.statusDetail = ev.detail;
        updateSelectionCard();
      }
    },

    process_done(ev) {
      state.busy = false;
      state.stageName = '';
      state.paused = false;
      state.statusDetail = '';
      renderPause();
      if (ev.handoff) {
        const name = ev.manager || 'your download manager';
        setStage('handoff'); setPill('ready', `Sent to ${name}`); setTransferBadge('ok', 'Sent'); setProgress(100);
        state.statusDetail = `${name} is downloading the file`;
        el.handoffTitle.textContent = `Sent to ${name}.`;
        el.handoff.hidden = false;
        el.handoff.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        toast('info', `Sent to ${name}. Check its window for progress.`, 8000);
      } else if (ev.success) {
        setStage('done'); setPill('ready', 'Completed'); setTransferBadge('ok', 'Done'); setProgress(100);
        toast('info', 'Movie downloaded to ' + (ev.path || 'your folder'), 9000);
      } else if (ev.cancelled) {
        setPill('ready', 'Cancelled'); setTransferBadge('idle', 'Cancelled');
        stageFromSelection(); setProgress(0);
        toast('warn', 'Transfer cancelled.');
      } else {
        setPill('error', 'Needs attention'); setTransferBadge('error', 'Failed');
        stageFromSelection();
        toast('error', ev.error || 'The transfer failed.', 9000);
      }
      updateSelectionCard();
      el.waitDot.classList.remove('is-live');
      updateGate();
      updateControls();
    },
  };

  window.__emit = (ev) => {
    try { const h = handlers[ev && ev.type]; if (h) h(ev); }
    catch (e) { console.error('event error', e); }
  };

  /* ---------------- wire up ---------------- */
  el.pwToggle.addEventListener('click', () => {
    const show = el.password.type === 'password';
    el.password.type = show ? 'text' : 'password';
    el.pwIcon.innerHTML = `<use href="#${show ? 'i-eye-off' : 'i-eye'}"/>`;
    el.pwToggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });

  el.connectBtn.addEventListener('click', onConnect);
  el.searchBtn.addEventListener('click', onSearch);
  el.search.addEventListener('keydown', (e) => { if (e.key === 'Enter') onSearch(); });
  el.browseBtn.addEventListener('click', onBrowse);
  el.processBtn.addEventListener('click', onProcess);
  $('#navNew').addEventListener('click', onNewDownload);
  el.pauseBtn.addEventListener('click', onPause);
  el.cancelBtn.addEventListener('click', onCancel);
  el.openMgrBtn.addEventListener('click', onOpenManager);
  el.managerBtn.addEventListener('click', onChooseManager);
  el.useManager.addEventListener('change', onUseManagerChange);
  el.clearBtn.addEventListener('click', onClearStorage);

  [el.email, el.password].forEach((i) => i.addEventListener('input', () => {
    if (state.accountState !== 'idle' && state.accountState !== 'connecting') setAccount('idle');
    updateGate();
  }));
  el.password.addEventListener('keydown', (e) => { if (e.key === 'Enter') onConnect(); });

  $$('[data-open-seedr]').forEach((b) => b.addEventListener('click', () => {
    call('open_seedr').catch((e) => toast('error', e.message));
  }));
  $$('[data-scroll]').forEach((b) => b.addEventListener('click', () => {
    const t = document.getElementById(b.dataset.scroll);
    if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));

  // dropdown behaviour
  el.variantBtn.addEventListener('click', () => openDropdown(el.variantList.hidden));
  el.variantList.addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (!li) return;
    openDropdown(false);
    chooseVariant(Number(li.dataset.index));
  });
  document.addEventListener('click', (e) => { if (!el.variant.contains(e.target)) openDropdown(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') openDropdown(false); });

  /* ---------------- init ---------------- */
  async function init() {
    updateSelectionCard();
    setStage('select');
    updateGate();
    try {
      const s = await call('get_state');
      if (s) {
        if (s.email) el.email.value = s.email;
        if (s.password) el.password.value = s.password;
        el.remember.checked = !!s.remember;
        el.folder.value = s.folder || '';
        state.manager = { name: s.manager_name || '', path: s.manager_path || '' };
        renderManager();
        el.useManager.checked = !!(s.use_manager && s.manager_path);
        if (s.notice) addLog(s.notice, 'warn');
      }
    } catch (e) { /* running outside the desktop app */ }
    updateGate();
  }

  if (window.pywebview && window.pywebview.api) init();
  else window.addEventListener('pywebviewready', init);
})();
