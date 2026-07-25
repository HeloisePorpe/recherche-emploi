'use strict';

// ============================================================================
// Stockage des candidatures + offres archivées.
//
// Les candidatures sont désormais **synchronisées** via un fichier commité
// (docs/candidatures.json) :
//   - Lecture : tous tes appareils + le robot lisent ce fichier -> synchro
//     multi-appareils, sans configuration.
//   - Écriture depuis le navigateur : via l'API GitHub, avec un jeton personnel
//     (fine-grained PAT) que tu colles une fois par appareil. Sans jeton, tes
//     modifications restent locales (localStorage) mais la lecture reste synchro.
//   - Le robot (GitHub Actions) écrit dans ce même fichier via son propre jeton.
//
// Le localStorage sert de cache local rapide et de file d'attente hors-ligne.
// Les offres archivées restent locales pour l'instant.
// ============================================================================

const GH_OWNER = 'HeloisePorpe';
const GH_REPO = 'recherche-emploi';
const GH_BRANCH = 'master';
const GH_PATH = 'docs/candidatures.json';
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

// Récupère le fichier partagé et le fusionne dans le cache local.
async function syncPull() {
  _emitSync('pulling');
  try {
    // Lecture publique via GitHub Pages (pas de jeton nécessaire).
    const res = await fetch('candidatures.json?_=' + nowTs(), { cache: 'no-store' });
    let remote = [];
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data)) remote = data;
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

function archivedIdSet() {
  return new Set(loadArchived().map((a) => a.id));
}

function isArchived(job) {
  return archivedIdSet().has(candidatureId(job));
}

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
    archivedAt: nowTs(),
  });
  saveArchived(list);
  return true;
}

function unarchiveJob(id) {
  saveArchived(loadArchived().filter((a) => a.id !== id));
}
