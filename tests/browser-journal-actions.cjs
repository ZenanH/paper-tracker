// Optional browser regression check. Requires Playwright and Chrome.
// All data and API requests are intercepted; the target server is never mutated.
// PAPER_TRACKER_TEST_URL=http://127.0.0.1:18898 node tests/browser-journal-actions.cjs
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const baseURL = process.env.PAPER_TRACKER_TEST_URL || 'http://127.0.0.1:18898/';

const journals = [
  { id: 'alpha', name: 'Journal of Computational Mechanics', group: '一区' },
  { id: 'beta', name: 'International Journal of Multiscale Computational Mechanics and Engineering Research', group: '二区' },
].map(journal => ({ ...journal, cas: {zone: 1, category: '工程技术'}, impact_factor: {} }));
const dates = ['2026-10-17', '2026-10-18', '2026-10-19'];
const days = Object.fromEntries(dates.map((date, day) => [date, {articles: journals.flatMap(journal =>
  Array.from({length: journal.id === 'alpha' ? 99 : 60}, (_, index) => ({
    id: `${journal.id}:${day}:${index}`, journal_id: journal.id, published_date: date,
    title_en: `${index % 2 ? 'Fluid' : 'Unified'} mechanics study ${day}-${index}`,
    title_zh: '计算力学研究', content_type: 'article', url: 'https://example.com/paper',
  }))) } ]));
const pending = {id: 'alpha:pending', journal_id: 'alpha', title_en: 'Undated mechanics paper',
  published_date: '2026-10-01', date_inferred: true,
  first_seen_at: '2026-10-19T12:00:00+08:00', content_type: 'article', url: 'https://example.com/undated'};
days['2026-10-01'] = {articles: [pending]};

