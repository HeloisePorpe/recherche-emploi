'use strict';

// ============================================================================
// Stockage des candidatures + offres archivées.
//
// Le suivi de candidatures est **privé** : il vit dans un dépôt GitHub PRIVÉ
// dédié (recherche-emploi-candidatures), jamais dans le dépôt public.
//   - Un dépôt privé n'étant pas lisible publiquement, lecture ET écriture
//     passent par l'API GitHub avec un jeton personnel (fine-grained PAT) collé
//     une fois par appareil.
//   - AVEC jeton : synchro complète multi-appareils + le robot.
//   - SANS jeton : suivi local sur l'appareil (localStorage), rien n'est envoyé.
//   - Le robot (GitHub Actions) écrira dans ce même fichier via un secret.
//
// Le localStorage sert de cache local rapide. Les offres archivées sont locales.
// ============================================================================

const GH_OWNER = 'HeloisePorpe';
const GH_REPO = 'recherche-emploi-candidatures'; // dépôt PRIVÉ dédié
const GH_BRANCH = 'main';
const GH_PATH = 'candidatures.json';
const GH_TOKEN_KEY = 'recherche-emploi-gh-token';

const CANDIDATURES_KEY = 'recherche-emploi-candidatures';
const ARCHIVED_KEY = 'recherche-emploi-archivees';

// Identifiant stable d'une offre (le lien, sinon titre + entreprise).
function candidatureId(job) {
  return job.link || ((job.title || '') + '|' + (job.company || ''));
}

function nowTs() { return Date.now(); }

// ── Jeton GitHub (par appareil, jamais commité) ─────────────────────────────
function getGhToken() {
  try { return localStorage.getItem(GH_TOKEN_KEY) || ''; } catch (_) { return ''; }
}
function setGhToken(token) {
  try {
    if (token) localStorage.setItem(GH_TOKEN_KEY, token.trim());
    else localStorage.removeItem(GH_TOKEN_KEY);
  } catch (_) { /* ignore */ }
}
function hasGhToken() { return !!getGhToken(); }

// ── Cache local des candidatures ────────────────────────────────────────────
function loadCandidatures() {
  try {
    const raw = localStorage.getItem(CANDIDATURES_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) return [];
    // Compat : garantit un updatedAt pour la fusion.
    arr.forEach((c) => { if (!c.updatedAt) c.updatedAt = c.addedAt || nowTs(); });
    return arr;
  } catch (_) {
    return [];
  }
}

function saveCandidatures(list) {
  try {
    localStorage.setItem(CANDIDATURES_KEY, JSON.stringify(list));
  } catch (_) { /* localStorage indisponible */ }
}

// Candidatures visibles (hors supprimées).
function activeCandidatures() {
  return loadCandidatures().filter((c) => !c.deleted);
}

function isTracked(job) {
  const id = candidatureId(job);
  return activeCandidatures().some((c) => c.id === id);
}

