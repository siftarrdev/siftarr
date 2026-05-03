// Dashboard Modals Module - Modal dialogs and toast notifications
// ===============================================================

function showToast(message) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'bg-surface-800 text-white border border-gray-700/60 rounded-xl px-5 py-3 text-sm shadow-2xl pointer-events-auto transition-opacity duration-300 opacity-0';
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.classList.remove('opacity-0'); toast.classList.add('opacity-100'); });
    setTimeout(() => {
        toast.classList.remove('opacity-100');
        toast.classList.add('opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function setSearchActionLoading(trigger, message = 'Searching...') {
    if (!trigger) return;
    trigger.disabled = true;
    trigger.dataset.originalText = trigger.textContent;
    trigger.innerHTML = '<span class="inline-flex items-center gap-1.5">' +
        '<svg class="animate-spin h-3.5 w-3.5" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
        '<span>' + window.escapeHtml(message) + '</span>' +
    '</span>';
    trigger.setAttribute('aria-busy', 'true');
}

function disableSearchControls(scope) {
    if (!scope) return;
    scope.querySelectorAll('[data-search-action="true"], [data-search-submit-control="true"]').forEach(control => {
        control.disabled = true;
    });
}

function getRequestTitleFromRow(row) {
    if (!row) return null;
    const titleCell = row.querySelector('td:nth-child(2)');
    const title = titleCell ? titleCell.textContent.trim() : '';
    return title || row.dataset.title || null;
}

function collectBulkSearchTitles(form, searchAll = false) {
    if (!form) return [];
    const selector = searchAll
        ? 'tbody tr'
        : 'input[name="request_ids"]:checked, input[name="torrent_ids"]:checked';
    const nodes = Array.from(form.querySelectorAll(selector));
    return nodes
        .map(node => getRequestTitleFromRow(searchAll ? node : node.closest('tr')))
        .filter(Boolean);
}

async function submitSearchRequest(action, body, redirectTo) {
    const response = await fetch(action, {
        method: 'POST',
        body: body,
        headers: { 'Accept': 'text/html' },
    });
    if (!response.ok) throw new Error('Search request failed (' + response.status + ')');
    return response.url || redirectTo || window.location.href;
}

function showBulkSearchStatus(form, searchAll = false) {
    if (!form) return;
    const panel = form.querySelector('[data-bulk-search-status="true"]');
    if (!panel) return;
    const selectedCount = form.querySelectorAll('input[name="request_ids"]:checked, input[name="torrent_ids"]:checked').length;
    const title = panel.querySelector('[data-bulk-search-status-title="true"]');
    const message = panel.querySelector('[data-bulk-search-status-message="true"]');
    if (title) title.textContent = searchAll ? 'Searching all pending requests' : 'Searching selected requests/torrents';
    if (message) {
        message.textContent = searchAll
            ? 'Searching all pending items. You will be redirected when the search starts.'
            : selectedCount > 0
            ? 'Searching ' + selectedCount + ' selected item' + (selectedCount === 1 ? '' : 's') + '. You will be redirected when the search starts.'
            : 'Preparing selected search. You will be redirected when the search starts.';
    }
    panel.classList.remove('hidden');
}

function postToAction(action, redirectTo, trigger = null) {
    const row = trigger ? trigger.closest('tr') : null;
    const title = getRequestTitleFromRow(row);
    if (trigger && trigger.dataset.searchAction === 'true') {
        setSearchActionLoading(trigger);
        disableSearchControls(trigger.closest('tr'));
        window.showSearchProgressPanel('Searching request', title ? 'Searching for ' + title + '…' : 'Searching request…', title ? [title] : []);
    }
    const body = new FormData();
    body.append('redirect_to', redirectTo);
    submitSearchRequest(action, body, redirectTo)
        .then(url => {
            window.completeSearchProgressPanel('Search complete. Reloading…');
            window.location.assign(url);
        })
        .catch(error => window.completeSearchProgressPanel(error.message || 'Search failed. Please try again.', true));
}

function handleBulkRequestActionSubmit(event, form) {
    const submitter = event.submitter;
    if (!submitter || submitter.dataset.searchSubmitControl !== 'true') return true;
    if (form.dataset.searchSubmitting === 'true') return false;
    event.preventDefault();
    form.dataset.searchSubmitting = 'true';
    const actionInput = document.createElement('input');
    actionInput.type = 'hidden';
    actionInput.name = 'action';
    actionInput.value = submitter.value;
    form.appendChild(actionInput);
    const searchAll = submitter.value === 'search_all_pending';
    const titles = collectBulkSearchTitles(form, searchAll);
    showBulkSearchStatus(form, searchAll);
    window.showSearchProgressPanel(
        searchAll ? 'Searching all pending requests' : 'Searching selected requests',
        titles.length ? 'Searching for ' + titles.slice(0, 3).join(', ') + (titles.length > 3 ? '…' : '…') : 'Preparing search…',
        titles
    );
    setSearchActionLoading(submitter, submitter.value === 'search_all_pending' ? 'Searching all...' : 'Searching...');
    const body = new FormData(form);
    disableSearchControls(form);
    submitSearchRequest(form.action, body, form.querySelector('input[name="redirect_to"]')?.value)
        .then(url => {
            window.completeSearchProgressPanel('Search complete. Reloading…');
            window.location.assign(url);
        })
        .catch(error => window.completeSearchProgressPanel(error.message || 'Search failed. Please try again.', true));
    return false;
}

function openDenyModal(requestId, redirectTo) {
    const modal = document.getElementById('deny-modal');
    const form = document.getElementById('deny-form');
    const redirect = document.getElementById('deny-redirect');
    const reason = document.getElementById('deny-reason');
    form.action = '/requests/' + requestId + '/deny';
    redirect.value = redirectTo || '/';
    reason.value = '';
    modal.classList.remove('hidden');
}

function closeDenyModal() {
    document.getElementById('deny-modal').classList.add('hidden');
}

function openReplaceModal(torrentId, requestId, torrentTitle, redirectTo) {
    const modal = document.getElementById('replace-modal');
    const form = document.getElementById('replace-form');
    const currentTorrentEl = document.getElementById('replace-current-torrent');
    const reason = document.getElementById('replace-reason');
    form.action = '/staged/' + torrentId + '/replace';
    document.getElementById('replace-redirect').value = redirectTo || '/?tab=downloading';
    currentTorrentEl.textContent = torrentTitle || 'Unknown torrent';
    reason.value = '';
    modal.classList.remove('hidden');
}

function closeReplaceModal() {
    document.getElementById('replace-modal').classList.add('hidden');
}

function bindSelectAll(toggle, checkboxSelector) {
    if (!toggle) return;
    toggle.addEventListener('change', event => {
        document.querySelectorAll(checkboxSelector).forEach(checkbox => {
            checkbox.checked = event.target.checked;
        });
    });
}

// Export functions to window for HTML onclick handlers
window.showToast = showToast;
window.postToAction = postToAction;
window.setSearchActionLoading = setSearchActionLoading;
window.showBulkSearchStatus = showBulkSearchStatus;
window.handleBulkRequestActionSubmit = handleBulkRequestActionSubmit;
window.openDenyModal = openDenyModal;
window.closeDenyModal = closeDenyModal;
window.openReplaceModal = openReplaceModal;
window.closeReplaceModal = closeReplaceModal;
window.bindSelectAll = bindSelectAll;
