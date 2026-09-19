const ARCHIVE_API = "api/archive";

const STORAGE_KEYS = {
  hiddenJournals: "paper-tracker-hidden-journals",
  settingsGroups: "paper-tracker-settings-journal-groups",
  theme: "paper-tracker-theme",
};

const state = {
  view: "daily",
  supplement: "late_additions",
  date: null,
  manifest: null,
  journals: [],
  journalMap: new Map(),
  hidden: new Set(),
  selected: new Set(),
  day: null,
  supplements: null,
  theme: "system",
  settingsGroups: {},
  archive: { items: {}, updated_at: null },
  archiveSelection: new Set(),
};

const elements = {
  list: document.querySelector("#paper-list"),
  heading: document.querySelector("#content-heading"),
  datePicker: document.querySelector("#date-picker"),
  previous: document.querySelector("#previous-day"),
  next: document.querySelector("#next-day"),
  yesterday: document.querySelector("#return-yesterday"),
  summary: document.querySelector("#toolbar-summary"),
  status: document.querySelector("#status-strip"),
  updateStatus: document.querySelector("#update-status"),
  archiveToolbar: document.querySelector("#archive-toolbar"),
  archiveCount: document.querySelector("#archive-count"),
  archiveSelection: document.querySelector("#archive-selection"),
  archiveSelectAll: document.querySelector("#archive-select-all"),
  archiveNone: document.querySelector("#archive-none"),
  archiveClear: document.querySelector("#archive-clear"),
  archiveRestore: document.querySelector("#archive-restore"),
  dailyToolbar: document.querySelector("#daily-toolbar"),
  supplementToolbar: document.querySelector("#supplement-toolbar"),
  supplementCount: document.querySelector("#supplement-count"),
  sourceNote: document.querySelector("#source-note"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsJournalList: document.querySelector("#settings-journal-list"),
  settingsJournalSummary: document.querySelector("#settings-journal-summary"),
};

const escapeHTML = (value = "") => String(value).replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

const formatDate = (value, options = {}) => new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "long", day: "numeric", weekday: "long", ...options,
}).format(new Date(`${value}T12:00:00+08:00`));

const formatDateTime = (value) => {
  if (!value) return "时间待确认";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间待确认";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "numeric", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(parsed);
};

const addDays = (value, amount) => {
  const date = new Date(`${value}T12:00:00+08:00`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
};

function readStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Private browsing modes can deny localStorage; the page still works per session.
  }
}

function parseStorageJSON(key, fallback) {
  try {
    const value = JSON.parse(readStorage(key) || "null");
    return value ?? fallback;
  } catch (error) {
    return fallback;
  }
}

function visibleJournals() {
  return state.journals.filter((journal) => !state.hidden.has(journal.id));
}

function normalizeJournalPreferences() {
  const ids = new Set(state.journals.map((journal) => journal.id));
  state.hidden = new Set([...state.hidden].filter((id) => ids.has(id)));
  if (state.journals.length && state.hidden.size === state.journals.length) {
    state.hidden.delete(state.journals[0].id);
  }
  state.selected = new Set(visibleJournals().map((journal) => journal.id));
}

function persistHiddenJournals() {
  writeStorage(STORAGE_KEYS.hiddenJournals, JSON.stringify([...state.hidden]));
}

function readThemePreference() {
  const stored = readStorage(STORAGE_KEYS.theme);
  return ["light", "dark", "system"].includes(stored) ? stored : "system";
}

function applyTheme(preference = state.theme) {
  const resolved = preference === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
  document.documentElement.dataset.theme = resolved;
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.content = resolved === "dark" ? "#121714" : "#f7f8f5";
  document.querySelectorAll("[data-theme-option]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.themeOption === preference);
    button.setAttribute("aria-pressed", String(button.dataset.themeOption === preference));
  });
}

function setTheme(preference) {
  if (!["light", "dark", "system"].includes(preference)) return;
  state.theme = preference;
  writeStorage(STORAGE_KEYS.theme, preference);
  applyTheme(preference);
}

