import { beforeEach, describe, expect, it, vi } from 'vitest';
import { approveRelease } from '../../frontend-src/dashboard/releases.ts';

describe('release approval warnings', () => {
  beforeEach(() => {
    window.showToast = vi.fn();
    window.confirm = vi.fn();
  });

  it('cancels without retrying and only resubmits explicitly requested flags', async () => {
    const button = document.createElement('button');
    button.dataset.stageUrl = '/requests/1/releases/2/use';
    button.dataset.stageFields = '{}';
    button.dataset.stageScope = '{}';
    global.fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { warnings: [{ code: 'rules', message: 'Fails current rules' }] } }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    window.confirm.mockReturnValue(false);

    await approveRelease(button);
    expect(fetch).toHaveBeenCalledTimes(1);

    const confirmedButton = document.createElement('button');
    confirmedButton.dataset.stageUrl = button.dataset.stageUrl;
    confirmedButton.dataset.stageFields = '{}';
    confirmedButton.dataset.stageScope = '{}';
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: { warnings: [{ code: 'rules', message: 'Fails current rules' }] } }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'ok' }), { status: 200 }));
    window.confirm.mockReturnValue(true);
    await approveRelease(confirmedButton);
    expect(fetch).toHaveBeenCalledTimes(2);
    const retryBody = fetch.mock.calls[1][1].body;
    expect(retryBody.get('confirm_rules')).toBe('true');
    expect(retryBody.get('confirm_identity')).toBeNull();
  });
});
