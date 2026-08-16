type SortDirection = 'asc' | 'desc';

interface TableSort {
  column: string | null;
  direction: SortDirection;
}

interface DashboardState {
  tableSortState: Record<string, TableSort>;
  mediaFilterState: Record<string, unknown>;
  visibleRequests: unknown[];
  currentDetailsIndex: number;
  currentReleases: unknown[];
  currentRequestId: string | number | null;
  currentTvSeasons: unknown[];
  currentRequestTimeline: unknown[];
}

export const dashboardState: DashboardState = {
  tableSortState: {
    active: { column: null, direction: 'asc' },
    pending: { column: null, direction: 'asc' },
    unreleased: { column: null, direction: 'asc' },
    staged: { column: null, direction: 'asc' },
    downloading: { column: null, direction: 'asc' },
    finished: { column: null, direction: 'asc' },
    rejected: { column: null, direction: 'asc' },
  },
  mediaFilterState: {},
  visibleRequests: [],
  currentDetailsIndex: -1,
  currentReleases: [],
  currentRequestId: null,
  currentTvSeasons: [],
  currentRequestTimeline: [],
};
