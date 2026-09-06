// Dashboard download-health warnings and explicit replacement review.
const healthProblems = new Map();
async function openHealthDetails(problem) {
  const requestId = Number(problem.request_id);
  if (!Number.isSafeInteger(requestId) || requestId <= 0 || !window.openRequestDetails) return;
  await window.openRequestDetails(requestId);
  const episode = String(problem.title || '').match(/S(\d{1,2})E(\d{1,3})/i);
  if (episode) {
    window.focusTvEpisode?.(requestId, Number(episode[1]), Number(episode[2]));
  } else {
    const season = String(problem.title || '').match(/S(\d{1,2})(?!\d)/i);
    const details = season && document.getElementById(`season-details-${requestId}-${Number(season[1])}`);
    if (details instanceof HTMLDetailsElement) details.open = true;
  }
}
function escapeHealthHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
function resolveHealthTarget(target = '[data-download-health]') {
  if (typeof target !== 'string') return target;
  return document.querySelector(target);
}
function statusLabel(problem) {
  if (problem.status === 'recovery_eligible') return 'Replacement review available';
  if (problem.status === 'missing') return 'Missing from qBittorrent';
  if (problem.status === 'ambiguous') return 'Title match needs review';
  return 'Download stalled';
}
function ageLabel(problem) {
  if (problem.age_hours === null || problem.age_hours === undefined) return 'Age unavailable';
  return `${Number(problem.age_hours).toFixed(1)}h without progress`;
}
function recoveryControl(problem) {
  if (!problem.replacement_available) {
    return '<span class="text-xs text-gray-500">Recovery is not currently eligible</span>';
  }
  return `<label class="sr-only" for="download-health-disposition-${problem.id}">Recovery disposition</label>
    <select id="download-health-disposition-${problem.id}" data-health-disposition="${problem.id}" class="form-input py-1.5 text-sm">
      <option value="cooldown">Temporarily skip this failed release</option>
      <option value="permanent_rejection">Permanently reject this release</option>
    </select>
    <button type="button" class="btn-primary btn-sm" data-health-recover="${problem.id}">Review replacement</button>`;
}
export function renderDownloadHealth(problems, target = '[data-download-health]') {
  const container = resolveHealthTarget(target);
  if (!container) return;
  const list = container.querySelector('[data-download-health-list]') || container;
  problems = problems.filter((problem) => Number.isSafeInteger(problem.id) && problem.id > 0);
  healthProblems.clear();
  problems.forEach((problem) => healthProblems.set(problem.id, problem));
  if (!problems.length) {
    list.innerHTML = '<p class="text-sm text-gray-500">No download health warnings.</p>';
    return;
  }
  list.innerHTML = problems
    .map(
      (
        problem,
      ) => `<article class="download-health-warning rounded border border-amber-700/50 p-3" data-health-attempt="${problem.id}">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 class="font-medium">${escapeHealthHtml(problem.title || 'Download')}</h3>
            <p class="text-sm text-amber-300">${escapeHealthHtml(statusLabel(problem))} · ${escapeHealthHtml(ageLabel(problem))}</p>
            <p class="text-xs text-gray-400">Progress ${problem.progress == null ? 'unknown' : `${(Number(problem.progress) * 100).toFixed(1)}%`} · state ${escapeHealthHtml(problem.qbit_state || 'unknown')}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            ${Number.isSafeInteger(problem.request_id) && Number(problem.request_id) > 0 ? `<button type="button" class="btn-ghost btn-sm" data-health-review="${problem.id}">Open request details</button>` : ''}
            ${recoveryControl(problem)}
          </div>
        </div>
        <p class="mt-2 text-xs text-gray-500">The original torrent and files remain in qBittorrent. No replacement is started automatically.</p>
      </article>`,
    )
    .join('');
  list.querySelectorAll('[data-health-review]').forEach((button) => {
    button.addEventListener('click', () => {
      const problem = healthProblems.get(Number(button.dataset.healthReview));
      if (problem) void openHealthDetails(problem);
    });
  });
  list.querySelectorAll('[data-health-recover]').forEach((button) => {
    button.addEventListener('click', () => {
      const attemptId = Number(button.dataset.healthRecover);
      if (Number.isInteger(attemptId)) confirmDownloadHealthRecovery(attemptId, button);
    });
  });
}
export async function loadDownloadHealth(target = '[data-download-health]') {
  const container = resolveHealthTarget(target);
  if (!container) return;
  try {
    const response = await fetch('/api/download-health', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`health request failed: ${response.status}`);
    const data = await response.json();
    renderDownloadHealth(data.problems || [], container);
  } catch (_error) {
    const list = container.querySelector('[data-download-health-list]') || container;
    list.innerHTML = '<p class="text-sm text-gray-500">Download health is temporarily unavailable.</p>';
  }
}
export async function confirmDownloadHealthRecovery(attemptId, button) {
  const disposition = document.querySelector(`[data-health-disposition="${attemptId}"]`)?.value || 'cooldown';
  const message =
    disposition === 'permanent_rejection'
      ? 'Permanently reject this exact failed release and review a replacement? The original torrent and files will remain in qBittorrent.'
      : 'Confirm replacement review for this exact failed release? The original torrent and files will remain in qBittorrent.';
  if (!window.confirm(message)) return;
  if (button) button.setAttribute('disabled', 'true');
  try {
    const response = await fetch(`/api/download-health/${attemptId}/recover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ confirm: true, disposition }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Recovery confirmation failed');
    if (window.showToast) window.showToast('Recovery confirmed. Review a replacement manually.');
    const problem = healthProblems.get(attemptId);
    await loadDownloadHealth();
    if (problem) await openHealthDetails(problem);
  } catch (error) {
    if (window.showToast) window.showToast(error instanceof Error ? error.message : 'Recovery confirmation failed');
    if (button) button.removeAttribute('disabled');
  }
}
let refreshTimer;
export function initDownloadHealth() {
  if (refreshTimer !== undefined) return;
  void loadDownloadHealth();
  refreshTimer = window.setInterval(() => {
    if (!document.hidden && !document.getElementById('content-downloading')?.classList.contains('hidden')) {
      void loadDownloadHealth();
    }
  }, 60000);
  window.addEventListener(
    'pagehide',
    () => {
      window.clearInterval(refreshTimer);
      refreshTimer = undefined;
    },
    { once: true },
  );
}
window.loadDownloadHealth = loadDownloadHealth;
window.renderDownloadHealth = renderDownloadHealth;
window.confirmDownloadHealthRecovery = confirmDownloadHealthRecovery;
window.initDownloadHealth = initDownloadHealth;
