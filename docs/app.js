'use strict';

// --- État global ---
let allJobs = [];
const state = {
  search: '',
  sort: 'score',
  minScore: 0,
  teleworkOnly: false,
  sources: new Set(), // sources cochées ; vide = toutes
};

// --- Références DOM ---
const els = {
  cards: document.getElementById('cards'),
  counter: document.getElementById('counter'),
  empty: document.getElementById('empty-state'),
  search: document.getElementById('search'),
  sort: document.getElementById('sort'),
  minScore: document.getElementById('min-score'),
  minScoreValue: document.getElementById('min-score-value'),
  teleworkOnly: document.getElementById('telework-only'),
  sourceFilters: document.getElementById('source-filters'),
  reset: document.getElementById('reset-filters'),
  toggleFilters: document.getElementById('toggle-filters'),
  filters: document.getElementById('filters'),
};

// --- Utilitaires ---
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function scoreClass(score) {
  if (score >= 7) return 'score-green';
  if (score >= 5) return 'score-orange';
  return 'score-red';
}

function getSalary(job) {
  const s = job.salary_extracted || job.salary_raw;
  if (!s) return '';
  return String(s).trim();
}

function formatDate(published) {
  if (!published) return '';
  const d = new Date(published);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
}

function dateValue(published) {
  if (!published) return 0;
  const t = new Date(published).getTime();
  return isNaN(t) ? 0 : t;
}

// --- Filtrage & tri ---
function getFilteredJobs() {
  const q = state.search.trim().toLowerCase();

  let jobs = allJobs.filter((job) => {
    // Recherche texte (titre + entreprise)
    if (q) {
      const hay = ((job.title || '') + ' ' + (job.company || '')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    // Note minimale
    if ((job.score || 0) < state.minScore) return false;
    // Télétravail
    if (state.teleworkOnly && !(job.telework_days > 0)) return false;
    // Sources
    if (state.sources.size > 0 && !state.sources.has(job.source)) return false;
    return true;
  });

  if (state.sort === 'date') {
    jobs.sort((a, b) => dateValue(b.published) - dateValue(a.published));
  } else {
    jobs.sort((a, b) => (b.score || 0) - (a.score || 0));
  }
  return jobs;
}

// --- Rendu d'une carte ---
function renderCard(job) {
  const score = job.score != null ? job.score : 0;
  const salary = getSalary(job);
  const telework = job.telework_days > 0 ? `Télétravail ${job.telework_days}j/sem` : '';
  const date = formatDate(job.published);

  const tags = [];
  if (job.source) tags.push(`<span class="tag tag-source">${escapeHtml(job.source)}</span>`);
  if (job.location) tags.push(`<span class="tag">${escapeHtml(job.location)}</span>`);
  if (salary) tags.push(`<span class="tag tag-salary">${escapeHtml(salary)}</span>`);
  if (telework) tags.push(`<span class="tag tag-telework">${escapeHtml(telework)}</span>`);
  if (date) tags.push(`<span class="tag">${escapeHtml(date)}</span>`);

  const reasons = Array.isArray(job.score_reasons) && job.score_reasons.length
    ? `<ul class="reasons">${job.score_reasons
        .map((r) => `<li>${escapeHtml(r)}</li>`)
        .join('')}</ul>`
    : '';

  const titleHtml = job.link
    ? `<a href="${escapeHtml(job.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(job.title || 'Sans titre')}</a>`
    : escapeHtml(job.title || 'Sans titre');

  const companyHtml = job.company
    ? `<div class="card-company">${escapeHtml(job.company)}</div>`
    : '';

  return `
    <article class="card">
      <div class="card-top">
        <h2 class="card-title">${titleHtml}</h2>
        <div class="score-badge ${scoreClass(score)}" title="Note : ${score}/10">
          <span class="num">${score}</span><span class="max">/10</span>
        </div>
      </div>
      ${companyHtml}
      <div class="card-meta">${tags.join('')}</div>
      ${reasons}
    </article>`;
}

// --- Rendu principal ---
function render() {
  const jobs = getFilteredJobs();
  els.counter.textContent =
    `${jobs.length} offre${jobs.length > 1 ? 's' : ''} affichée${jobs.length > 1 ? 's' : ''}` +
    (jobs.length !== allJobs.length ? ` sur ${allJobs.length}` : '');

  if (jobs.length === 0) {
    els.cards.innerHTML = '';
    els.empty.hidden = false;
  } else {
    els.empty.hidden = true;
    els.cards.innerHTML = jobs.map(renderCard).join('');
  }
}

// --- Construction des filtres de source ---
function buildSourceFilters() {
  const sources = [...new Set(allJobs.map((j) => j.source).filter(Boolean))].sort();
  els.sourceFilters.innerHTML = sources
    .map(
      (s) => `
      <label class="checkbox-line">
        <input type="checkbox" class="source-cb" value="${escapeHtml(s)}" />
        <span>${escapeHtml(s)}</span>
      </label>`
    )
    .join('');

  els.sourceFilters.querySelectorAll('.source-cb').forEach((cb) => {
    cb.addEventListener('change', () => {
      if (cb.checked) state.sources.add(cb.value);
      else state.sources.delete(cb.value);
      render();
    });
  });
}

// --- Écouteurs ---
function bindEvents() {
  els.search.addEventListener('input', () => {
    state.search = els.search.value;
    render();
  });
  els.sort.addEventListener('change', () => {
    state.sort = els.sort.value;
    render();
  });
  els.minScore.addEventListener('input', () => {
    state.minScore = Number(els.minScore.value);
    els.minScoreValue.textContent = state.minScore;
    render();
  });
  els.teleworkOnly.addEventListener('change', () => {
    state.teleworkOnly = els.teleworkOnly.checked;
    render();
  });
  els.reset.addEventListener('click', () => {
    state.search = '';
    state.sort = 'score';
    state.minScore = 0;
    state.teleworkOnly = false;
    state.sources.clear();
    els.search.value = '';
    els.sort.value = 'score';
    els.minScore.value = 0;
    els.minScoreValue.textContent = '0';
    els.teleworkOnly.checked = false;
    els.sourceFilters.querySelectorAll('.source-cb').forEach((cb) => (cb.checked = false));
    render();
  });
  els.toggleFilters.addEventListener('click', () => {
    const open = els.filters.classList.toggle('open');
    els.toggleFilters.setAttribute('aria-expanded', String(open));
  });
}

// --- Initialisation ---
async function init() {
  try {
    const res = await fetch('jobs_output.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allJobs = await res.json();
    if (!Array.isArray(allJobs)) throw new Error('Format inattendu');
    buildSourceFilters();
    bindEvents();
    render();
  } catch (err) {
    els.counter.textContent = 'Erreur de chargement';
    els.cards.innerHTML = `<p class="empty-state">Impossible de charger les offres (${escapeHtml(err.message)}).<br>Lancez un serveur local (python -m http.server) pour tester.</p>`;
  }
}

init();
