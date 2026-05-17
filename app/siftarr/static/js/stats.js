const form = document.getElementById('stats-range-form');
const rangeSelect = document.getElementById('stats-range');
const customRange = document.querySelector('.stats-custom-range');
const loading = document.getElementById('stats-loading');
const errorBox = document.getElementById('stats-error');
const emptyBox = document.getElementById('stats-empty');
const content = document.getElementById('stats-content');
const rangeLabel = document.getElementById('stats-range-label');

function setHidden(el, hidden) {
    el.classList.toggle('hidden', hidden);
}

function formatNumber(value) {
    return new Intl.NumberFormat().format(value || 0);
}

function formatMs(value) {
    if (value === null || value === undefined) return '--';
    if (value >= 3600000) return `${(value / 3600000).toFixed(1)}h`;
    if (value >= 60000) return `${(value / 60000).toFixed(1)}m`;
    if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
    return `${Math.round(value)}ms`;
}

function renderBars(container, rows, valueKey = 'value', availability = 'stats') {
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

function allPoints(rows) {
    if (!rows) return [];
    if (rows.length > 0 && rows[0].points) return rows.flatMap((row) => row.points || []);
    return rows;
}

function renderPointSeries(container, rows, availability = 'stats') {
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
    const seriesRows = rows[0]?.points ? rows : [{ label: 'Total', points: rows }];
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
        total.textContent = formatNumber((series.points || []).reduce((sum, point) => sum + (point.value || 0), 0));
        title.append(label, total);
        const bars = document.createElement('div');
        bars.className = 'flex h-24 items-end gap-1 rounded-lg bg-surface-850 p-2';
        (series.points || []).forEach((point) => {
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

function updateCards(cards, availability = {}) {
    document.querySelector('[data-card="total_requests"]').textContent = formatNumber(cards.total_requests);
    document.querySelector('[data-card="downloads_processed"]').textContent = availability.downloads_processed === 'unavailable' ? '--' : formatNumber(cards.downloads_processed);
    document.querySelector('[data-card="approval_rate"]').textContent = cards.approval_rate === null ? '--' : `${cards.approval_rate}%`;
    document.querySelector('[data-card="evaluated_requests"]').textContent = availability.evaluated_requests === 'unavailable' ? 'Unavailable' : `${formatNumber(cards.evaluated_requests)} evaluated`;
    document.querySelector('[data-card="rules"]').textContent = `${formatNumber(cards.enabled_rules)} / ${formatNumber(cards.total_rules)}`;
}

function renderStats(data) {
    rangeLabel.textContent = data.range.label;
    updateCards(data.cards, data.availability);
    renderBars(document.querySelector('[data-chart="resolution_split"]'), data.charts.resolution_split, 'value', data.availability?.resolution_split);
    renderBars(document.querySelector('[data-chart="source_split"]'), data.charts.source_split, 'value', data.availability?.source_split);
    renderBars(document.querySelector('[data-chart="rule_outcomes"]'), data.charts.rule_outcomes, 'value', data.availability?.rule_outcomes);
    renderBars(document.querySelector('[data-chart="processing_times"]'), data.charts.processing_times, 'avg_ms', data.availability?.processing_times);
    const series = data.charts.time_series || {};
    renderPointSeries(document.querySelector('[data-series-chart="downloads"]'), series.downloads, data.availability?.downloads_series);
    renderPointSeries(document.querySelector('[data-series-chart="failures"]'), series.failures, data.availability?.failures_series);
    renderPointSeries(document.querySelector('[data-series-chart="rule_rejections"]'), series.rule_rejections, data.availability?.rule_rejections_series);
    renderPointSeries(document.querySelector('[data-series-chart="indexer_behavior"]'), series.indexer_behavior, data.availability?.indexer_behavior_series);
    setHidden(emptyBox, !data.empty);
    setHidden(content, false);
}

function buildUrl() {
    const params = new URLSearchParams(new FormData(form));
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
        errorBox.textContent = err.message;
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
