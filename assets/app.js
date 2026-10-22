const $ = (id) => document.getElementById(id);
const PAGE_SIZE = 60;
const countFormatter = new Intl.NumberFormat('en-US');
const countLabel = (count, noun) => `${countFormatter.format(count)} ${noun}${count === 1 ? '' : 's'}`;
const state = {
  view: 'papers',
  theme: 'system',
  manifest: null,
  journalData: {},
  journals: [],
  unfollowedJournals: [],
  articles: [],
  dayCache: new Map(),
  failedDates: [],
  loading: false,
  search: '',
  page: 1,
  selection: new Set(),
  archive: {
    items: {},
    cleared_items: {},
    favorites: {}
  },
  archiveAvailable: false,
  archiveRevision: 0,
  archiveDatasetId: 'initial',
  busy: false,
  saveProgress: null,
  admin: null,
  adminLoaded: false,
  adminBusy: false,
  poll: null,
  llmDirty: false,
  filterDirty: false,
  refreshAfterJob: false,
  refreshStarting: false,
  metricsStarting: false,
  metricsJob: null,
  metricsError: '',
  filterIds: new Set(),
  prompt: '',
  match: null,
};
const escape = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const icons = {
  // Lucide Settings, ISC license: assets/icons.LICENSE.txt.
  settings: '<path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"/><circle cx="12" cy="12" r="3"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1.5"/><path d="M5 8v12h14V8M10 12h4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || ''}</svg>`;
const time = (value) => {
  if (!value || Number.isNaN(Date.parse(value))) return 'Not checked';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
};
const compactTime = value => !value || Number.isNaN(Date.parse(value)) ? 'Not checked' :
  new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Shanghai', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}).format(new Date(value));
const dataDate = value => !value || Number.isNaN(Date.parse(value)) ? 'Not checked' :
  new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Shanghai', day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(value));

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {}
  return null;
}

