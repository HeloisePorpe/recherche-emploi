'use strict';

// Colonnes du Kanban (ordre = progression de la candidature)
const COLUMNS = [
  { key: 'a_postuler', label: 'À postuler' },
  { key: 'postule', label: 'Postulé' },
  { key: 'entretien', label: 'Entretien' },
  { key: 'reponse', label: 'Réponse' },
];
const VALID_STATUS = new Set(COLUMNS.map((c) => c.key));

// Statut détecté par le robot (email) -> libellé + classe.
const AUTO_META = {
  postule: { label: 'Candidature envoyée', cls: 'auto-sent' },
  en_attente: { label: 'En attente de réponse', cls: 'auto-wait' },
  positif: { label: 'Réponse positive', cls: 'auto-pos' },
  negatif: { label: 'Réponse négative', cls: 'auto-neg' },
};

const board = document.getElementById('board');

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function updateStatus(id, status) {
  if (!VALID_STATUS.has(status)) return;
  updateCandidature(id, { status });
}

function updateNotes(id, notes) {
  updateCandidature(id, { notes });
}

function cardHtml(c) {
  const status = VALID_STATUS.has(c.status) ? c.status : 'a_postuler';
  const link = c.link
    ? `<a href="${escapeHtml(c.link)}" target="_blank" rel="noopener noreferrer" class="kc-link">Voir l'offre ↗</a>`
    : '';
  const company = c.company ? `<div class="kc-company">${escapeHtml(c.company)}</div>` : '';
  const loc = c.location ? `<div class="kc-loc">${escapeHtml(c.location)}</div>` : '';
  const auto = c.auto && AUTO_META[c.auto.status]
    ? `<div class="kc-auto ${AUTO_META[c.auto.status].cls}" title="Détecté par email">
         🤖 ${AUTO_META[c.auto.status].label}${c.auto.reason ? ' — ' + escapeHtml(c.auto.reason) : ''}
       </div>`
    : '';
  // Contrôle de statut (tactile) : déplacer la carte sans glisser-déposer,
  // indispensable sur mobile où le drag ne fonctionne pas.
  const moveCtrl = `
    <div class="kc-move">
      <label class="kc-move-label" for="mv-${escapeHtml(c.id)}">Statut</label>
      <select class="kc-move-sel" id="mv-${escapeHtml(c.id)}" data-move="${escapeHtml(c.id)}">
        ${COLUMNS.map((col) => `<option value="${col.key}"${col.key === status ? ' selected' : ''}>${escapeHtml(col.label)}</option>`).join('')}
      </select>
    </div>`;
  return `
    <article class="kanban-card" draggable="true" data-id="${escapeHtml(c.id)}" data-status="${status}">
      <div class="kc-top">
        <h3 class="kc-title">${escapeHtml(c.title)}</h3>
        <button class="kc-del" type="button" data-del="${escapeHtml(c.id)}" title="Retirer" aria-label="Retirer">×</button>
      </div>
      ${company}
      ${loc}
      ${auto}
      ${link}
      ${moveCtrl}
      <textarea class="kc-notes" data-notes="${escapeHtml(c.id)}" placeholder="Notes (contact, date, relance...)"
        rows="2">${escapeHtml(c.notes || '')}</textarea>
    </article>`;
}

function render() {
  const list = activeCandidatures();
  board.innerHTML = COLUMNS.map((col) => {
    const cards = list.filter((c) => (VALID_STATUS.has(c.status) ? c.status : 'a_postuler') === col.key);
    return `
      <section class="column" data-col="${col.key}">
        <header class="col-head">
          <span class="col-title">${col.label}</span>
          <span class="col-count">${cards.length}</span>
        </header>
        <div class="col-body" data-drop="${col.key}">
          ${cards.map(cardHtml).join('') || '<p class="col-empty">Vide</p>'}
        </div>
      </section>`;
  }).join('');
}

