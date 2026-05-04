// Search SSE Progress Panel
// ==========================
// Reusable SSE-based search progress panel for the dashboard.

let activeSearchEventSource = null;

function getProgressBar() {
    return document.getElementById('search-progress-bar');
}

function getProgressPanel() {
    return document.getElementById('search-progress-panel');
}

function getProgressStatus() {
    return document.getElementById('search-progress-status');
}

function getProgressTitle() {
    return document.getElementById('search-progress-title');
}

function getProgressSubtitle() {
    return document.getElementById('search-progress-subtitle');
}

function getProgressActiveWrap() {
    return document.getElementById('search-progress-active-wrap');
}

function getProgressActiveList() {
    return document.getElementById('search-progress-active-list');
}

function resetProgressPanel() {
    const bar = getProgressBar();
    const status = getProgressStatus();
    const title = getProgressTitle();
    const subtitle = getProgressSubtitle();
    const activeWrap = getProgressActiveWrap();
    const activeList = getProgressActiveList();

    if (bar) {
        bar.style.width = '0%';
        bar.classList.remove('bg-emerald-500', 'bg-red-500');
        bar.classList.add('bg-brand-500');
    }
    if (status) status.textContent = 'Preparing search…';
    if (title) title.textContent = 'Searching';
    if (subtitle) subtitle.textContent = 'Initializing…';
    if (activeWrap) activeWrap.classList.add('hidden');
    if (activeList) activeList.innerHTML = '';
}

function closeSearchProgress() {
    if (activeSearchEventSource) {
        activeSearchEventSource.close();
        activeSearchEventSource = null;
    }
    const panel = getProgressPanel();
    if (panel) panel.classList.add('hidden');
    resetProgressPanel();
}

function setPhaseStyles(bar, phase) {
    bar.classList.remove('bg-brand-500', 'bg-emerald-500', 'bg-red-500');
    if (phase === 'complete') {
        bar.classList.add('bg-emerald-500');
    } else if (phase === 'error') {
        bar.classList.add('bg-red-500');
    } else {
        bar.classList.add('bg-brand-500');
    }
}

function _invokeCallback(cb, data) {
    if (typeof cb === 'function') {
        try {
            cb(data);
        } catch (e) {
            // eslint-disable-next-line no-console
            console.error('SSE callback error:', e);
        }
    }
}

function startSearchProgress(requestId, title, onComplete, onError) {
    closeSearchProgress();
    resetProgressPanel();

    const panel = getProgressPanel();
    if (panel) panel.classList.remove('hidden');

    const titleEl = getProgressTitle();
    if (titleEl && title) titleEl.textContent = title;

    let finished = false;

    const url = '/requests/' + requestId + '/search/stream';
    const es = new EventSource(url);
    activeSearchEventSource = es;

    es.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        const bar = getProgressBar();
        const status = getProgressStatus();
        const titleEl = getProgressTitle();

        if (data.percent !== undefined && bar) {
            bar.style.width = data.percent + '%';
        }
        if (data.message && status) {
            status.textContent = data.message;
        }
        if (title && titleEl) {
            titleEl.textContent = title;
        }

        if (data.phase) {
            switch (data.phase) {
                case 'starting':
                    if (bar) bar.style.width = '5%';
                    break;
                case 'backfilling':
                    if (bar) bar.style.width = '15%';
                    break;
                case 'searching':
                    if (bar) bar.style.width = '50%';
                    break;
                case 'complete':
                    finished = true;
                    if (bar) {
                        bar.style.width = '100%';
                        setPhaseStyles(bar, 'complete');
                    }
                    _invokeCallback(onComplete, data);
                    setTimeout(() => closeSearchProgress(), 3000);
                    break;
                case 'error':
                    finished = true;
                    if (bar) {
                        bar.style.width = '100%';
                        setPhaseStyles(bar, 'error');
                    }
                    if (status && data.message) {
                        status.textContent = data.message;
                    }
                    _invokeCallback(onError, data);
                    break;
            }
        }
    };

    es.onerror = () => {
        if (finished) {
            es.close();
            activeSearchEventSource = null;
            return;
        }
        const bar = getProgressBar();
        const status = getProgressStatus();
        if (bar) {
            bar.style.width = '100%';
            setPhaseStyles(bar, 'error');
        }
        if (status) status.textContent = 'Connection lost';
        es.close();
        activeSearchEventSource = null;
        _invokeCallback(onError, { phase: 'error', message: 'Connection lost' });
    };
}