function setTheme(value) {
  state.theme = ['system', 'light', 'dark'].includes(value) ? value : 'system';
  document.documentElement.dataset.theme = state.theme === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : state.theme;
  storage('paper-tracker-theme', state.theme);
  document.querySelector('meta[name="theme-color"]').content = document.documentElement.dataset.theme === 'dark' ? '#13191f' : '#edf1f3';
  document.querySelectorAll('[data-theme]').forEach(button => {
    if (button === document.documentElement) return;
    const active = button.dataset.theme === state.theme;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}
async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 30000);
  try {
    const response = await fetch(path, {
      cache: 'no-store',
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      }
    });
    let body;
    try {
      body = await response.json();
    } catch {
      throw new Error(`Invalid service response (${response.status})`);
    }
    if (!response.ok) throw new Error(englishMessage(body.error, `Service returned ${response.status}`));
    return body;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please try again.');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
const adminRequest = (path, payload) => request(`api/admin${path}`, payload === undefined ? {} : {
  method: 'POST',
  body: JSON.stringify(payload)
});

function englishMessage(value, fallback) {
  // Older running services may still return localized operational messages.
  return typeof value === 'string' && value.trim() && !/\p{Script=Han}/u.test(value) ? value : fallback;
}
let noticeTimer;
let stickyFrame = null;

function updateStickyOffsets() {
  if (stickyFrame !== null) return;
  stickyFrame = requestAnimationFrame(() => {
    stickyFrame = null;
    const navigation = document.querySelector('.site-header');
    const navHeight = getComputedStyle(navigation).position === 'sticky' ? Math.ceil(navigation.getBoundingClientRect().height) : 0;
    const saveHeight = $('save-status').hidden ? 0 : Math.ceil($('save-status').getBoundingClientRect().height);
    const selectionHeight = $('selection-bar').hidden ? 0 : Math.ceil($('selection-bar').getBoundingClientRect().height) + 16;
    const headers = [...document.querySelectorAll('.journal-header')];
    const journalHeight = Math.max(0, ...headers.map(header => Math.ceil(header.getBoundingClientRect().height)));
    document.documentElement.style.setProperty('--navigation-height', `${navHeight}px`);
    document.documentElement.style.setProperty('--save-status-height', `${saveHeight}px`);
    document.documentElement.style.setProperty('--journal-sticky-top', `${navHeight + saveHeight + selectionHeight}px`);
    document.documentElement.style.setProperty('--journal-header-height', `${journalHeight}px`);
  });
}

function notify(message, error = false) {
  $('notice').hidden = false;
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  clearTimeout(noticeTimer);
  if (!error) noticeTimer = setTimeout(() => $('notice').hidden = true, 5000);
}

function inline(id, message, error = false) {
  $(id).textContent = message;
  $(id).classList.toggle('error', error);
}

function safeURL(value) {
  try {
    const u = new URL(value);
    return ['https:', 'http:'].includes(u.protocol) ? u.href : '#';
  } catch {
    return '#';
  }
}

function syncURL() {
  const p = new URLSearchParams();
  if (state.view !== 'papers') p.set('view', state.view);
  if (state.search) p.set('q', state.search);
  if (state.page > 1) p.set('page', state.page);
  history.replaceState(null, '', `${location.pathname}${p.size?'?'+p:''}`);
}

function resetReading() {
  state.page = 1;
  state.selection.clear();
  renderReading();
  syncURL();
}

function isRead(id) {
  return Boolean(state.archive.items[id] || state.archive.cleared_items[id]);
}

function selectedRecords() {
  const all = new Map([...state.articles, ...Object.values(state.archive.items), ...Object.values(state.archive.favorites)].map(a => [a.id, a]));
  return [...state.selection].map(id => all.get(id)).filter(Boolean);
}

function unreadArticles() {
  const followed = new Set(state.journals.map(journal => journal.id));
  return state.articles
    .filter(article => followed.has(article.journal_id) && !isRead(article.id))
    .filter(article => !article.content_type || article.content_type === 'article')
    .filter(article => article.published_date >= state.manifest.retention.start && article.published_date <= state.manifest.retention.end);
}

function filteredArticles() {
  const favorites = state.view === 'favorites';
  const records = favorites ?
    Object.values(state.archive.favorites) :
    unreadArticles();
  const query = state.search.trim().toLocaleLowerCase();
  return records.filter(article => !query || [article.title_en, article.title_zh].some(title => (title || '').toLocaleLowerCase().includes(query)))
    .sort(articleOrder);
}

function journalArchiveControl(journalId, count) {
  const disabled = state.loading || state.failedDates.length || state.busy || !state.archiveAvailable || !count;
  const journalName = state.journals.find(journal => journal.id === journalId)?.name || journalId;
  const scope = `Archive all ${countLabel(count, 'unread paper')} in ${journalName}, including other pages and search results.`;
  return `<button class="button journal-archive-button" type="button" data-archive-journal="${escape(journalId)}"
    aria-label="${escape(scope)}" title="${escape(disabled && (state.loading || state.failedDates.length) ? 'Load all paper data before archiving a journal.' : scope)}" ${disabled ? 'disabled' : ''}>
    ${icon('archive')}<span>Archive all <span class="count">${countFormatter.format(count)}</span></span>
  </button>`;
}

function articleOrder(a, b) {
  const compare = (x, y) => x === y ? 0 : x < y ? -1 : 1;
  // Always use publication dates; undated papers follow dated papers.
  const publishedDate = article => article.published_date || article.date || article.archived_date || '';
  return compare(a.journal_id || '', b.journal_id || '') || compare(publishedDate(b), publishedDate(a)) || compare(a.id, b.id);
}

function badges(journal) {
  const m = journal.impact_factor || {};
  const status = ({
    seeded_unverified: 'not checked',
    check_failed: 'check failed',
    not_found: 'not confirmed',
    year_unverified: 'update unconfirmed'
  })[m.status] || (m.checked_at ? 'not confirmed' : 'not checked');
  const metric = m.value === null || m.value === undefined ? `IF ${status}` : `IF ${escape(m.value)} · ${escape(m.year || 'year unknown')}${m.status==='verified'?'':' · '+status}`;
  return `<div class="journal-badges"><span class="badge zone" lang="zh-CN">中科院 ${escape(journal.cas?.zone || '—')} 区 · ${escape(journal.cas?.category || '待核实')}</span>${journal.cas?.top?'<span class="badge top">Top</span>':''}<span class="badge" title="${escape(m.checked_at?'Checked '+time(m.checked_at):'IF not checked')}">${metric}</span>${journal.group?`<span class="badge" lang="zh-CN">${escape(journal.group)}</span>`:''}</div>`;
}

function paperMarkup(a) {
  const favorite = Boolean(state.archive.favorites[a.id]);
  const href = escape(safeURL(a.url || (a.doi ? `https://doi.org/${a.doi}` : '')));
  return `<li class="article-item"><label class="article-check"><input type="checkbox" data-select="${escape(a.id)}" ${state.selection.has(a.id)?'checked':''} ${state.busy?'disabled':''} /><span class="sr-only">Select ${escape(a.title_en)}</span></label><div class="article-body">${a.title_zh?`<a class="article-title" lang="zh-CN" href="${href}" target="_blank" rel="noopener noreferrer">${escape(a.title_zh)}</a>`:''}<a class="article-title-en${a.title_zh?'':' primary-title'}" href="${href}" target="_blank" rel="noopener noreferrer">${escape(a.title_en)}</a><div class="article-footnote"><span>${escape(a.published_date || a.date || '')}</span>${!a.title_zh?'<span>Translation pending</span>':''}</div></div><div class="article-actions"><button class="article-action${favorite?' is-favorite':''}" data-favorite="${escape(a.id)}" aria-label="${favorite?'Remove favorite':'Favorite paper'}" aria-pressed="${favorite}" title="${favorite?'Remove favorite':'Favorites'}" ${state.busy||!state.archiveAvailable?'disabled':''}>${icon('star')}</button></div></li>`;
}

function renderSelection() {
  $('selection-bar').hidden = state.selection.size === 0;
  $('selection-count').textContent = `${state.selection.size} selected`;
  $('bulk-favorite').disabled = state.busy || !state.archiveAvailable;
  document.querySelectorAll('[data-select]').forEach(input => input.checked = state.selection.has(input.dataset.select));
  document.querySelectorAll('[data-select-group]').forEach(input => {
    const ids = [...input.closest('.journal-section').querySelectorAll('[data-select]')].map(x => x.dataset.select);
    input.checked = ids.every(id => state.selection.has(id));
    input.indeterminate = !input.checked && ids.some(id => state.selection.has(id));
  });
  updateStickyOffsets();
}

function renderCounts() {
  $('journal-count').textContent = state.journals.length || '';
  $('favorite-count').textContent = Object.keys(state.archive.favorites).length || '';
}

function renderSaveProgress() {
  const progress = state.saveProgress;
  $('save-status').hidden = !progress;
  if (progress) {
    $('save-label').textContent = progress.phase === 'checking' ? 'Checking saved papers…' : ({
      add: 'Archiving papers…',
      favorite: 'Saving favorites…',
      unfavorite: 'Removing favorites…'
    })[progress.action] || 'Saving changes…';
    $('save-count').textContent = `${countFormatter.format(progress.confirmed)} / ${countLabel(progress.total, 'paper')} confirmed`;
    $('save-progress').setAttribute('aria-valuemax', progress.total);
    $('save-progress').setAttribute('aria-valuenow', progress.confirmed);
    $('save-fill').style.transform = `scaleX(${progress.confirmed / progress.total})`;
    $('save-detail').textContent = progress.phase === 'checking' ?
      'Checking the server. Keep this page open.' : progress.slow ?
      `Slow connection · Batch ${progress.batch} of ${progress.batches}. Keep this page open.` :
      `Batch ${progress.batch} of ${progress.batches} · Keep this page open.`;
  }
  updateStickyOffsets();
}

function renderReading() {
  if (!state.manifest) return;
  renderSaveProgress();
  const favorites = state.view === 'favorites';
  $('paper-search').placeholder = favorites ? 'Search favorite titles…' : 'Search titles…';
  $('paper-search').setAttribute('aria-label', favorites ? 'Search favorite titles in English or Chinese' : 'Search titles in English or Chinese');
  $('paper-search-form').title = favorites ? 'All favorites' : `${state.manifest.retention.start} — ${state.manifest.retention.end}`;
  $('load-status').textContent = state.loading ? 'Loading…' : state.failedDates.length ? `${countLabel(state.failedDates.length, 'day')} unavailable. Results incomplete.` : '';
  if (state.busy && !state.saveProgress) $('load-status').textContent += `${$('load-status').textContent ? ' · ' : ''}Saving…`;
  $('load-status').hidden = !$('load-status').textContent;
  if (state.failedDates.length && !state.loading) {
    const retry = document.createElement('button');
    retry.className = 'button quiet';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => loadWindow());
    $('load-status').append(retry);
  }
  const records = filteredArticles(),
    pages = Math.max(1, Math.ceil(records.length / PAGE_SIZE));
  state.page = Math.min(Math.max(state.page, 1), pages);
  const visible = records.slice((state.page - 1) * PAGE_SIZE, state.page * PAGE_SIZE);
  const visibleCounts = new Map();
  const unreadCounts = new Map();
  for (const article of records) visibleCounts.set(article.journal_id, (visibleCounts.get(article.journal_id) || 0) + 1);
  if (!favorites)
    for (const article of unreadArticles()) unreadCounts.set(article.journal_id, (unreadCounts.get(article.journal_id) || 0) + 1);
  $('results-summary').textContent = countLabel(records.length, favorites ? 'favorite' : 'paper');
  $('results-summary').hidden = favorites && !state.search;
  $('select-visible').disabled = !visible.length || state.busy;
  $('paper-list').setAttribute('aria-busy', String(state.loading || state.busy));
  if (!visible.length) {
    let title = state.loading ? 'Loading…' : favorites ? 'No favorites yet' : state.failedDates.length ? 'No papers loaded' : 'All caught up';
    let message = !state.loading && favorites ? 'Star a paper to save it.' : '';
    if (!state.loading && state.search) {
      title = 'No matches';
      message = 'Try another title.';
    }
    if (!state.archiveAvailable && favorites) {
      title = 'Favorites unavailable';
      message = 'Reload to retry.';
    }
    $('paper-list').innerHTML = `<div class="empty-state"><strong>${title}</strong>${message ? `<span>${message}</span>` : ''}</div>`;
  } else {
    const groups = new Map();
    for (const a of visible) {
      if (!groups.has(a.journal_id)) groups.set(a.journal_id, []);
      groups.get(a.journal_id).push(a);
    }
    $('paper-list').innerHTML = [...groups].map(([id, items]) => {
      const j = [...state.journals, ...state.unfollowedJournals].find(j => j.id === id) || {
        name: id
      };
      return `<section class="journal-section"><header class="journal-header${favorites ? ' is-favorites' : ''}"><div><h2 class="journal-name">${escape(j.name)} <span class="count">${countLabel(visibleCounts.get(id), 'paper')}</span></h2>${badges(j)}</div><div class="journal-heading-actions">${favorites ? `<label class="journal-selection"><input type="checkbox" data-select-group="${escape(id)}" ${state.busy?'disabled':''} /><span class="sr-only">Select this journal on this page</span></label>` : journalArchiveControl(id, unreadCounts.get(id) || 0)}</div></header><ol class="article-list">${items.map(paperMarkup).join('')}</ol></section>`;
    }).join('');
  }
  $('pagination').hidden = pages === 1;
  $('page-number').textContent = `${state.page} / ${pages}`;
  $('previous-page').disabled = state.page <= 1;
  $('next-page').disabled = state.page >= pages;
  renderSelection();
  renderCounts();
}

