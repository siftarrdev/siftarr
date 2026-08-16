import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { closeSearchProgress, startSearchProgress } from '/static/js/dashboard/search_sse.js';

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.close = vi.fn();
    FakeEventSource.instances.push(this);
  }
}

describe('dashboard search SSE lifecycle', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    globalThis.EventSource = FakeEventSource;
    document.body.innerHTML = '<section id="search-progress-panel" class="hidden"></section>';
  });

  afterEach(() => {
    closeSearchProgress();
    vi.unstubAllGlobals();
  });

  it('closes the active stream before starting a replacement search', () => {
    startSearchProgress(12, 'First');
    startSearchProgress(34, 'Second');

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].url).toBe('/requests/12/search/stream');
    expect(FakeEventSource.instances[0].close).toHaveBeenCalledOnce();
    expect(FakeEventSource.instances[1].url).toBe('/requests/34/search/stream');
    expect(document.querySelector('#search-progress-panel').classList.contains('hidden')).toBe(false);
  });
});
