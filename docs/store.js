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
const GH_ARCHIVE_PATH = 'archivees.json'; // offres écartées, synchronisées comme les candidatures
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
    // Retire les mentions de genre (H/F, F/H, M/F et variantes H/F/D, F/H/X…) et
    // les mentions de contrat, pour rapprocher les mêmes offres écrites différemment.
    .replace(/\(.*?\)|\bh[/\-]?f(?:[/\-][dwx])?\b|\bf[/\-]?h(?:[/\-][dwx])?\b|\bm[/\-]?f(?:[/\-][dwx])?\b|\bcdi\b|\bcdd\b/g, ' ')
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

// Deux titres désignent le même poste : identiques, l'un préfixe de l'autre, ou
// l'un est un sous-ensemble de mots de l'autre (≥ 3 mots communs). Tolère les
// variantes réelles entre plateformes : « (Senior) Campaign Marketing Manager »
// vs « Senior Campaign Marketing Manager - Paris H/F ». Utilisé quand l'entreprise
// concorde déjà (rapprochement souple), donc on peut être généreux sur le titre.
function _titleSimilar(a, b) {
  const na = _normTxt(a), nb = _normTxt(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const short = na.length <= nb.length ? na : nb;
  const long = na.length <= nb.length ? nb : na;
  if (short.length >= 12 && long.startsWith(short)) return true;
  const wa = na.split(' ').filter(Boolean);
  const wb = nb.split(' ').filter(Boolean);
  const small = wa.length <= wb.length ? wa : wb;
  const bigSet = new Set(wa.length <= wb.length ? wb : wa);
  return small.length >= 3 && small.every((w) => bigSet.has(w));
}

// Candidature correspondant à une offre (entreprise proche + titre proche), sinon null.
function candidatureForJob(job) {
  const list = _candCache || activeCandidatures();
  const jt = job.title || '';
  if (!_normTxt(jt)) return null;
  const jc = job.company || '';
  return list.find((c) => _companyLoose(jc, c.company || '') && _titleSimilar(jt, c.title || '')) || null;
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
     'location', 'link', 'addedAt',
     // Champs de détail éditables sur la carte (fiche offre)
     'address', 'commuteMin', 'teleworkDays', 'salary', 'contractType',
     'criteria', 'appliedDate', 'platform', 'offerText'].forEach((k) => {
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
    // Pré-remplit la fiche à partir des données de l'offre (modifiables ensuite).
    const num = (v) => (typeof v === 'number' && !Number.isNaN(v) ? v : undefined);
    let salary;
    const sraw = job.salary_raw || job.salary_extracted || '';
    const sm = String(sraw).replace(/\s/g, '').match(/(\d{4,6})/);
    if (sm) salary = Number(sm[1]);
    list.push({
      id,
      title: job.title || 'Sans titre',
      company: job.company || '',
      location: job.location || '',
      link: job.link || '',
      status: 'a_postuler',
      notes: '',
      address: job.location || '',
      commuteMin: num(job.commute_minutes),
      teleworkDays: num(job.telework_days),
      salary,
      contractType: job.contract_type || '',
      offerText: job.description || '',
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

// ── Offres archivées (jugées non pertinentes) — synchronisées ───────────────
// Comme les candidatures, les archives vivent dans le dépôt privé (archivees.json)
// dès qu'un jeton est configuré, et en cache localStorage. Elles survivent donc à
// un vidage de cache et se partagent entre appareils. Sans jeton : local seulement.
// La désarchivage passe par un tombstone (deleted) pour que le retrait se propage.
function loadArchivedRaw() {
  try {
    const raw = localStorage.getItem(ARCHIVED_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) return [];
    // Compat : garantit une clé stable et un updatedAt pour la fusion.
    arr.forEach((a) => {
      if (!a.key) a.key = archiveKey(a);
      if (!a.updatedAt) a.updatedAt = a.archivedAt || nowTs();
    });
    return arr;
  } catch (_) {
    return [];
  }
}

// Archives actives (hors tombstones) — pour l'affichage et le matching.
function loadArchived() {
  return loadArchivedRaw().filter((a) => !a.deleted);
}

function saveArchived(list) {
  try {
    localStorage.setItem(ARCHIVED_KEY, JSON.stringify(list));
  } catch (_) { /* localStorage indisponible */ }
}

// Fusion de deux listes d'archives : union par clé, l'entrée au updatedAt le plus
// récent gagne (gère les tombstones de désarchivage). Les archives ne font que
// grandir → une union est sûre et non destructrice.
function mergeArchived(a, b) {
  const byKey = new Map();
  const consider = (x) => {
    if (!x) return;
    const k = x.key || archiveKey(x);
    const prev = byKey.get(k);
    if (!prev) { byKey.set(k, { ...x, key: k }); return; }
    const xNewer = (x.updatedAt || x.archivedAt || 0) >= (prev.updatedAt || prev.archivedAt || 0);
    byKey.set(k, xNewer ? { ...x, key: k } : prev);
  };
  (a || []).forEach(consider);
  (b || []).forEach(consider);
  return [...byKey.values()];
}

// Signature d'une liste d'archives (clés + état supprimé) pour éviter un PUT inutile.
function _archiveSig(list) {
  return (list || [])
    .map((a) => (a.key || archiveKey(a)) + (a.deleted ? '#x' : ''))
    .sort()
    .join('|');
}

// Synchronise les archives : lit le fichier distant, fusionne, et ne pousse
// que si le résultat diffère du distant. Un seul GET par appel, PUT si besoin.
let _archiveTimer = null;
async function syncArchived(retry = 1) {
  const token = getGhToken();
  if (!token) return loadArchived();
  const api = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/contents/${GH_ARCHIVE_PATH}`;
  const headers = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' };
  let remote = [], sha = null;
  try {
    const getRes = await fetch(`${api}?ref=${GH_BRANCH}&_=${nowTs()}`, { headers, cache: 'no-store' });
    if (getRes.ok) {
      const meta = await getRes.json();
      sha = meta.sha;
      try { remote = JSON.parse(_b64decode(meta.content || '')); } catch (_) { remote = []; }
      if (!Array.isArray(remote)) remote = [];
    } else if (getRes.status !== 404) {
      return loadArchived(); // réseau/API indisponible : on garde le local
    }
  } catch (_) {
    return loadArchived();
  }
  const merged = mergeArchived(loadArchivedRaw(), remote);
  saveArchived(merged);
  if (_archiveSig(remote) === _archiveSig(merged)) return merged; // rien à pousser
  try {
    const body = {
      message: 'MAJ archives (dashboard)',
      content: _b64encode(JSON.stringify(merged, null, 2)),
      branch: GH_BRANCH,
    };
    if (sha) body.sha = sha;
    const putRes = await fetch(api, { method: 'PUT', headers, body: JSON.stringify(body) });
    if (putRes.status === 409 && retry > 0) return syncArchived(retry - 1); // conflit de SHA
  } catch (_) { /* on garde le local, réessai au prochain chargement */ }
  return merged;
}

// Pousse les archives avec un léger délai (regroupe les écarts rapprochés).
function scheduleSyncArchived() {
  if (!hasGhToken()) return;
  if (_archiveTimer) clearTimeout(_archiveTimer);
  _archiveTimer = setTimeout(() => { syncArchived(); }, 1200);
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
  const list = loadArchivedRaw();
  const existing = list.find((a) => (a.key || archiveKey(a)) === key);
  if (existing) {
    if (!existing.deleted) return false;   // déjà archivée et active
    existing.deleted = false;              // ré-archivage : on lève le tombstone
    existing.updatedAt = nowTs();
  } else {
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
      updatedAt: nowTs(),
    });
  }
  saveArchived(list);
  scheduleSyncArchived();
  return true;
}

// Désarchivage par clé stable (data-unarchive = archiveKey de l'offre).
// Tombstone (deleted) plutôt que suppression, pour que le retrait se synchronise
// et ne « revienne » pas depuis un autre appareil au prochain pull.
function unarchiveJob(key) {
  const list = loadArchivedRaw();
  const a = list.find((x) => (x.key || archiveKey(x)) === key);
  if (a) {
    a.deleted = true;
    a.updatedAt = nowTs();
    saveArchived(list);
    scheduleSyncArchived();
  }
}