function renderMetrics() {
  const job = state.admin?.job;
  if (state.admin) state.metricsJob = job?.kind === 'metrics' || job?.stage === 'metrics' ? job : null;
  const metricsJob = state.metricsJob;
  const running = state.metricsStarting || metricsJob?.status === 'running';
  $('update-metrics').setAttribute('aria-busy', String(Boolean(running)));
  $('metrics-spinner').hidden = !running;
  $('metrics-label').textContent = state.metricsStarting ? 'Starting…' : running ? 'Updating IF…' : 'Update IF';
  const status = $('metrics-status');
  status.hidden = !state.metricsStarting && !metricsJob && !state.metricsError;
  const progress = state.metricsStarting || state.metricsError ? null : metricsJob?.metrics_progress;
  const parts = [];
  if (state.metricsError) parts.push(state.metricsError);
  else if (state.metricsStarting) parts.push('Starting IF update…');
  else if (!state.admin && running) parts.push('Connection lost. Reconnecting…');
  else if (metricsJob?.status === 'failed') parts.push('IF update failed');
  else if (metricsJob?.status === 'interrupted') parts.push('IF update interrupted');
  else if (metricsJob?.status === 'success') parts.push('IF check finished');
  if (progress || running) {
    parts.push(`${Number(progress?.completed || 0)}/${Number(progress?.total ?? state.journals.length)} journals checked`);
    if (progress?.failed) parts.push(`${Number(progress.failed)} failed`);
    if (progress?.unconfirmed) parts.push(`${Number(progress.unconfirmed)} not confirmed`);
  }
  status.textContent = parts.join(' · ');
  status.title = running && progress?.current_journal ? `Checking ${progress.current_journal}` : '';
}

