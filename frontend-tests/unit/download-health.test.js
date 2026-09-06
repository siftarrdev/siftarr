import { beforeEach, describe, expect, it, vi } from 'vitest';
import { confirmDownloadHealthRecovery, renderDownloadHealth } from '../../frontend-src/dashboard/download_health.ts';

const problem = {
  id: 7,
  request_id: 12,
  title: 'Example Show S01E02 1080p',
  status: 'recovery_eligible',
  progress: 0,
  replacement_available: true,
};

describe('download health review', () => {
  beforeEach(() => {
    document.body.innerHTML = '<section data-download-health><div data-download-health-list></div></section>';
    window.confirm = vi.fn();
    window.showToast = vi.fn();
    window.openRequestDetails = vi.fn().mockResolvedValue(undefined);
    window.focusTvEpisode = vi.fn();
    window.searchEpisode = vi.fn();
    global.fetch = vi.fn();
  });

  it('escapes release names rather than executing their HTML', () => {
    renderDownloadHealth([{ ...problem, title: '<img src=x onerror=alert(1)>' }]);
    expect(document.querySelector('img')).toBeNull();
    expect(document.body.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('does not send a recovery request when confirmation is cancelled', async () => {
    renderDownloadHealth([problem]);
    window.confirm.mockReturnValue(false);
    await confirmDownloadHealthRecovery(problem.id);
    expect(fetch).not.toHaveBeenCalled();
    expect(window.openRequestDetails).not.toHaveBeenCalled();
  });

  it('opens the existing episode details after confirmed recovery without starting a search', async () => {
    renderDownloadHealth([problem]);
    window.confirm.mockReturnValue(true);
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'recovered', replacement_review_url: '/requests/12/details' })),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ problems: [] })));
    await confirmDownloadHealthRecovery(problem.id);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ confirm: true, disposition: 'cooldown' });
    expect(window.openRequestDetails).toHaveBeenCalledWith(12);
    expect(window.focusTvEpisode).toHaveBeenCalledWith(12, 1, 2);
    expect(window.searchEpisode).not.toHaveBeenCalled();
  });

  it('shows a fresh-state conflict and does not navigate on a refused recovery', async () => {
    renderDownloadHealth([problem]);
    window.confirm.mockReturnValue(true);
    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ detail: 'Torrent has resumed' }), { status: 409 }));
    await confirmDownloadHealthRecovery(problem.id);
    expect(window.showToast).toHaveBeenCalledWith('Torrent has resumed');
    expect(window.openRequestDetails).not.toHaveBeenCalled();
  });
});
