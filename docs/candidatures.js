'use strict';

// Colonnes du Kanban (ordre = progression de la candidature)
const COLUMNS = [
  { key: 'a_postuler', label: 'À postuler' },
  { key: 'postule', label: 'Postulé' },
  { key: 'entretien', label: 'Entretien' },
  { key: 'reponse', label: 'Réponse' },
];
const VALID_STATUS = new Set(COLUMNS.map((c) => c.key));

const board = document.getElementById('board');

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function updateStatus(id, status) {
  if (!VALID_STATUS.has(status)) return;
  const list = loadCandidatures();
  const c = list.find((x) => x.id === id);
  if (c && c.status !== status) {
    c.status = status;
    saveCandidatures(list);
  }
}

function updateNotes(id, notes) {
  const list = loadCandidatures();
  const c = list.find((x) => x.id === id);
  if (c) { c.notes = notes; saveCandidatures(list); }
}

function removeCandidature(id) {
  saveCandidatures(loadCandidatures().filter((c) => c.id !== id));
}

function cardHtml(c) {
  const status = VALID_STATUS.has(c.status) ? c.status : 'a_postuler';
  const link = c.link
    ? `<a href="${escapeHtml(c.link)}" target="_blank" rel="noopener noreferrer" class="kc-link">Voir l'offre ↗</a>`
    : '';
  const company = c.company ? `<div class="kc-company">${escapeHtml(c.company)}</div>` : '';
  const loc = c.location ? `<div class="kc-loc">${escapeHtml(c.location)}</div>` : '';
  return `
    <article class="kanban-card" draggable="true" data-id="${escapeHtml(c.id)}" data-status="${status}">
      <div class="kc-top">
        <h3 class="kc-title">${escapeHtml(c.title)}</h3>
        <button class="kc-del" type="button" data-del="${escapeHtml(c.id)}" title="Retirer" aria-label="Retirer">×</button>
      </div>
      ${company}
      ${loc}
      ${link}
      <textarea class="kc-notes" data-notes="${escapeHtml(c.id)}" placeholder="Notes (contact, date, relance...)"
        rows="2">${escapeHtml(c.notes || '')}</textarea>
    </article>`;
}

function render() {
  const list = loadCandidatures();
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

render();