function renderJournals() {
  renderMetrics();
  $('metrics-version').textContent = `CAS 2025 · ${state.journalData.metrics_checked_at ? 'IF checked ' + dataDate(state.journalData.metrics_checked_at) : 'IF not checked'}`;
  $('metrics-version').title = `IF last checked: ${time(state.journalData.metrics_checked_at)}`;
  $('following-list').innerHTML = state.journals.length ? state.journals.map(j => `<article class="following-item"><div><h3>${escape(j.name)}</h3>${badges(j)}${j.impact_factor?.checked_at ? `<p class="journal-data-date">Checked ${escape(dataDate(j.impact_factor.checked_at))}</p>` : ''}</div><button class="button quiet" data-unfollow="${escape(j.id)}" ${!state.admin||state.admin.job?.status==='running'?'disabled':''}>Unfollow</button></article>`).join('') : '<div class="empty-state"><strong>No journals yet</strong></div>';
  renderCounts();
}

function switchView(view) {
  state.view = ['journals', 'papers', 'favorites'].includes(view) ? view : 'papers';
  state.selection.clear();
  state.page = 1;
  document.querySelectorAll('[data-view]').forEach(b => {
    const active = b.dataset.view === state.view;
    b.classList.toggle('is-active', active);
    if (active) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  $('journals-view').hidden = state.view !== 'journals';
  $('reading-view').hidden = state.view === 'journals';
  $('paper-update-bar').hidden = state.view !== 'papers';
  $('run-now').hidden = state.view !== 'papers';
  $('page-title').textContent = {journals: 'Journals', papers: 'New papers', favorites: 'Favorites'}[state.view];
  renderJournals();
  renderReading();
  syncURL();
}

function renderUpdate() {
  const m = state.manifest;
  if (!m) return;
  const s = m.collection_summary || {},
    ok = Number(s.ok || 0),
    failed = Number(s.failed || 0);
  const dateParts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
  const now = Date.now();
  const one = new Date(`${today}T01:00:00+08:00`).getTime();
  const expected = now < one ? one - 24 * 60 * 60 * 1000 : one;
  const checked = Date.parse(m.collection_checked_at || m.updated_at || '');
  const stale = !Number.isFinite(checked) || checked < expected;
  const job = state.admin?.job;
  const running = job?.status === 'running';
  const paperUpdate = running && ['manual', 'daily', 'daily-catchup', 'backfill'].includes(job.kind);
  const progress = paperUpdate ? job.collection_progress : null;
  const completed = Number(progress?.completed || 0);
  const total = Number(progress?.total ?? state.journals.length);
  const currentFailed = Number(progress?.failed || 0);
  const journalSummary = paperUpdate ? `${completed}/${total} journals checked` : `${ok}/${ok + failed} journals`;
  const displayedFailed = paperUpdate ? currentFailed : failed;
  const task = ({metrics: 'IF update', add: 'Journal setup', delete: 'Unfollow', 'restore-filtered': 'Paper restore'})[job?.kind] || 'Paper update';
  const paperStage = ['manual', 'daily', 'daily-catchup', 'backfill'].includes(job?.kind) && ({collect: 'Collecting and filtering papers…', translate: 'Translating titles…', metrics: 'Updating IF…', validate: 'Checking papers…'})[job?.stage];
  const active = paperStage || ({metrics: 'Updating IF…', add: 'Adding journal…', delete: 'Unfollowing journal…', 'restore-filtered': 'Restoring papers…'})[job?.kind] || 'Updating papers…';
  const jobMessage = running ? active : job?.status === 'failed' ? `${task} failed` : job?.status === 'interrupted' ? `${task} interrupted` : state.adminLoaded && !state.admin ? 'Management offline' : '';
  const jobMarkup = jobMessage ? `<span class="job-status${running ? '' : ' failure'}">${running ? '<span class="saving-spinner" aria-hidden="true"></span>' : ''}${escape(jobMessage)}</span>` : '';
  $('update-status').title = `Collection: ${m.default_date} · Updated: ${time(m.updated_at)}`;
  $('update-status').innerHTML = `<span class="status-dot${displayedFailed||stale?' is-warning':''}" aria-hidden="true"></span><time datetime="${escape(m.updated_at || '')}">Updated ${escape(compactTime(m.updated_at))}</time><span>${journalSummary}</span>${!paperUpdate&&ok+failed===0?'<span>Not collected</span>':''}${stale&&!jobMessage?'<span class="failure">Delayed</span>':''}${displayedFailed?`<span class="failure">${displayedFailed} failed</span>`:''}${jobMarkup}`;
}
async function loadMetadata() {
  const [manifest, journalData] = await Promise.all([request('data/manifest.json'), request('data/journals.json')]);
  state.manifest = manifest;
  state.journalData = journalData;
  state.journals = journalData.journals || [];
  state.unfollowedJournals = journalData.unfollowed_journals || [];
  renderUpdate();
  renderJournals();
}
let windowRequest = 0;
async function loadWindow() {
  const generation = ++windowRequest;
  state.loading = true;
  state.failedDates = [];
  renderReading();
  const dates = (state.manifest.available_dates || []).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d) && d >= state.manifest.retention.start && d <= state.manifest.retention.end).sort().reverse();
  let cursor = 0,
    done = 0;
  const articles = new Map();
  await Promise.all(Array.from({
    length: Math.min(6, dates.length)
  }, async () => {
    while (cursor < dates.length) {
      const date = dates[cursor++];
      try {
        const day = state.dayCache.get(date) || await request(`data/days/${date}.json`);
        if (generation !== windowRequest) return;
        state.dayCache.set(date, day);
        for (const a of day.articles || []) {
          articles.set(a.id, a);
        }
      } catch {
        if (generation === windowRequest) state.failedDates.push(date);
      }
      done++;
      if (generation === windowRequest && state.view === 'papers') $('load-status').textContent = `Loaded ${done}/${dates.length} days…`;
    }
  }));
  if (generation !== windowRequest) return;
  state.articles = [...articles.values()].sort(articleOrder);
  state.loading = false;
  renderReading();
}

