'use strict';

// Stockage partagé des candidatures suivies (dashboard + page Kanban).
// État conservé dans le navigateur (localStorage) — propre à chaque appareil.
const CANDIDATURES_KEY = 'recherche-emploi-candidatures';

// Identifiant stable d'une offre (le lien, sinon titre + entreprise).
function candidatureId(job) {
  return job.link || ((job.title || '') + '|' + (job.company || ''));
}

function loadCandidatures() {
  try {
    const raw = localStorage.getItem(CANDIDATURES_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch (_) {
    return [];
  }
}

function saveCandidatures(list) {
  try {
    localStorage.setItem(CANDIDATURES_KEY, JSON.stringify(list));
  } catch (_) { /* localStorage indisponible */ }
}

function isTracked(job) {
  const id = candidatureId(job);
  return loadCandidatures().some((c) => c.id === id);
}

// ── Offres archivées (jugées non pertinentes) ───────────────────────────────
const ARCHIVED_KEY = 'recherche-emploi-archivees';

function loadArchived() {
  try {
    const raw = localStorage.getItem(ARCHIVED_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch (_) {
    return [];
  }
}

function saveArchived(list) {
  try {
    localStorage.setItem(ARCHIVED_KEY, JSON.stringify(list));
  } catch (_) { /* localStorage indisponible */ }
}

function archivedIdSet() {
  return new Set(loadArchived().map((a) => a.id));
}

function isArchived(job) {
  return archivedIdSet().has(candidatureId(job));
}

// Archive une offre (conserve ses données pour analyse ultérieure).
function archiveJob(job) {
  const id = candidatureId(job);
  const list = loadArchived();
  if (list.some((a) => a.id === id)) return false;
  list.push({
    id,
    title: job.title || '',
    company: job.company || '',
    location: job.location || '',
    link: job.link || '',
    source: job.source || '',
    score: job.score,
    telework_days: job.telework_days,
    commute_minutes: job.commute_minutes,
    contract_type: job.contract_type || null,
    published: job.published || '',
    archivedAt: Date.now(),
  });
  saveArchived(list);
  return true;
}

function unarchiveJob(id) {
  saveArchived(loadArchived().filter((a) => a.id !== id));
}

// Ajoute une offre au suivi. Renvoie false si déjà présente.
function addCandidature(job) {
  const id = candidatureId(job);
  const list = loadCandidatures();
  if (list.some((c) => c.id === id)) return false;
  list.push({
    id,
    title: job.title || 'Sans titre',
    company: job.company || '',
    location: job.location || '',
    link: job.link || '',
    status: 'a_postuler',
    notes: '',
    addedAt: Date.now(),
  });
  saveCandidatures(list);
  return true;
}
