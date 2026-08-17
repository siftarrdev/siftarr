export {};

type Point = { label: string; value: number };
type StatRow = { label: string; value?: number; avg_ms?: number };
type Series = { label: string; points?: Point[] };
type ChartRow = Point | Series;
type Cards = {
  total_requests: number;
  downloads_processed: number;
  approval_rate: number | null;
  evaluated_requests: number;
  enabled_rules: number;
  total_rules: number;
};
type StatsData = {
  range: { label: string };
  cards: Cards;
  availability?: Record<string, string>;
  empty: boolean;
  charts: {
    resolution_split?: StatRow[];
    source_split?: StatRow[];
    rule_outcomes?: StatRow[];
    processing_times?: StatRow[];
    time_series?: Record<string, (Point | Series)[]>;
  };
};

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing stats element: ${selector}`);
  return element;
}

const form = requiredElement<HTMLFormElement>('#stats-range-form');
const rangeSelect = requiredElement<HTMLSelectElement>('#stats-range');
const customRange = requiredElement<HTMLElement>('.stats-custom-range');
const loading = requiredElement<HTMLElement>('#stats-loading');
const errorBox = requiredElement<HTMLElement>('#stats-error');
const emptyBox = requiredElement<HTMLElement>('#stats-empty');
const content = requiredElement<HTMLElement>('#stats-content');
const rangeLabel = requiredElement<HTMLElement>('#stats-range-label');

function setHidden(el: HTMLElement, hidden: boolean) {
  el.classList.toggle('hidden', hidden);
}

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat().format(value || 0);
}

function formatMs(value: number | null | undefined) {
  if (value === null || value === undefined) return '--';
  if (value >= 3600000) return `${(value / 3600000).toFixed(1)}h`;
  if (value >= 60000) return `${(value / 60000).toFixed(1)}m`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function renderBars(
  container: HTMLElement,
  rows: StatRow[] | undefined,
  valueKey: 'value' | 'avg_ms' = 'value',
  availability: string = 'stats',
) {
  container.innerHTML = '';
  if (availability === 'unavailable') {
    const message = document.createElement('p');
    message.className = 'text-sm text-gray-500';
    message.textContent = 'Unavailable for historical data.';
    container.appendChild(message);
    return;
  }
  if (!rows || rows.length === 0) {
    const message = document.createElement('p');
    message.className = 'text-sm text-gray-500';
    message.textContent = 'No data for this range.';
    container.appendChild(message);
    return;
  }
  const max = Math.max(...rows.map((row) => row[valueKey] || 0), 1);
  rows.forEach((row) => {
    const value = row[valueKey] || 0;
    const width = Math.max((value / max) * 100, value > 0 ? 4 : 0);
    const item = document.createElement('div');
    const header = document.createElement('div');
    header.className = 'mb-1 flex items-center justify-between gap-3 text-sm';
    const label = document.createElement('span');
    label.className = 'truncate text-gray-300';
    label.textContent = row.label;
    const amount = document.createElement('span');
    amount.className = 'font-mono text-gray-400';
    amount.textContent = valueKey === 'avg_ms' ? formatMs(value) : formatNumber(value);
    header.append(label, amount);
    const track = document.createElement('div');
    track.className = 'h-2 overflow-hidden rounded-full bg-surface-850';
    const bar = document.createElement('div');
    bar.className = 'h-full rounded-full bg-brand-500';
    bar.style.width = `${width}%`;
    track.appendChild(bar);
    item.append(header, track);
    container.appendChild(item);
  });
}

function allPoints(rows: ChartRow[] | undefined): Point[] {
  if (!rows) return [];
  const first = rows[0];
  if (first && 'points' in first) {
    return rows.flatMap((row) => ('points' in row && row.points ? row.points : []));
  }
  return rows as Point[];
}

function renderPointSeries(container: HTMLElement, rows: ChartRow[] | undefined, availability: string = 'stats') {
  container.innerHTML = '';
  if (availability === 'unavailable') {
    const message = document.createElement('p');
    message.className = 'text-sm text-gray-500';
    message.textContent = 'Unavailable for this range.';
    container.appendChild(message);
    return;
  }
  const points = allPoints(rows);
  const hasValues = points.some((point) => (point.value || 0) > 0);
  if (!rows || rows.length === 0 || !hasValues) {
    const message = document.createElement('p');
    message.className = 'text-sm text-gray-500';
    message.textContent = 'No data for this range.';
    container.appendChild(message);
    return;
  }
  const first = rows[0];
  const seriesRows: Series[] =
    first && 'points' in first ? (rows as Series[]) : [{ label: 'Total', points: rows as Point[] }];
  const max = Math.max(...allPoints(seriesRows).map((point) => point.value || 0), 1);
  const wrapper = document.createElement('div');
  wrapper.className = 'space-y-4';
  seriesRows.forEach((series) => {
    const block = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'mb-2 flex items-center justify-between text-xs text-gray-400';
    const label = document.createElement('span');
    label.textContent = series.label;
    const total = document.createElement('span');
    total.className = 'font-mono';
    total.textContent = formatNumber(
      (series.points || []).reduce((sum: number, point: Point) => sum + (point.value || 0), 0),
    );
    title.append(label, total);
    const bars = document.createElement('div');
    bars.className = 'flex h-24 items-end gap-1 rounded-lg bg-surface-850 p-2';
    (series.points || []).forEach((point: Point) => {
      const bar = document.createElement('div');
      const height = Math.max(((point.value || 0) / max) * 100, point.value > 0 ? 5 : 1);
      bar.className = 'min-w-1 flex-1 rounded-t bg-brand-500/80';
      bar.style.height = `${height}%`;
      bar.title = `${point.label}: ${formatNumber(point.value)}`;
      bars.appendChild(bar);
    });
    block.append(title, bars);
    wrapper.appendChild(block);
  });
  container.appendChild(wrapper);
}

function updateCards(cards: Cards, availability: Record<string, string> = {}) {
  (document.querySelector('[data-card="total_requests"]') as HTMLElement).textContent = formatNumber(
    cards.total_requests,
  );
  (document.querySelector('[data-card="downloads_processed"]') as HTMLElement).textContent =
    availability.downloads_processed === 'unavailable' ? '--' : formatNumber(cards.downloads_processed);
  (document.querySelector('[data-card="approval_rate"]') as HTMLElement).textContent =
    cards.approval_rate === null ? '--' : `${cards.approval_rate}%`;
  (document.querySelector('[data-card="evaluated_requests"]') as HTMLElement).textContent =
    availability.evaluated_requests === 'unavailable'
      ? 'Unavailable'
      : `${formatNumber(cards.evaluated_requests)} evaluated`;
  (document.querySelector('[data-card="rules"]') as HTMLElement).textContent =
    `${formatNumber(cards.enabled_rules)} / ${formatNumber(cards.total_rules)}`;
}

function renderStats(data: StatsData) {
  rangeLabel.textContent = data.range.label;
  updateCards(data.cards, data.availability);
  renderBars(
    document.querySelector('[data-chart="resolution_split"]') as HTMLElement,
    data.charts.resolution_split,
    'value',
    data.availability?.resolution_split,
  );
  renderBars(
    document.querySelector('[data-chart="source_split"]') as HTMLElement,
    data.charts.source_split,
    'value',
    data.availability?.source_split,
  );
  renderBars(
    document.querySelector('[data-chart="rule_outcomes"]') as HTMLElement,
    data.charts.rule_outcomes,
    'value',
    data.availability?.rule_outcomes,
  );
  renderBars(
    document.querySelector('[data-chart="processing_times"]') as HTMLElement,
    data.charts.processing_times,
    'avg_ms',
    data.availability?.processing_times,
  );
  const series: Record<string, ChartRow[]> = data.charts.time_series || {};
  renderPointSeries(
    document.querySelector('[data-series-chart="downloads"]') as HTMLElement,
    series.downloads,
    data.availability?.downloads_series,
  );
  renderPointSeries(
    document.querySelector('[data-series-chart="failures"]') as HTMLElement,
    series.failures,
    data.availability?.failures_series,
  );
  renderPointSeries(
    document.querySelector('[data-series-chart="rule_rejections"]') as HTMLElement,
    series.rule_rejections,
    data.availability?.rule_rejections_series,
  );
  renderPointSeries(
    document.querySelector('[data-series-chart="indexer_behavior"]') as HTMLElement,
    series.indexer_behavior,
    data.availability?.indexer_behavior_series,
  );
  setHidden(emptyBox, !data.empty);
  setHidden(content, false);
}

function buildUrl(): string {
  const params = new URLSearchParams(
    Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value)]),
  );
  if (params.get('range') !== 'custom') {
    params.delete('start');
    params.delete('end');
  }
  return `/stats/data?${params.toString()}`;
}

async function loadStats() {
  setHidden(loading, false);
  setHidden(errorBox, true);
  try {
    const response = await fetch(buildUrl(), { credentials: 'same-origin' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load stats');
    renderStats(data);
  } catch (err) {
    errorBox.textContent = err instanceof Error ? err.message : 'Failed to load stats';
    setHidden(errorBox, false);
  } finally {
    setHidden(loading, true);
  }
}

function syncCustomRange() {
  const custom = rangeSelect.value === 'custom';
  customRange.classList.toggle('hidden', !custom);
  customRange.classList.toggle('grid', custom);
}

rangeSelect.addEventListener('change', syncCustomRange);
form.addEventListener('submit', (event) => {
  event.preventDefault();
  loadStats();
});

syncCustomRange();
loadStats();