function articlePayload(a) {
  return {
    id: a.id,
    journal_id: a.journal_id,
    date: a.published_date || a.archived_date || a.date || null,
    title_en: a.title_en || '',
    title_zh: a.title_zh || '',
    url: a.url || '',
    doi: a.doi || '',
    translation_status: a.translation_status || (a.title_zh ? 'translated' : 'pending'),
    date_inferred: Boolean(a.date_inferred),
    date_inference: a.date_inference || null,
    first_seen_at: a.first_discovered_at || a.first_seen_at || null
  };
}

function applyArchiveLocally(action, records) {
  const timestamp = new Date().toISOString();
  state.archive = {
    ...state.archive,
    items: {
      ...state.archive.items
    },
    cleared_items: {
      ...state.archive.cleared_items
    },
    favorites: {
      ...state.archive.favorites
    },
  };
  for (const record of records) {
    const id = typeof record === 'string' ? record : record.id;
    if (action === 'add' || action === 'favorite') {
      const bucket = action === 'add' ? state.archive.items : state.archive.favorites;
      const existing = bucket[id] || (action === 'add' && state.archive.cleared_items[id]) || {};
      const dateKey = action === 'add' ? 'archived_at' : 'favorited_at';
      bucket[id] = {
        ...existing,
        ...articlePayload(record),
        [dateKey]: existing[dateKey] || timestamp
      };
      if (action === 'add') {
        delete state.archive.cleared_items[id];
        delete bucket[id].cleared_at;
      }
    } else if (action === 'unfavorite') {
      delete state.archive.favorites[id];
    }
  }
}
async function mutateArchive(action, records) {
  if (state.busy || !records.length) return;
  const snapshot = state.archive;
  state.archiveRevision++;
  state.busy = true;
  state.saveProgress = {
    action, total: records.length, confirmed: 0, batch: 1,
    batches: Math.ceil(records.length / 128), phase: 'saving', slow: false
  };
  let waitTimer;
  applyArchiveLocally(action, records);
  state.selection.clear();
  renderReading();
  try {
    const items = ['add', 'favorite'].includes(action);
    const payload = {
      action,
      ...(items ? {
        items: records.map(articlePayload)
      } : {
        ids: records.map(a => typeof a === 'string' ? a : a.id)
      })
    };
    // One journal may contain thousands of papers. Keep each atomic API write
    // below the request limit while a single click completes the whole journal.
    const values = payload.items || payload.ids;
    for (let offset = 0; offset < values.length; offset += 128) {
      if (state.saveProgress) {
        state.saveProgress.batch = Math.floor(offset / 128) + 1;
        state.saveProgress.slow = false;
        renderSaveProgress();
        waitTimer = setTimeout(() => {
          state.saveProgress.slow = true;
          renderSaveProgress();
        }, 8000);
      }
      const batch = {
        action,
        dataset_id: state.archiveDatasetId,
        [items ? 'items' : 'ids']: values.slice(offset, offset + 128)
      };
      const response = await request('api/archive', {
        method: 'POST',
        headers: {
          Prefer: 'return=minimal'
        },
        body: JSON.stringify(batch),
        timeout: 60000
      });
      clearTimeout(waitTimer);
      state.archive.updated_at = response.updated_at;
      if (state.saveProgress) {
        state.saveProgress.confirmed += batch[items ? 'items' : 'ids'].length;
        renderSaveProgress();
      }
    }
    const message = ({
      add: 'Archived',
      favorite: 'Saved to favorites',
      unfavorite: 'Favorite removed'
    })[action];
    notify(action === 'add' ? `${message} ${countLabel(records.length, 'paper')}` : message);
  } catch (error) {
    clearTimeout(waitTimer);
    if (state.saveProgress) {
      state.saveProgress.phase = 'checking';
      renderSaveProgress();
    }
    state.archive = snapshot;
    let reconciled = false;
    try {
      const saved = await request('api/archive');
      state.archiveDatasetId = saved.dataset_id || 'initial';
      state.archive = {
        items: saved.items || {},
        cleared_items: saved.cleared_items || {},
        favorites: saved.favorites || {},
        updated_at: saved.updated_at
      };
      reconciled = true;
    } catch {
      /* Keep the snapshot if the saved state cannot be confirmed. */
    }
    notify(`Save not confirmed: ${error.message}. ${reconciled ? 'Saved state reloaded. Retry any remaining papers.' : 'Refresh to check saved state before retrying.'}`, true);
  } finally {
    clearTimeout(waitTimer);
    state.busy = false;
    state.saveProgress = null;
    renderReading();
    renderCounts();
    syncURL();
  }
}

