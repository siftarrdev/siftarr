// @ts-nocheck
// Dashboard Modals Module - Modal dialogs and toast notifications
// ===============================================================

import { refreshCurrentTabContent } from './core.js';

function showToast(message) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className =
    'bg-surface-800 text-white border border-gray-700/60 rounded-xl px-5 py-3 text-sm shadow-2xl pointer-events-auto transition-opacity duration-300 opacity-0';
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove('opacity-0');
    toast.classList.add('opacity-100');
  });
  setTimeout(() => {
    toast.classList.remove('opacity-100');
    toast.classList.add('opacity-0');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function showSearchProgressToast(title, message, items) {
  const container = document.getElementById('toast-container');
  if (!container) return { update: () => {}, dismiss: () => {} };

  const el = document.createElement('div');
  el.className =
    'search-progress-toast bg-surface-800 border border-gray-700/70 rounded-2xl shadow-2xl p-4 pointer-events-auto w-full max-w-sm transition-opacity duration-300 opacity-0';
  el.innerHTML =
    '<div class="flex items-start justify-between gap-3 mb-3">' +
    '<div class="min-w-0">' +
    '<h3 class="text-sm font-semibold text-white">' +
    window.escapeHtml(title) +
    '</h3>' +
    '<p class="text-xs text-gray-500 mt-0.5">Working…</p>' +
    '</div>' +
    '</div>' +
    '<div class="mb-3"><div class="w-full h-2 rounded-full bg-surface-700 overflow-hidden">' +
    '<div class="search-progress-bar h-2 rounded-full bg-brand-500 transition-all duration-700" style="width: 0%"></div>' +
    '</div></div>' +
    '<p class="search-progress-status text-sm text-gray-400">' +
    window.escapeHtml(message) +
    '</p>' +
    '<div class="search-progress-items-wrap hidden mt-3">' +
    '<div class="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Searching for</div>' +
    '<ul class="search-progress-items space-y-1 text-gray-300 max-h-28 overflow-y-auto"></ul>' +
    '</div>';

  // Populate items list
  const itemsWrap = el.querySelector('.search-progress-items-wrap');
  const itemsList = el.querySelector('.search-progress-items');
  if (itemsList && itemsWrap && items.length) {
    itemsWrap.classList.remove('hidden');
    items.slice(0, 5).forEach((item) => {
      const li = document.createElement('li');
      li.className = 'rounded-lg bg-surface-850 px-2.5 py-1 text-xs text-gray-300 truncate';
      li.textContent = item;
      itemsList.appendChild(li);
    });
    if (items.length > 5) {
      const li = document.createElement('li');
      li.className = 'px-2.5 py-0.5 text-[11px] text-gray-500';
      li.textContent = '+' + (items.length - 5) + ' more';
      itemsList.appendChild(li);
    }
  }

  container.appendChild(el);

  // Animate bar
  const bar = el.querySelector('.search-progress-bar');
  if (bar) {
    requestAnimationFrame(() => {
      bar.style.width = '15%';
    });
    setTimeout(() => {
      if (bar) bar.style.width = '90%';
    }, 150);
  }

  // Fade in
  requestAnimationFrame(() => {
    el.classList.remove('opacity-0');
    el.classList.add('opacity-100');
  });

  return {
    update(newMessage, failed = false) {
      const statusEl = el.querySelector('.search-progress-status');
      const barEl = el.querySelector('.search-progress-bar');
      const textEl = el.querySelector('p.text-xs.text-gray-500');
      if (statusEl) {
        statusEl.textContent = newMessage;
        statusEl.classList.remove('text-gray-400', 'text-emerald-400', 'text-red-400');
        statusEl.classList.add(failed ? 'text-red-400' : 'text-emerald-400');
      }
      if (barEl) {
        barEl.style.width = '100%';
        barEl.classList.remove('bg-brand-500', 'bg-emerald-500', 'bg-red-500');
        barEl.classList.add(failed ? 'bg-red-500' : 'bg-emerald-500');
      }
      if (textEl) textEl.textContent = failed ? 'Failed' : 'Complete';
    },
    dismiss(delayMs = 3000) {
      setTimeout(() => {
        el.classList.remove('opacity-100');
        el.classList.add('opacity-0');
        setTimeout(() => el.remove(), 300);
      }, delayMs);
    },
  };
}

function setSearchActionLoading(trigger, message = 'Searching...') {
  if (!trigger) return;
  trigger.disabled = true;
  trigger.dataset.originalText = trigger.textContent;
  trigger.innerHTML =
    '<span class="inline-flex items-center gap-1.5">' +
    '<svg class="animate-spin h-3.5 w-3.5" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' +
    '<span>' +
    window.escapeHtml(message) +
    '</span>' +
    '</span>';
  trigger.setAttribute('aria-busy', 'true');
}

function disableSearchControls(scope) {
  if (!scope) return;
  scope.querySelectorAll('[data-search-action="true"], [data-search-submit-control="true"]').forEach((control) => {
    control.disabled = true;
  });
}

function isVisibleBulkCheckbox(checkbox) {
  return !!(checkbox && (checkbox.offsetParent || checkbox.getClientRects().length));
}

function getDedupedCheckedBulkCheckboxes(form, selector = 'input[name="request_ids"]:checked') {
  if (!form) return [];
  const source = Array.from(form.querySelectorAll(selector)).filter(isVisibleBulkCheckbox);
  const seen = new Set();
  return source.filter((checkbox) => {
    if (!checkbox.value || seen.has(checkbox.value)) return false;
    seen.add(checkbox.value);
    return true;
  });
}

function getRequestTitleFromRow(row) {
  if (!row) return null;
  const titleCell = row.querySelector('td:nth-child(2)');
  const cardTitle = row.querySelector('[data-card-title]');
  const title = titleCell ? titleCell.textContent.trim() : cardTitle ? cardTitle.textContent.trim() : '';
  return title || row.dataset.title || null;
}

function collectBulkSearchTitles(form, searchAll = false) {
  if (!form) return [];
  const selector = searchAll ? 'tbody tr' : 'input[name="request_ids"]:checked, input[name="torrent_ids"]:checked';
  const nodes = searchAll
    ? Array.from(form.querySelectorAll(selector)).filter((node) => node.offsetParent || node.getClientRects().length)
    : getDedupedCheckedBulkCheckboxes(form, selector);
  return nodes
    .map((node) => getRequestTitleFromRow(searchAll ? node : node.closest('tr') || node.closest('[data-request-id]')))
    .filter(Boolean);
}

async function submitSearchRequest(action, body, redirectTo) {
  const response = await fetch(action, {
    method: 'POST',
    body: body,
    headers: { Accept: 'text/html' },
  });
  if (!response.ok) throw new Error('Search request failed (' + response.status + ')');
  return response.url || redirectTo || window.location.href;
}

function showBulkSearchStatus(form, searchAll = false) {
  if (!form) return;
  const panel = form.querySelector('[data-bulk-search-status="true"]');
  if (!panel) return;
  const selectedCount = getDedupedCheckedBulkCheckboxes(
    form,
    'input[name="request_ids"]:checked, input[name="torrent_ids"]:checked',
  ).length;
  const title = panel.querySelector('[data-bulk-search-status-title="true"]');
  const message = panel.querySelector('[data-bulk-search-status-message="true"]');
  if (title) title.textContent = searchAll ? 'Searching all pending requests' : 'Searching selected requests/torrents';
  if (message) {
    message.textContent = searchAll
      ? 'Searching all pending items. You will be redirected when the search starts.'
      : selectedCount > 0
        ? 'Searching ' +
          selectedCount +
          ' selected item' +
          (selectedCount === 1 ? '' : 's') +
          '. You will be redirected when the search starts.'
        : 'Preparing selected search. You will be redirected when the search starts.';
  }
  panel.classList.remove('hidden');
}

async function postToAction(action, redirectTo, trigger = null, scope = null) {
  const row = scope || (trigger ? trigger.closest('tr') || trigger.closest('[data-request-id]') : null);
  const title = getRequestTitleFromRow(row);
  if (trigger && trigger.dataset.searchAction === 'true') {
    setSearchActionLoading(trigger);
    disableSearchControls(row);
    const match = action.match(/\/requests\/(\d+)\/search/);
    const requestId = match ? parseInt(match[1], 10) : null;
    if (requestId) {
      function resetTrigger() {
        trigger.disabled = false;
        trigger.innerHTML = trigger.dataset.originalText || trigger.textContent;
        trigger.removeAttribute('aria-busy');
        if (row) {
          row
            .querySelectorAll('[data-search-action="true"], [data-search-submit-control="true"]')
            .forEach((control) => {
              control.disabled = false;
            });
        }
      }
      const isTv = String(row?.dataset.type || '').toLowerCase() === 'tv';
      if (isTv) {
        try {
          const searchChoice = await window.confirmLargeTvSearch(requestId);
          if (searchChoice === 'none') {
            resetTrigger();
            return;
          }
          if (searchChoice === 'packs') {
            // Pack searches render into the TV details accordion, so open it
            // directly on that scope before starting the selected search.
            window.detailsAutoSearchStarted[requestId] = true;
            await window.openRequestDetails(requestId, null, { focusTvScope: 'season_packs' });
            window.isTvScopeSearchRunning = true;
            try {
              await window.searchAllSeasonPacks(requestId);
            } finally {
              window.isTvScopeSearchRunning = false;
              resetTrigger();
            }
            return;
          }
        } catch (error) {
          resetTrigger();
          window.showToast('Could not prepare TV search: ' + error.message);
          return;
        }
      }
      const onComplete = function (data) {
        window.updateRequestRow(requestId, data.result);
        resetTrigger();
        refreshCurrentTabContent();
      };
      const onError = function () {
        resetTrigger();
      };
      if (isTv) {
        window.startTvSearchProgress(
          '/requests/' + requestId + '/search/stream',
          'Search for new: ' + title,
          onComplete,
          onError,
          '/requests/' + requestId + '/search/cancel',
        );
      } else {
        window.startSearchProgress(requestId, title, onComplete, onError);
      }
    }
    return;
  }
  const body = new FormData();
  body.append('redirect_to', redirectTo);
  submitSearchRequest(action, body, redirectTo)
    .then((url) => {
      setTimeout(() => window.location.assign(url), 800);
    })
    .catch(() => {});
}

async function handleBulkDenyAction(event, form) {
  event.preventDefault();
  const submitter = event.submitter;
  const formData = new FormData(form);
  // Ensure action is set
  if (submitter && submitter.value) {
    formData.set('action', submitter.value);
  }

  // Collect selected request IDs from checkboxes
  const checkboxes = getDedupedCheckedBulkCheckboxes(form);
  const ids = checkboxes.map((cb) => cb.value).filter(Boolean);
  if (ids.length === 0) {
    window.showToast('No requests selected.');
    return false;
  }
  formData.delete('request_ids');
  ids.forEach((id) => formData.append('request_ids', id));

  if (submitter) submitter.disabled = true;
  try {
    const response = await fetch('/requests/bulk', {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: formData,
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || `Server error: ${response.status}`);
    }
    // Uncheck all selected checkboxes
    checkboxes.forEach((cb) => (cb.checked = false));
    window.showToast('Request(s) denied');
    await refreshCurrentTabContent();
  } catch (err) {
    window.showToast('Error: ' + err.message);
  } finally {
    if (submitter) submitter.disabled = false;
  }
  return false;
}

function handleBulkRequestActionSubmit(event, form) {
  const submitter = event.submitter;
  if (!submitter) return true;

  // Handle deny actions via fetch
  if (submitter.value === 'deny') {
    return handleBulkDenyAction(event, form);
  }

  // Search actions
  if (submitter.dataset.searchSubmitControl !== 'true') return true;
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
  setSearchActionLoading(submitter, searchAll ? 'Searching all...' : 'Searching...');
  disableSearchControls(form);

  var ids = [];
  if (!searchAll) {
    var checkboxes = getDedupedCheckedBulkCheckboxes(form);
    ids = checkboxes
      .map(function (cb) {
        return parseInt(cb.value, 10);
      })
      .filter(Boolean);
  }

  function resetSubmitter() {
    form.dataset.searchSubmitting = '';
    var originalText = submitter.dataset.originalText || submitter.value;
    submitter.disabled = false;
    submitter.innerHTML = '';
    submitter.textContent = originalText;
    submitter.removeAttribute('aria-busy');
  }

  if (searchAll || ids.length > 0) {
    window.startBulkSearchProgress(
      ids,
      titles,
      function (data) {
        if (data.results) {
          data.results.forEach(function (r) {
            window.updateRequestRow(r.request_id, r);
          });
        }
        resetSubmitter();
        // Refresh stats cards after search completes
        refreshCurrentTabContent();
      },
      function () {
        resetSubmitter();
      },
      { searchAllPending: searchAll },
    );
  } else {
    resetSubmitter();
  }
  return false;
}

let _denyRequestId = null;
let _denyRedirectTo = null;

function openDenyModal(requestId, redirectTo) {
  _denyRequestId = requestId;
  _denyRedirectTo = redirectTo || '/';
  const modal = document.getElementById('deny-modal');
  const reason = document.getElementById('deny-reason');
  if (reason) reason.value = '';
  if (modal) modal.classList.remove('hidden');
}

async function submitDenyRequest() {
  if (!_denyRequestId) return;
  const btn = document.getElementById('deny-submit-btn');
  if (btn) btn.disabled = true;
  const reason = document.getElementById('deny-reason');

  try {
    const formData = new FormData();
    formData.append('redirect_to', _denyRedirectTo);
    if (reason && reason.value) formData.append('reason', reason.value);

    const response = await fetch('/requests/' + _denyRequestId + '/deny', {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: formData,
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new Error(errorData?.detail || `Server error: ${response.status}`);
    }
    closeDenyModal();
    window.showToast('Request denied');
    await refreshCurrentTabContent();
  } catch (err) {
    window.showToast('Error: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function closeDenyModal() {
  const modal = document.getElementById('deny-modal');
  if (modal) modal.classList.add('hidden');
  _denyRequestId = null;
  _denyRedirectTo = null;
}

function bindDenyModalHandlers() {
  const submitBtn = document.getElementById('deny-submit-btn');
  if (submitBtn && !submitBtn.getAttribute('onclick') && submitBtn.dataset.denyHandlerBound !== 'true') {
    submitBtn.addEventListener('click', (event) => {
      event.preventDefault();
      window.submitDenyRequest();
    });
    submitBtn.dataset.denyHandlerBound = 'true';
  }
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
  if (toggle.dataset.selectAllBound === 'true') return;
  toggle.dataset.selectAllBound = 'true';
  toggle.addEventListener('change', (event) => {
    document.querySelectorAll(checkboxSelector).forEach((checkbox) => {
      if (!isVisibleBulkCheckbox(checkbox)) return;
      checkbox.checked = event.target.checked;
    });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bindDenyModalHandlers);
} else {
  bindDenyModalHandlers();
}

export {
  showToast,
  showSearchProgressToast,
  postToAction,
  setSearchActionLoading,
  showBulkSearchStatus,
  handleBulkRequestActionSubmit,
  openDenyModal,
  submitDenyRequest,
  closeDenyModal,
  bindDenyModalHandlers,
  openReplaceModal,
  closeReplaceModal,
  bindSelectAll,
};

// Temporary compatibility facade for HTML onclick handlers and unconverted modules.
Object.assign(window, {
  showToast,
  showSearchProgressToast,
  postToAction,
  setSearchActionLoading,
  showBulkSearchStatus,
  handleBulkRequestActionSubmit,
  openDenyModal,
  submitDenyRequest,
  closeDenyModal,
  bindDenyModalHandlers,
  openReplaceModal,
  closeReplaceModal,
  bindSelectAll,
});
