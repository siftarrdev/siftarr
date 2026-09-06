import { beforeEach, describe, expect, it, vi } from 'vitest';
import { refreshCurrentTabContent } from '/static/js/dashboard/core.js';
import { loadSearchHistory } from '/static/js/dashboard/details.js';
import { openReplaceModalFromElement } from '/static/js/dashboard/modals.js';
import { dashboardState } from '/static/js/dashboard/core/state.js';

describe('dashboard regressions', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    dashboardState.currentRequestId = null;
    window.activeDetailsRequestId = null;
    window.detailsLoadToken = 0;
  });

  it('refreshes stat cards through their stable data attribute', async () => {
    document.body.innerHTML =
      '<div id="content-pending" class="tab-content"></div><div data-dashboard-stat-cards>old</div>';
    window.saveTabState = () => () => {};
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => '<div id="content-pending">new tab</div><div data-dashboard-stat-cards>new stats</div>',
    });

    await refreshCurrentTabContent();

    expect(document.querySelector('[data-dashboard-stat-cards]').textContent).toBe('new stats');
  });

  it.each([
    'Apostrophe \' and quotes "',
    String.raw`Backslash \\ and <angle brackets>`,
    '<script>alert("xss")</script>',
  ])('reads replace title %j from data attributes as text', (title) => {
    document.body.innerHTML =
      '<form id="replace-form"></form><input id="replace-redirect"><div id="replace-current-torrent"></div><div id="replace-reason"></div><div id="replace-modal" class="hidden"></div>';
    const button = document.createElement('button');
    button.dataset.torrentTitle = title;

    openReplaceModalFromElement(button, 4, 9, '/?tab=downloading');

    expect(document.getElementById('replace-current-torrent').textContent).toBe(button.dataset.torrentTitle);
  });

  it('ignores stale search history loads, including A to B to A', async () => {
    document.body.innerHTML =
      '<div id="request-details-search-history"></div><span id="activity-count" data-timeline="2"></span>';
    const requests = new Map();
    globalThis.fetch = vi.fn(
      (url) =>
        new Promise((resolve, reject) => {
          const pending = requests.get(url) || [];
          pending.push({ resolve, reject });
          requests.set(url, pending);
        }),
    );

    dashboardState.currentRequestId = 'A';
    window.activeDetailsRequestId = 'A';
    window.detailsLoadToken = 1;
    const firstA = loadSearchHistory();
    dashboardState.currentRequestId = 'B';
    window.activeDetailsRequestId = 'B';
    window.detailsLoadToken = 2;
    const b = loadSearchHistory();
    dashboardState.currentRequestId = 'A';
    window.activeDetailsRequestId = 'A';
    window.detailsLoadToken = 3;
    const secondA = loadSearchHistory();

    requests
      .get('/requests/A/search-history?limit=5')
      .shift()
      .resolve({ ok: true, json: async () => ({ runs: [{ status: 'stale A' }] }) });
    await firstA;
    expect(document.querySelector('#request-details-search-history').textContent).toContain('Loading search history');
    expect(document.getElementById('activity-count').textContent).toBe('');
    requests.get('/requests/B/search-history?limit=5').shift().reject(new Error('stale B'));
    await b;
    expect(document.querySelector('#request-details-search-history').textContent).toContain('Loading search history');
    expect(document.getElementById('activity-count').textContent).toBe('');
    requests
      .get('/requests/A/search-history?limit=5')
      .shift()
      .resolve({ ok: true, json: async () => ({ runs: [{ status: 'current A' }] }) });
    await secondA;

    expect(document.querySelector('#request-details-search-history').textContent).toContain('current A');
    expect(document.querySelector('#request-details-search-history').textContent).not.toContain('stale A');
    expect(document.getElementById('activity-count').textContent).toBe('3');
  });

  it('invalidates history when navigation changes the details token without another history call', async () => {
    document.body.innerHTML =
      '<div id="request-details-search-history"></div><span id="activity-count" data-timeline="1"></span>';
    let resolveHistory;
    globalThis.fetch = vi.fn(() => new Promise((resolve) => (resolveHistory = resolve)));
    dashboardState.currentRequestId = 'A';
    window.activeDetailsRequestId = 'A';
    window.detailsLoadToken = 10;
    const load = loadSearchHistory();
    window.detailsLoadToken = 11;
    window.activeDetailsRequestId = 'B';
    dashboardState.currentRequestId = 'B';
    resolveHistory({ ok: true, json: async () => ({ runs: [{ status: 'stale' }] }) });
    await load;
    expect(document.querySelector('#request-details-search-history').textContent).toContain('Loading search history');
    expect(document.getElementById('activity-count').textContent).toBe('');
  });

  it('ignores a newer same-request load and a closed modal', async () => {
    document.body.innerHTML =
      '<div id="request-details-search-history"></div><span id="activity-count" data-timeline="1"></span>';
    const pending = [];
    globalThis.fetch = vi.fn(() => new Promise((resolve, reject) => pending.push({ resolve, reject })));
    dashboardState.currentRequestId = 'A';
    window.activeDetailsRequestId = 'A';
    window.detailsLoadToken = 20;
    const older = loadSearchHistory();
    window.detailsLoadToken = 21;
    const newer = loadSearchHistory();
    pending.shift().resolve({ ok: true, json: async () => ({ runs: [{ status: 'older' }] }) });
    await older;
    expect(document.querySelector('#request-details-search-history').textContent).toContain('Loading search history');
    window.activeDetailsRequestId = null;
    pending.shift().reject(new Error('modal closed'));
    await newer;
    expect(document.querySelector('#request-details-search-history').textContent).toContain('Loading search history');
    expect(document.getElementById('activity-count').textContent).toBe('');
  });
});
