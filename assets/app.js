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

function journalHeader(journal, count) {
  const metric = journal.impact_factor || {};
  const hasMetric = metric.value !== undefined && metric.value !== null && metric.value !== "";
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
      <span class="journal-count">${count} 篇</span>
    </header>`;
}

function articleMarkup(article, supplementType = null) {
  const href = escapeHTML(article.url || "#");
  const translated = article.translation_status === "translated" && article.title_zh;
  const detail = supplementType === "late_additions"
    ? `归档日期 ${escapeHTML(article.archived_date || article.published_date || "未知")}`
    : supplementType === "date_pending" ? `日期精度 ${escapeHTML(article.date_precision || "缺失")}` : "";
  return `<li class="article-item">
    ${translated ? `<a class="article-title-zh" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_zh)}</a>` : `<span class="pending-translation">中文翻译处理中</span>`}
    <a class="article-title-en" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_en || "无标题")}</a>
    ${(detail || article.doi) ? `<div class="article-footnote">${detail ? `<span>${detail}</span>` : ""}${article.doi ? `<span>DOI ${escapeHTML(article.doi)}</span>` : ""}</div>` : ""}
  </li>`;
}

function renderGrouped(articles, supplementType = null) {
  const visible = articles.filter((article) => state.selected.has(article.journal_id) && article.content_type === "article");
  if (!visible.length) {
    elements.list.innerHTML = `<div class="empty-state"><div><i data-lucide="inbox"></i><strong>当前范围没有论文</strong><span>可调整日期或期刊筛选。</span></div></div>`;
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
      ${journalHeader(journal, items.length)}
      <ol class="article-list">${items.map((item) => articleMarkup(item, supplementType)).join("")}</ol>
    </section>`;
  }).join("");
  renderIcons();
  return visible.length;
}

function renderDaily() {
  const articles = state.day?.articles || [];
  const count = articles.filter((article) => state.selected.has(article.journal_id) && article.content_type === "article").length;
  elements.heading.innerHTML = `<h1>${escapeHTML(formatDate(state.date))}</h1><p>${count} 篇新论文 · 按首次在线发表日期归档</p>`;
  elements.summary.textContent = state.manifest.updated_at ? `最近更新 ${formatDateTime(state.manifest.updated_at)}` : "";
  const failures = Object.values(state.day?.journal_status || {}).filter((status) => status.status === "failed").length;
  elements.status.hidden = failures === 0;
  if (failures) {
    elements.status.innerHTML = `<i data-lucide="triangle-alert"></i><span>${failures} 本期刊本次获取失败，现有数据已保留。</span>`;
  } else {
    elements.status.replaceChildren();
  }
  renderGrouped(articles);
}

function renderSupplements() {
  const articles = state.supplements?.[state.supplement] || [];
  const label = state.supplement === "late_additions" ? "迟到补录" : "日期待核实";
  elements.heading.innerHTML = `<h1>${label}</h1><p>${articles.length} 条记录 · ${state.supplement === "late_additions" ? "已补回原发表日期" : "尚未进入每日归档"}</p>`;
  elements.status.hidden = true;
  elements.status.replaceChildren();
  renderGrouped(articles, state.supplement);
}

function renderCurrent() {
  if (state.view === "daily") renderDaily(); else renderSupplements();
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
    const params = new URLSearchParams(location.search);
    state.view = params.get("view") === "supplements" ? "supplements" : "daily";
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
    if (state.view === "daily") await loadDay(state.date); else switchView("supplements");
    switchView(state.view);
    renderUpdateStatus(state.manifest);
  } catch (error) {
    elements.heading.innerHTML = "";
    elements.list.innerHTML = `<div class="error-state"><div><i data-lucide="circle-alert"></i><strong>网站数据尚未生成</strong><span>${escapeHTML(error.message)}</span></div></div>`;
  }
  renderIcons();
}

window.addEventListener("DOMContentLoaded", start);