// --- Glisser-déposer ---
let dragId = null;

board.addEventListener('dragstart', (e) => {
  const card = e.target.closest('.kanban-card');
  if (!card) return;
  dragId = card.getAttribute('data-id');
  card.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
});
board.addEventListener('dragend', (e) => {
  const card = e.target.closest('.kanban-card');
  if (card) card.classList.remove('dragging');
  dragId = null;
});
board.addEventListener('dragover', (e) => {
  const body = e.target.closest('.col-body');
  if (!body) return;
  e.preventDefault();
  body.classList.add('drop-hover');
});
board.addEventListener('dragleave', (e) => {
  const body = e.target.closest('.col-body');
  if (body) body.classList.remove('drop-hover');
});
board.addEventListener('drop', (e) => {
  const body = e.target.closest('.col-body');
  if (!body || !dragId) return;
  e.preventDefault();
  body.classList.remove('drop-hover');
  updateStatus(dragId, body.getAttribute('data-drop'));
  render();
});

// --- Déplacement par menu (tactile / mobile) ---
board.addEventListener('change', (e) => {
  const sel = e.target.closest('[data-move]');
  if (!sel) return;
  updateStatus(sel.getAttribute('data-move'), sel.value);
  render();
});

// --- Notes & suppression ---
board.addEventListener('input', (e) => {
  const ta = e.target.closest('[data-notes]');
  if (ta) updateNotes(ta.getAttribute('data-notes'), ta.value);
});
board.addEventListener('click', (e) => {
  const del = e.target.closest('[data-del]');
  if (!del) return;
  removeCandidature(del.getAttribute('data-del'));
  render();
});

// --- Ajout manuel ---
document.getElementById('add-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const title = document.getElementById('add-title').value.trim();
  if (!title) return;
  addCandidature({
    title,
    company: document.getElementById('add-company').value.trim(),
    link: document.getElementById('add-link').value.trim(),
  });
  e.target.reset();
  render();
});

// --- Barre de synchronisation ---
const SYNC_LABELS = {
  idle: hasGhToken() ? '🔒 Synchronisé (privé)' : '📴 Local — ajoute un jeton pour synchroniser',
  pulling: '↻ Lecture…',
  pushing: '↥ Enregistrement…',
  error: '⚠ Erreur de synchro (voir jeton)',
  offline: '⚠ Hors ligne (modifs locales)',
};
const syncStatusEl = document.getElementById('sync-status');
function refreshSyncStatus(state) {
  const s = state || syncState();
  syncStatusEl.textContent = SYNC_LABELS[s] || '';
  syncStatusEl.className = 'sync-status sync-' + s;
}
onSyncChange((state) => {
  refreshSyncStatus(state);
  if (state === 'idle') render(); // re-render après une lecture/écriture réussie
});

// --- Réglages du jeton ---
const tokenPanel = document.getElementById('sync-panel');
const tokenInput = document.getElementById('gh-token');
const tokenState = document.getElementById('gh-token-state');
function refreshTokenState() {
  tokenState.textContent = hasGhToken()
    ? 'Jeton enregistré sur cet appareil : tu peux modifier le suivi (il sera synchronisé).'
    : 'Aucun jeton : lecture synchronisée seulement (tes modifications restent sur cet appareil).';
}
document.getElementById('sync-toggle').addEventListener('click', () => {
  tokenPanel.hidden = !tokenPanel.hidden;
  refreshTokenState();
});
document.getElementById('gh-token-save').addEventListener('click', () => {
  setGhToken(tokenInput.value);
  tokenInput.value = '';
  refreshTokenState();
  refreshSyncStatus();
  syncPush();
});
document.getElementById('gh-token-clear').addEventListener('click', () => {
  setGhToken('');
  refreshTokenState();
  refreshSyncStatus();
});

// --- Initialisation : afficher le cache immédiatement, puis synchroniser ---
render();
refreshSyncStatus();
syncPull().then(render);