function findArticle(id) {
  return state.articles.find(a => a.id === id) || state.archive.items[id] || state.archive.favorites[id];
}
async function loadArchive() {
  if (state.busy) return;
  const revision = state.archiveRevision;
  try {
    const archive = await request('api/archive');
    if (revision !== state.archiveRevision) return;
    state.archiveDatasetId = archive.dataset_id || 'initial';
    state.archive = {
      items: archive.items || {},
      cleared_items: archive.cleared_items || {},
      favorites: archive.favorites || {}
    };
    state.archiveAvailable = true;
  } catch {
    if (revision !== state.archiveRevision) return;
    state.archiveAvailable = false;
    notify('The archive service is unavailable. Favorites and archives are temporarily disabled.', true);
  }
  renderCounts();
}

function renderAdmin() {
  const a = state.admin,
    running = a?.job?.status === 'running';
  for (const id of ['match-journal', 'run-now', 'update-metrics', 'save-filter', 'llm-save', 'llm-disable', 'llm-verify']) $(id).disabled = !a || running || state.adminBusy;
  const refreshing = state.refreshStarting || (running && ['manual', 'daily', 'daily-catchup', 'backfill'].includes(a.job.kind));
  $('run-now').setAttribute('aria-busy', String(Boolean(refreshing)));
  $('refresh-spinner').hidden = !refreshing;
  $('refresh-label').textContent = state.refreshStarting ? 'Starting…' : refreshing ? 'Refreshing…' : 'Refresh';
  for (const id of ['llm-base-url', 'llm-model', 'llm-api-key', 'edit-rules']) $(id).disabled = !a || running || state.adminBusy;
  const matchButton = $('confirm-follow');
  if (matchButton) matchButton.disabled = !a || running || state.adminBusy;
  renderUpdate();
  if (!state.llmDirty) {
    $('llm-base-url').value = a?.llm?.base_url || '';
    $('llm-model').value = a?.llm?.model || '';
    $('llm-api-key').value = '';
  }
  $('llm-api-key').placeholder = a?.llm?.api_key_set ? 'Saved; leave blank to keep it' : 'Enter API key';
  $('llm-summary').textContent = a?.llm?.configured ? 'Configured' : 'Not configured';
  if (!state.filterDirty) {
    state.filterIds = new Set(a?.filter_journal_ids || []);
    state.prompt = a?.filter_prompt || '';
  }
  $('rules-summary').textContent = state.prompt ? 'Custom rules' : 'Default rules';
  $('filter-excluded-count').textContent = `Filtered out in the current window · ${countLabel(Number(a?.excluded_count || 0), 'title')}`;
  $('filter-summary').textContent = `Journals · ${state.filterIds.size}/${state.journals.length}`;
  // Preserve focus and open controls while polling; list changes only when its data changes.
  const filterSignature = JSON.stringify([state.journals.map(j => j.id), [...state.filterIds], Boolean(a), running, state.adminBusy]);
  if ($('filter-journal-list').dataset.signature !== filterSignature) {
    $('filter-journal-list').innerHTML = state.journals.map(j => `<label class="filter-option"><input type="checkbox" data-filter-setting="${escape(j.id)}" ${state.filterIds.has(j.id)?'checked':''} ${!a||running||state.adminBusy?'disabled':''} /><span>${escape(j.name)}</span></label>`).join('');
    $('filter-journal-list').dataset.signature = filterSignature;
  }
  renderJournals();
}