// ── Rapprochement offre <-> candidature (anti double-candidature) ────────────
// Reconnaît la même offre même diffusée sur plusieurs plateformes / avec des
// variantes d'entreprise ("Aravati" vs "Aravati France").
function _normTxt(x) {
  return (x || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\(.*?\)|\bh\/?f\b|\bf\/?h\b|\bm\/?f\b|\bcdi\b|\bcdd\b/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ').trim();
}
function _titleKey(t) { return _normTxt(t).slice(0, 40); }
function _companyLoose(a, b) {
  a = _normTxt(a); b = _normTxt(b);
  if (!a || !b) return true;
  return a.indexOf(b) >= 0 || b.indexOf(a) >= 0;
}

let _candCache = null;
function refreshCandidatureCache() { _candCache = activeCandidatures(); return _candCache; }

// Candidature correspondant à une offre (titre proche + entreprise proche), sinon null.
function candidatureForJob(job) {
  const list = _candCache || activeCandidatures();
  const jt = _titleKey(job.title || '');
  if (!jt) return null;
  const jc = job.company || '';
  return list.find((c) => _titleKey(c.title || '') === jt && _companyLoose(jc, c.company || '')) || null;
}

// L'offre a-t-elle déjà fait l'objet d'une candidature (envoyée / entretien /
// réponse) ? Le simple « suivi » (à postuler) ne compte pas comme postulée.
function jobApplied(job) {
  const c = candidatureForJob(job);
  if (!c) return false;
  const auto = c.auto && c.auto.status;
  return auto === 'en_attente' || auto === 'positif' || auto === 'negatif'
      || c.status === 'postule' || c.status === 'entretien' || c.status === 'reponse';
}

// ── Fusion de deux listes (par id, dernière écriture gagnante) ──────────────
// Champs utilisateur (status, notes, deleted...) : version au updatedAt le plus
// récent. Champ `auto` (écrit par le robot) : la version la plus récente.
function mergeCandidatures(a, b) {
  const byId = new Map();
  const consider = (c) => {
    if (!c || !c.id) return;
    const prev = byId.get(c.id);
    if (!prev) { byId.set(c.id, { ...c }); return; }
    const merged = { ...prev };
    const cNewer = (c.updatedAt || 0) >= (prev.updatedAt || 0);
    const userSrc = cNewer ? c : prev;
    ['status', 'notes', 'deleted', 'updatedAt', 'title', 'company',
     'location', 'link', 'addedAt'].forEach((k) => {
      if (userSrc[k] !== undefined) merged[k] = userSrc[k];
    });
    const aAuto = prev.auto, bAuto = c.auto;
    if (aAuto && bAuto) {
      merged.auto = (bAuto.date || 0) >= (aAuto.date || 0) ? bAuto : aAuto;
    } else {
      merged.auto = bAuto || aAuto || undefined;
    }
    byId.set(c.id, merged);
  };
  (a || []).forEach(consider);
  (b || []).forEach(consider);
  return [...byId.values()];
}

// ── Synchronisation ─────────────────────────────────────────────────────────
let _syncTimer = null;
let _syncState = 'idle'; // idle | pulling | pushing | error | offline
const _syncListeners = [];

function onSyncChange(fn) { _syncListeners.push(fn); }
function _emitSync(state, detail) {
  _syncState = state;
  _syncListeners.forEach((fn) => { try { fn(state, detail); } catch (_) {} });
}
function syncState() { return _syncState; }

// UTF-8 <-> base64 (pour l'API GitHub).
function _b64encode(str) { return btoa(unescape(encodeURIComponent(str))); }
function _b64decode(b64) { return decodeURIComponent(escape(atob(b64))); }

// Récupère le fichier privé (via l'API GitHub + jeton) et le fusionne au cache.
// Sans jeton : pas de synchro (données locales uniquement).
async function syncPull() {
  const token = getGhToken();
  if (!token) { _emitSync('idle'); return loadCandidatures(); }
  _emitSync('pulling');
  try {
    const api = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/contents/${GH_PATH}`
      + `?ref=${GH_BRANCH}&_=${nowTs()}`;
    const res = await fetch(api, {
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      cache: 'no-store',
    });
    let remote = [];
    if (res.ok) {
      const meta = await res.json();
      try { remote = JSON.parse(_b64decode(meta.content || '')); } catch (_) { remote = []; }
      if (!Array.isArray(remote)) remote = [];
    } else if (res.status !== 404) {
      throw new Error('HTTP ' + res.status);
    }
    const merged = mergeCandidatures(loadCandidatures(), remote);
    saveCandidatures(merged);
    _emitSync('idle');
    return merged;
  } catch (err) {
    _emitSync('offline', err.message);
    return loadCandidatures();
  }
}

// Pousse le cache local vers le fichier commité (si un jeton est configuré).
// Gère les conflits de SHA en refusionnant puis en réessayant une fois.
async function syncPush(retry = 1) {
  const token = getGhToken();
  if (!token) return false; // pas de jeton -> écriture locale seulement
  _emitSync('pushing');
  const api = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/contents/${GH_PATH}`;
  const headers = {
    Authorization: 'Bearer ' + token,
    Accept: 'application/vnd.github+json',
  };
  try {
    let sha = null, remote = [];
    const getRes = await fetch(`${api}?ref=${GH_BRANCH}&_=${nowTs()}`, { headers, cache: 'no-store' });
    if (getRes.ok) {
      const meta = await getRes.json();
      sha = meta.sha;
      try { remote = JSON.parse(_b64decode(meta.content || '')); } catch (_) { remote = []; }
      if (!Array.isArray(remote)) remote = [];
    } else if (getRes.status !== 404) {
      throw new Error('GET ' + getRes.status);
    }
    const merged = mergeCandidatures(loadCandidatures(), remote);
    saveCandidatures(merged);
    const body = {
      message: 'MAJ candidatures (dashboard)',
      content: _b64encode(JSON.stringify(merged, null, 2)),
      branch: GH_BRANCH,
    };
    if (sha) body.sha = sha;
    const putRes = await fetch(api, { method: 'PUT', headers, body: JSON.stringify(body) });
    if (putRes.status === 409 && retry > 0) {
      return syncPush(retry - 1); // conflit de SHA : refusion + nouvel essai
    }
    if (!putRes.ok) throw new Error('PUT ' + putRes.status);
    _emitSync('idle');
    return true;
  } catch (err) {
    _emitSync('error', err.message);
    return false;
  }
}

// Pousse avec un léger délai (regroupe les modifications rapprochées).
function scheduleSync() {
  if (!hasGhToken()) return;
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(() => { syncPush(); }, 1200);
}

// ── CRUD candidatures ───────────────────────────────────────────────────────
// Ajoute une offre au suivi. Renvoie false si déjà présente (et active).
function addCandidature(job) {
  const id = candidatureId(job);
  const list = loadCandidatures();
  const existing = list.find((c) => c.id === id);
  if (existing && !existing.deleted) return false;
  if (existing) {
    existing.deleted = false;
    existing.status = 'a_postuler';
    existing.updatedAt = nowTs();
  } else {
    list.push({
      id,
      title: job.title || 'Sans titre',
      company: job.company || '',
      location: job.location || '',
      link: job.link || '',
      status: 'a_postuler',
      notes: '',
      addedAt: nowTs(),
      updatedAt: nowTs(),
    });
  }
  saveCandidatures(list);
  scheduleSync();
  return true;
}

function updateCandidature(id, changes) {
  const list = loadCandidatures();
  const c = list.find((x) => x.id === id);
  if (!c) return false;
  Object.assign(c, changes, { updatedAt: nowTs() });
  saveCandidatures(list);
  scheduleSync();
  return true;
}

function removeCandidature(id) {
  // Suppression douce (tombstone) pour que la suppression se synchronise.
  const list = loadCandidatures();
  const c = list.find((x) => x.id === id);
  if (c) {
    c.deleted = true;
    c.updatedAt = nowTs();
    saveCandidatures(list);
    scheduleSync();
  }
}

// ── Offres archivées (jugées non pertinentes) — locales ─────────────────────
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

// Clé d'archivage STABLE (indépendante du lien) : titre normalisé + entreprise
// normalisée. Le lien Adzuna change d'un scan à l'autre — s'en servir comme clé
// faisait « revenir » les offres écartées. On matche donc par titre + entreprise,
// avec la même logique souple que les candidatures (variantes H/F, "Aravati" vs
// "Aravati France", même offre diffusée sur plusieurs plateformes).
function archiveKey(job) {
  return _titleKey(job.title || '') + '|' + _normTxt(job.company || '');
}

// Une offre correspond-elle à une entrée archivée ? Clé stable, sinon titre
// proche + entreprise souple, sinon (compat) ancien id/lien exact.
function _matchesArchived(job, list) {
  const jk = archiveKey(job);
  const jt = _titleKey(job.title || '');
  const jc = job.company || '';
  const jid = candidatureId(job);
  return list.some((a) => {
    if ((a.key || archiveKey(a)) === jk) return true;
    if (jt && _titleKey(a.title || '') === jt && _companyLoose(jc, a.company || '')) return true;
    if (a.id && (a.id === job.link || a.id === jid)) return true;  // entrées héritées
    return false;
  });
}

// Prédicat prêt à l'emploi : charge la liste une fois puis teste chaque offre.
function makeArchivedMatcher() {
  const list = loadArchived();
  return (job) => _matchesArchived(job, list);
}

function isArchived(job) {
  return _matchesArchived(job, loadArchived());
}

function archiveJob(job) {
  const key = archiveKey(job);
  const list = loadArchived();
  if (list.some((a) => (a.key || archiveKey(a)) === key)) return false;
  list.push({
    key,
    id: candidatureId(job),  // conservé pour compat / restauration
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
    archivedAt: nowTs(),
  });
  saveArchived(list);
  return true;
}

// Restauration par clé stable (data-unarchive = archiveKey de l'offre).
function unarchiveJob(key) {
  saveArchived(loadArchived().filter((a) => (a.key || archiveKey(a)) !== key));
}
