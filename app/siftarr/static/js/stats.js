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

function renderBars(container, rows, valueKey = 'value') {
    container.innerHTML = '';
    if (!rows || rows.length === 0) {
        container.innerHTML = '<p class="text-sm text-gray-500">No data for this range.</p>';
        return;
    }
    const max = Math.max(...rows.map((row) => row[valueKey] || 0), 1);
    rows.forEach((row) => {
        const value = row[valueKey] || 0;
        const width = Math.max((value / max) * 100, value > 0 ? 4 : 0);
        const item = document.createElement('div');
        item.innerHTML = `
            <div class="mb-1 flex items-center justify-between gap-3 text-sm">
                <span class="truncate text-gray-300">${row.label}</span>
                <span class="font-mono text-gray-400">${valueKey === 'avg_ms' ? formatMs(value) : formatNumber(value)}</span>
            </div>
            <div class="h-2 overflow-hidden rounded-full bg-surface-850">
                <div class="h-full rounded-full bg-brand-500" style="width: ${width}%"></div>
            </div>`;
        container.appendChild(item);
    });
}

function updateCards(cards) {
    document.querySelector('[data-card="total_requests"]').textContent = formatNumber(cards.total_requests);
    document.querySelector('[data-card="downloads_processed"]').textContent = formatNumber(cards.downloads_processed);
    document.querySelector('[data-card="approval_rate"]').textContent = cards.approval_rate === null ? '--' : `${cards.approval_rate}%`;
    document.querySelector('[data-card="evaluated_requests"]').textContent = `${formatNumber(cards.evaluated_requests)} evaluated`;
    document.querySelector('[data-card="rules"]').textContent = `${formatNumber(cards.enabled_rules)} / ${formatNumber(cards.total_rules)}`;
}

function renderStats(data) {
    rangeLabel.textContent = data.range.label;
    updateCards(data.cards);
    renderBars(document.querySelector('[data-chart="resolution_split"]'), data.charts.resolution_split);
    renderBars(document.querySelector('[data-chart="source_split"]'), data.charts.source_split);
    renderBars(document.querySelector('[data-chart="rule_outcomes"]'), data.charts.rule_outcomes);
    renderBars(document.querySelector('[data-chart="processing_times"]'), data.charts.processing_times, 'avg_ms');
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