async function loadAdmin() {
  clearTimeout(state.poll);
  const wasRunning = state.admin?.job?.status === 'running' || state.metricsJob?.status === 'running';
  const previousJob = state.admin?.job || state.metricsJob;
  try {
    state.admin = await adminRequest('/journals');
    state.adminLoaded = true;
    renderAdmin();
    const newlyCompleted = previousJob && state.admin.job?.finished_at && previousJob.finished_at !== state.admin.job.finished_at;
    if ((wasRunning || state.refreshAfterJob || newlyCompleted) && state.admin.job?.status !== 'running') {
      await loadMetadata();
      state.dayCache.clear();
      state.selection.clear();
      await Promise.all([loadWindow(), loadArchive()]);
      renderReading();
      state.refreshAfterJob = false;
      if (state.admin.job?.kind === 'manual') {
        const failed = Number(state.manifest.collection_summary?.failed || 0);
        if (state.admin.job.status === 'failed') notify('Paper refresh failed. Check the task status and retry.', true);
        else if (failed) notify(`Refresh finished with ${countLabel(failed, 'failed journal')}. You can retry.`, true);
        else if (state.failedDates.length) notify('Papers updated, but some dates could not be loaded. Retry loading the list.', true);
        else notify('Papers refreshed');
      }
    }
  } catch {
    state.admin = null;
    state.adminLoaded = true;
    renderAdmin();
    if (wasRunning || state.refreshAfterJob) state.poll = setTimeout(loadAdmin, 7000);
  }
  if (state.admin?.job?.status === 'running') state.poll = setTimeout(loadAdmin, 3000);
}
async function startJob(path, payload = {}) {
  if (state.adminBusy || !state.admin || state.admin.job?.status === 'running') return;
  state.adminBusy = true;
  state.refreshStarting = path === '/run';
  state.metricsStarting = path === '/metrics/update';
  if (state.metricsStarting) {
    state.metricsError = '';
    state.metricsJob = null;
  }
  renderAdmin();
  try {
    state.admin = await adminRequest(path, payload);
    state.refreshAfterJob = true;
    renderAdmin();
    notify('Update started');
    clearTimeout(state.poll);
    state.poll = setTimeout(loadAdmin, 1500);
    return true;
  } catch (e) {
    if (path === '/metrics/update') state.metricsError = e.message;
    notify(e.message, true);
  } finally {
    state.adminBusy = false;
    state.refreshStarting = false;
    state.metricsStarting = false;
    renderAdmin();
  }
}
async function matchJournal(event) {
  event.preventDefault();
  state.match = null;
  state.adminBusy = true;
  renderAdmin();
  inline('journal-match', 'Matching…');
  try {
    const result = await adminRequest('/journals/resolve', {
      name: $('journal-name').value.trim()
    });
    state.match = result;
    const existing = state.journals.some(j => (j.issns || []).some(issn => result.issns.includes(issn)));
    $('journal-match').innerHTML = `<div class="match-result"><strong>${escape(result.name)}</strong>${badges(result)}${existing?'<p class="muted">Already following</p>':''}${!existing?'<label class="check-label"><input id="follow-filter" type="checkbox" />Apply title filter</label><div class="form-actions"><button class="button primary" id="confirm-follow">Follow</button></div>':''}</div>`;
  } catch (e) {
    inline('journal-match', e.message, true);
  } finally {
    state.adminBusy = false;
    renderAdmin();
  }
}
async function followJournal() {
  if (!state.match) return;
  const started = await startJob('/journals', {
    name: state.match.name,
    filter_enabled: $('follow-filter')?.checked || false
  });
  if (started) {
    state.match = null;
    $('journal-match').replaceChildren();
    $('journal-name').value = '';
  }
}

function llmPayload() {
  return {
    base_url: $('llm-base-url').value.trim(),
    model: $('llm-model').value.trim(),
    api_key: $('llm-api-key').value
  };
}
async function modelAction(verify = false) {
  if (!$('llm-form').reportValidity()) return;
  state.adminBusy = true;
  renderAdmin();
  inline('llm-status', verify ? 'Testing the model and structured responses…' : 'Saving…');
  try {
    const payload = llmPayload();
    const result = await adminRequest(verify ? '/llm/verify' : '/llm', payload);
    if (verify) inline('llm-status', 'Connection verified');
    else {
      state.llmDirty = false;
      $('llm-api-key').value = '';
      state.admin = result;
      inline('llm-status', 'Saved');
    }
  } catch (e) {
    inline('llm-status', e.message, true);
  } finally {
    state.adminBusy = false;
    renderAdmin();
  }
}
async function saveFilter() {
  state.adminBusy = true;
  renderAdmin();
  try {
    state.admin = await adminRequest('/filter', {
      journal_ids: [...state.filterIds],
      prompt: state.prompt
    });
    state.filterDirty = false;
    inline('filter-status', 'Saved');
  } catch (e) {
    inline('filter-status', e.message, true);
  } finally {
    state.adminBusy = false;
    renderAdmin();
  }
}

function closeDialog(dialog, event) {
  if (event.target !== dialog) return;
  const r = dialog.getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
}

