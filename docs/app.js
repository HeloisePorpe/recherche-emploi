'use strict';

// --- État global ---
let allJobs = [];
const STORAGE_KEY = 'recherche-emploi-filtres-v2';
const MAX_COMMUTE = 75;  // minutes ; au-delà, on masque sauf 100 % télétravail

const defaultState = () => ({
  search: '',
  sort: 'score',
  recency: 0,      // jours ; 0 = toutes les dates
  minScore: 0,
  minSalary: 0,    // € annuels bruts ; 0 = indifférent
  salaryOnly: false,
  teleworkOnly: false,
  myCriteria: true,      // filtre trajet + télétravail — activé par défaut
  criteriaStrict: false, // masquer aussi les offres à l'info manquante
  sources: new Set(), // sources cochées ; vide = toutes
});

let state = defaultState();

// --- Références DOM ---
const els = {
  cards: document.getElementById('cards'),
  counter: document.getElementById('counter'),
  empty: document.getElementById('empty-state'),
  search: document.getElementById('search'),
  sort: document.getElementById('sort'),
  recency: document.getElementById('recency'),
  minScore: document.getElementById('min-score'),
  minScoreValue: document.getElementById('min-score-value'),
  minSalary: document.getElementById('min-salary'),
  minSalaryValue: document.getElementById('min-salary-value'),
  salaryOnly: document.getElementById('salary-only'),
  teleworkOnly: document.getElementById('telework-only'),
  myCriteria: document.getElementById('my-criteria'),
  criteriaSub: document.getElementById('criteria-sub'),
  criteriaStrict: document.getElementById('criteria-strict'),
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

// Normalise un libellé de salaire hétérogène en fourchette annuelle brute (€).
// Gère : "55000-70000 €", "Annuel de 50000 à 55000 Euros",
//        "Mensuel de 3000 à 3300 Euros sur 12 mois", "492-1823 €"...
// Renvoie { min, max, mid } ou null si non exploitable.
function parseSalaryAnnual(job) {
  const raw = job.salary_extracted || job.salary_raw;
  if (!raw) return null;

  let s = String(raw).toLowerCase().replace(/ /g, ' ');

  // Nombre de mois de versement (défaut 12), puis on retire ces mentions
  const monthsMatch = s.match(/sur\s*([\d.]+)\s*mois/);
  const monthCount = monthsMatch ? Math.round(parseFloat(monthsMatch[1])) || 12 : 12;
  s = s.replace(/sur\s*[\d.]+\s*mois/g, ' ').replace(/\d+\s*mois/g, ' ');

  // Extraction des montants
  const nums = (s.match(/\d[\d\s.,]*\d|\d/g) || [])
    .map((n) => parseFloat(n.replace(/\s/g, '').replace(',', '.')))
    .filter((n) => !isNaN(n) && n > 0);
  if (!nums.length) return null;

  const rawMin = Math.min(...nums);
  const rawMax = Math.max(...nums);

  // Annuel si mot-clé explicite, ou si les montants sont clairement annuels
  const isAnnual =
    /annuel|année|\bk€|\bk\b|par an|\/an|à l'année/.test(s) ||
    (!/mensuel|par mois|\/mois/.test(s) && rawMax >= 10000);

  const mult = isAnnual ? 1 : monthCount;
  const min = Math.round(rawMin * mult);
  const max = Math.round(rawMax * mult);
  return { min, max, mid: Math.round((min + max) / 2) };
}

function formatEuro(n) {
  return new Intl.NumberFormat('fr-FR').format(n) + ' €';
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

// --- Persistance des filtres ---
function saveState() {
  try {
    const data = { ...state, sources: [...state.sources] };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (_) { /* localStorage indisponible : on ignore */ }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    state = {
      ...defaultState(),
      ...data,
      sources: new Set(Array.isArray(data.sources) ? data.sources : []),
    };
  } catch (_) { /* données corrompues : on garde les défauts */ }
}

// Reflète l'état (chargé) dans les contrôles du formulaire
function syncControls() {
  els.search.value = state.search;
  els.sort.value = state.sort;
  els.recency.value = String(state.recency);
  els.minScore.value = state.minScore;
  els.minScoreValue.textContent = state.minScore;
  els.minSalary.value = state.minSalary;
  els.minSalaryValue.textContent = state.minSalary > 0 ? formatEuro(state.minSalary) : 'Indifférent';
  els.salaryOnly.checked = state.salaryOnly;
  els.teleworkOnly.checked = state.teleworkOnly;
  els.myCriteria.checked = state.myCriteria;
  els.criteriaStrict.checked = state.criteriaStrict;
  els.criteriaSub.hidden = !state.myCriteria;
  els.sourceFilters.querySelectorAll('.source-cb').forEach((cb) => {
    cb.checked = state.sources.has(cb.value);
  });
}

// Évalue les critères perso trajet + télétravail.
// Renvoie : 'ok' | 'no' | 'unknown-commute'
//   - 100 % télétravail (5) en France : toujours OK (peu importe le trajet).
//   - Sinon : OK si le trajet est ≤ 75 min ; masqué au-delà.
//   - Trajet inconnu : renvoyé à part (affiché en lenient, masqué en strict).
function criteriaStatus(job) {
  const tw = job.telework_days;         // nombre ou null
  const commute = job.commute_minutes;  // nombre ou null
  const inFrance = job.in_france !== false;

  if (tw === 5) return inFrance ? 'ok' : 'no';   // 100 % télétravail
  if (commute == null) return 'unknown-commute'; // trajet non calculé
  if (commute <= MAX_COMMUTE) return 'ok';        // ≤ 75 min
  return 'no';                                     // > 75 min et pas full-remote
}

// --- Filtrage & tri ---
function getFilteredJobs() {
  const q = state.search.trim().toLowerCase();
  const now = Date.now();
  const recencyMs = state.recency > 0 ? state.recency * 86400000 : 0;

  let jobs = allJobs.filter((job) => {
    // Recherche texte (titre + entreprise + lieu + description)
    if (q) {
      const hay = (
        (job.title || '') + ' ' +
        (job.company || '') + ' ' +
        (job.location || '') + ' ' +
        (job.description || '')
      ).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    // Ancienneté
    if (recencyMs) {
      const pub = dateValue(job.published);
      if (!pub || now - pub > recencyMs) return false;
    }
    // Note minimale
    if ((job.score || 0) < state.minScore) return false;
    // Salaire affiché uniquement
    if (state.salaryOnly && !job._sal) return false;
    // Salaire minimum (l'offre passe si le haut de fourchette atteint le seuil)
    if (state.minSalary > 0) {
      if (!job._sal || job._sal.max < state.minSalary) return false;
    }
    // Télétravail
    if (state.teleworkOnly && !(job.telework_days > 0)) return false;
    // Critères perso trajet + télétravail
    if (state.myCriteria) {
      const st = criteriaStatus(job);
      if (st === 'no') return false;
      if (state.criteriaStrict && st !== 'ok') return false;
    }
    // Sources
    if (state.sources.size > 0 && !state.sources.has(job.source)) return false;
    return true;
  });

  if (state.sort === 'date') {
    jobs.sort((a, b) => dateValue(b.published) - dateValue(a.published));
  } else if (state.sort === 'salary') {
    jobs.sort((a, b) => {
      const av = a._sal ? a._sal.mid : -1;
      const bv = b._sal ? b._sal.mid : -1;
      return bv - av;
    });
  } else {
    jobs.sort((a, b) => (b.score || 0) - (a.score || 0));
  }
  return jobs;
}

// Nombre de filtres actifs (hors recherche texte et tri)
function activeFilterCount() {
  let n = 0;
  if (state.recency > 0) n++;
  if (state.minScore > 0) n++;
  if (state.minSalary > 0) n++;
  if (state.salaryOnly) n++;
  if (state.teleworkOnly) n++;
  if (state.myCriteria) n++;
  if (state.sources.size > 0) n++;
  if (state.search.trim()) n++;
  return n;
}

// --- Rendu d'une carte ---
function renderCard(job) {
  const score = job.score != null ? job.score : 0;
  const salary = getSalary(job);
  const tw = job.telework_days;
  const telework = tw === 5 ? '100 % télétravail' : (tw > 0 ? `Télétravail ${tw}j/sem` : '');
  const commute = job.commute_minutes != null ? `🚆 ${job.commute_minutes} min` : '';
  const date = formatDate(job.published);

  const tags = [];
  if (job.source) tags.push(`<span class="tag tag-source">${escapeHtml(job.source)}</span>`);
  if (job.location) tags.push(`<span class="tag">${escapeHtml(job.location)}</span>`);
  if (salary) tags.push(`<span class="tag tag-salary">${escapeHtml(salary)}</span>`);
  if (telework) tags.push(`<span class="tag tag-telework">${escapeHtml(telework)}</span>`);
  if (commute) tags.push(`<span class="tag tag-commute">${escapeHtml(commute)}</span>`);
  if (date) tags.push(`<span class="tag">${escapeHtml(date)}</span>`);

  // Badge "à vérifier" quand le filtre perso est actif et le trajet manque
  if (state.myCriteria && criteriaStatus(job) === 'unknown-commute') {
    tags.push('<span class="tag tag-warn">Trajet à vérifier</span>');
  }

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

  const tracked = isTracked(job);
  const followBtn = `<button class="follow-btn${tracked ? ' followed' : ''}" type="button"
      data-follow="${escapeHtml(candidatureId(job))}"${tracked ? ' disabled' : ''}>
      ${tracked ? '✓ Suivie' : '➕ Suivre'}</button>`;

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
      <div class="card-actions">${followBtn}</div>
    </article>`;
}

// --- Rendu principal ---
function render() {
  const jobs = getFilteredJobs();
  const active = activeFilterCount();
  els.counter.textContent =
    `${jobs.length} offre${jobs.length > 1 ? 's' : ''} affichée${jobs.length > 1 ? 's' : ''}` +
    (jobs.length !== allJobs.length ? ` sur ${allJobs.length}` : '') +
    (active > 0 ? ` · ${active} filtre${active > 1 ? 's' : ''} actif${active > 1 ? 's' : ''}` : '');

  if (jobs.length === 0) {
    els.cards.innerHTML = '';
    els.empty.hidden = false;
  } else {
    els.empty.hidden = true;
    els.cards.innerHTML = jobs.map(renderCard).join('');
  }
  saveState();
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
  els.recency.addEventListener('change', () => {
    state.recency = Number(els.recency.value);
    render();
  });
  els.minScore.addEventListener('input', () => {
    state.minScore = Number(els.minScore.value);
    els.minScoreValue.textContent = state.minScore;
    render();
  });
  els.minSalary.addEventListener('input', () => {
    state.minSalary = Number(els.minSalary.value);
    els.minSalaryValue.textContent = state.minSalary > 0 ? formatEuro(state.minSalary) : 'Indifférent';
    render();
  });
  els.salaryOnly.addEventListener('change', () => {
    state.salaryOnly = els.salaryOnly.checked;
    render();
  });
  els.teleworkOnly.addEventListener('change', () => {
    state.teleworkOnly = els.teleworkOnly.checked;
    render();
  });
  els.myCriteria.addEventListener('change', () => {
    state.myCriteria = els.myCriteria.checked;
    els.criteriaSub.hidden = !state.myCriteria;
    render();
  });
  els.criteriaStrict.addEventListener('change', () => {
    state.criteriaStrict = els.criteriaStrict.checked;
    render();
  });
  els.reset.addEventListener('click', () => {
    state = defaultState();
    syncControls();
    render();
  });
  els.toggleFilters.addEventListener('click', () => {
    const open = els.filters.classList.toggle('open');
    els.toggleFilters.setAttribute('aria-expanded', String(open));
  });
  // Bouton "Suivre" (délégation : les cartes sont re-rendues)
  els.cards.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-follow]');
    if (!btn) return;
    const id = btn.getAttribute('data-follow');
    const job = allJobs.find((j) => candidatureId(j) === id);
    if (job && addCandidature(job)) {
      btn.textContent = '✓ Suivie';
      btn.classList.add('followed');
      btn.disabled = true;
    }
  });
}

// --- Initialisation ---
async function init() {
  try {
    const res = await fetch('jobs_output.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allJobs = await res.json();
    if (!Array.isArray(allJobs)) throw new Error('Format inattendu');
    // Pré-calcul du salaire annuel normalisé pour filtre & tri
    allJobs.forEach((job) => { job._sal = parseSalaryAnnual(job); });
    loadState();
    buildSourceFilters();
    syncControls();
    bindEvents();
    render();
  } catch (err) {
    els.counter.textContent = 'Erreur de chargement';
    els.cards.innerHTML = `<p class="empty-state">Impossible de charger les offres (${escapeHtml(err.message)}).<br>Lancez un serveur local (python -m http.server) pour tester.</p>`;
  }
}

init();
