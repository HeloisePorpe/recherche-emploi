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
  cdiOnly: false,
  hideFlagged: false,    // masquer les offres avec alertes de filtrage
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
  cdiOnly: document.getElementById('cdi-only'),
  hideFlagged: document.getElementById('hide-flagged'),
  myCriteria: document.getElementById('my-criteria'),
  criteriaSub: document.getElementById('criteria-sub'),
  criteriaStrict: document.getElementById('criteria-strict'),
  sourceFilters: document.getElementById('source-filters'),
  reset: document.getElementById('reset-filters'),
  toggleFilters: document.getElementById('toggle-filters'),
  filters: document.getElementById('filters'),
  toggleArchived: document.getElementById('toggle-archived'),
  archivedCount: document.getElementById('archived-count'),
  exportArchived: document.getElementById('export-archived'),
  archiveBlock: document.getElementById('archive-block'),
};

// Vue "archivées" (mode d'affichage, non persisté)
let showArchived = false;

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
  els.cdiOnly.checked = state.cdiOnly;
  els.hideFlagged.checked = state.hideFlagged;
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
  const archived = archivedIdSet();

  // Vue "archivées" : on n'affiche que les offres masquées (tri par note)
  if (showArchived) {
    return allJobs
      .filter((job) => archived.has(candidatureId(job)))
      .sort((a, b) => (b.score || 0) - (a.score || 0));
  }

  let jobs = allJobs.filter((job) => {
    // Offres archivées : exclues de la liste normale
    if (archived.has(candidatureId(job))) return false;
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
    // CDI uniquement
    if (state.cdiOnly && job.contract_type !== 'CDI') return false;
    // Masquer les offres signalées
    if (state.hideFlagged && Array.isArray(job.flags) && job.flags.length) return false;
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
  if (state.cdiOnly) n++;
  if (state.hideFlagged) n++;
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
  if (job.contract_type === 'CDI') tags.push('<span class="tag tag-cdi">CDI</span>');
  if (job.location) tags.push(`<span class="tag">${escapeHtml(job.location)}</span>`);
  if (salary) tags.push(`<span class="tag tag-salary">${escapeHtml(salary)}</span>`);
  if (telework) tags.push(`<span class="tag tag-telework">${escapeHtml(telework)}</span>`);
  if (commute) tags.push(`<span class="tag tag-commute">${escapeHtml(commute)}</span>`);

  // Badge "à vérifier" quand le filtre perso est actif et le trajet manque
  if (state.myCriteria && criteriaStatus(job) === 'unknown-commute') {
    tags.push('<span class="tag tag-warn">Trajet à vérifier</span>');
  }

  // Alertes de filtrage (issues du scraper)
  if (Array.isArray(job.flags)) {
    job.flags.forEach((f) => tags.push(`<span class="tag tag-flag">⚠ ${escapeHtml(f)}</span>`));
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

  const dateHtml = date
    ? `<div class="card-date">📅 Publiée le ${escapeHtml(date)}</div>`
    : '';

  const id = escapeHtml(candidatureId(job));
  let actions;
  if (showArchived) {
    actions = `<button class="restore-btn" type="button" data-unarchive="${id}">↩︎ Restaurer</button>`;
  } else {
    const tracked = isTracked(job);
    const followBtn = `<button class="follow-btn${tracked ? ' followed' : ''}" type="button"
        data-follow="${id}"${tracked ? ' disabled' : ''}>${tracked ? '✓ Suivie' : '➕ Suivre'}</button>`;
    const archiveBtn = `<button class="archive-btn" type="button" data-archive="${id}"
        title="Masquer cette offre (non pertinente)">✕ Pas pertinent</button>`;
    actions = followBtn + archiveBtn;
  }

  return `
    <article class="card">
      <div class="card-top">
        <h2 class="card-title">${titleHtml}</h2>
        <div class="score-badge ${scoreClass(score)}" title="Note : ${score}/10">
          <span class="num">${score}</span><span class="max">/10</span>
        </div>
      </div>
      ${companyHtml}
      ${dateHtml}
      <div class="card-meta">${tags.join('')}</div>
      ${reasons}
      <div class="card-actions">${actions}</div>
    </article>`;
}

// --- Rendu principal ---
function render() {
  const jobs = getFilteredJobs();
  const nbArchived = loadArchived().length;

  // Bloc archivées : libellé du bouton + état
  els.toggleArchived.textContent = showArchived
    ? '← Retour aux offres'
    : `🗄️ Voir les archivées (${nbArchived})`;
  els.archiveBlock.classList.toggle('viewing', showArchived);

  if (showArchived) {
    els.counter.textContent =
      `${jobs.length} offre${jobs.length > 1 ? 's' : ''} archivée${jobs.length > 1 ? 's' : ''}`;
  } else {
    const active = activeFilterCount();
    els.counter.textContent =
      `${jobs.length} offre${jobs.length > 1 ? 's' : ''} affichée${jobs.length > 1 ? 's' : ''}` +
      (jobs.length !== allJobs.length ? ` sur ${allJobs.length}` : '') +
      (active > 0 ? ` · ${active} filtre${active > 1 ? 's' : ''} actif${active > 1 ? 's' : ''}` : '');
  }

  if (jobs.length === 0) {
    els.cards.innerHTML = '';
    els.empty.textContent = showArchived
      ? 'Aucune offre archivée.'
      : 'Aucune offre ne correspond aux filtres.';
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
  els.cdiOnly.addEventListener('change', () => {
    state.cdiOnly = els.cdiOnly.checked;
    render();
  });
  els.hideFlagged.addEventListener('change', () => {
    state.hideFlagged = els.hideFlagged.checked;
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
    showArchived = false;
    syncControls();
    render();
  });
  els.toggleFilters.addEventListener('click', () => {
    const open = els.filters.classList.toggle('open');
    els.toggleFilters.setAttribute('aria-expanded', String(open));
  });
  // Actions sur les cartes (délégation : les cartes sont re-rendues)
  els.cards.addEventListener('click', (e) => {
    const follow = e.target.closest('[data-follow]');
    if (follow) {
      const job = allJobs.find((j) => candidatureId(j) === follow.getAttribute('data-follow'));
      if (job && addCandidature(job)) {
        follow.textContent = '✓ Suivie';
        follow.classList.add('followed');
        follow.disabled = true;
      }
      return;
    }
    const archive = e.target.closest('[data-archive]');
    if (archive) {
      const job = allJobs.find((j) => candidatureId(j) === archive.getAttribute('data-archive'));
      if (job) { archiveJob(job); render(); }
      return;
    }
    const unarchive = e.target.closest('[data-unarchive]');
    if (unarchive) {
      unarchiveJob(unarchive.getAttribute('data-unarchive'));
      render();
    }
  });

  // Vue archivées + export
  els.toggleArchived.addEventListener('click', () => {
    showArchived = !showArchived;
    render();
  });
  els.exportArchived.addEventListener('click', () => {
    const list = loadArchived();
    if (!list.length) { alert('Aucune offre archivée à exporter.'); return; }
    const blob = new Blob([JSON.stringify(list, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'offres-archivees.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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
