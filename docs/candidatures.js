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

// Critères cochables (fiche offre). `key` stocké dans candidature.criteria.
const CRITERIA = [
  { key: 'commute_ok', label: '🚇 Trajet OK' },
  { key: 'telework_ok', label: '🏠 Télétravail ≥ 2j' },
  { key: 'salary_ok', label: '💶 Salaire cible' },
  { key: 'sector_ok', label: '🏦 Secteur +' },
  { key: 'cdi', label: '📄 CDI' },
  { key: 'favorite', label: '❤️ Coup de cœur' },
];

const board = document.getElementById('board');
// Mémorise les fiches « Détails » ouvertes pour ne pas les refermer au re-rendu.
const openDetails = new Set();

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

// Enregistre un champ de la fiche (adresse / trajet / télétravail / salaire /
// contrat). Pas de re-rendu : on préserve le focus pendant la saisie.
function saveField(el) {
  const id = el.getAttribute('data-id');
  const field = el.getAttribute('data-f');
  let val = el.value;
  if (field === 'commuteMin' || field === 'teleworkDays' || field === 'salary') {
    val = val === '' ? null : Number(val);
    if (val !== null && Number.isNaN(val)) val = null;
  }
  updateCandidature(id, { [field]: val });
}

function saveCrit(el) {
  const id = el.getAttribute('data-id');
  const key = el.getAttribute('data-crit');
  const c = activeCandidatures().find((x) => x.id === id);
  const criteria = Object.assign({}, c && c.criteria);
  criteria[key] = el.checked;
  updateCandidature(id, { criteria });
}

// Ligne récapitulative (vue repliée) des infos structurées renseignées.
function summaryInner(c) {
  const bits = [];
  if (c.commuteMin != null && c.commuteMin !== '') bits.push(`🚇 ${c.commuteMin} min`);
  if (c.teleworkDays != null && c.teleworkDays !== '') bits.push(`🏠 ${c.teleworkDays} j/sem`);
  if (c.salary) bits.push(`💶 ${Number(c.salary).toLocaleString('fr-FR')} €`);
  if (c.contractType) bits.push(`📄 ${escapeHtml(c.contractType)}`);
  return bits.join(' · ');
}

function detailsHtml(c) {
  const id = escapeHtml(c.id);
  const twOpts = ['', 0, 1, 2, 3, 4, 5].map((n) =>
    `<option value="${n}"${String(c.teleworkDays ?? '') === String(n) ? ' selected' : ''}>${n === '' ? '—' : n}</option>`).join('');
  const ctOpts = ['', 'CDI', 'CDD', 'Alternance', 'Freelance', 'Stage', 'Autre'].map((v) =>
    `<option value="${v}"${(c.contractType || '') === v ? ' selected' : ''}>${v || '—'}</option>`).join('');
  const crit = c.criteria || {};
  const checks = CRITERIA.map((cr) =>
    `<label class="kc-check"><input type="checkbox" data-crit="${cr.key}" data-id="${id}"${crit[cr.key] ? ' checked' : ''} /><span>${cr.label}</span></label>`).join('');
  return `
    <details class="kc-details"${openDetails.has(c.id) ? ' open' : ''}>
      <summary>✏️ Détails &amp; critères</summary>
      <div class="kc-form">
        <label class="kc-f kc-f-full"><span>📍 Adresse</span>
          <input type="text" data-f="address" data-id="${id}" value="${escapeHtml(c.address || '')}" placeholder="Ville, arrondissement…" /></label>
        <div class="kc-grid">
          <label class="kc-f"><span>🚇 Trajet (min)</span>
            <input type="number" inputmode="numeric" min="0" max="300" data-f="commuteMin" data-id="${id}" value="${c.commuteMin ?? ''}" placeholder="—" /></label>
          <label class="kc-f"><span>🏠 Télétravail (j/sem)</span>
            <select data-f="teleworkDays" data-id="${id}">${twOpts}</select></label>
          <label class="kc-f"><span>💶 Salaire brut/an (€)</span>
            <input type="number" inputmode="numeric" min="0" step="1000" data-f="salary" data-id="${id}" value="${c.salary ?? ''}" placeholder="ex. 45000" /></label>
          <label class="kc-f"><span>📄 Contrat</span>
            <select data-f="contractType" data-id="${id}">${ctOpts}</select></label>
        </div>
        <div class="kc-criteria">
          <span class="kc-crit-title">Critères</span>
          <div class="kc-checks">${checks}</div>
        </div>
        <label class="kc-f kc-f-full"><span>📝 Notes</span>
          <textarea class="kc-notes" data-notes="${id}" placeholder="Contact, date, relance, ressenti…" rows="3">${escapeHtml(c.notes || '')}</textarea></label>
      </div>
    </details>`;
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
  const summary = summaryInner(c);
  const summaryHtml = summary ? `<div class="kc-summary">${summary}</div>` : '';
  const notesPreview = (c.notes || '').trim() && !openDetails.has(c.id)
    ? `<div class="kc-notes-preview">📝 ${escapeHtml((c.notes || '').trim().slice(0, 90))}${(c.notes || '').trim().length > 90 ? '…' : ''}</div>`
    : '';
  return `
    <article class="kanban-card" draggable="true" data-id="${escapeHtml(c.id)}" data-status="${status}">
      <div class="kc-top">
        <h3 class="kc-title">${escapeHtml(c.title)}</h3>
        <button class="kc-del" type="button" data-del="${escapeHtml(c.id)}" title="Retirer" aria-label="Retirer">×</button>
      </div>
      ${company}
      ${loc}
      ${summaryHtml}
      ${auto}
      ${link}
      ${moveCtrl}
      ${detailsHtml(c)}
      ${notesPreview}
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

// --- Déplacement par menu (tactile) + champs de la fiche ---
board.addEventListener('change', (e) => {
  const sel = e.target.closest('[data-move]');
  if (sel) { updateStatus(sel.getAttribute('data-move'), sel.value); render(); return; }
  const f = e.target.closest('[data-f]');
  if (f) { saveField(f); return; }
  const cr = e.target.closest('[data-crit]');
  if (cr) { saveCrit(cr); return; }
});

// --- Notes & suppression ---
board.addEventListener('input', (e) => {
  const ta = e.target.closest('[data-notes]');
  if (ta) updateNotes(ta.getAttribute('data-notes'), ta.value);
});

// Mémorise l'ouverture/fermeture des fiches « Détails » (toggle ne bulle pas).
board.addEventListener('toggle', (e) => {
  const d = e.target;
  if (!d || !d.classList || !d.classList.contains('kc-details')) return;
  const card = d.closest('.kanban-card');
  const id = card && card.getAttribute('data-id');
  if (!id) return;
  if (d.open) openDetails.add(id); else openDetails.delete(id);
}, true);
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
  // Re-render après une lecture/écriture réussie, SAUF si l'utilisatrice est en
  // train de saisir dans une carte (ne pas lui voler le focus ni replier sa fiche).
  if (state === 'idle') {
    const ae = document.activeElement;
    const editing = ae && ae.closest && ae.closest('.kanban-card');
    if (!editing) render();
  }
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
