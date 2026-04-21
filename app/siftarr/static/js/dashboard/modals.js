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

function postToAction(action, redirectTo) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = action;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'redirect_to';
    input.value = redirectTo;
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
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

function openReplaceModal(torrentId, requestId, torrentTitle) {
    const modal = document.getElementById('replace-modal');
    const form = document.getElementById('replace-form');
    const currentTorrentEl = document.getElementById('replace-current-torrent');
    const reason = document.getElementById('replace-reason');
    form.action = '/staged/' + torrentId + '/replace';
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
window.openDenyModal = openDenyModal;
window.closeDenyModal = closeDenyModal;
window.openReplaceModal = openReplaceModal;
window.closeReplaceModal = closeReplaceModal;
window.bindSelectAll = bindSelectAll;