function bindEvents() {
  window.addEventListener('resize', updateStickyOffsets);
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(updateStickyOffsets);
    observer.observe(document.querySelector('.site-header'));
    observer.observe($('selection-bar'));
    observer.observe($('save-status'));
  }
  document.addEventListener('keydown', e => {
    document.documentElement.classList.add('keyboard');
    if (e.key === 'Tab') document.documentElement.classList.add('keyboard-navigation');
  });
  document.addEventListener('pointerdown', () => document.documentElement.classList.remove('keyboard', 'keyboard-navigation'), {capture: true});
  document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));
  document.querySelectorAll('button[data-theme]').forEach(b => b.addEventListener('click', () => setTheme(b.dataset.theme)));
  $('paper-search-form').addEventListener('submit', event => {
    event.preventDefault();
    state.search = $('paper-search').value.trim();
    resetReading();
  });
  $('paper-search').addEventListener('input', () => {
    if (!$('paper-search').value && state.search) {
      state.search = '';
      resetReading();
    }
  });
  for (const [id, delta] of [
      ['previous-page', -1],
      ['next-page', 1]
    ]) $(id).addEventListener('click', () => {
    state.page += delta;
    renderReading();
    syncURL();
    $('reading-view').scrollIntoView({
      block: 'start'
    });
  });
  $('paper-list').addEventListener('change', e => {
    const input = e.target;
    if (input.dataset.select) {
      input.checked ? state.selection.add(input.dataset.select) : state.selection.delete(input.dataset.select);
    }
    if (input.dataset.selectGroup) {
      input.closest('.journal-section').querySelectorAll('[data-select]').forEach(x => input.checked ? state.selection.add(x.dataset.select) : state.selection.delete(x.dataset.select));
    }
    renderSelection();
  });
  $('paper-list').addEventListener('click', e => {
    const journalButton = e.target.closest('[data-archive-journal]');
    if (journalButton) {
      if (!journalButton.disabled) mutateArchive('add', unreadArticles().filter(article => article.journal_id === journalButton.dataset.archiveJournal));
      return;
    }
    const b = e.target.closest('[data-favorite]');
    if (!b || b.disabled) return;
    const id = b.dataset.favorite;
    const article = findArticle(id);
    if (!article) return;
    mutateArchive(state.archive.favorites[id] ? 'unfavorite' : 'favorite', [article]);
  });
  $('select-visible').addEventListener('click', () => {
    document.querySelectorAll('[data-select]').forEach(x => state.selection.add(x.dataset.select));
    renderSelection();
  });
  $('clear-selection').addEventListener('click', () => {
    state.selection.clear();
    renderSelection();
  });
  $('bulk-favorite').addEventListener('click', () => mutateArchive('favorite', selectedRecords()));
  $('settings-button').addEventListener('click', () => {
    $('settings-dialog').showModal();
    loadAdmin();
  });
  $('close-settings').addEventListener('click', () => $('settings-dialog').close());
  for (const id of ['settings-dialog', 'rules-dialog']) $(id).addEventListener('click', e => closeDialog($(id), e));
  $('settings-dialog').addEventListener('close', () => {
    $('llm-api-key').value = '';
  });
  $('journal-search-form').addEventListener('submit', matchJournal);
  $('journal-match').addEventListener('click', e => {
    if (e.target.closest('#confirm-follow')) followJournal();
  });
  $('journal-name').addEventListener('input', () => {
    state.match = null;
    $('journal-match').replaceChildren();
  });
  $('following-list').addEventListener('click', async e => {
    const b = e.target.closest('[data-unfollow]');
    if (!b) return;
    const j = state.journals.find(j => j.id === b.dataset.unfollow);
    if (!confirm(`Unfollow ${j.name}? Favorites will be kept.`)) return;
    try {
      state.admin = await request(`api/admin/journals/${encodeURIComponent(j.id)}`, {
        method: 'DELETE'
      });
      state.refreshAfterJob = true;
      renderAdmin();
      state.poll = setTimeout(loadAdmin, 1500);
    } catch (e) {
      notify(e.message, true);
    }
  });
  $('run-now').addEventListener('click', () => startJob('/run', {mode: 'daily'}));
  $('update-metrics').addEventListener('click', () => startJob('/metrics/update'));
  $('llm-form').addEventListener('submit', e => {
    e.preventDefault();
    modelAction();
  });
  $('llm-verify').addEventListener('click', () => modelAction(true));
  for (const id of ['llm-base-url', 'llm-model', 'llm-api-key']) $(id).addEventListener('input', () => {
    state.llmDirty = true;
    inline('llm-status', 'Unsaved changes');
  });
  $('llm-disable').addEventListener('click', async () => {
    try {
      state.admin = await adminRequest('/llm/disable', {});
      state.llmDirty = false;
      renderAdmin();
      inline('llm-status', 'Disabled');
    } catch (e) {
      inline('llm-status', e.message, true);
    }
  });
  $('edit-rules').addEventListener('click', () => {
    $('filter-prompt').value = state.prompt;
    $('rules-dialog').returnValue = 'cancel';
    $('rules-dialog').showModal();
  });
  $('rules-dialog').addEventListener('close', () => {
    if ($('rules-dialog').returnValue === 'apply') {
      state.prompt = $('filter-prompt').value.trim();
      state.filterDirty = true;
      renderAdmin();
      inline('filter-status', 'Unsaved changes');
    }
  });
  $('filter-journal-list').addEventListener('change', e => {
    const id = e.target.dataset.filterSetting;
    if (!id) return;
    e.target.checked ? state.filterIds.add(id) : state.filterIds.delete(id);
    state.filterDirty = true;
    $('filter-summary').textContent = `Journals · ${state.filterIds.size}/${state.journals.length}`;
    inline('filter-status', 'Unsaved changes');
  });
  $('save-filter').addEventListener('click', saveFilter);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      loadAdmin();
      loadArchive().then(renderReading);
    }
  });
}
async function start() {
  $('settings-button').innerHTML = icon('settings');
  $('close-settings').innerHTML = icon('close');
  $('search-icon').innerHTML = icon('search');
  setTheme(storage('paper-tracker-theme') || 'system');
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (state.theme === 'system') setTheme('system');
  });
  bindEvents();
  const params = new URLSearchParams(location.search);
  const legacy = params.get('view');
  state.view = ['library', 'archive', 'favorites'].includes(legacy) ? 'favorites' : legacy === 'history' ? 'papers' : ['papers', 'journals'].includes(legacy) ? legacy : 'papers';
  state.search = params.get('q') || '';
  $('paper-search').value = state.search;
  try {
    await loadMetadata();
    await loadArchive();
    switchView(state.view);
    await Promise.all([loadWindow(), loadAdmin()]);
    const requestedPage = Number(params.get('page'));
    state.page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
    renderReading();
    syncURL();
  } catch (e) {
    $('paper-list').innerHTML = `<div class="empty-state"><strong>Paper data is unavailable</strong><span>${escape(e.message)}. Check the data directory and service status.</span></div>`;
    $('update-status').textContent = 'Data is not ready';
    $('paper-list').setAttribute('aria-busy', 'false');
    await loadAdmin();
  }
}
window.addEventListener('beforeunload', e => {
  if (state.busy) {
    e.preventDefault();
    e.returnValue = 'Saving';
  }
});
start();
