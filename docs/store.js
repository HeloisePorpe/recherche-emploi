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
