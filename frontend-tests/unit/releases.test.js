import { beforeEach, describe, expect, it } from 'vitest';
import { renderReleaseCard, renderSeasonAccordion } from '/static/js/dashboard/releases.js';

describe('dashboard release rendering', () => {
  beforeEach(() => {
    window.siftarrStagingModeEnabled = true;
    window.detailsControlState = {};
  });

  it('tones annotations from relevant rule metadata before using fallbacks', () => {
    const card = (release) => {
      const root = document.createElement('div');
      root.innerHTML = renderReleaseCard({ title: 'Release', passed: true, ...release }, 9);
      return root;
    };

    expect(
      card({
        resolution: '2160p',
        matches: [{ rule_name: 'Resolution 2160p', matched: true, effect: 'allow' }],
      }).querySelector('[data-release-resolution]').classList,
    ).toContain('text-emerald-400');
    expect(
      card({
        codec: 'x265',
        matches: [{ rule_name: 'Codec x265', matched: true, effect: 'disallow' }],
      }).querySelector('[data-release-codec]').classList,
    ).toContain('text-red-400');
    expect(
      card({
        resolution: '2160p',
        matches: [{ rule_name: 'Indexer trusted', matched: true, effect: 'allow' }],
      }).querySelector('[data-release-resolution]').classList,
    ).toContain('text-gray-400');
    expect(card({ codec: 'x265', matches: [] }).querySelector('[data-release-codec]').classList).toContain(
      'text-emerald-400',
    );
  });

  it('keeps episode search available for staged and pending episodes', () => {
    const html = renderSeasonAccordion({
      request: { id: 9 },
      active_staged_torrents: [
        {
          id: 71,
          status: 'staged',
          target_scope: { type: 'single_episode', season_number: 1, episode_number: 3 },
        },
        {
          id: 72,
          status: 'staged',
          target_scope: { type: 'single_episode', season_number: 1, episode_number: 2 },
        },
      ],
      tv_info: {
        seasons: [
          {
            id: 1,
            season_number: 1,
            status: 'staged',
            available_count: 0,
            total_count: 2,
            staged_count: 1,
            pending_count: 1,
            unreleased_count: 0,
            episodes: [
              { id: 2, episode_number: 2, title: 'Two', air_date: '2026-08-14', status: 'staged' },
              { id: 3, episode_number: 3, title: 'Three', status: 'pending' },
            ],
          },
        ],
        releases_by_episode: {
          '1-2': [{ id: 200, passed: true, download_url: 'https://example.test/torrent' }],
        },
        releases_by_season: {},
        aggregate_counts: { available: 0, total: 2 },
      },
    });

    expect(html).toContain('Search S01E02 again');
    expect(html).toContain('Search S01E03 again');
    expect(html).toContain('Airs: 2026-08-14');
    expect(html).not.toContain('stageTopEpisodeRelease');
  });
});
