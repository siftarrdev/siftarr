import { beforeEach, describe, expect, it, vi } from 'vitest';
import { startProgressStream } from '../../frontend-src/progress_stream.ts';

const ids = {
  panel: 'panel',
  bar: 'bar',
  progressText: 'text',
  status: 'status',
  closeBtn: 'close',
  activeWrap: 'wrap',
  activeList: 'list',
};

class FakeEventSource {
  static last;
  constructor(url) {
    this.url = url;
    this.close = vi.fn();
    FakeEventSource.last = this;
  }
  emit(payload) {
    this.onmessage({ data: JSON.stringify(payload) });
  }
}

function start() {
  return startProgressStream({
    url: '/stream',
    ids,
    initialStatus: 'Connecting...',
    errorMessage: 'Connection error.',
    phases: {
      fetching: { fallbackPercent: 15, status: (data) => data.message, active: (data) => data.active || [] },
      processing: { fallbackPercent: 20, minimumPercent: 20, status: () => 'Working' },
    },
  });
}

describe('progress stream', () => {
  beforeEach(() => {
    globalThis.EventSource = FakeEventSource;
    document.body.innerHTML = `
      <section id="panel" class="hidden"></section>
      <div id="bar"></div><span id="text"></span><p id="status"></p>
      <button id="close" class="hidden" disabled></button>
      <div id="wrap" class="hidden"><ul id="list"></ul></div>`;
  });

  it('uses the per-phase fallback percent and message, and keeps the stream open', () => {
    start();
    const source = FakeEventSource.last;
    expect(document.getElementById('panel').classList.contains('hidden')).toBe(false);

    source.emit({ phase: 'fetching', message: 'Fetching requests', active: ['A'] });
    expect(document.getElementById('bar').style.width).toBe('15%');
    expect(document.getElementById('status').textContent).toBe('Fetching requests');
    expect(document.getElementById('list').children).toHaveLength(1);

    source.emit({ phase: 'processing', current: 1, total: 10 });
    expect(document.getElementById('bar').style.width).toBe('20%');
    expect(document.getElementById('text').textContent).toBe('1/10');
    expect(source.close).not.toHaveBeenCalled();
  });

  it('finishes with a success state on complete', () => {
    start();
    const source = FakeEventSource.last;
    source.emit({ phase: 'complete', message: 'Done' });

    expect(document.getElementById('bar').style.width).toBe('100%');
    expect(document.getElementById('status').textContent).toBe('Done');
    expect(document.getElementById('status').classList.contains('text-emerald-400')).toBe(true);
    expect(document.getElementById('close').disabled).toBe(false);
    expect(source.close).toHaveBeenCalled();
  });

  it('shows an error state on error events and on connection errors', () => {
    start();
    const source = FakeEventSource.last;
    source.emit({ phase: 'error', message: 'Boom' });
    expect(document.getElementById('bar').classList.contains('bg-red-500')).toBe(true);
    expect(document.getElementById('status').textContent).toBe('Boom');
    expect(document.getElementById('close').classList.contains('hidden')).toBe(false);
    expect(source.close).toHaveBeenCalled();

    start();
    FakeEventSource.last.onerror();
    expect(document.getElementById('status').textContent).toBe('Connection error.');
    expect(document.getElementById('bar').style.width).toBe('100%');
  });
});
