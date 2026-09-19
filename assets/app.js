const ARCHIVE_API = "api/archive";
const ADMIN_API = "api/admin";

const STORAGE_KEYS = {
  hiddenJournals: "paper-tracker-hidden-journals",
  settingsGroups: "paper-tracker-settings-journal-groups",
  theme: "paper-tracker-theme",
};

const state = {
  view: "daily",
  mode: "history",
  date: null,
  dailyDay: null,
  archiveDate: null,
  manifest: null,
  journals: [],
  journalMap: new Map(),
  hidden: new Set(),
  selected: new Set(),
  day: null,
  supplements: null,
  theme: "system",
  settingsGroups: {},
  archive: { items: {}, cleared_items: {}, favorites: {}, updated_at: null },
  archiveSelection: new Set(),
  admin: null,
  adminWasRunning: false,
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
  favoriteCount: document.querySelector("#favorite-count"),
  archiveSelection: document.querySelector("#archive-selection"),
  archiveSelectAll: document.querySelector("#archive-select-all"),
  archiveNone: document.querySelector("#archive-none"),
  archiveClear: document.querySelector("#archive-clear"),
  archiveRestore: document.querySelector("#archive-restore"),
  dailyToolbar: document.querySelector("#daily-toolbar"),
  dailyDate: document.querySelector("#daily-date"),
  historyToolbar: document.querySelector("#history-toolbar"),
  historyDateControls: document.querySelector("#history-date-controls"),
  archivePrevious: document.querySelector("#archive-previous"),
  archiveNext: document.querySelector("#archive-next"),
  archiveYesterday: document.querySelector("#archive-yesterday"),
  archiveDatePicker: document.querySelector("#archive-date-picker"),
  supplementCount: document.querySelector("#supplement-count"),
  sourceNote: document.querySelector("#source-note"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsJournalList: document.querySelector("#settings-journal-list"),
  settingsJournalSummary: document.querySelector("#settings-journal-summary"),
  settingsArchiveSummary: document.querySelector("#settings-archive-summary"),
  settingsRestoreArchive: document.querySelector("#settings-restore-archive"),
  journalAdminSummary: document.querySelector("#journal-admin-summary"),
  journalAdminStatus: document.querySelector("#journal-admin-status"),
  journalAdminList: document.querySelector("#journal-admin-list"),
  journalNameInput: document.querySelector("#journal-name-input"),
  journalAddRun: document.querySelector("#journal-add-run"),
  journalRunNow: document.querySelector("#journal-run-now"),
};

let adminPollTimer = null;

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
  if (state.view === "history") {
    if (state.date) params.set("date", state.date);
    if (state.mode === "supplements") params.set("mode", "supplements");
  }
  if (state.view === "archive" && state.archiveDate) params.set("adate", state.archiveDate);
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

function normalizeArchive(data) {
  return {
    items: data && typeof data.items === "object" && data.items ? data.items : {},
    cleared_items: data && typeof data.cleared_items === "object" && data.cleared_items ? data.cleared_items : {},
    favorites: data && typeof data.favorites === "object" && data.favorites ? data.favorites : {},
    updated_at: data?.updated_at || null,
  };
}

function updateSettingsArchiveUI() {
  const count = Object.keys(state.archive.cleared_items).length;
  elements.settingsArchiveSummary.textContent = count ? `${count} 篇已清空` : "没有已清空的归档";
  elements.settingsRestoreArchive.disabled = count === 0;
  elements.settingsRestoreArchive.textContent = count ? `恢复归档 (${count})` : "恢复归档";
}

function renderJournalAdmin() {
  const payload = state.admin;
  if (!payload) {
    elements.journalAdminSummary.textContent = "管理服务暂不可用";
    elements.journalAdminStatus.textContent = "无法读取期刊管理状态。";
    elements.journalAdminList.replaceChildren();
    elements.journalAddRun.disabled = true;
    elements.journalRunNow.disabled = true;
    return;
  }
  const journals = payload.journals || [];
  const job = payload.job || {};
  const running = job.status === "running";
  const statusLabels = { idle: "空闲", running: "运行中", success: "上次运行成功", failed: "上次运行失败" };
  elements.journalAdminSummary.textContent = `${journals.length} 本 · 每日 ${payload.schedule || "01:00"}（北京时间）`;
  const nextRun = job.next_run ? `下次 ${formatDateTime(job.next_run)}` : "";
  const jobMessage = job.message ? ` · ${job.message}` : "";
  const latestLog = job.log?.length ? String(job.log[job.log.length - 1]).split("\n").pop() : "";
  const logMessage = latestLog ? ` · ${latestLog}` : "";
  elements.journalAdminStatus.classList.toggle("is-error", job.status === "failed");
  elements.journalAdminStatus.innerHTML = `<strong>${escapeHTML(statusLabels[job.status] || "状态未知")}</strong><span>${escapeHTML(jobMessage.replace(/^ · /, ""))}${escapeHTML(logMessage)}${(jobMessage || logMessage) && nextRun ? " · " : ""}${escapeHTML(nextRun)}</span>`;
  elements.journalAddRun.disabled = running;
  elements.journalRunNow.disabled = running;
  elements.journalNameInput.disabled = running;
  elements.journalAdminList.innerHTML = journals.map((journal) => `
    <div class="journal-admin-item">
      <div>
        <strong>${escapeHTML(journal.name)}</strong>
        <span>${journal.origin === "user" ? "用户添加" : "镜像默认"} · ${escapeHTML(journal.group || "未分组")} · 中科院 ${escapeHTML(journal.cas?.zone ?? "-")} 区</span>
      </div>
      <button class="icon-button compact-icon-button" type="button" data-delete-journal="${escapeHTML(journal.id)}" data-journal-name="${escapeHTML(journal.name)}" title="删除期刊" aria-label="删除 ${escapeHTML(journal.name)}" ${running ? "disabled" : ""}>
        <i data-lucide="trash-2"></i>
      </button>
    </div>`).join("");
  renderIcons();
}

async function adminRequest(path, options = {}) {
  const response = await fetch(`${ADMIN_API}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = {};
  try { payload = await response.json(); } catch (error) { /* Keep the status error below. */ }
  if (!response.ok) throw new Error(payload.error || `服务返回 ${response.status}`);
  return payload;
}

function scheduleAdminPoll() {
  if (adminPollTimer) clearTimeout(adminPollTimer);
  if (state.admin?.job?.status !== "running") return;
  adminPollTimer = setTimeout(() => loadJournalAdmin(true), 2500);
}

async function loadJournalAdmin(fromPoll = false) {
  try {
    const payload = await adminRequest("/journals");
    const wasRunning = state.adminWasRunning;
    state.admin = payload;
    state.adminWasRunning = payload.job?.status === "running";
    renderJournalAdmin();
    if (fromPoll && wasRunning && payload.job?.status === "success") {
      window.location.reload();
      return;
    }
    scheduleAdminPoll();
  } catch (error) {
    state.admin = null;
    renderJournalAdmin();
  }
}

async function addJournalAndRun() {
  const name = elements.journalNameInput.value.trim();
  if (!name) {
    elements.journalNameInput.focus();
    return;
  }
  elements.journalAddRun.disabled = true;
  elements.journalAdminStatus.textContent = "正在匹配期刊信息…";
  try {
    state.admin = await adminRequest("/journals", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    elements.journalNameInput.value = "";
    state.adminWasRunning = true;
    renderJournalAdmin();
    scheduleAdminPoll();
  } catch (error) {
    elements.journalAdminStatus.classList.add("is-error");
    elements.journalAdminStatus.textContent = `添加失败：${error.message}`;
    elements.journalAddRun.disabled = false;
  }
}

async function deleteJournal(journalId, journalName) {
  if (!window.confirm(`确定删除「${journalName}」？该期刊的三个月论文、补录、归档状态及无引用翻译缓存都会删除。`)) return;
  try {
    state.admin = await adminRequest(`/journals/${encodeURIComponent(journalId)}`, { method: "DELETE" });
    state.adminWasRunning = true;
    renderJournalAdmin();
    scheduleAdminPoll();
  } catch (error) {
    elements.journalAdminStatus.classList.add("is-error");
    elements.journalAdminStatus.textContent = `删除失败：${error.message}`;
  }
}

async function runDailyNow() {
  try {
    state.admin = await adminRequest("/run", { method: "POST", body: "{}" });
    state.adminWasRunning = true;
    renderJournalAdmin();
    scheduleAdminPoll();
  } catch (error) {
    elements.journalAdminStatus.classList.add("is-error");
    elements.journalAdminStatus.textContent = `运行失败：${error.message}`;
  }
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
  updateSettingsArchiveUI();
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
  // 补录视图传 "auto"：按条目自身的 supplement_type 决定附加说明
  const kind = supplementType === "auto" ? (article.supplement_type || "") : (supplementType || "");
  const detail = kind === "late_additions"
    ? `归档日期 ${escapeHTML(article.archived_date || article.published_date || "未知")}`
    : kind === "date_pending" ? `日期精度 ${escapeHTML(article.date_precision || "缺失")}` : "";
  const control = options.control || null;
  const footnote = options.footnote || "";
  const favorite = favoriteControl(article);
  return `<li class="article-item has-favorite${control ? " has-check" : ""}">
    ${control || ""}
    <div class="article-body">
      ${translated ? `<a class="article-title-zh" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_zh)}</a>` : `<span class="pending-translation">中文翻译处理中</span>`}
      <a class="article-title-en" href="${href}" target="_blank" rel="noopener noreferrer">${escapeHTML(article.title_en || "无标题")}</a>
      ${(detail || article.doi || footnote) ? `<div class="article-footnote">${detail ? `<span>${detail}</span>` : ""}${article.doi ? `<span>DOI ${escapeHTML(article.doi)}</span>` : ""}${footnote ? `<span>${footnote}</span>` : ""}</div>` : ""}
    </div>
    ${favorite}
  </li>`;
}

function isFavorite(articleId) {
  return Boolean(articleId && state.archive.favorites[articleId]);
}

function favoriteControl(article) {
  const articleId = article.id || "";
  const active = isFavorite(articleId);
  const label = active ? "取消收藏" : "收藏这篇论文";
  return `<button class="favorite-button${active ? " is-active" : ""}" type="button"
    data-favorite-article="${escapeHTML(articleId)}" aria-pressed="${active}" aria-label="${label}" title="${label}">
    <i data-lucide="star"></i>
  </button>`;
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
    const archiveHint = Object.keys(state.archive.cleared_items).length
      ? "已读论文已从列表移出；清空的归档可在右上角设置中恢复显示。"
      : "可到「已归档」查看或恢复为未读。";
    elements.list.innerHTML = `<div class="empty-state"><div><i data-lucide="inbox"></i><strong>${archivable ? "本日论文已全部归档" : "当前范围没有论文"}</strong><span>${archivable ? archiveHint : "可调整日期或期刊筛选。"}</span></div></div>`;
    renderIcons();
    return 0;
  }
  const groups = visible.reduce((all, article) => {
    (all[article.journal_id] ||= []).push(article); return all;
  }, {});
  elements.list.innerHTML = Object.entries(groups).sort(([a], [b]) => {
    const left = state.journalMap.get(a)?.name || a;
    const right = state.journalMap.get(b)?.name || b;
    return left.localeCompare(right);
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

function countVisible(articles) {
  return articles.filter((article) => (
    state.selected.has(article.journal_id)
    && article.content_type === "article"
    && !isArchived(article.id)
  )).length;
}

function countArchived(articles) {
  return articles.filter((article) => (
    state.selected.has(article.journal_id)
    && article.content_type === "article"
    && isArchived(article.id)
  )).length;
}

function renderSourceFailures(day) {
  const failures = Object.values(day?.journal_status || {}).filter((status) => status.status === "failed").length;
  elements.status.hidden = failures === 0;
  if (failures) {
    elements.status.innerHTML = `<i data-lucide="triangle-alert"></i><span>${failures} 本期刊本次获取失败，现有数据已保留。</span>`;
  } else {
    elements.status.replaceChildren();
  }
}

// 每日论文：固定展示昨日（manifest.default_date），日期不可更改
function renderDaily() {
  const date = state.manifest.default_date;
  const articles = state.dailyDay?.articles || [];
  const count = countVisible(articles);
  const archivedCount = countArchived(articles);
  const archivedNote = archivedCount ? ` · 已归档 ${archivedCount} 篇` : "";
  elements.dailyDate.textContent = date ? formatDate(date) : "昨日";
  elements.heading.innerHTML = `<p>${count} 篇新论文 · 按首次在线发表日期归档${archivedNote}</p>`;
  elements.summary.textContent = state.manifest.updated_at ? `最近更新 ${formatDateTime(state.manifest.updated_at)}` : "";
  renderSourceFailures(state.dailyDay);
  renderGrouped(articles, null, { archivable: true });
}

// 历史及补录：历史（按日浏览）/ 补录（迟到补录 + 日期待核实）
function renderHistory() {
  elements.historyDateControls.hidden = state.mode === "supplements";
  if (state.mode === "supplements") return renderSupplements();
  const articles = state.day?.articles || [];
  const count = countVisible(articles);
  const archivedCount = countArchived(articles);
  const archivedNote = archivedCount ? ` · 已归档 ${archivedCount} 篇` : "";
  elements.heading.innerHTML = `<p>${count} 篇论文 · 历史记录${archivedNote}</p>`;
  renderSourceFailures(state.day);
  renderGrouped(articles, null, { archivable: true });
}

function renderSupplements() {
  const late = state.supplements?.late_additions || [];
  const pending = state.supplements?.date_pending || [];
  const articles = [...late, ...pending];
  elements.heading.replaceChildren();
  elements.status.hidden = true;
  elements.status.replaceChildren();
  renderGrouped(articles, "auto", { archivable: true });
}

function isArchived(articleId) {
  return Boolean(articleId && (state.archive.items[articleId] || state.archive.cleared_items[articleId]));
}

function archiveItemsVisible() {
  return Object.values(state.archive.items)
    .filter((item) => state.selected.has(item.journal_id))
    .filter((item) => !state.archiveDate || String(item.date || "") === state.archiveDate)
    .sort((left, right) => String(right.archived_at || "").localeCompare(String(left.archived_at || "")));
}

function renderArchive() {
  const items = archiveItemsVisible();
  elements.heading.innerHTML = `<p>${items.length} 篇已读论文</p>`;
  elements.status.hidden = true;
  elements.status.replaceChildren();

  if (!items.length) {
    const note = Object.keys(state.archive.cleared_items).length
      ? "已清空的归档仍保持已读，可在右上角设置中恢复显示。"
      : "在「每日论文」或「历史」里勾选读过的论文、或整本期刊的「全部已读」即可归档。";
    elements.list.innerHTML = `<div class="empty-state"><div><i data-lucide="archive"></i><strong>这一天还没有已归档论文</strong><span>${note}</span></div></div>`;
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

function favoriteItemsVisible() {
  return Object.values(state.archive.favorites)
    .filter((item) => state.selected.has(item.journal_id))
    .sort((left, right) => String(right.favorited_at || "").localeCompare(String(left.favorited_at || "")));
}

function renderFavorites() {
  const items = favoriteItemsVisible();
  elements.heading.replaceChildren();
  elements.status.hidden = true;
  elements.status.replaceChildren();
  if (!items.length) {
    elements.list.innerHTML = '<div class="empty-state"><div><i data-lucide="star"></i><strong>还没有收藏论文</strong><span>点击论文题目右侧的星标，即可在这里集中查看。</span></div></div>';
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
    return `<section class="journal-section" id="journal-${escapeHTML(journalId)}">
      ${journalHeader(journal || { name, cas: {}, impact_factor: {} }, entries.length)}
      <ol class="article-list">${entries.map((entry) => articleMarkup(entry, null, {
        footnote: entry.favorited_at ? `收藏于 ${escapeHTML(formatDateTime(entry.favorited_at))}` : "",
      })).join("")}</ol>
    </section>`;
  }).join("");
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
  else if (state.view === "favorites") renderFavorites();
  else renderHistory();
  renderIcons();
}

// 保留在历史窗口 [start, end] 内
function clampToRetention(value) {
  const { start, end } = state.manifest.retention;
  if (!value) return state.manifest.default_date;
  if (value < start) return start;
  if (value > end) return end;
  return value;
}

// 历史视图：载入指定日期
async function loadHistoryDay(value) {
  const date = clampToRetention(value);
  state.date = date;
  elements.datePicker.value = date;
  elements.previous.disabled = date <= state.manifest.retention.start;
  elements.next.disabled = date >= state.manifest.retention.end;
  setLoading();
  try {
    state.day = await fetchJSON(`data/days/${date}.json`, true) || { date, articles: [], journal_status: {} };
    renderHistory();
  } catch (error) {
    elements.list.innerHTML = `<div class="error-state"><div><i data-lucide="cloud-off"></i><strong>无法加载该日数据</strong><span>${escapeHTML(error.message)}</span></div></div>`;
    renderIcons();
  }
  syncURL();
}

// 每日视图：固定昨日数据，只加载一次
async function loadDailyDay() {
  const date = state.manifest.default_date;
  try {
    state.dailyDay = await fetchJSON(`data/days/${date}.json`, true) || { date, articles: [], journal_status: {} };
  } catch (error) {
    state.dailyDay = { date, articles: [], journal_status: {} };
  }
}

// 已归档视图：切换归档日期
function setArchiveDate(value) {
  const date = clampToRetention(value);
  state.archiveDate = date;
  elements.archiveDatePicker.value = date;
  elements.archivePrevious.disabled = date <= state.manifest.retention.start;
  elements.archiveNext.disabled = date >= state.manifest.retention.end;
  state.archiveSelection.clear();
  renderArchive();
  syncURL();
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view-tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  elements.dailyToolbar.hidden = view !== "daily";
  elements.historyToolbar.hidden = view !== "history";
  elements.archiveToolbar.hidden = view !== "archive";
  // 历史视图按需加载所选日期（首次进入或数据尚未就绪）
  if (view === "history" && !state.day) {
    loadHistoryDay(state.date);
    return;
  }
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
  elements.archiveClear.disabled = count === 0;
}

function updateFavoriteCount() {
  const count = Object.keys(state.archive.favorites).length;
  elements.favoriteCount.hidden = count === 0;
  elements.favoriteCount.textContent = count;
}

function updateSupplementCount() {
  const articles = [
    ...(state.supplements?.late_additions || []),
    ...(state.supplements?.date_pending || []),
  ];
  const count = articles.filter((article) => (
    article.content_type === "article" && !isArchived(article.id)
  )).length;
  elements.supplementCount.hidden = count === 0;
  elements.supplementCount.textContent = count;
}

async function loadArchive() {
  try {
    const data = await fetchJSON(ARCHIVE_API);
    state.archive = normalizeArchive(data);
  } catch (error) {
    state.archive = normalizeArchive(null);
  }
  updateArchiveCount();
  updateFavoriteCount();
  updateSupplementCount();
  updateSettingsArchiveUI();
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
        date: article.archived_date || article.published_date || article.date || state.date || state.dailyDay?.date || state.manifest?.default_date || null,
        title_en: article.title_en || "",
        title_zh: article.title_zh || "",
        url: article.url || "",
        doi: article.doi || "",
        translation_status: article.translation_status || (article.title_zh ? "translated" : "pending"),
      })),
    });
    state.archive = normalizeArchive(data);
  } catch (error) {
    if (window.console && console.error) console.error("[paper-tracker] archive failed", error);
    showError(`归档失败：${error.message}`, () => archiveArticles(articles));
    return false;
  }
  updateArchiveCount();
  updateSupplementCount();
  updateSettingsArchiveUI();
  renderCurrent();
  return true;
}

function articlePayload(article) {
  return {
    id: article.id,
    journal_id: article.journal_id,
    date: article.archived_date || article.published_date || article.date || state.date || state.dailyDay?.date || state.manifest?.default_date || null,
    title_en: article.title_en || "",
    title_zh: article.title_zh || "",
    url: article.url || "",
    doi: article.doi || "",
    translation_status: article.translation_status || (article.title_zh ? "translated" : "pending"),
  };
}

async function toggleFavorite(article) {
  if (!article?.id) return false;
  const active = isFavorite(article.id);
  try {
    const payload = active
      ? { action: "unfavorite", ids: [article.id] }
      : { action: "favorite", items: [articlePayload(article)] };
    state.archive = normalizeArchive(await postArchive(payload));
  } catch (error) {
    if (window.console && console.error) console.error("[paper-tracker] favorite failed", error);
    showError(`${active ? "取消收藏" : "收藏"}失败：${error.message}`, () => toggleFavorite(article));
    return false;
  }
  updateFavoriteCount();
  renderCurrent();
  return true;
}

async function restoreArticles(ids) {
  if (!ids.length) return true;
  const snapshot = [...ids];
  try {
    const data = await postArchive({ action: "remove", ids });
    state.archive = normalizeArchive(data);
  } catch (error) {
    if (window.console && console.error) console.error("[paper-tracker] restore failed", error);
    showError(`恢复失败：${error.message}`, () => restoreArticles(snapshot));
    return false;
  }
  ids.forEach((id) => state.archiveSelection.delete(id));
  updateArchiveCount();
  updateSupplementCount();
  updateSettingsArchiveUI();
  renderCurrent();
  return true;
}

async function clearArchive(ids) {
  if (!ids.length) return true;
  const snapshot = [...ids];
  try {
    state.archive = normalizeArchive(await postArchive({ action: "clear", ids }));
  } catch (error) {
    showError(`清空归档失败：${error.message}`, () => clearArchive(snapshot));
    return false;
  }
  state.archiveSelection.clear();
  updateArchiveCount();
  updateSettingsArchiveUI();
  renderCurrent();
  return true;
}

async function restoreClearedArchive(ids) {
  if (!ids.length) return true;
  const snapshot = [...ids];
  try {
    state.archive = normalizeArchive(await postArchive({ action: "restore_cleared", ids }));
  } catch (error) {
    showError(`恢复归档失败：${error.message}`, () => restoreClearedArchive(snapshot));
    return false;
  }
  updateArchiveCount();
  renderSettings();
  renderCurrent();
  return true;
}

function currentArticles() {
  if (state.view === "daily") return state.dailyDay?.articles || [];
  if (state.view === "archive") return Object.values(state.archive.items);
  if (state.view === "favorites") return Object.values(state.archive.favorites);
  if (state.view === "history" && state.mode === "supplements") {
    return [...(state.supplements?.late_additions || []), ...(state.supplements?.date_pending || [])];
  }
  return state.day?.articles || [];
}

function findArticle(articleId) {
  return currentArticles().find((item) => item.id === articleId)
    || state.archive.items[articleId]
    || state.archive.cleared_items[articleId]
    || state.archive.favorites[articleId]
    || null;
}

function bindEvents() {
  document.querySelectorAll(".view-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  // 历史及补录：日期控件
  elements.previous.addEventListener("click", () => loadHistoryDay(addDays(state.date, -1)));
  elements.next.addEventListener("click", () => loadHistoryDay(addDays(state.date, 1)));
  elements.yesterday.addEventListener("click", () => loadHistoryDay(state.manifest.default_date));
  elements.datePicker.addEventListener("change", () => loadHistoryDay(elements.datePicker.value));
  // 历史及补录：历史 / 补录 子标签
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
    state.mode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
    renderHistory(); syncURL();
  }));
  // 已归档：日期控件
  elements.archivePrevious.addEventListener("click", () => setArchiveDate(addDays(state.archiveDate, -1)));
  elements.archiveNext.addEventListener("click", () => setArchiveDate(addDays(state.archiveDate, 1)));
  elements.archiveYesterday.addEventListener("click", () => setArchiveDate(state.manifest.default_date));
  elements.archiveDatePicker.addEventListener("change", () => setArchiveDate(elements.archiveDatePicker.value));
  document.querySelector("#settings-button").addEventListener("click", () => {
    renderSettings();
    elements.settingsDialog.showModal();
    loadJournalAdmin();
  });
  document.querySelector("#close-settings").addEventListener("click", () => elements.settingsDialog.close());
  elements.settingsDialog.addEventListener("click", (event) => {
    if (event.target === elements.settingsDialog) elements.settingsDialog.close();
  });
  elements.list.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.dataset.archiveArticle) {
      const article = currentArticles().find((item) => item.id === target.dataset.archiveArticle);
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
      const articles = currentArticles().filter((item) => item.journal_id === journalId && item.content_type === "article");
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
  elements.list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-favorite-article]");
    if (!button) return;
    const article = findArticle(button.dataset.favoriteArticle);
    if (!article) return;
    button.disabled = true;
    toggleFavorite(article).then((ok) => {
      if (!ok && button.isConnected) button.disabled = false;
    });
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
    if (!window.confirm(`确定清空全部 ${ids.length} 篇归档记录？这些论文将从归档页隐藏，但仍保持已读，可在设置中恢复归档。`)) return;
    clearArchive(ids);
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
  elements.settingsRestoreArchive.addEventListener("click", () => {
    restoreClearedArchive(Object.keys(state.archive.cleared_items));
  });
  elements.journalAddRun.addEventListener("click", addJournalAndRun);
  elements.journalNameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") addJournalAndRun();
  });
  elements.journalRunNow.addEventListener("click", runDailyNow);
  elements.journalAdminList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-journal]");
    if (button) deleteJournal(button.dataset.deleteJournal, button.dataset.journalName);
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
    await Promise.all([loadArchive(), loadDailyDay()]);
    const params = new URLSearchParams(location.search);
    const requestedView = params.get("view");
    state.view = ["history", "archive", "favorites"].includes(requestedView) ? requestedView : "daily";
    state.mode = params.get("mode") === "supplements" ? "supplements" : "history";
    state.date = clampToRetention(params.get("date") || manifest.default_date);
    state.archiveDate = clampToRetention(params.get("adate") || manifest.default_date);
    for (const picker of [elements.datePicker, elements.archiveDatePicker]) {
      picker.min = manifest.retention.start;
      picker.max = manifest.retention.end;
    }
    elements.datePicker.value = state.date;
    elements.archiveDatePicker.value = state.archiveDate;
    elements.previous.disabled = state.date <= manifest.retention.start;
    elements.next.disabled = state.date >= manifest.retention.end;
    elements.archivePrevious.disabled = state.archiveDate <= manifest.retention.start;
    elements.archiveNext.disabled = state.archiveDate >= manifest.retention.end;
    elements.sourceNote.textContent = journalData.source.label;
    updateSupplementCount();
    renderSettings();
    bindEvents();
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("is-active", item.dataset.mode === state.mode));
    // 历史视图需要该日数据
    if (state.view === "history") await loadHistoryDay(state.date);
    switchView(state.view);
    renderUpdateStatus(state.manifest);
  } catch (error) {
    elements.heading.innerHTML = "";
    elements.list.innerHTML = `<div class="error-state"><div><i data-lucide="circle-alert"></i><strong>网站数据尚未生成</strong><span>${escapeHTML(error.message)}</span></div></div>`;
  }
  renderIcons();
}

window.addEventListener("DOMContentLoaded", start);
