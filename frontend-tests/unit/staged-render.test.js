import { beforeEach, describe, expect, it } from 'vitest';
import { renderQbitDownloadSummary } from '/static/js/dashboard/staged.js';

describe('qBittorrent download summary', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="qbit-download-summary"></div>';
  });

  it('renders aggregate torrent and transfer totals', () => {
    renderQbitDownloadSummary([
      {
        count: 2,
        totals: {
          dlspeed: 3 * 1024 * 1024,
          upspeed: 1024 * 1024,
          downloaded: 2 * 1024 * 1024 * 1024,
          size: 5 * 1024 * 1024 * 1024,
        },
      },
      {
        count: 1,
        totals: { dlspeed: 0, upspeed: 0, downloaded: 0, size: 1024 * 1024 * 1024 },
      },
    ]);

    expect(document.querySelector('#qbit-download-summary').innerHTML).toContain('3 torrents');
    expect(document.querySelector('#qbit-download-summary').textContent).toContain('3.0 MB/s');
    expect(document.querySelector('#qbit-download-summary').textContent).toContain('2.00 GB / 6.00 GB');
  });
});