function fixture() {
  return {items: {}, cleared_items: {'alpha:0:0': days[dates[0]].articles[0]},
    favorites: {preserved: {id: 'preserved', journal_id: 'alpha', title_en: 'Favorite stays saved', date: '2026-08-01'}}};
}
async function ready(page) {
  await page.waitForFunction(() => document.querySelector('#paper-list')?.getAttribute('aria-busy') === 'false');
}
async function waitUntil(predicate) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for a mocked request');
}
async function stub(page, model, {failedDate = false} = {}) {
  await page.route('**/data/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/supplements.json')) model.legacySupplementReads++;
    if (path.endsWith('/manifest.json')) return route.fulfill({json: {
      default_date: dates[2], retention: {start: '2026-07-19', end: dates[2]}, available_dates: Object.keys(days).sort(),
      updated_at: '2026-10-20T01:00:00+08:00', collection_summary: {ok: 2 - (model.failedJournals || 0), failed: model.failedJournals || 0}}});
    if (path.endsWith('/journals.json')) return route.fulfill({json: {journals}});
    const date = path.match(/days\/(\d{4}-\d{2}-\d{2})\.json/)?.[1];
    if (date && failedDate && date === dates[2]) return route.fulfill({status: 503, json: {error: 'Offline day'}});
    if (date && days[date]) {
      model.dateReads = (model.dateReads || 0) + 1;
      return route.fulfill({json: {articles: [...days[date].articles, ...(model.newPaper && date === dates[2] ? [model.newPaper] : [])]}});
    }
    return route.fulfill({status: 404, json: {error: 'Unknown test data'}});
  });
  await page.route('**/api/admin/**', async route => {
    if (new URL(route.request().url()).pathname === '/api/admin/run' && route.request().method() === 'POST') {
      (model.refreshRequests ||= []).push(route.request().postDataJSON());
      if (model.holdStart) await new Promise(resolve => {model.releaseStart = resolve;});
      if (model.rejectRefresh) return route.fulfill({status: 503, json: {error: 'Refresh service unavailable'}});
      model.job = {status: 'running', kind: 'manual', stage: 'collect'};
      return route.fulfill({status: 202, json: {journals, job: model.job, llm: {}, filter_journal_ids: [], filter_prompt: ''}});
    }
    if (model.adminOffline) return route.fulfill({status: 503, json: {error: 'Management unavailable'}});
    return route.fulfill({json: {journals, llm: {}, job: model.job || {status: 'idle'}, filter_journal_ids: [], filter_prompt: '', excluded_count: model.excludedCount || 0}});
  });
  await page.route('**/api/archive', async route => {
    if (route.request().method() === 'GET') {
      if (model.holdRecovery && model.posts.length >= 2) await new Promise(resolve => {model.releaseRecovery = resolve;});
      return route.fulfill({json: model.saved});
    }
    const payload = route.request().postDataJSON(); model.posts.push(payload);
    if (model.holdBatches || model.holdSingle) await new Promise(resolve => {model.releaseBatch = resolve; model.blockedBatch = model.posts.length;});
    if (model.failSecond && model.posts.length === 2) return route.fulfill({status: 500, json: {error: 'Simulated batch failure'}});
    if (payload.action === 'add') for (const article of payload.items) model.saved.items[article.id] = article;
    if (payload.action === 'remove') for (const id of payload.ids) delete model.saved.items[id];
    if (payload.action === 'favorite') for (const article of payload.items) model.saved.favorites[article.id] = article;
    if (payload.action === 'unfavorite') for (const id of payload.ids) delete model.saved.favorites[id];
    return route.fulfill({json: {updated_at: new Date().toISOString()}});
  });
}

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const model = {saved: fixture(), posts: [], failSecond: false, legacySupplementReads: 0, excludedCount: 7}; await stub(page, model);
    await page.goto(`${baseURL}?mode=pending&date=2026-07-20&journals=`); await ready(page);
    await page.locator('#settings-button').click();
    assert.deepEqual(await page.locator('#settings-dialog .settings-section h3').allTextContents().then(values => values.map(value => value.trim())), ['Appearance', 'Model', 'AI title filter']);
    assert.equal(await page.locator('[id*="gist"],[id^="github-"]').count(), 0);
    await page.locator('#llm-model').fill('unsaved-test-model');
    await page.locator('#llm-api-key').fill('unsaved-test-key');
    assert.equal(await page.locator('#filter-excluded-count').innerText(), 'Filtered out in the current window · 7 titles');
    for (const width of [320, 1440]) {
      await page.setViewportSize({width, height: 844});
      for (const theme of ['light', 'dark']) {
        await page.locator(`button[data-theme="${theme}"]`).click();
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        if (process.env.PAPER_TRACKER_SCREENSHOTS) {
          const fs = require('node:fs'), path = require('node:path');
          fs.mkdirSync(process.env.PAPER_TRACKER_SCREENSHOTS, {recursive: true});
          await page.screenshot({path: path.join(process.env.PAPER_TRACKER_SCREENSHOTS, `settings-${width}-${theme}.png`)});
        }
      }
    }
    await page.locator('button[data-theme="system"]').click();
    await page.locator('#close-settings').click();
    await page.waitForFunction(() => document.querySelector('#llm-api-key').value === '');
    assert.equal(await page.locator('#llm-api-key').inputValue(), '');
    await page.setViewportSize({width: 1440, height: 1000});
    assert.equal(model.legacySupplementReads, 0);
    assert.equal(await page.locator('#paper-modes,#date-row,[data-archive],#bulk-archive').count(), 0);
    assert.equal(await page.locator('#filter-toggle,#journal-filters').count(), 0);
    const expected = Object.values(days).reduce((n, day) => n + day.articles.length, 0) - 1;
    assert.match(await page.locator('#results-summary').innerText(), new RegExp(`${expected} papers`));
    assert.equal(await page.locator('.article-footnote').first().locator('span').first().innerText(), dates[2]);
    assert(!page.url().includes('mode=')); assert(!page.url().includes('journals='));
    const total = Number((await page.locator('[data-archive-journal="alpha"]').innerText()).replace(/\D/g, ''));
    assert(total > 60);
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: 844}); await page.evaluate(() => scrollTo(0, 0));
      await page.locator('[data-select]').first().check();
      await page.evaluate(() => {const section = document.querySelector('.journal-section'); scrollTo(0, section.getBoundingClientRect().top + scrollY + 700);});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const position = await page.locator('.journal-header').first().evaluate(header => {
        const button = header.querySelector('[data-archive-journal]'), r = button.getBoundingClientRect();
        return {top: header.getBoundingClientRect().top, selectionBottom: document.querySelector('#selection-bar').getBoundingClientRect().bottom,
          clickable: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest('button') === button,
          overflow: document.documentElement.scrollWidth > innerWidth};
      });
      assert(position.clickable); assert(!position.overflow); assert(position.top >= position.selectionBottom + 6);
      console.log('Sticky header:', width, position); await page.locator('#clear-selection').click();
    }
    await page.setViewportSize({width: 320, height: 568});
    await page.goto(`${baseURL}?page=6`); await ready(page);
    await page.evaluate(() => {const section = document.querySelector('.journal-section'); scrollTo(0, section.getBoundingClientRect().top + scrollY + 700);});
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const longHeader = await page.locator('[data-archive-journal="beta"]').evaluate(button => {
      const r = button.getBoundingClientRect();
      return {clickable: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest('button') === button,
        overflow: document.documentElement.scrollWidth > innerWidth};
    });
    assert(longHeader.clickable); assert(!longHeader.overflow);
    console.log('Long journal name on narrow screen:', longHeader);
    await page.goto(baseURL); await ready(page);
    await page.locator('#paper-search').fill('Unified');
    await page.locator('#search-papers').click();
    assert.equal(Number((await page.locator('[data-archive-journal="alpha"]').innerText()).replace(/\D/g, '')), total);
    model.holdBatches = true;
    await page.locator('[data-archive-journal="alpha"]').click();
    await page.waitForFunction(() => document.querySelector('#save-progress').getAttribute('aria-valuenow') === '0');
    assert(await page.locator('#save-status').isVisible());
    assert.match(await page.locator('#save-count').innerText(), new RegExp(`0 / ${total} papers confirmed`));
    assert(await page.locator('[data-archive-journal="beta"]').isDisabled());
    assert.equal(await page.locator('#save-status .saving-spinner').evaluate(node => getComputedStyle(node).animationName), 'saving-spin');
    await page.waitForFunction(() => document.querySelector('#save-detail').textContent.includes('Slow connection'), null, {timeout: 12000});
    assert.equal(await page.locator('#save-progress').getAttribute('aria-valuenow'), '0');
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: 844});
      await page.evaluate(() => {const section = document.querySelector('.journal-section'); scrollTo(0, section.getBoundingClientRect().top + scrollY + 700);});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const layout = await page.locator('.journal-header').first().evaluate(header => ({
        top: header.getBoundingClientRect().top,
        progressBottom: document.querySelector('#save-status').getBoundingClientRect().bottom,
        overflow: document.documentElement.scrollWidth > innerWidth
      }));
      assert(!layout.overflow); assert(layout.top >= layout.progressBottom);
    }
    await page.emulateMedia({reducedMotion: 'reduce'});
    assert.equal(await page.locator('#save-status .saving-spinner').evaluate(node => getComputedStyle(node).animationName), 'none');
    await page.emulateMedia({reducedMotion: 'no-preference'});
    await page.locator('[data-view="favorites"]').click();
    assert(await page.locator('#save-status').isVisible());
    await page.locator('[data-view="papers"]').click();
    for (const [batch, confirmed] of [[1, 0], [2, 128], [3, 256]]) {
      await page.waitForFunction(value => document.querySelector('#save-progress').getAttribute('aria-valuenow') === String(value), confirmed);
      await waitUntil(() => model.blockedBatch === batch);
      assert.equal(model.blockedBatch, batch);
      assert.match(await page.locator('#save-detail').innerText(), new RegExp(`batch ${batch} of 3`, 'i'));
      model.releaseBatch();
    }
    await ready(page);
    model.holdBatches = false;
    assert(await page.locator('#save-status').isHidden());
    assert.match(await page.locator('#notice').innerText(), new RegExp(`Archived ${total} papers`));
    console.log('Save feedback: slow connection, confirmed batch counts, persistent across views, reduced motion, no header overlap.');
    assert.equal(model.posts.reduce((n, post) => n + post.items.length, 0), total);
    assert(model.posts.length > 1); assert.equal(Object.keys(model.saved.favorites).length, 1);
    assert(!model.posts.some(post => post.items.some(article => article.id === 'alpha:0:0')));
    assert(model.posts.some(post => post.items.some(article => article.id === pending.id)));
    console.log('Complete journal:', total, 'papers; batches:', model.posts.map(post => post.items.length));
    await page.locator('#paper-search').fill(''); await page.waitForTimeout(200);
    assert.equal(await page.locator('[data-archive-journal="alpha"]').count(), 0);
    await page.locator('[data-view="favorites"]').click();
    assert.equal(await page.locator('#page-title').innerText(), 'Favorites');
    assert.equal(await page.locator('#results-summary').innerText(), '1 favorite');
    assert.equal(await page.locator('#favorite-count').innerText(), '1');
    assert.equal(await page.locator('.article-item').count(), 1);
    assert.equal(await page.locator('[data-library],#library-modes,#clear-archive,#bulk-restore').count(), 0);
    for (const legacy of ['library&library=archive', 'archive', 'favorites']) {
      await page.goto(`${baseURL}?view=${legacy}`); await ready(page);
      assert.equal(new URL(page.url()).search, '?view=favorites');
      assert.equal(await page.locator('#results-summary').innerText(), '1 favorite');
    }
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert(await page.locator('[data-favorite="preserved"]').isVisible());
    }
    await page.locator('[data-favorite="preserved"]').click(); await ready(page);
    assert.equal(await page.locator('.article-item').count(), 0);
    assert.equal(Object.keys(model.saved.items).length, total);
    assert.equal(Object.keys(model.saved.cleared_items).length, 1);
    assert.equal(Object.keys(model.saved.favorites).length, 0);
    await page.reload(); await ready(page);
    assert.equal(await page.locator('#results-summary').innerText(), '0 favorites');
    console.log('Favorites: no categories, legacy routes migrated, independent archive state preserved.');
    model.saved = fixture(); model.posts = []; model.holdSingle = true;
    await page.goto(baseURL); await ready(page);
    await page.locator('[data-favorite]').first().click();
    await waitUntil(() => model.blockedBatch === 1);
    assert(await page.locator('#save-status').isVisible());
    assert.match(await page.locator('#save-count').innerText(), /0 \/ 1 paper confirmed/);
    const unload = await page.evaluate(() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return {cancelled: event.defaultPrevented, busy: document.querySelector('#save-status').hidden === false};
    });
    assert.deepEqual(unload, {cancelled: true, busy: true});
    model.releaseBatch(); model.holdSingle = false;
    await page.waitForFunction(() => document.querySelector('#save-status').hidden);
    assert.equal(await page.locator('#save-status').isHidden(), true);
    assert.equal(Object.keys(model.saved.favorites).length, 2);
    console.log('Single favorite: visible save state and beforeunload protection while pending.');
    await page.locator('[data-view="favorites"]').click();
    model.saved.favorites = Object.fromEntries([
      {id: 'older', date: dates[0], favorited_at: '2026-10-21T00:00:00Z'},
      {id: 'undated', favorited_at: '2026-10-22T00:00:00Z'},
      {id: 'newer', date: dates[2], favorited_at: '2026-10-20T00:00:00Z'},
    ].map(record => [record.id, {...record, journal_id: 'alpha', title_en: record.id}]));
    await page.reload(); await ready(page);
    assert.deepEqual(await page.locator('[data-favorite]').evaluateAll(buttons => buttons.map(button => button.dataset.favorite)), ['newer', 'older', 'undated']);
    console.log('Publication order: newest first, undated last, favorite time ignored.');
    model.saved.favorites = Object.fromEntries(journals.map(journal => [journal.id, {
      id: journal.id, journal_id: journal.id, date: dates[2],
      title_en: 'Cross-journal mechanics', title_zh: '跨期刊力学研究'
    }]));
    await page.evaluate(() => localStorage.setItem('paper-tracker-hidden-journals', '["beta"]'));
    await page.goto(`${baseURL}?view=favorites&journals=alpha&q=mechanics`); await ready(page);
    assert.equal(await page.locator('.article-item').count(), 2);
    assert(!new URL(page.url()).searchParams.has('journals'));
    assert.equal(await page.locator('#filter-toggle,#journal-filters').count(), 0);
    await page.locator('#paper-search').fill('跨期刊');
    await page.locator('#paper-search').press('Enter');
    assert.equal(await page.locator('.article-item').count(), 2);
    await page.goto(`${baseURL}?view=favorites&journals=`); await ready(page);
    assert.equal(await page.locator('.article-item').count(), 2);
    console.log('All-journal search: English and Chinese match both journals; old URL filters and browser preferences ignored.');
    model.saved = fixture(); model.posts = []; model.failSecond = true; model.holdRecovery = true;
    await page.goto(baseURL); await ready(page); await page.locator('[data-archive-journal="alpha"]').click();
    await page.waitForFunction(() => document.querySelector('#save-label').textContent === 'Checking saved papers…');
    assert(await page.locator('#save-status').isVisible());
    assert.equal(await page.locator('#save-progress').getAttribute('aria-valuenow'), '128');
    await waitUntil(() => Boolean(model.releaseRecovery));
    assert(model.releaseRecovery);
    model.releaseRecovery(); model.holdRecovery = false;
    await ready(page);
    assert.equal(Object.keys(model.saved.items).length, 128);
    assert.match(await page.locator('#notice').innerText(), /Saved state reloaded/);
    const remaining = Number((await page.locator('[data-archive-journal="alpha"]').innerText()).replace(/\D/g, ''));
    assert.equal(remaining, total - 128);
    console.log('Partial failure: successful batch kept; remaining:', remaining);
    const failed = await browser.newPage(); await stub(failed, {saved: fixture(), posts: []}, {failedDate: true});
    await failed.goto(baseURL); await ready(failed);
    assert(await failed.locator('[data-archive-journal]').first().isDisabled());

    const refreshPage = await browser.newPage({viewport: {width: 390, height: 844}, reducedMotion: 'reduce'});
    const refreshErrors = []; refreshPage.on('pageerror', error => refreshErrors.push(error.message));
    const refreshModel = {saved: fixture(), posts: [], holdStart: true};
    await stub(refreshPage, refreshModel);
    await refreshPage.goto(baseURL); await ready(refreshPage);
    const refresh = refreshPage.getByRole('button', {name: 'Refresh papers', exact: true});
    await refresh.waitFor();
    for (const view of ['journals', 'favorites']) {
      await refreshPage.locator(`[data-view="${view}"]`).click();
      assert(await refresh.isHidden());
      assert(await refreshPage.locator('#update-status').isHidden());
      assert.equal(await refreshPage.locator('#paper-update-bar').evaluate(element => element.getBoundingClientRect().height), 0);
    }
    await refreshPage.locator('[data-view="papers"]').click();
    assert(await refresh.isVisible());
    assert(await refreshPage.locator('#update-status').isVisible());
    await refreshPage.waitForFunction(() => !document.querySelector('#run-now').disabled);
    await refresh.focus(); await refresh.press('Enter');
    await waitUntil(() => Boolean(refreshModel.releaseStart));
    assert.equal(await refresh.locator('#refresh-label').innerText(), 'Starting…');
    assert(await refresh.isDisabled());
    await refresh.evaluate(button => button.click());
    assert.deepEqual(refreshModel.refreshRequests, [{mode: 'daily'}]);
    refreshModel.holdStart = false; refreshModel.releaseStart();
    await refreshPage.waitForFunction(() => document.querySelector('#refresh-label').textContent === 'Refreshing…');
    assert.equal(await refresh.getAttribute('aria-busy'), 'true');
    assert(await refreshPage.locator('#refresh-spinner').isVisible());
    assert.match(await refreshPage.locator('#update-status').innerText(), /Collecting and filtering papers/);
    refreshModel.job.stage = 'translate';
    await refreshPage.waitForFunction(() => document.querySelector('#update-status').textContent.includes('Translating titles'));
    for (const theme of ['light', 'dark']) for (const width of [320, 390, 768, 1440]) {
      await refreshPage.setViewportSize({width, height: 844});
      await refreshPage.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      assert.equal(await refreshPage.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert(await refresh.isVisible());
    }
    await refreshPage.locator('[data-view="favorites"]').click();
    assert(await refresh.isHidden());
    assert(await refreshPage.locator('#update-status').isHidden());
    assert.equal(await refreshPage.locator('.article-item').count(), 1);
    await refreshPage.locator('[data-view="journals"]').click();
    assert(await refresh.isHidden());
    assert(await refreshPage.locator('#update-status').isHidden());
    await refreshPage.locator('[data-view="papers"]').click();
    assert(await refresh.isVisible());
    assert(await refreshPage.locator('#update-status').isVisible());
    assert.match(await refreshPage.locator('#update-status').innerText(), /Translating titles/);
    const readsBefore = refreshModel.dateReads;
    refreshModel.newPaper = {id: 'alpha:2:-fresh', journal_id: 'alpha', published_date: dates[2],
      title_en: 'Fresh mechanics result', title_zh: '刚采集的新论文', translation_status: 'translated', url: 'https://example.com/fresh'};
    refreshModel.job = {status: 'success', kind: 'manual', finished_at: '2026-10-21T10:00:00Z'};
    await refreshPage.waitForFunction(() => document.querySelector('#notice').textContent === 'Papers refreshed');
    await ready(refreshPage);
    assert(refreshModel.dateReads > readsBefore);
    assert.equal(await refresh.getAttribute('aria-busy'), 'false');
    assert.equal(await refresh.locator('#refresh-label').innerText(), 'Refresh');
    assert(!(await refresh.isDisabled()));
    assert.equal(await refreshPage.getByText('刚采集的新论文', {exact: true}).count(), 1);
    assert.equal(await refreshPage.locator('[data-select="alpha:0:0"]').count(), 0);
    assert(refreshModel.saved.cleared_items['alpha:0:0']);
    assert.equal(Object.keys(refreshModel.saved.favorites).length, 1);
    await refresh.click();
    await waitUntil(() => refreshModel.refreshRequests.length === 2);
    refreshModel.job = {status: 'failed', kind: 'manual', finished_at: '2026-10-21T10:01:00Z'};
    await refreshPage.waitForFunction(() => document.querySelector('#notice').textContent.startsWith('Paper refresh failed'));
    assert(!(await refresh.isDisabled()));
    assert.equal(await refreshPage.getByText('刚采集的新论文', {exact: true}).count(), 1);
    await refresh.click();
    await waitUntil(() => refreshModel.refreshRequests.length === 3);
    refreshModel.failedJournals = 1;
    refreshModel.job = {status: 'success', kind: 'manual', finished_at: '2026-10-21T10:02:00Z'};
    await refreshPage.waitForFunction(() => document.querySelector('#notice').textContent.includes('1 failed journal'));
    assert(!(await refresh.isDisabled()));
    refreshModel.rejectRefresh = true;
    await refresh.click();
    await refreshPage.waitForFunction(() => document.querySelector('#notice').textContent === 'Refresh service unavailable');
    assert(!(await refresh.isDisabled()));
    assert.equal(await refresh.getAttribute('aria-busy'), 'false');
    assert.equal(refreshModel.refreshRequests.length, 4);
    assert.deepEqual(refreshErrors, []);
    console.log('Refresh: New papers only, one background update, real stages, browse while running, new translated papers, archived papers stay hidden, failures and retry; both themes and reduced motion.');
    assert.deepEqual(errors, []); console.log('Journal action browser checks passed.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