function startBulkSearchProgress(requestIds, titles, onComplete, onError) {
    closeSearchProgress();
    resetProgressPanel();

    const panel = getProgressPanel();
    if (panel) panel.classList.remove('hidden');

    const titleEl = getProgressTitle();
    if (titleEl) titleEl.textContent = 'Bulk Search';

    const activeWrap = getProgressActiveWrap();
    const activeList = getProgressActiveList();
    if (activeWrap && titles && titles.length > 0) {
        activeWrap.classList.remove('hidden');
        if (activeList) {
            activeList.innerHTML = titles.map(function(t) {
                return '<li class="text-xs truncate" data-bulk-title="' + window.escapeHtml(t) + '">' + window.escapeHtml(t) + '</li>';
            }).join('');
        }
    }

    let finished = false;

    const params = requestIds.map(function(id) { return 'request_ids=' + encodeURIComponent(id); }).join('&');
    const url = '/requests/bulk/search/stream?' + params;
    const es = new EventSource(url);
    activeSearchEventSource = es;

    es.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        const bar = getProgressBar();
        const status = getProgressStatus();
        const subtitle = getProgressSubtitle();
        const list = getProgressActiveList();

        if (data.phase === 'starting') {
            if (bar) bar.style.width = '5%';
            if (status) status.textContent = 'Starting bulk search…';
            if (subtitle && data.total) subtitle.textContent = data.total + ' request(s) queued';
        }

        if (data.phase === 'searching') {
            if (data.total && data.current !== undefined) {
                const pct = Math.round((data.current / data.total) * 100);
                if (bar) bar.style.width = pct + '%';
            }
            if (data.title && status) {
                status.textContent = 'Searching: ' + data.title;
            }
            if (subtitle && data.current !== undefined && data.total !== undefined) {
                subtitle.textContent = data.current + ' of ' + data.total;
            }
            if (list && data.title) {
                const items = list.querySelectorAll('li');
                items.forEach(function(li) {
                    if (li.textContent === data.title) {
                        li.classList.add('text-emerald-400');
                    }
                });
            }
        }

        if (data.phase === 'complete') {
            finished = true;
            if (bar) {
                bar.style.width = '100%';
                setPhaseStyles(bar, 'complete');
            }
            const count = data.results ? data.results.length : 0;
            if (status) status.textContent = 'Finished — ' + count + ' request(s) processed';
            if (subtitle) subtitle.textContent = 'Done';
            _invokeCallback(onComplete, data);
            setTimeout(() => closeSearchProgress(), 3000);
        }

        if (data.phase === 'error') {
            finished = true;
            if (bar) {
                bar.style.width = '100%';
                setPhaseStyles(bar, 'error');
            }
            if (status && data.message) {
                status.textContent = data.message;
            }
            _invokeCallback(onError, data);
        }
    };

    es.onerror = () => {
        if (finished) {
            es.close();
            activeSearchEventSource = null;
            return;
        }
        const bar = getProgressBar();
        const status = getProgressStatus();
        if (bar) {
            bar.style.width = '100%';
            setPhaseStyles(bar, 'error');
        }
        if (status) status.textContent = 'Connection lost';
        es.close();
        activeSearchEventSource = null;
        _invokeCallback(onError, { phase: 'error', message: 'Connection lost' });
    };
}

function startTvSearchProgress(streamUrl, title, onComplete, onError) {
    closeSearchProgress();
    resetProgressPanel();

    const panel = getProgressPanel();
    if (panel) panel.classList.remove('hidden');

    const titleEl = getProgressTitle();
    if (titleEl && title) titleEl.textContent = title;

    let finished = false;

    const es = new EventSource(streamUrl);
    activeSearchEventSource = es;

    es.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        const bar = getProgressBar();
        const status = getProgressStatus();

        if (data.percent !== undefined && bar) {
            bar.style.width = data.percent + '%';
        }
        if (data.message && status) {
            status.textContent = data.message;
        }

        if (data.phase) {
            switch (data.phase) {
                case 'complete':
                    finished = true;
                    if (bar) {
                        bar.style.width = '100%';
                        setPhaseStyles(bar, 'complete');
                    }
                    _invokeCallback(onComplete, data);
                    setTimeout(() => closeSearchProgress(), 3000);
                    break;
                case 'error':
                    finished = true;
                    if (bar) {
                        bar.style.width = '100%';
                        setPhaseStyles(bar, 'error');
                    }
                    _invokeCallback(onError, data);
                    break;
            }
        }
    };

    es.onerror = () => {
        if (finished) {
            es.close();
            activeSearchEventSource = null;
            return;
        }
        const bar = getProgressBar();
        const status = getProgressStatus();
        if (bar) {
            bar.style.width = '100%';
            setPhaseStyles(bar, 'error');
        }
        if (status) status.textContent = 'Connection lost';
        es.close();
        activeSearchEventSource = null;
        _invokeCallback(onError, { phase: 'error', message: 'Connection lost' });
    };
}

function updateRequestRow(requestId, result) {
    const row = document.querySelector('tr[data-request-id="' + requestId + '"]');
    if (!row) return;

    const status = result.status || '';
    const badgeCell = row.querySelector('td > .badge');
    if (badgeCell) {
        badgeCell.classList.remove('badge-green', 'badge-yellow', 'badge-blue', 'badge-red', 'badge-gray');
        switch (status) {
            case 'completed':
                badgeCell.classList.add('badge-green');
                break;
            case 'staged':
            case 'pending':
                badgeCell.classList.add('badge-yellow');
                break;
            case 'downloading':
                badgeCell.classList.add('badge-blue');
                break;
            case 'failed':
                badgeCell.classList.add('badge-red');
                break;
            default:
                badgeCell.classList.add('badge-gray');
                break;
        }
        badgeCell.textContent = status;
    }

    const searchButtons = row.querySelectorAll('[data-search-action="true"]');
    searchButtons.forEach(function(btn) {
        btn.disabled = false;
    });

    row.setAttribute('data-status', status);
}

window.closeSearchProgress = closeSearchProgress;
window.startSearchProgress = startSearchProgress;
window.startBulkSearchProgress = startBulkSearchProgress;
window.startTvSearchProgress = startTvSearchProgress;
window.updateRequestRow = updateRequestRow;