async function fetchJSON(path, optional = false) {
  const response = await fetch(path, { cache: "no-store" });
  if (optional && response.status === 404) return null;
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function setLoading() {
  elements.list.replaceChildren(document.querySelector("#loading-template").content.cloneNode(true));
}

function syncURL() {
  const params = new URLSearchParams();
  if (state.view !== "daily") params.set("view", state.view);
  if (state.view === "daily" && state.date && state.date !== state.manifest.default_date) {
    params.set("date", state.date);
  }
  if (state.view === "supplements") params.set("type", state.supplement);
  history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}`);
}

function renderIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function groupBy(items, key) {
  return items.reduce((groups, item) => {
    const value = item[key] || "其他";
    (groups[value] ||= []).push(item);
    return groups;
  }, {});
}

function renderSettings() {
  const groups = groupBy(state.journals, "group");
  const visibleCount = visibleJournals().length;
  elements.settingsJournalSummary.textContent = `显示 ${visibleCount} / ${state.journals.length} 本`;
  elements.settingsJournalList.innerHTML = Object.entries(groups).map(([group, journals], index) => `
    <details class="settings-journal-group" data-settings-group="${escapeHTML(group)}" ${(state.settingsGroups[group] ?? index === 0) ? "open" : ""}>
      <summary class="settings-group-heading"><span>${escapeHTML(group)}</span><span>${journals.length}</span></summary>
      ${journals.map((journal) => `
        <label class="settings-journal-option">
          <input type="checkbox" data-journal-visibility="${escapeHTML(journal.id)}" ${!state.hidden.has(journal.id) ? "checked" : ""} />
          <span><strong>${escapeHTML(journal.name)}</strong><small>${escapeHTML(journal.cas?.zone ?? "-")}区 · ${escapeHTML(journal.cas?.category ?? "")}</small></span>
        </label>`).join("")}
    </details>`).join("");
  elements.settingsJournalList.querySelectorAll(".settings-journal-group").forEach((group) => {
    group.addEventListener("toggle", () => {
      state.settingsGroups[group.dataset.settingsGroup] = group.open;
      writeStorage(STORAGE_KEYS.settingsGroups, JSON.stringify(state.settingsGroups));
    });
  });
  elements.settingsJournalList.querySelectorAll("[data-journal-visibility]").forEach((input) => {
    input.addEventListener("change", () => {
      const journalId = input.dataset.journalVisibility;
      if (!input.checked && visibleJournals().length <= 1) {
        input.checked = true;
        return;
      }
      if (input.checked) {
        state.hidden.delete(journalId);
      } else {
        state.hidden.add(journalId);
      }
      normalizeJournalPreferences();
      persistHiddenJournals();
      renderSettings();
      renderCurrent();
      syncURL();
    });
  });
}

function journalHeader(journal, count, options = {}) {
  const metric = journal.impact_factor || {};
  const hasMetric = metric.value !== undefined && metric.value !== null && metric.value !== "";
  const control = options.journalControl || "";
  return `
    <header class="journal-header">
      <div>
        <h2 class="journal-name">${escapeHTML(journal.name)}</h2>
        <div class="journal-meta">
          <span class="meta-badge zone">中科院 ${escapeHTML(journal.cas?.zone ?? "-")} 区 · ${escapeHTML(journal.cas?.category ?? "")}</span>
          ${journal.cas?.top ? '<span class="meta-badge top">Top</span>' : ""}
          ${hasMetric ? `<span class="meta-badge">IF ${escapeHTML(metric.value)} · ${escapeHTML(metric.year || "年份待核实")}</span>` : ""}
        </div>
      </div>
      <div class="journal-actions">
        <span class="journal-count">${count} 篇</span>
        ${control}
      </div>
    </header>`;
}

// 期刊级「全部已读」勾选：勾上即把本刊当前列表整批归档
function journalArchiveControl(journalId) {
  return `<label class="journal-check" title="勾选表示本刊这些论文都已读过，整批归档">
    <input type="checkbox" data-archive-journal="${escapeHTML(journalId)}" />
    <span>全部已读</span>
  </label>`;
}

function articleMarkup(article, supplementType = null, options = {}) {
  const href = escapeHTML(article.url || "#");
  // 只要有中文标题就展示；归档条目可能不带 translation_status（历史数据兼容）
  const translated = Boolean(article.title_zh);
  const detail = supplementType === "late_additions"
    ? `归档日期 ${escapeHTML(article.archived_date || article.published_date || "未知")}`
    : supplementType === "date_pending" ? `日期精度 ${escapeHTML(article.date_precision || "缺失")}` : "";
  const control = options.control || null;
  const footnote = options.footnote || "";
  return `<li class="article-item${control ? " has-check" : ""}">
    ${control || ""}
    <div class="article-body">
      ${translated ? `<a class="article-title-zh" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_zh)}</a>` : `<span class="pending-translation">中文翻译处理中</span>`}
      <a class="article-title-en" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_en || "无标题")}</a>
      ${(detail || article.doi || footnote) ? `<div class="article-footnote">${detail ? `<span>${detail}</span>` : ""}${article.doi ? `<span>DOI ${escapeHTML(article.doi)}</span>` : ""}${footnote ? `<span>${footnote}</span>` : ""}</div>` : ""}
    </div>
  </li>`;
}

// 单篇「已读」勾选
function articleArchiveControl(articleId) {
  return `<label class="article-check" title="勾选表示这篇已读过，归档后可在「已归档」中找回">
    <input type="checkbox" data-archive-article="${escapeHTML(articleId)}" />
    <span class="sr-only">标记为已读</span>
  </label>`;
}

function renderGrouped(articles, supplementType = null, options = {}) {
  const archivable = Boolean(options.archivable);
  const visible = articles.filter((article) => (
    state.selected.has(article.journal_id)
    && article.content_type === "article"
    && !(archivable && isArchived(article.id))
  ));
  if (!visible.length) {
    elements.list.innerHTML = `<div class="empty-state"><div><i data-lucide="inbox"></i><strong>${archivable ? "本日论文已全部归档" : "当前范围没有论文"}</strong><span>${archivable ? "可到「已归档」查看或恢复。" : "可调整日期或期刊筛选。"}</span></div></div>`;
    renderIcons();
    return 0;
  }
  const groups = visible.reduce((all, article) => {
    (all[article.journal_id] ||= []).push(article); return all;
  }, {});
  elements.list.innerHTML = Object.entries(groups).sort(([a], [b]) => {
    return state.journalMap.get(a).name.localeCompare(state.journalMap.get(b).name);
  }).map(([journalId, items]) => {
    const journal = state.journalMap.get(journalId);
    if (!journal) return "";
    return `<section class="journal-section" id="journal-${escapeHTML(journalId)}">
      ${journalHeader(journal, items.length, { journalControl: archivable ? journalArchiveControl(journalId) : "" })}
      <ol class="article-list">${items.map((item) => articleMarkup(item, supplementType, {
        control: archivable ? articleArchiveControl(item.id) : null,
      })).join("")}</ol>
    </section>`;
  }).join("");
  renderIcons();
  return visible.length;
}

function renderDaily() {
  const articles = state.day?.articles || [];
  const count = articles.filter((article) => (
    state.selected.has(article.journal_id)
    && article.content_type === "article"
    && !isArchived(article.id)
  )).length;
  const archivedCount = articles.filter((article) => (
    state.selected.has(article.journal_id)
    && article.content_type === "article"
    && isArchived(article.id)
  )).length;
  const archivedNote = archivedCount ? ` · 已归档 ${archivedCount} 篇` : "";
  elements.heading.innerHTML = `<h1>${escapeHTML(formatDate(state.date))}</h1><p>${count} 篇新论文 · 按首次在线发表日期归档${archivedNote}</p>`;
  elements.summary.textContent = state.manifest.updated_at ? `最近更新 ${formatDateTime(state.manifest.updated_at)}` : "";
  const failures = Object.values(state.day?.journal_status || {}).filter((status) => status.status === "failed").length;
  elements.status.hidden = failures === 0;
  if (failures) {
    elements.status.innerHTML = `<i data-lucide="triangle-alert"></i><span>${failures} 本期刊本次获取失败，现有数据已保留。</span>`;
  } else {
    elements.status.replaceChildren();
  }
  renderGrouped(articles, null, { archivable: true });
}

function renderSupplements() {
  const articles = state.supplements?.[state.supplement] || [];
  const label = state.supplement === "late_additions" ? "迟到补录" : "日期待核实";
  elements.heading.innerHTML = `<h1>${label}</h1><p>${articles.length} 条记录 · ${state.supplement === "late_additions" ? "已补回原发表日期" : "尚未进入每日归档"}</p>`;
  elements.status.hidden = true;
  elements.status.replaceChildren();
  renderGrouped(articles, state.supplement);
}

function isArchived(articleId) {
  return Boolean(articleId && state.archive.items[articleId]);
}

function archiveItemsVisible() {
  return Object.values(state.archive.items)
    .filter((item) => state.selected.has(item.journal_id))
    .sort((left, right) => String(right.archived_at || "").localeCompare(String(left.archived_at || "")));
}

function renderArchive() {
  const items = archiveItemsVisible();
  const total = Object.keys(state.archive.items).length;
  const hidden = total - items.length;
  elements.heading.innerHTML = `<h1>已归档</h1><p>${items.length} 篇已读论文${hidden > 0 ? ` · 另有 ${hidden} 篇被期刊筛选隐藏` : ""}</p>`;
  elements.status.hidden = true;
  elements.status.replaceChildren();

  if (!items.length) {
    elements.list.innerHTML = `<div class="empty-state"><div><i data-lucide="archive"></i><strong>${total ? "当前筛选下没有已归档论文" : "还没有已归档的论文"}</strong><span>${total ? "可在设置中恢复期刊显示。" : "在「每日论文」里勾选读过的论文或整本期刊即可归档。"}</span></div></div>`;
    updateArchiveSelectionUI();
    renderIcons();
    return;
  }

  const groups = items.reduce((all, item) => {
    (all[item.journal_id] ||= []).push(item); return all;
  }, {});

  elements.list.innerHTML = Object.entries(groups).sort(([a], [b]) => {
    const nameA = state.journalMap.get(a)?.name || a;
    const nameB = state.journalMap.get(b)?.name || b;
    return nameA.localeCompare(nameB);
  }).map(([journalId, entries]) => {
    const journal = state.journalMap.get(journalId);
    const name = journal?.name || journalId;
    const allSelected = entries.every((entry) => state.archiveSelection.has(entry.id));
    return `<section class="journal-section" id="journal-${escapeHTML(journalId)}">
      ${journalHeader(journal || { name, cas: {}, impact_factor: {} }, entries.length, {
        journalControl: `<label class="journal-check" title="选择本刊全部已归档论文">
          <input type="checkbox" data-archive-group="${escapeHTML(journalId)}" ${allSelected ? "checked" : ""} />
          <span>选择本刊</span>
        </label>`,
      })}
      <ol class="article-list">${entries.map((entry) => articleMarkup(entry, null, {
        control: `<label class="article-check" title="选择这篇以恢复">
          <input type="checkbox" data-archive-item="${escapeHTML(entry.id)}" ${state.archiveSelection.has(entry.id) ? "checked" : ""} />
          <span class="sr-only">选择这篇</span>
        </label>`,
        footnote: entry.archived_at ? `归档于 ${escapeHTML(formatDateTime(entry.archived_at))}` : "",
      })).join("")}</ol>
    </section>`;
  }).join("");
  updateArchiveSelectionUI();
  renderIcons();
}

function updateArchiveSelectionUI() {
  const count = state.archiveSelection.size;
  elements.archiveSelection.textContent = count ? `已选 ${count} 篇` : "未选择";
  elements.archiveRestore.disabled = count === 0;
  elements.archiveRestore.textContent = count ? `恢复所选 (${count})` : "恢复所选";
}

function renderCurrent() {
  if (state.view === "daily") renderDaily();
  else if (state.view === "archive") renderArchive();
  else renderSupplements();
  renderIcons();
}

async function loadDay(value) {
  if (!value) value = state.manifest.default_date;
  if (value < state.manifest.retention.start) value = state.manifest.retention.start;
  if (value > state.manifest.retention.end) value = state.manifest.retention.end;
  state.date = value;
  elements.datePicker.value = value;
  elements.previous.disabled = value <= state.manifest.retention.start;
  elements.next.disabled = value >= state.manifest.retention.end;
  setLoading();
  try {
    state.day = await fetchJSON(`data/days/${value}.json`, true) || { date: value, articles: [], journal_status: {} };
    renderDaily();
  } catch (error) {
    elements.list.innerHTML = `<div class="error-state"><div><i data-lucide="cloud-off"></i><strong>无法加载该日数据</strong><span>${escapeHTML(error.message)}</span></div></div>`;
    renderIcons();
  }
  syncURL();
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view-tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  elements.dailyToolbar.hidden = view !== "daily";
  elements.supplementToolbar.hidden = view !== "supplements";
  elements.archiveToolbar.hidden = view !== "archive";
  renderCurrent();
  syncURL();
}

function renderUpdateStatus(manifest) {
  if (!manifest) {
    elements.updateStatus.innerHTML = '<span class="update-dot is-unknown" aria-hidden="true"></span><span class="update-label">数据 · 状态暂不可用</span>';
    return;
  }
  const summary = manifest.collection_summary || {};
  const okCount = Number(summary.ok || 0);
  const failedCount = Number(summary.failed || 0);
  const total = okCount + failedCount;
  const stateKey = failedCount > 0 ? "failure" : "success";
  const labels = { success: "正常", failure: "部分失败", unknown: "未知" };
  const timestamp = manifest.updated_at;
  const failedDetail = failedCount > 0 ? ` · 失败 ${failedCount}` : "";
  elements.updateStatus.innerHTML = `
    <span class="update-dot status-${stateKey}" aria-hidden="true"></span>
    <span class="update-label">采集 · ${labels[stateKey]}</span>
    <span class="update-detail">${okCount}/${total} 期刊${failedDetail}</span>
    <time datetime="${escapeHTML(timestamp || "")}">${escapeHTML(formatDateTime(timestamp))}</time>`;
}

// 请求失败分类：请求本身有错（重试无意义）标 fatal，网络类失败可重试
function fatalError(message) {
  const error = new Error(message);
  error.fatal = true;
  return error;
}

const ARCHIVE_TIMEOUT_MS = 20000;
const ARCHIVE_ATTEMPTS = 2;

async function postArchiveOnce(payload, body) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), ARCHIVE_TIMEOUT_MS) : null;
  try {
    const response = await fetch(ARCHIVE_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body,
      ...(controller ? { signal: controller.signal } : {}),
    });
    if (response.ok) return response.json();
    let detail = `服务返回 ${response.status}`;
    try {
      const parsed = await response.json();
      if (parsed && parsed.error) detail = parsed.error;
    } catch (error) { /* 非 JSON 响应，保留状态码 */ }
    // 4xx 是请求/数据问题，重试没有意义
    if (response.status < 500) throw fatalError(detail);
    throw new Error(`${detail}（服务端错误，已重试）`);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function postArchive(payload) {
  const body = JSON.stringify(payload);
  let lastError = null;
  for (let attempt = 1; attempt <= ARCHIVE_ATTEMPTS; attempt += 1) {
    try {
      return await postArchiveOnce(payload, body);
    } catch (error) {
      if (error && error.fatal) throw error;
      lastError = error;
      if (attempt < ARCHIVE_ATTEMPTS) await new Promise((resolve) => setTimeout(resolve, 700));
    }
  }
  const reason = lastError && lastError.name === "AbortError"
    ? `请求超时（${ARCHIVE_TIMEOUT_MS / 1000} 秒）`
    : "网络连接失败";
  throw new Error(`${reason}，已自动重试仍失败`);
}

let pendingRetry = null;

function showError(message, retry) {
  pendingRetry = typeof retry === "function" ? retry : null;
  elements.status.hidden = false;
  elements.status.innerHTML = `<i data-lucide="triangle-alert"></i><span>${escapeHTML(message)}</span>${pendingRetry ? '<button class="text-button compact-button" type="button" id="status-retry">重试</button>' : ""}`;
  const button = elements.status.querySelector("#status-retry");
  if (button && pendingRetry) {
    button.addEventListener("click", () => {
      const retry = pendingRetry;
      elements.status.hidden = true;
      elements.status.replaceChildren();
      pendingRetry = null;
      retry();
    });
  }
  renderIcons();
}

function updateArchiveCount() {
  const count = Object.keys(state.archive.items).length;
  elements.archiveCount.hidden = count === 0;
  elements.archiveCount.textContent = count;
}

async function loadArchive() {
  try {
    const data = await fetchJSON(ARCHIVE_API);
    state.archive = { items: data.items || {}, updated_at: data.updated_at || null };
  } catch (error) {
    state.archive = { items: {}, updated_at: null };
  }
  updateArchiveCount();
}

async function archiveArticles(articles) {
  const candidates = articles.filter((article) => article && article.id && !isArchived(article.id));
  if (!candidates.length) return true;
  try {
    const data = await postArchive({
      action: "add",
      items: candidates.map((article) => ({
        id: article.id,
        journal_id: article.journal_id,
        date: article.archived_date || article.published_date || state.date || null,
        title_en: article.title_en || "",
        title_zh: article.title_zh || "",
        url: article.url || "",
        doi: article.doi || "",
        translation_status: article.translation_status || (article.title_zh ? "translated" : "pending"),
      })),
    });
    state.archive = { items: data.items || {}, updated_at: data.updated_at || null };
  } catch (error) {
    if (window.console && console.error) console.error("[paper-tracker] archive failed", error);
    showError(`归档失败：${error.message}`, () => archiveArticles(articles));
    return false;
  }
  updateArchiveCount();
  renderCurrent();
  return true;
}

async function restoreArticles(ids) {
  if (!ids.length) return true;
  const snapshot = [...ids];
  try {
    const data = await postArchive({ action: "remove", ids });
    state.archive = { items: data.items || {}, updated_at: data.updated_at || null };
  } catch (error) {
    if (window.console && console.error) console.error("[paper-tracker] restore failed", error);
    showError(`恢复失败：${error.message}`, () => restoreArticles(snapshot));
    return false;
  }
  ids.forEach((id) => state.archiveSelection.delete(id));
  updateArchiveCount();
  renderCurrent();
  return true;
}

function bindEvents() {
  document.querySelectorAll(".view-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  elements.previous.addEventListener("click", () => loadDay(addDays(state.date, -1)));
  elements.next.addEventListener("click", () => loadDay(addDays(state.date, 1)));
  elements.yesterday.addEventListener("click", () => loadDay(state.manifest.default_date));
  elements.datePicker.addEventListener("change", () => loadDay(elements.datePicker.value));
  document.querySelectorAll("[data-supplement]").forEach((button) => button.addEventListener("click", () => {
    state.supplement = button.dataset.supplement;
    document.querySelectorAll("[data-supplement]").forEach((item) => item.classList.toggle("is-active", item === button));
    renderSupplements(); syncURL();
  }));
  document.querySelector("#settings-button").addEventListener("click", () => {
    renderSettings();
    elements.settingsDialog.showModal();
  });
  document.querySelector("#close-settings").addEventListener("click", () => elements.settingsDialog.close());
  elements.settingsDialog.addEventListener("click", (event) => {
    if (event.target === elements.settingsDialog) elements.settingsDialog.close();
  });
  elements.list.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.dataset.archiveArticle) {
      const article = (state.day?.articles || []).find((item) => item.id === target.dataset.archiveArticle);
      if (target.checked && article) {
        target.disabled = true;
        archiveArticles([article]).then((ok) => {
          if (!ok) target.checked = false;
          target.disabled = false;
        });
      } else if (!target.checked) {
        target.checked = false;
      }
      return;
    }
    if (target.dataset.archiveJournal) {
      const journalId = target.dataset.archiveJournal;
      const articles = (state.day?.articles || []).filter((item) => item.journal_id === journalId && item.content_type === "article");
      if (target.checked) {
        target.disabled = true;
        archiveArticles(articles).then((ok) => {
          if (!ok) target.checked = false;
          target.disabled = false;
        });
      }
      return;
    }
    if (target.dataset.archiveItem) {
      const id = target.dataset.archiveItem;
      if (target.checked) state.archiveSelection.add(id);
      else state.archiveSelection.delete(id);
      const section = target.closest(".journal-section");
      if (section) {
        const boxes = [...section.querySelectorAll("[data-archive-item]")];
        const group = section.querySelector("[data-archive-group]");
        if (group) group.checked = boxes.length > 0 && boxes.every((box) => box.checked);
      }
      updateArchiveSelectionUI();
      return;
    }
    if (target.dataset.archiveGroup) {
      const journalId = target.dataset.archiveGroup;
      archiveItemsVisible()
        .filter((item) => item.journal_id === journalId)
        .forEach((item) => {
          if (target.checked) state.archiveSelection.add(item.id);
          else state.archiveSelection.delete(item.id);
        });
      renderArchive();
    }
  });

  elements.archiveSelectAll.addEventListener("click", () => {
    archiveItemsVisible().forEach((item) => state.archiveSelection.add(item.id));
    renderArchive();
  });
  elements.archiveNone.addEventListener("click", () => {
    state.archiveSelection.clear();
    renderArchive();
  });
  elements.archiveClear.addEventListener("click", () => {
    const ids = Object.keys(state.archive.items);
    if (!ids.length) return;
    if (!window.confirm(`确定清空全部 ${ids.length} 篇归档记录？论文会重新出现在「每日论文」中。`)) return;
    state.archiveSelection.clear();
    restoreArticles(ids);
  });
  elements.archiveRestore.addEventListener("click", () => {
    restoreArticles([...state.archiveSelection]);
  });

  document.querySelector("#restore-journals").addEventListener("click", () => {
    state.hidden.clear();
    state.selected = new Set(state.journals.map((journal) => journal.id));
    persistHiddenJournals();
    renderSettings();
    renderCurrent();
    syncURL();
  });
  document.querySelectorAll("[data-theme-option]").forEach((button) => button.addEventListener("click", () => {
    setTheme(button.dataset.themeOption);
  }));
}

async function start() {
  setLoading();
  state.theme = readThemePreference();
  applyTheme(state.theme);
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystemThemeChange = () => { if (state.theme === "system") applyTheme("system"); };
  if (systemTheme.addEventListener) systemTheme.addEventListener("change", onSystemThemeChange);
  else systemTheme.addListener(onSystemThemeChange);
  try {
    const [manifest, journalData, supplements] = await Promise.all([
      fetchJSON("data/manifest.json"), fetchJSON("data/journals.json"), fetchJSON("data/supplements.json", true),
    ]);
    state.manifest = manifest;
    state.journals = journalData.journals || [];
    state.journalMap = new Map(state.journals.map((journal) => [journal.id, journal]));
    state.hidden = new Set(parseStorageJSON(STORAGE_KEYS.hiddenJournals, []));
    state.settingsGroups = parseStorageJSON(STORAGE_KEYS.settingsGroups, {});
    normalizeJournalPreferences();
    state.supplements = supplements || { late_additions: [], date_pending: [] };
    await loadArchive();
    const params = new URLSearchParams(location.search);
    const requestedView = params.get("view");
    state.view = ["supplements", "archive"].includes(requestedView) ? requestedView : "daily";
    state.supplement = params.get("type") === "date_pending" ? "date_pending" : "late_additions";
    state.date = params.get("date") || manifest.default_date;
    elements.datePicker.min = manifest.retention.start;
    elements.datePicker.max = manifest.retention.end;
    elements.sourceNote.textContent = journalData.source.label;
    const supplementsTotal = (manifest.late_addition_count || 0) + (manifest.date_pending_count || 0);
    elements.supplementCount.hidden = supplementsTotal === 0;
    elements.supplementCount.textContent = supplementsTotal;
    renderSettings();
    bindEvents();
    document.querySelectorAll("[data-supplement]").forEach((item) => item.classList.toggle("is-active", item.dataset.supplement === state.supplement));
    if (state.view === "daily") await loadDay(state.date);
    switchView(state.view);
    renderUpdateStatus(state.manifest);
  } catch (error) {
    elements.heading.innerHTML = "";
    elements.list.innerHTML = `<div class="error-state"><div><i data-lucide="circle-alert"></i><strong>网站数据尚未生成</strong><span>${escapeHTML(error.message)}</span></div></div>`;
  }
  renderIcons();
}

window.addEventListener("DOMContentLoaded", start);
