// Library page behaviour.
//
// Loaded after the inline bootstrap in index.html, which is where the
// server-injected values live (ITEMS, PREFERRED_QUALITY, TRAKT_STATE and the
// rest). Those are top-level const declarations in a classic script, so they are
// in scope here, and the function declarations here are reachable from the
// inline onclick handlers in the markup.

const LIBRARY_VIEW_STATE = {
  movies: { query: '', genre: '', filtersOpen: false, missing: false, upgrade: false, incomplete: false, missingSubtitles: false, downloading: false, collections: false },
  tv: { query: '', genre: '', filtersOpen: false, missing: false, upgrade: false, incomplete: false, missingSubtitles: false, downloading: false },
};

// ── Section switching
const SECTIONS = ['discover', 'movies', 'tv', 'favourites', 'settings'];
const LAST_SECTION_KEY = 'media-library.last-section';

function resolveInitialSection() {
  try {
    const saved = localStorage.getItem(LAST_SECTION_KEY);
    if (saved && SECTIONS.includes(saved)) return saved;
  } catch (_error) {
    // Ignore storage issues and fall back to server-selected section.
  }
  return SECTIONS.includes(INITIAL_SECTION) ? INITIAL_SECTION : 'movies';
}

// Reset hero to idle state when switching sections.
// Swaps which section is on screen without touching the hero, so opening an
// episode list can keep the show's hero card in place above it.
function activateSection(name) {
  SECTIONS.forEach(s => {
    const el = document.getElementById('section-' + s);
    if (el) el.style.display = 'none';
    const nb = document.getElementById('nav-' + s);
    if (nb) nb.classList.remove('active');
  });
  const target = document.getElementById('section-' + name);
  if (target) target.style.display = 'block';
  const navBtn = document.getElementById('nav-' + name);
  if (navBtn) navBtn.classList.add('active');
  try {
    localStorage.setItem(LAST_SECTION_KEY, name);
  } catch (_error) {
    // Ignore storage issues.
  }
  if (name === 'discover') loadDiscoverOnce();
  if (name === 'settings') loadIgnoredItemsOnce();
}

function showSection(name) {
  closeHero();
  // Navigating by hand should land on the poster grid, not a stale episode list.
  closeEpisodeView();
  activateSection(name);
}

// Show the section selected by the server.
showSection(resolveInitialSection());

function escAttr(str) {
  return escHtml(String(str)).replace(/"/g, '&quot;');
}

function formatTorrentSize(value) {
  const raw = String(value || '').trim();
  if (!raw) return '?';
  const bytes = Number(raw);
  if (!Number.isFinite(bytes) || bytes < 0) return raw;
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = bytes / 1024;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  const decimals = size >= 100 ? 0 : (size >= 10 ? 1 : 2);
  return size.toFixed(decimals) + ' ' + units[idx];
}

function normalizeTitle(value) {
  const raw = String(value || '').trim().toLowerCase();
  if (raw.startsWith('the ')) return raw.slice(4);
  if (raw.startsWith('an ')) return raw.slice(3);
  if (raw.startsWith('a ')) return raw.slice(2);
  return raw;
}

// The player page selects which file to play with ?episode=. Episode names are
// real filenames and routinely contain & # and spaces, so the encoding is not
// optional — building this URL by hand is how one of them silently truncates.
function watchUrl(mediaId, episodeFile) {
  const base = `/video/${mediaId}`;
  return episodeFile ? `${base}?episode=${encodeURIComponent(episodeFile)}` : base;
}


function libraryItemTitle(item) {
  if (item.title && !String(item.title).startsWith('tt')) return item.title;
  if (item.path) {
    const parts = String(item.path).split(/[\\/]/);
    return parts[parts.length - 1] || item.path;
  }
  return item.imdb_id || 'Untitled';
}

function libraryItemMissing(item) {
  return !!item.file_missing;
}

function libraryItemIncomplete(item) {
  if (libraryItemMissing(item) || libraryItemDownloading(item)) return false;
  return !item.poster_url || !item.synopsis || !item.year || !item.current_quality;
}

function libraryItemMissingSubtitles(item) {
  if (libraryItemMissing(item) || libraryItemDownloading(item)) return false;
  return !item.subtitles;
}

function libraryItemDownloading(item) {
  return ['starting', 'handed_off', 'downloading'].includes(item.download_status);
}

function libraryItemUpgrade(item) {
  if (libraryItemMissing(item) || libraryItemDownloading(item)) return false;
  return item.upgrade_available === true;
}

function libraryItemMatchesQuery(item, query) {
  if (!query) return true;
  const haystack = [libraryItemTitle(item), item.collection_name || '', item.imdb_id || '', item.path || ''].join(' ').toLowerCase();
  return haystack.includes(query);
}

function libraryCardMarkup(item) {
  const title = libraryItemTitle(item);
  const noPoster = item.path
    ? String(item.path).split(/[\\/]/).pop().slice(0, 40)
    : (item.imdb_id || '?');
  return `
    <div class="card${item.upgrade_available ? ' has-upgrade' : ''}" id="card-${escAttr(item.id)}" data-id="${escAttr(item.id)}" onclick="openHero(this)">
      <div class="card-poster">
        ${item.poster_url ? `
          <img src="${escAttr(item.poster_url)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
          <div class="card-no-poster" style="display:none">${escHtml((title || '?').charAt(0).toUpperCase() || '?')}</div>
        ` : `
          <div class="card-no-poster" style="font-size:1rem;padding:8px;text-align:center;color:rgba(240,230,255,0.35);">${escHtml(noPoster || '?')}</div>
        `}
        ${libraryItemMissing(item) ? '<div class="card-badge card-badge-missing" title="file missing">&#x26A0;</div>' : ''}
        ${!libraryItemMissing(item) && !libraryItemDownloading(item) && item.upgrade_available ? '<div class="card-badge card-badge-upgrade" title="Higher quality available">&#x2191;</div>' : ''}
        ${libraryItemDownloading(item) ? `<div class="card-badge card-badge-download" title="${escAttr(item.download_message || item.download_status || 'Downloading')}">&#x2193;</div>` : ''}
        ${item.favourite ? '<div class="card-badge card-badge-fav">&#10084;</div>' : ''}
        ${libraryItemIncomplete(item) ? '<div class="card-badge card-badge-incomplete" title="Incomplete metadata">&#x3C;&#x2F;&#x3E;</div>' : ''}
        ${libraryItemMissingSubtitles(item) ? `<div class="card-badge card-badge-subtitles${libraryItemIncomplete(item) ? ' adjacent' : ''}" title="Missing subtitles">CC</div>` : ''}
        <div class="card-overlay">
          <div class="card-title">${escHtml(title)}</div>
          ${item.year ? `<div class="card-year">${escHtml(String(item.year))}</div>` : ''}
        </div>
      </div>
    </div>
  `;
}

function collectionPosterUrl(group) {
  const withPoster = (group.items || []).find(item => item.poster_url);
  return withPoster ? withPoster.poster_url : '';
}

function collectionCardMarkup(group) {
  const posterUrl = collectionPosterUrl(group);
  const firstId = group.items.length ? group.items[0].id : '';
  return `
    <div class="card card-collection" data-id="${escAttr(firstId)}" onclick="openHero(this)">
      <div class="card-poster">
        ${posterUrl ? `<img src="${escAttr(posterUrl)}" alt="" loading="lazy">` : `<div class="card-no-poster">${escHtml((group.name || '?').charAt(0).toUpperCase() || '?')}</div>`}
        <div class="card-badge card-badge-collection">${escHtml(String(group.items.length))}</div>
      </div>
    </div>
  `;
}

function getSectionItems(sectionId) {
  return (LIBRARY_SECTION_ITEMS[sectionId] || []).map(id => ITEMS[id]).filter(Boolean);
}

// ── TV seasons and episodes
function tvEpisodeElements() {
  return {
    view: document.getElementById('tv-episode-view'),
    grid: document.getElementById('media-grid-tv'),
    panel: document.querySelector('#section-tv .library-sticky-panel'),
    list: document.getElementById('tv-episode-list'),
    heading: document.getElementById('tv-episode-heading'),
    count: document.getElementById('tv-episode-count'),
    select: document.getElementById('hero-season-select'),
  };
}

function closeEpisodeView() {
  const el = tvEpisodeElements();
  if (el.view) el.view.classList.remove('is-open');
  if (el.grid) el.grid.style.display = '';
  if (el.panel) el.panel.style.display = '';
  if (el.list) el.list.innerHTML = '';
}

// The chip reports whether the show is still running; the button is about
// whether anything is missing. They are deliberately independent — an ended
// show can still be missing whole seasons, and gating the button on "airing"
// left those unreachable from the hero.
function setAiringUi(inProduction, status, missingCount) {
  const chip = document.getElementById('hero-chip-airing');
  const btn = document.getElementById('hero-check-new-btn');
  if (chip) {
    chip.style.display = inProduction ? 'inline-block' : 'none';
    chip.textContent = 'Airing';
    chip.title = status ? `TMDB status: ${status}` : '';
  }
  if (!btn) return;
  const missing = Number(missingCount) || 0;
  // An airing show is worth re-checking even when nothing is missing today.
  const offer = missing > 0 || !!inProduction;
  btn.style.display = offer ? 'inline-block' : 'none';
  btn.textContent = missing > 0
    ? `Find ${missing} missing`
    : 'Check for new content';
  btn.title = missing > 0
    ? `${missing} aired episode${missing === 1 ? '' : 's'} you do not have`
    : 'Check TMDB for episodes you do not have';
}

// A flag, deliberately and only a flag. Which copy of an episode to keep is a
// judgement about codecs, subtitles and disc space, and deleting the wrong one
// cannot be undone — so this says how many need a look and offers no action.
function setDuplicatesUi(duplicateCount) {
  const chip = document.getElementById('hero-chip-duplicates');
  if (!chip) return;
  const count = Number(duplicateCount) || 0;
  chip.style.display = count > 0 ? 'inline-block' : 'none';
  chip.textContent = `${count} duplicated`;
  chip.title = count === 1
    ? '1 episode is held more than once — check which copy you want to keep'
    : `${count} episodes are held more than once — check which copies you want to keep`;
}

async function loadSeasonsForHero(item) {
  const el = tvEpisodeElements();
  setAiringUi(false, null, 0);
  setDuplicatesUi(0);
  if (!el.select) return;
  el.select.style.display = 'none';
  el.select.innerHTML = '';
  if (!item || item.media_type !== 'tv' || !item.id) return;

  let data;
  try {
    const response = await fetch(`/api/tv/${item.id}/seasons`, { headers: { 'Accept': 'application/json' } });
    if (!response.ok) return;
    data = await response.json();
  } catch (_error) {
    return;
  }
  if (!data.ok) return;
  setAiringUi(!!data.in_production, data.status, data.missing_count);
  setDuplicatesUi(data.duplicate_count);
  if (!(data.seasons || []).length) return;

  const options = ['<option value="">Season…</option>'].concat(data.seasons.map(season => {
    const counts = season.owned_count
      ? `${season.owned_count}`
      : `${season.featurette_count} extra${season.featurette_count === 1 ? '' : 's'}`;
    return `<option value="${escAttr(season.season_number)}">${escHtml(season.name)} (${counts})</option>`;
  }));
  // Extras that no folder attributes to a season would otherwise be
  // unreachable — and for a show with no SxxExx at all, that is everything.
  if (data.extras_count) {
    options.push(`<option value="extras">Extras (${data.extras_count})</option>`);
  }
  el.select.innerHTML = options.join('');
  el.select.style.display = 'inline-block';
}

async function showSeasonEpisodes(mediaId, seasonNumber) {
  const el = tvEpisodeElements();
  if (!el.view || !el.list) return;
  const item = ITEMS[mediaId] || {};
  const isExtras = seasonNumber === 'extras';

  // The episode list takes the place of the poster grid, so the toolbar that
  // filters posters is hidden too until Back to Shows restores both.
  if (el.grid) el.grid.style.display = 'none';
  if (el.panel) el.panel.style.display = 'none';
  el.view.classList.add('is-open');
  el.heading.textContent = isExtras
    ? `${item.title || 'Show'} — Extras`
    : `${item.title || 'Show'} — Season ${seasonNumber}`;
  el.count.textContent = '';
  el.list.innerHTML = '<div class="tv-episode-empty">Loading…</div>';

  if (isExtras) {
    el.list.innerHTML = '';
    const added = await renderFeaturettes(mediaId, el.list, 'extras');
    if (!added) {
      el.list.innerHTML = '<div class="tv-episode-empty">No extras found.</div>';
    }
    return;
  }

  let data;
  try {
    const response = await fetch(`/api/tv/${mediaId}/season/${seasonNumber}`, {
      headers: { 'Accept': 'application/json' },
    });
    data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'failed');
  } catch (_error) {
    el.list.innerHTML = '<div class="tv-episode-empty">Could not load episodes.</div>';
    return;
  }

  const episodes = data.episodes || [];
  if (!episodes.length) {
    el.list.innerHTML = '';
    const added = await renderFeaturettes(mediaId, el.list, seasonNumber);
    if (!added) {
      el.list.innerHTML = '<div class="tv-episode-empty">No episodes found for this season.</div>';
    }
    return;
  }
  el.count.textContent = `${episodes.length} episode${episodes.length === 1 ? '' : 's'}`;
  el.list.innerHTML = episodes.map(episode => {
    const meta = [
      episode.air_date || '',
      episode.runtime ? `${episode.runtime} min` : '',
    ].filter(Boolean).join(' · ');
    const href = watchUrl(mediaId, episode.file);
    // A plain div stands in when TMDB has no still, so rows stay aligned.
    const thumb = episode.still_url
      ? `<img class="tv-episode-thumb" src="${escAttr(episode.still_url)}" alt="" loading="lazy">`
      : '<div class="tv-episode-thumb"></div>';
    return `
      <div class="tv-episode-row">
        <div class="tv-episode-num">S${String(episode.season_number).padStart(2, '0')}E${String(episode.episode_number).padStart(2, '0')}</div>
        ${thumb}
        <div class="tv-episode-main">
          <div class="tv-episode-title">${escHtml(episode.title)}</div>
          ${meta ? `<div class="tv-episode-meta">${escHtml(meta)}</div>` : ''}
          ${episode.synopsis ? `<div class="tv-episode-synopsis">${escHtml(episode.synopsis)}</div>` : ''}
        </div>
        <a class="hero-action-btn" href="${escAttr(href)}">▶ Watch</a>
      </div>`;
  }).join('');

  renderFeaturettes(mediaId, el.list, seasonNumber);
}

async function showMissingForShow(mediaId) {
  const el = tvEpisodeElements();
  if (!el.view || !el.list) return;
  const item = ITEMS[mediaId] || {};
  const btn = document.getElementById('hero-check-new-btn');
  if (btn) btn.disabled = true;

  if (el.grid) el.grid.style.display = 'none';
  if (el.panel) el.panel.style.display = 'none';
  el.view.classList.add('is-open');
  el.heading.textContent = `${item.title || 'Show'} — New & Missing`;
  el.count.textContent = '';
  el.list.innerHTML = '<div class="tv-episode-empty">Checking TMDB for new episodes…</div>';

  try {
    const response = await fetch(`/api/tv/${mediaId}/missing`, {
      headers: { 'Accept': 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'failed');

    if (!data.show) {
      el.list.innerHTML =
        '<div class="tv-episode-empty">Nothing missing — you have every episode that has aired.</div>';
      return;
    }
    el.count.textContent = `${data.missing_count} episode${data.missing_count === 1 ? '' : 's'}`;
    // Same markup as the Discover block, so the two entry points look alike.
    el.list.innerHTML = showMissingEpisodesMarkup(data.show);
  } catch (_error) {
    el.list.innerHTML = '<div class="tv-episode-empty">Could not check for new episodes.</div>';
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Bonus features carry no episode number, so they hang off the bottom of the
// list rather than being mixed in with the episodes. Scoped to the season whose
// folder holds them, so browsing Season 4 does not list Season 1's extras.
async function renderFeaturettes(mediaId, listEl, season) {
  let data;
  try {
    const url = `/api/tv/${mediaId}/unmatched?season=${encodeURIComponent(season)}`;
    const response = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!response.ok) return false;
    data = await response.json();
  } catch (_error) {
    return false;
  }
  const files = (data && data.ok && data.files) || [];
  if (!files.length) return false;

  const rows = files.map(file => {
    const href = watchUrl(mediaId, file.file);
    return `
      <div class="tv-featurette-row">
        <div class="tv-featurette-name" title="${escAttr(file.name)}">${escHtml(file.label || file.name)}</div>
        <a class="hero-action-btn" href="${escAttr(href)}">▶ Watch</a>
      </div>`;
  }).join('');

  const block = document.createElement('div');
  block.className = 'tv-featurettes';
  block.innerHTML =
    `<div class="tv-featurettes-title">Featurettes &amp; Extras (${files.length})</div>${rows}`;
  listEl.appendChild(block);
  return true;
}

function libraryItemHasGenre(item, genre) {
  const wanted = String(genre || '').trim().toLowerCase();
  if (!wanted) return true;
  return [item.genre_1, item.genre_2]
    .some(value => String(value || '').trim().toLowerCase() === wanted);
}

// Genre chips are shown on the hero for discover items too, where the title may
// not be in the library at all; filtering the matching library section is still
// the useful outcome, so the media type picks the section rather than the source.
function applyGenreFilter(genre, mediaType) {
  const sectionId = mediaType === 'tv' ? 'tv' : 'movies';
  const state = LIBRARY_VIEW_STATE[sectionId];
  if (!state) return;
  state.genre = String(genre || '').trim();
  showSection(sectionId);
  renderLibrarySection(sectionId);
  syncLibraryToolbar(sectionId);
}

function clearGenreFilter(sectionId) {
  const state = LIBRARY_VIEW_STATE[sectionId];
  if (!state) return;
  state.genre = '';
  renderLibrarySection(sectionId);
  syncLibraryToolbar(sectionId);
}

// Reuses the existing "downloading" toolbar filter rather than a second
// mechanism, so the toolbar button lights up and can be clicked off again.
function applyDownloadingFilter(mediaType) {
  const sectionId = mediaType === 'tv' ? 'tv' : 'movies';
  const state = LIBRARY_VIEW_STATE[sectionId];
  if (!state) return;
  state.downloading = true;
  state.filtersOpen = true;
  showSection(sectionId);
  renderLibrarySection(sectionId);
  syncLibraryToolbar(sectionId);
}

function renderLibrarySection(sectionId) {
  const grid = document.getElementById(`media-grid-${sectionId}`);
  if (!grid) return;
  const state = LIBRARY_VIEW_STATE[sectionId] || { query: '', missing: false, upgrade: false, incomplete: false, missingSubtitles: false, downloading: false, collections: false };
  const query = String(state.query || '').trim().toLowerCase();
  const items = getSectionItems(sectionId).filter(item => {
    if (!libraryItemMatchesQuery(item, query)) return false;
    if (state.missing && !libraryItemMissing(item)) return false;
    if (state.upgrade && !libraryItemUpgrade(item)) return false;
    if (state.incomplete && !libraryItemIncomplete(item)) return false;
    if (state.missingSubtitles && !libraryItemMissingSubtitles(item)) return false;
    if (state.downloading && !libraryItemDownloading(item)) return false;
    if (state.genre && !libraryItemHasGenre(item, state.genre)) return false;
    return true;
  });

  let entries = [];
  if (sectionId === 'movies' && state.collections) {
    const grouped = new Map();
    const standalone = [];
    for (const item of items) {
      if (item.collection_id && item.collection_name) {
        const key = String(item.collection_id);
        if (!grouped.has(key)) grouped.set(key, { id: item.collection_id, name: item.collection_name, items: [] });
        grouped.get(key).items.push(item);
      } else {
        standalone.push({ sortKey: normalizeTitle(libraryItemTitle(item)), html: libraryCardMarkup(item) });
      }
    }
    for (const group of grouped.values()) {
      group.items.sort((a, b) => normalizeTitle(libraryItemTitle(a)).localeCompare(normalizeTitle(libraryItemTitle(b))) || (a.year || 0) - (b.year || 0));
      entries.push({ sortKey: normalizeTitle(group.name), html: collectionCardMarkup(group) });
    }
    entries = entries.concat(standalone);
  } else {
    entries = items.map(item => ({ sortKey: normalizeTitle(libraryItemTitle(item)), html: libraryCardMarkup(item) }));
  }

  entries.sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  grid.innerHTML = entries.map(entry => entry.html).join('');
}

function syncLibraryToolbar(sectionId) {
  const state = LIBRARY_VIEW_STATE[sectionId];
  if (!state) return;
  const searchInput = document.getElementById(`library-search-input-${sectionId}`);
  if (searchInput && searchInput.value !== state.query) searchInput.value = state.query;
  document.querySelectorAll(`[data-library-filter="${sectionId}"]`).forEach(btn => {
    const key = btn.dataset.filterKey;
    btn.classList.toggle('active', !!state[key]);
  });
  const filterToggle = document.querySelector(`[data-library-filter-toggle="${sectionId}"]`);
  if (filterToggle) filterToggle.classList.toggle('active', !!state.filtersOpen);
  const filterActions = document.getElementById(`library-filter-actions-${sectionId}`);
  if (filterActions) filterActions.classList.toggle('open', !!state.filtersOpen);
  const genrePill = document.getElementById(`library-genre-pill-${sectionId}`);
  if (genrePill) {
    const genre = String(state.genre || '').trim();
    genrePill.classList.toggle('is-visible', !!genre);
    genrePill.textContent = genre ? `${genre} ✕` : '';
  }
}

function discoverCardMarkup(item) {
  const title = item.title || 'Untitled';
  const poster = item.poster_url
    ? `<img src="${escAttr(item.poster_url)}" alt="" loading="lazy">`
    : escHtml(title.charAt(0).toUpperCase() || '?');
  return `
    <button
      type="button"
      class="discover-card discover-card-poster-only discover-hero-trigger"
      data-tmdb-id="${escAttr(item.tmdb_id || '')}"
      data-media-type="${escAttr(item.media_type || 'movie')}"
      data-title="${escAttr(title)}"
      data-year="${escAttr(item.year || '')}"
      data-poster-url="${escAttr(item.poster_url || '')}"
      title="${escAttr(title)}"
    >
      <div class="discover-card-poster">${poster}</div>
    </button>
  `;
}

function renderDiscoverStrip(containerId, noteId, items, emptyText, metaText) {
  const container = document.getElementById(containerId);
  const note = document.getElementById(noteId);
  const meta = document.getElementById(containerId + '-meta');
  if (meta) meta.textContent = metaText || '';
  if (!items.length) {
    container.innerHTML = '';
    note.textContent = emptyText;
    note.style.display = 'block';
    return;
  }
  container.innerHTML = items.map(discoverCardMarkup).join('');
  note.style.display = 'none';
}

function renderCollections(collections) {
  const container = document.getElementById('discover-collections');
  const note = document.getElementById('discover-collections-note');
  const missingItems = collections.flatMap(group =>
    (group.missing || []).map(item => ({
      ...item,
      collection_id: group.collection_id,
      collection_name: group.collection_name,
    }))
  );
  document.getElementById('discover-collections-meta').textContent = missingItems.length
    ? `${missingItems.length} missing titles`
    : '';
  if (!missingItems.length) {
    container.innerHTML = '';
    note.textContent = TMDB_CONFIGURED
      ? 'No incomplete movie collections found in the current library.'
      : 'Configure TMDB to load trending titles.';
    note.style.display = 'block';
    return;
  }
  container.innerHTML = missingItems.map(item => {
    const title = item.title || 'Untitled';
    const poster = item.poster_url
      ? `<img src="${escAttr(item.poster_url)}" alt="" loading="lazy">`
      : escHtml(title.charAt(0).toUpperCase() || '?');
    return `
      <div class="discover-card discover-card-poster-only discover-collection-card" title="${escAttr(title)}">
        <div class="discover-ignore-actions">
          <button type="button" class="discover-ignore-btn" data-ignore-kind="title" data-tmdb-id="${escAttr(item.tmdb_id)}" data-collection-id="${escAttr(item.collection_id || '')}" data-title="${escAttr(title)}" data-collection-name="${escAttr(item.collection_name || '')}" title="Ignore this title">Hide</button>
          ${item.collection_id ? `<button type="button" class="discover-ignore-btn" data-ignore-kind="collection" data-collection-id="${escAttr(item.collection_id)}" data-collection-name="${escAttr(item.collection_name || '')}" title="Ignore this collection">Series</button>` : ''}
        </div>
        <button
          type="button"
          class="discover-hero-trigger"
          data-tmdb-id="${escAttr(item.tmdb_id || '')}"
          data-media-type="movie"
          data-title="${escAttr(title)}"
          data-year="${escAttr(item.year || '')}"
          data-poster-url="${escAttr(item.poster_url || '')}"
          style="all:unset;display:block;cursor:pointer;"
        >
          <div class="discover-card-poster">${poster}</div>
        </button>
      </div>
    `;
  }).join('');
  note.style.display = 'none';
}

async function refreshDiscover() {
  DISCOVER_STATE.loaded = false;
  document.getElementById('discover-watchlist-note').textContent = 'Refreshing…';
  document.getElementById('discover-collections-note').textContent = 'Refreshing…';
  document.getElementById('discover-trending-note').textContent = 'Refreshing…';
  await loadDiscoverOnce();
}

async function ignoreDiscoverItem(kind, tmdbId, collectionId, title, collectionName) {
  const body = new URLSearchParams();
  if (tmdbId) body.set('tmdb_id', tmdbId);
  if (collectionId) body.set('collection_id', collectionId);
  if (title) body.set('title', title);
  if (collectionName) body.set('collection_name', collectionName);
  const endpoint = kind === 'collection' ? '/api/discover/ignore-collection' : '/api/discover/ignore-title';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Accept': 'application/json',
    },
    body: body.toString(),
  });
  if (!response.ok) {
    throw new Error('ignore_failed');
  }
  await refreshDiscover();
  IGNORES_STATE.loaded = false;
  if (document.getElementById('section-settings').style.display === 'block') {
    await loadIgnoredItemsOnce();
  }
}

function renderIgnoredList(containerId, items, kind) {
  const container = document.getElementById(containerId);
  if (!items.length) {
    container.innerHTML = '<div class="ignored-empty">Nothing ignored.</div>';
    return;
  }
  container.innerHTML = items.map(item => {
    const key = kind === 'collection' ? item.collection_id : item.tmdb_id;
    const label = kind === 'collection'
      ? (item.collection_name || `Collection #${item.collection_id}`)
      : (item.title || `Title #${item.tmdb_id}`);
    const sub = kind === 'collection'
      ? `Collection ID: ${item.collection_id}`
      : `TMDB ID: ${item.tmdb_id}${item.collection_name ? ` · ${item.collection_name}` : ''}`;
    return `
      <div class="ignored-item">
        <div><strong>${escHtml(label)}</strong><br>${escHtml(sub)}</div>
        <button type="button" class="ignored-unignore" data-unignore-kind="${kind}" data-ignore-key="${escAttr(key)}">Unignore</button>
      </div>
    `;
  }).join('');
}

async function loadIgnoredItemsOnce(force = false) {
  if ((IGNORES_STATE.loaded && !force) || IGNORES_STATE.loading) return;
  IGNORES_STATE.loading = true;
  try {
    const response = await fetch('/api/discover/ignored', { headers: { 'Accept': 'application/json' } });
    const data = await response.json();
    renderIgnoredList('ignored-titles-list', data.titles || [], 'title');
    renderIgnoredList('ignored-collections-list', data.collections || [], 'collection');
    IGNORES_STATE.loaded = true;
  } catch (_error) {
    document.getElementById('ignored-titles-list').innerHTML = '<div class="ignored-empty">Unable to load ignored titles.</div>';
    document.getElementById('ignored-collections-list').innerHTML = '<div class="ignored-empty">Unable to load ignored collections.</div>';
  } finally {
    IGNORES_STATE.loading = false;
  }
}

function traktStatusMessage() {
  if (TRAKT_STATE.connected) {
    return `Connected as ${TRAKT_STATE.username || 'your Trakt account'} via OAuth.`;
  }
  if (TRAKT_STATE.deviceFlow) {
    return 'Waiting for Trakt authorization.';
  }
  return '';
}

function setTraktInlineStatus(message, isError = false) {
  const el = document.getElementById('trakt-device-status');
  if (!el) return;
  el.textContent = message || '';
  el.style.color = isError ? '#f87171' : 'var(--text-muted)';
}

function renderTraktControls() {
  document.getElementById('trakt-auth-summary').textContent = traktStatusMessage();
  document.getElementById('trakt-connect-btn').style.display = (!TRAKT_STATE.oauthAvailable || TRAKT_STATE.connected) ? 'none' : 'inline-flex';
  document.getElementById('trakt-disconnect-btn').style.display = TRAKT_STATE.connected ? 'inline-flex' : 'none';
  document.getElementById('trakt-connect-btn').disabled = Boolean(TRAKT_STATE.deviceFlow);

  const devicePanel = document.getElementById('trakt-device-panel');
  const openBtn = document.getElementById('trakt-open-btn');
  const copyBtn = document.getElementById('trakt-copy-btn');
  if (TRAKT_STATE.deviceFlow) {
    devicePanel.style.display = 'block';
    openBtn.style.display = 'inline-flex';
    copyBtn.style.display = 'inline-flex';
    document.getElementById('trakt-device-code').textContent = TRAKT_STATE.deviceFlow.user_code || '';
    if (!document.getElementById('trakt-device-status').textContent) {
      setTraktInlineStatus('Copy or note the code, then open Trakt and keep this page open while it checks for approval.');
    }
  } else {
    devicePanel.style.display = 'none';
    openBtn.style.display = 'none';
    copyBtn.style.display = 'none';
    document.getElementById('trakt-device-code').textContent = '';
    setTraktInlineStatus('');
  }
}

function scheduleTraktPoll(intervalSeconds) {
  if (TRAKT_STATE.pollTimer) {
    clearTimeout(TRAKT_STATE.pollTimer);
  }
  TRAKT_STATE.pollTimer = window.setTimeout(() => {
    pollTraktConnect();
  }, Math.max(1, intervalSeconds || 5) * 1000);
}

async function startTraktConnect() {
  try {
    const response = await fetch('/api/trakt/connect/start', {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'trakt_connect_failed');
    }
    TRAKT_STATE.deviceFlow = data.flow || null;
    TRAKT_STATE.connected = false;
    TRAKT_STATE.configured = true;
    renderTraktControls();
    setTraktInlineStatus('Code ready. Open Trakt, enter the code, and keep this page open while it checks for approval.');
    if (TRAKT_STATE.deviceFlow && TRAKT_STATE.deviceFlow.verification_url) {
      scheduleTraktPoll(TRAKT_STATE.deviceFlow.interval || 5);
    }
  } catch (_error) {
    setTraktInlineStatus('Unable to start Trakt OAuth. Check the app-level Trakt credentials.', true);
  }
}

async function pollTraktConnect() {
  if (!TRAKT_STATE.deviceFlow || TRAKT_STATE.polling) return;
  TRAKT_STATE.polling = true;
  try {
    const response = await fetch('/api/trakt/connect/poll', {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
    });
    const data = await response.json();
    if (response.ok && data.ok && data.status === 'connected') {
      window.location.href = '/?section=settings';
      return;
    }
    if (response.ok && data.ok && data.status === 'pending') {
      setTraktInlineStatus('Waiting for approval…');
      scheduleTraktPoll(data.interval || (TRAKT_STATE.deviceFlow.interval || 5));
      return;
    }
    TRAKT_STATE.deviceFlow = null;
    renderTraktControls();
    const messageMap = {
      trakt_oauth_expired: 'The Trakt code expired. Start again.',
      trakt_oauth_denied: 'Trakt authorization was denied.',
      trakt_oauth_not_found: 'That Trakt device code was not found. Start again.',
      trakt_oauth_already_used: 'This Trakt code was already used. Start again if needed.',
    };
    setTraktInlineStatus(messageMap[data.error] || 'Trakt authorization failed.', true);
  } catch (_error) {
    setTraktInlineStatus('Unable to reach Trakt right now.', true);
    scheduleTraktPoll(TRAKT_STATE.deviceFlow ? (TRAKT_STATE.deviceFlow.interval || 5) : 5);
  } finally {
    TRAKT_STATE.polling = false;
  }
}

async function disconnectTrakt() {
  try {
    await fetch('/api/trakt/disconnect', {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
    });
  } finally {
    window.location.href = '/?section=settings';
  }
}

async function unignoreDiscoverItem(kind, key) {
  const body = new URLSearchParams();
  if (kind === 'collection') body.set('collection_id', key);
  else body.set('tmdb_id', key);
  const endpoint = kind === 'collection' ? '/api/discover/unignore-collection' : '/api/discover/unignore-title';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Accept': 'application/json',
    },
    body: body.toString(),
  });
  if (!response.ok) {
    throw new Error('unignore_failed');
  }
  await loadIgnoredItemsOnce(true);
  await refreshDiscover();
}

async function unignoreAll(kind) {
  const endpoint = kind === 'collection'
    ? '/api/discover/unignore-all-collections'
    : '/api/discover/unignore-all-titles';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Accept': 'application/json' },
  });
  if (!response.ok) {
    throw new Error('unignore_all_failed');
  }
  await loadIgnoredItemsOnce(true);
  await refreshDiscover();
}

async function openDiscoverHero(trigger) {
  const tmdbId = (trigger.dataset.tmdbId || '').trim();
  const mediaType = (trigger.dataset.mediaType || 'movie').trim();
  const fallbackTitle = (trigger.dataset.title || '').trim();
  const fallbackPoster = (trigger.dataset.posterUrl || '').trim();
  const fallbackYear = parseInt(trigger.dataset.year || '', 10);
  if (!tmdbId) {
    openHeroFromItem({
      title: fallbackTitle || 'Title',
      media_type: mediaType,
      year: Number.isFinite(fallbackYear) ? fallbackYear : null,
      poster_url: fallbackPoster || null,
    }, null, false);
    return;
  }

  const query = new URLSearchParams({ tmdb_id: tmdbId, media_type: mediaType, title: fallbackTitle });
  try {
    const response = await fetch('/api/discover/hero?' + query.toString(), { headers: { 'Accept': 'application/json' } });
    const payload = await response.json();
    const item = (payload && payload.item) ? payload.item : {};
    if (!item.poster_url && fallbackPoster) item.poster_url = fallbackPoster;
    if (!item.year && Number.isFinite(fallbackYear)) item.year = fallbackYear;
    if (!item.title && fallbackTitle) item.title = fallbackTitle;
    openHeroFromItem(item, null, false);
  } catch (_error) {
    openHeroFromItem({
      title: fallbackTitle || 'Title',
      media_type: mediaType,
      year: Number.isFinite(fallbackYear) ? fallbackYear : null,
      poster_url: fallbackPoster || null,
    }, null, false);
  }
}

async function addDiscoverHeroItem() {
  const item = HERO_STATE.item;
  if (!item) return;
  const isDiscover = HERO_STATE.source === 'discover';
  const isMissingLibraryItem = HERO_STATE.source === 'library' && !!item.file_missing;
  if (!isDiscover && !isMissingLibraryItem) return;
  if (!item.tmdb_id || !TMDB_CONFIGURED) return;

  const addBtn = isMissingLibraryItem
    ? document.getElementById('hero-add-missing-btn')
    : document.getElementById('hero-add-btn');
  const statusEl = isMissingLibraryItem
    ? document.getElementById('hero-library-status')
    : document.getElementById('hero-discover-status');
  addBtn.disabled = true;
  statusEl.textContent = 'Adding…';
  try {
    const response = await fetch('/api/discover/add-and-search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify({
        tmdb_id: item.tmdb_id,
        media_type: item.media_type || 'movie',
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || 'add_failed');
    }
    statusEl.textContent = 'Added to library. Choose a torrent below.';
    showTorrentModal(payload.media_id, payload.title || item.title || 'Title', payload.candidates || []);
    if (isDiscover) {
      await refreshDiscover();
    }
    addBtn.disabled = false;
  } catch (_error) {
    addBtn.disabled = false;
    statusEl.textContent = 'Add failed.';
  }
}

async function retryLibraryDownloadSearch(options = {}) {
  const item = HERO_STATE.item;
  if (!item || HERO_STATE.source !== 'library' || !item.id) return;
  const allowUpgradeSearch = !!options.allowUpgradeSearch;
  if ((item.media_type || 'movie') !== 'movie') return;
  if (!libraryItemDownloading(item) && !(allowUpgradeSearch && item.upgrade_available)) return;
  const retryBtn = document.getElementById('hero-retry-download-btn');
  const statusEl = document.getElementById('hero-library-status');
  retryBtn.disabled = true;
  statusEl.textContent = 'Searching…';
  try {
    const response = await fetch('/api/library/retry-download/' + item.id, {
      headers: { 'Accept': 'application/json' },
    });
    const payload = await response.json();
    if (payload.error === 'search_unavailable') {
      // Distinct from an empty result set: the search never actually ran.
      statusEl.textContent = 'Search engine unreachable.';
      showToast(payload.message || 'Torrent search engine unreachable.', 'error');
      return;
    }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || 'retry_failed');
    }
    statusEl.textContent = payload.candidates && payload.candidates.length
      ? 'Choose a torrent below.'
      : 'No candidates found right now.';
    showTorrentModal(payload.media_id, payload.title || item.title || 'Title', payload.candidates || []);
  } catch (_error) {
    statusEl.textContent = 'Search failed.';
    showToast('Unable to load torrent candidates.', 'error');
  } finally {
    retryBtn.disabled = false;
  }
}

function showTorrentModal(mediaId, title, candidates) {
  const modal = document.getElementById('torrent-modal');
  const body = document.getElementById('torrent-modal-body');
  const heading = document.getElementById('torrent-modal-title');
  TORRENT_MODAL_STATE.mediaId = mediaId;
  TORRENT_MODAL_STATE.title = title || 'Title';
  heading.textContent = `Choose a torrent · ${TORRENT_MODAL_STATE.title}`;

  if (!candidates.length) {
    body.innerHTML = '<div class="torrent-modal-note">No candidates were found right now. You can run a manual quality check later.</div>';
  } else {
    body.innerHTML = candidates.map((row, idx) => {
      const quality = row.quality ? ` · ${escHtml(row.quality)}` : '';
      const seeds = escHtml(row.seeds || '0');
      const size = escHtml(formatTorrentSize(row.size));
      const pub = escHtml(row.pub_date || '');
      const desc = row.desc_link ? `<a class="torrent-link" target="_blank" rel="noopener noreferrer" href="${escAttr(row.desc_link)}">Open page</a>` : '';
      return `
        <div class="torrent-candidate">
          <div class="torrent-candidate-title">${idx + 1}. ${escHtml(row.name || 'Untitled release')}</div>
          <div class="torrent-candidate-meta">Seeds ${seeds} · Size ${size}${quality}${pub ? ` · ${pub}` : ''}</div>
          <div class="torrent-candidate-actions">
            <button type="button" class="torrent-btn" data-torrent-launch="1" data-link="${escAttr(row.link || '')}" data-desc-link="${escAttr(row.desc_link || '')}">Download</button>
            ${desc}
          </div>
        </div>
      `;
    }).join('');
  }

  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

function closeTorrentModal() {
  const modal = document.getElementById('torrent-modal');
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
}

function metadataFieldState(value, kind = 'text') {
  if (kind === 'number') return Number.isFinite(value) ? 'present' : 'missing';
  if (kind === 'year') return value ? 'present' : 'missing';
  return String(value || '').trim() ? 'present' : 'missing';
}

function metadataFieldRows(item) {
  const fields = [
    { label: 'Title', value: item.title },
    { label: 'IMDb ID', value: item.imdb_id },
    { label: 'TMDB ID', value: item.tmdb_id },
    { label: 'Year', value: item.year, kind: 'year' },
    { label: 'Type', value: item.media_type },
    { label: 'Path', value: item.path },
    { label: 'Poster URL', value: item.poster_url },
    { label: 'Synopsis', value: item.synopsis },
    { label: 'Actors', value: item.actors },
    { label: 'Rating', value: item.rating, kind: 'number' },
    { label: 'Quality', value: item.current_quality },
    { label: 'Subtitles', value: item.subtitles },
  ];

  return fields.map(field => {
    const state = metadataFieldState(field.value, field.kind);
    const valueText = state === 'present' ? String(field.value) : 'Missing';
    return `
      <div class="metadata-row">
        <div class="metadata-label">${escHtml(field.label)}</div>
        <div class="metadata-value" title="${escAttr(valueText)}">${escHtml(valueText)}</div>
        <div class="metadata-state ${state}">${state}</div>
      </div>
    `;
  }).join('');
}

function closeMetadataModal() {
  const modal = document.getElementById('metadata-modal');
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
}

const POSTER_LANGUAGES = [
  ['en', 'English'], ['all', 'Any language'], ['none', 'No text'],
  ['fr', 'French'], ['de', 'German'], ['es', 'Spanish'], ['it', 'Italian'],
  ['ja', 'Japanese'], ['ru', 'Russian'], ['zh', 'Chinese'],
];

function resetPosterPicker(item) {
  const picker = document.getElementById('poster-picker');
  const grid = document.getElementById('poster-grid');
  const note = document.getElementById('poster-picker-note');
  const langSelect = document.getElementById('poster-lang');
  const loadBtn = document.getElementById('poster-load-btn');
  if (!picker) return;

  grid.innerHTML = '';
  // Artwork is not fetched until asked for: TMDB returns 100+ images for some
  // titles, which is a lot to pull just for opening the settings panel.
  if (!item.tmdb_id) {
    note.textContent = 'No TMDB id for this title, so its artwork cannot be looked up.';
    langSelect.style.display = 'none';
    loadBtn.style.display = 'none';
    return;
  }
  note.textContent = 'Load the artwork TMDB has for this title.';
  langSelect.style.display = '';
  loadBtn.style.display = '';
  if (!langSelect.options.length) {
    langSelect.innerHTML = POSTER_LANGUAGES
      .map(([code, label]) => `<option value="${escAttr(code)}">${escHtml(label)}</option>`).join('');
  }
}

async function loadPosterOptions() {
  const item = HERO_STATE.item;
  const grid = document.getElementById('poster-grid');
  const note = document.getElementById('poster-picker-note');
  const langSelect = document.getElementById('poster-lang');
  const loadBtn = document.getElementById('poster-load-btn');
  if (!item || !item.id) return;

  loadBtn.disabled = true;
  note.textContent = 'Loading artwork…';
  grid.innerHTML = '';
  try {
    const url = `/api/library/${item.id}/posters?language=${encodeURIComponent(langSelect.value)}`;
    const response = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'failed');

    const posters = data.posters || [];
    if (!posters.length) {
      note.textContent = 'No artwork in that language.';
      return;
    }
    note.textContent = `${posters.length} to choose from — click one to use it.`;
    const current = String(data.current_poster_url || '');
    grid.innerHTML = posters.map(poster => {
      const isCurrent = current && current === poster.poster_url;
      const tag = poster.language ? poster.language.toUpperCase() : 'No text';
      return `<button type="button" class="poster-option${isCurrent ? ' is-current' : ''}"
                      data-poster-url="${escAttr(poster.poster_url)}"
                      title="${escAttr(`${poster.width}×${poster.height} · rated ${poster.vote_average}`)}">
                <img src="${escAttr(poster.thumb_url)}" alt="" loading="lazy">
                <span class="poster-option-tag">${escHtml(tag)}</span>
              </button>`;
    }).join('');
  } catch (_error) {
    note.textContent = 'Could not load artwork.';
  } finally {
    loadBtn.disabled = false;
  }
}

async function choosePoster(posterUrl) {
  const item = HERO_STATE.item;
  const note = document.getElementById('poster-picker-note');
  if (!item || !item.id || !posterUrl) return;
  note.textContent = 'Saving…';
  try {
    const response = await fetch(`/api/library/${item.id}/poster`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ poster_url: posterUrl }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'failed');

    // Update the card, the hero background and the in-memory item together, so
    // the change shows without a reload. A cache-buster is needed because the
    // poster is cached under the same filename.
    const bust = `${data.poster_url}?v=${Date.now()}`;
    item.poster_url = data.poster_url;
    if (ITEMS[item.id]) ITEMS[item.id].poster_url = data.poster_url;
    const cardImg = document.querySelector(`#card-${item.id} .card-poster img`);
    if (cardImg) cardImg.src = bust;
    const heroBg = document.getElementById('hero-bg');
    if (heroBg) heroBg.style.backgroundImage = `url('${bust}')`;
    note.textContent = 'Artwork updated.';
    showToast('Poster updated.', 'success');
    document.querySelectorAll('.poster-option').forEach(el => {
      el.classList.toggle('is-current', el.dataset.posterUrl === posterUrl);
    });
  } catch (_error) {
    note.textContent = 'Could not save that poster.';
    showToast('Unable to save the poster.', 'error');
  }
}

function openMetadataModal() {
  const item = HERO_STATE.item;
  if (HERO_STATE.source !== 'library' || !item || !item.id) return;

  const modal = document.getElementById('metadata-modal');
  const title = document.getElementById('metadata-modal-title');
  const table = document.getElementById('metadata-modal-table');
  const actions = document.getElementById('metadata-modal-actions');
  const note = document.getElementById('metadata-modal-note');

  title.textContent = 'Settings · ' + (item.title || item.imdb_id || 'Title');
  table.innerHTML = metadataFieldRows(item);
  note.textContent = '';
  resetPosterPicker(item);

  const actionButtons = [];
  actionButtons.push('<button type="button" id="metadata-refresh-btn" class="metadata-action-btn primary">Refresh metadata</button>');
  if (!String(item.current_quality || '').trim() && item.path && !item.file_missing) {
    actionButtons.push('<button type="button" id="metadata-quality-btn" class="metadata-action-btn">Scan quality</button>');
  }
  if (!String(item.subtitles || '').trim() && item.path && !item.file_missing) {
    actionButtons.push('<button type="button" id="metadata-subs-btn" class="metadata-action-btn">Fetch subtitles</button>');
  }
  actions.innerHTML = actionButtons.join('');

  const refreshBtn = document.getElementById('metadata-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      note.textContent = 'Refreshing metadata...';
      const ok = await refreshMetadataForItem(item.id, { button: refreshBtn, noteEl: note });
      if (ok) {
        openMetadataModal();
      }
    });
  }

  const qualityBtn = document.getElementById('metadata-quality-btn');
  if (qualityBtn) {
    qualityBtn.addEventListener('click', async () => {
      qualityBtn.disabled = true;
      note.textContent = 'Scanning quality...';
      await scanQualityForItem(item.id);
      note.textContent = 'Quality scan complete.';
      openMetadataModal();
    });
  }

  const subsBtn = document.getElementById('metadata-subs-btn');
  if (subsBtn) {
    subsBtn.addEventListener('click', async () => {
      subsBtn.disabled = true;
      note.textContent = 'Fetching subtitles...';
      await fetchSubtitles(item.id);
      note.textContent = 'Subtitle fetch complete.';
      openMetadataModal();
    });
  }

  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

/** Update (or remove) the DL badge on a card element without a page reload. */
function setCardDlBadge(mediaId, status, message) {
  const card = document.getElementById('card-' + mediaId);
  if (!card) return;
  const ACTIVE = ['starting', 'handed_off', 'downloading'];
  let badge = card.querySelector('.card-badge-download');
  if (ACTIVE.includes(status)) {
    if (!badge) {
      badge = document.createElement('div');
      badge.className = 'card-badge card-badge-download';
      badge.textContent = 'DL';
      card.querySelector('.card-poster').appendChild(badge);
    }
    badge.title = message || status;
  } else {
    if (badge) badge.remove();
  }
}

async function startTorrentDownload(link, descLink) {
  const response = await fetch('/api/discover/start-download', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      link: link || '',
      desc_link: descLink || '',
      media_id: TORRENT_MODAL_STATE.mediaId,
    }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error('launch_failed');
  }

  if (TORRENT_MODAL_STATE.mediaId && ITEMS[TORRENT_MODAL_STATE.mediaId]) {
    if (payload.mode === 'qbittorrent') {
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_status = 'downloading';
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_source = 'qb_webui';
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_message = payload.save_path
        ? ('Destination: ' + payload.save_path)
        : 'Downloading';
      if (payload.torrent_hash) {
        ITEMS[TORRENT_MODAL_STATE.mediaId].download_torrent_hash = payload.torrent_hash;
      }
    } else {
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_status = 'handed_off';
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_source = 'external_client';
      ITEMS[TORRENT_MODAL_STATE.mediaId].download_message = 'Sent to torrent client';
    }
    const mid = TORRENT_MODAL_STATE.mediaId;
    setCardDlBadge(mid, ITEMS[mid].download_status, ITEMS[mid].download_message);
  }

  if (payload.mode === 'qbittorrent') {
    showToast('Download submitted to qBittorrent.');
    closeTorrentModal();
    window.location.href = '/?section=' + encodeURIComponent(payload.section || 'movies');
    return;
  }

  if (!payload.launch_url) {
    throw new Error('launch_failed');
  }

  window.open(payload.launch_url, '_blank');
  showToast('Handed off to torrent client.');
  closeTorrentModal();
  window.location.href = '/?section=' + encodeURIComponent(payload.section || 'movies');
}

async function loadDiscoverOnce() {
  if (DISCOVER_STATE.loaded || DISCOVER_STATE.loading) return;
  DISCOVER_STATE.loading = true;
  try {
    const response = await fetch('/api/discover', { headers: { 'Accept': 'application/json' } });
    const data = await response.json();
    renderDiscoverStrip(
      'discover-watchlist',
      'discover-watchlist-note',
      data.watchlist || [],
      data.trakt_connected ? 'Your Trakt watchlist is empty.' : 'Connect Trakt to load your watchlist.',
      (data.watchlist || []).length ? `${data.watchlist.length} titles` : ''
    );
    renderCollections(data.collections || []);
    renderDiscoverStrip(
      'discover-trending',
      'discover-trending-note',
      data.trending || [],
      data.tmdb_configured ? 'No trending titles were returned.' : 'Configure TMDB to load trending titles.',
      (data.trending || []).length ? 'This week on TMDB' : ''
    );
    DISCOVER_STATE.loaded = true;
  } catch (_error) {
    document.getElementById('discover-watchlist-note').textContent = 'Failed to load Discover data.';
    document.getElementById('discover-collections-note').textContent = 'Failed to load Discover data.';
    document.getElementById('discover-trending-note').textContent = 'Failed to load Discover data.';
  } finally {
    DISCOVER_STATE.loading = false;
  }
  // Fetched separately: checking every show against TMDB is slower than the
  // rest of Discover, so it must not hold the other blocks up.
  loadMissingEpisodes();
}

async function loadMissingEpisodes() {
  const listEl = document.getElementById('discover-episodes');
  const noteEl = document.getElementById('discover-episodes-note');
  const metaEl = document.getElementById('discover-episodes-meta');
  if (!listEl) return;
  noteEl.textContent = 'Checking your shows for new episodes…';
  listEl.innerHTML = '';

  let data;
  try {
    const response = await fetch('/api/discover/missing-episodes', {
      headers: { 'Accept': 'application/json' },
    });
    data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'failed');
  } catch (_error) {
    noteEl.textContent = 'Could not check for new episodes.';
    return;
  }

  const shows = data.shows || [];
  metaEl.textContent = shows.length
    ? `${data.episode_count} episode${data.episode_count === 1 ? '' : 's'} across ${shows.length} show${shows.length === 1 ? '' : 's'}`
    : '';
  if (!shows.length) {
    noteEl.textContent = 'Every show in your library is up to date.';
    return;
  }
  noteEl.textContent = '';
  listEl.innerHTML = shows.map(showMissingEpisodesMarkup).join('');
}

function showMissingEpisodesMarkup(show) {
  const badge = show.in_production
    ? '<span class="dep-badge dep-badge-airing">Airing</span>'
    : `<span class="dep-badge dep-badge-ended">${escHtml(show.status || 'Ended')}</span>`;
  const next = show.next_air_date
    ? `<span class="dep-count">next episode ${escHtml(show.next_air_date)}</span>`
    : `<span class="dep-count">${show.missing_count} missing</span>`;

  const seasons = (show.seasons || []).map(season => {
    const episodes = season.missing.map(episode => {
      const label = `S${String(season.season_number).padStart(2, '0')}E${String(episode.episode_number).padStart(2, '0')}`;
      const title = episode.title || '';
      return `<span class="dep-ep" role="button" tabindex="0"
                    data-episode-search="${escAttr(show.media_id)}"
                    data-season="${escAttr(season.season_number)}"
                    data-episode="${escAttr(episode.episode_number)}"
                    title="${escAttr(title + ' — click to find a torrent')}">
                <span class="dep-ep-num">${label}</span>${escHtml(title.slice(0, 34))}
              </span>`;
    }).join('');
    return `
      <div class="dep-season">
        <div class="dep-season-head">
          ${escHtml(season.name)} &middot; ${season.owned_count}/${season.episode_count} owned
          <span class="dep-actions">
            <button type="button" class="settings-inline-btn" data-ignore-tv="${escAttr(show.tmdb_id)}"
                    data-ignore-season="${escAttr(season.season_number)}">Hide season</button>
          </span>
        </div>
        <div class="dep-eps">${episodes}</div>
      </div>`;
  }).join('');

  return `
    <div class="dep-show">
      <div class="dep-show-head">
        <span class="dep-show-title">${escHtml(show.title || 'Untitled')}</span>
        ${badge}
        ${next}
        <span class="dep-actions">
          <button type="button" class="settings-inline-btn" data-ignore-tv="${escAttr(show.tmdb_id)}"
                  data-ignore-season="all">Hide show</button>
        </span>
      </div>
      ${seasons}
    </div>`;
}

async function runDiscoverSearch(rawQuery) {
  const input = document.getElementById('discover-search-input');
  const note = document.getElementById('discover-search-note');
  const resultsEl = document.getElementById('discover-search-results');
  const query = (rawQuery || input.value || '').trim();
  if (!query) return;
  input.value = query;
  note.textContent = `Searching for “${query}”…`;
  resultsEl.style.display = 'none';
  resultsEl.innerHTML = '';
  try {
    const response = await fetch('/api/search-imdb?q=' + encodeURIComponent(query), { headers: { 'Accept': 'application/json' } });
    const data = await response.json();
    renderSearchResults(data.query || query, data.results || []);
  } catch (_error) {
    note.textContent = 'Search failed. Check TMDB configuration and try again.';
  }
}

function renderSearchResults(query, results) {
  const note = document.getElementById('discover-search-note');
  const resultsEl = document.getElementById('discover-search-results');
  resultsEl.style.display = 'flex';
  if (!results.length) {
    note.textContent = `No results found for “${query}”.`;
    resultsEl.innerHTML = '';
    return;
  }
  note.textContent = `${results.length} result${results.length === 1 ? '' : 's'} for “${query}”.`;
  resultsEl.innerHTML = results.map(result => {
    const title = result.title || 'Untitled';
    const meta = [result.media_type === 'tv' ? 'TV Show' : 'Movie', result.year].filter(Boolean).join(' · ');
    const poster = result.thumbnail
      ? `<img src="${escAttr(result.thumbnail)}" alt="" loading="lazy">`
      : escHtml(title.charAt(0).toUpperCase() || '?');
    const buttonLabel = result.in_library ? 'Already in library' : 'Add to library';
    return `
      <div class="discover-result-card">
        <div class="discover-result-thumb">${poster}</div>
        <div class="discover-result-body">
          <div class="discover-result-title">${escHtml(title)}</div>
          <div class="discover-result-meta">${escHtml(meta)}</div>
          <form class="discover-add-form">
            <input type="hidden" name="tmdb_id" value="${escAttr(result.tmdb_id)}">
            <input type="hidden" name="media_type" value="${escAttr(result.media_type)}">
            <input type="hidden" name="return_section" value="discover">
            <input type="text" name="current_quality" placeholder="Quality (e.g. 1080p)" style="width:170px;">
            <input type="text" name="path" placeholder="Local path (optional)" style="width:240px;">
            <button type="submit" ${result.in_library ? 'disabled' : ''}>${buttonLabel}</button>
          </form>
          <div class="discover-result-status"></div>
        </div>
      </div>
    `;
  }).join('');
}

async function submitDiscoverAdd(form) {
  const button = form.querySelector('button[type="submit"]');
  const status = form.parentElement.querySelector('.discover-result-status');
  const payload = new FormData(form);
  button.disabled = true;
  status.textContent = 'Adding…';
  try {
    const response = await fetch('/add', {
      method: 'POST',
      body: payload,
      headers: { 'X-Requested-With': 'fetch' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'add_failed');
    }
    button.textContent = 'Added';
    status.textContent = 'Saved to library.';
  } catch (_error) {
    button.disabled = false;
    status.textContent = 'Add failed.';
  }
}

document.getElementById('discover-search-form').addEventListener('submit', event => {
  event.preventDefault();
  runDiscoverSearch();
});

document.querySelectorAll('[data-library-filter-toggle]').forEach(btn => {
  btn.addEventListener('click', () => {
    const sectionId = btn.dataset.libraryFilterToggle;
    const state = LIBRARY_VIEW_STATE[sectionId];
    if (!state) return;
    state.filtersOpen = !state.filtersOpen;
    syncLibraryToolbar(sectionId);
  });
});

document.querySelectorAll('[data-library-filter]').forEach(btn => {
  btn.addEventListener('click', () => {
    const sectionId = btn.dataset.libraryFilter;
    const key = btn.dataset.filterKey;
    const state = LIBRARY_VIEW_STATE[sectionId];
    if (!state || !key) return;
    state[key] = !state[key];
    syncLibraryToolbar(sectionId);
    renderLibrarySection(sectionId);
  });
});

document.querySelectorAll('[data-library-genre-clear]').forEach(btn => {
  btn.addEventListener('click', () => clearGenreFilter(btn.dataset.libraryGenreClear));
});

const seasonSelect = document.getElementById('hero-season-select');
if (seasonSelect) {
  seasonSelect.addEventListener('change', () => {
    const season = seasonSelect.value;
    const item = HERO_STATE.item;
    if (!season || !item || !item.id) return;
    // activateSection, not showSection: the show's hero card stays above the
    // episode list so you can still see what you are browsing.
    activateSection('tv');
    showSeasonEpisodes(item.id, season === 'extras' ? 'extras' : Number(season));
  });
}

const backToShowsBtn = document.getElementById('tv-back-to-shows');
if (backToShowsBtn) backToShowsBtn.addEventListener('click', closeEpisodeView);

const checkNewBtn = document.getElementById('hero-check-new-btn');
if (checkNewBtn) {
  checkNewBtn.addEventListener('click', () => {
    const item = HERO_STATE.item;
    if (!item || !item.id) return;
    // activateSection keeps the hero card in place above the results.
    activateSection('tv');
    showMissingForShow(item.id);
  });
}

async function findEpisodeTorrents(mediaId, season, episode) {
  const noteEl = document.getElementById('discover-episodes-note');
  const label = `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;
  if (noteEl) noteEl.textContent = `Searching for ${label}…`;
  try {
    const url = `/api/tv/${mediaId}/episode-candidates?season=${encodeURIComponent(season)}`
      + `&episode=${encodeURIComponent(episode)}`;
    const response = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const payload = await response.json();
    if (payload.error === 'search_unavailable') {
      // Distinct from "no releases exist": the search never ran.
      if (noteEl) noteEl.textContent = 'Search engine unreachable.';
      showToast('Torrent search engine unreachable.', 'error');
      return;
    }
    if (!response.ok || !payload.ok) throw new Error(payload.error || 'search_failed');
    if (noteEl) {
      noteEl.textContent = (payload.candidates || []).length
        ? '' : `No releases found for ${label}.`;
    }
    showTorrentModal(payload.media_id, payload.title || label, payload.candidates || []);
  } catch (_error) {
    if (noteEl) noteEl.textContent = 'Episode search failed.';
    showToast('Unable to search for that episode.', 'error');
  }
}

// Delegated: the episode rows are rebuilt whenever the block reloads.
document.addEventListener('click', event => {
  const epEl = event.target.closest('[data-episode-search]');
  if (epEl) {
    findEpisodeTorrents(
      Number(epEl.dataset.episodeSearch), Number(epEl.dataset.season), Number(epEl.dataset.episode));
    return;
  }
  const ignoreEl = event.target.closest('[data-ignore-tv]');
  if (ignoreEl) {
    const body = { tmdb_id: Number(ignoreEl.dataset.ignoreTv), season: ignoreEl.dataset.ignoreSeason };
    fetch('/api/discover/ignore-tv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body),
    }).then(() => loadMissingEpisodes());
  }
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const epEl = event.target.closest && event.target.closest('[data-episode-search]');
  if (!epEl) return;
  event.preventDefault();
  findEpisodeTorrents(
    Number(epEl.dataset.episodeSearch), Number(epEl.dataset.season), Number(epEl.dataset.episode));
});

const DISCOVER_COLLAPSED_KEY = 'media-library.discover-collapsed';

function loadCollapsedDiscoverBlocks() {
  try {
    const raw = localStorage.getItem(DISCOVER_COLLAPSED_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch (_error) {
    return new Set();
  }
}

function setDiscoverBlockCollapsed(key, collapsed) {
  const block = document.querySelector(`[data-discover-block="${key}"]`);
  if (!block) return;
  block.classList.toggle('collapsed', collapsed);
  const btn = block.querySelector('.discover-collapse-btn');
  if (!btn) return;
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  const titleEl = block.querySelector('.discover-block-title');
  const title = titleEl ? titleEl.textContent.trim() : '';
  btn.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${title}`.trim());
}

document.querySelectorAll('[data-discover-toggle]').forEach(header => {
  const key = header.dataset.discoverToggle;
  setDiscoverBlockCollapsed(key, loadCollapsedDiscoverBlocks().has(key));
  // The chevron button carries no listener of its own, so a click on it (or
  // Enter/Space, which fires a click) bubbles here and toggles exactly once.
  header.addEventListener('click', () => {
    const stored = loadCollapsedDiscoverBlocks();
    const collapsed = !stored.has(key);
    if (collapsed) stored.add(key); else stored.delete(key);
    try {
      localStorage.setItem(DISCOVER_COLLAPSED_KEY, JSON.stringify([...stored]));
    } catch (_error) {
      // Storage unavailable; the collapse still applies for this page view.
    }
    setDiscoverBlockCollapsed(key, collapsed);
  });
});

const posterLoadBtn = document.getElementById('poster-load-btn');
if (posterLoadBtn) posterLoadBtn.addEventListener('click', loadPosterOptions);
const posterLangSelect = document.getElementById('poster-lang');
if (posterLangSelect) {
  // Changing language reloads only when posters are already on screen, so it
  // does not fire a request the moment the panel opens.
  posterLangSelect.addEventListener('change', () => {
    const grid = document.getElementById('poster-grid');
    if (grid && grid.children.length) loadPosterOptions();
  });
}
const posterGridEl = document.getElementById('poster-grid');
if (posterGridEl) {
  posterGridEl.addEventListener('click', event => {
    const option = event.target.closest('[data-poster-url]');
    if (option) choosePoster(option.dataset.posterUrl);
  });
}

const heroMetadataCard = document.getElementById('hero-card-metadata');
if (heroMetadataCard) {
  heroMetadataCard.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (heroMetadataCard.style.display !== 'none') openMetadataModal();
    }
  });
}

const downloadChipEl = document.getElementById('hero-chip-download');
if (downloadChipEl) {
  const activate = () => {
    // HERO_STATE is cleared by showSection, so read the type before filtering.
    const mediaType = downloadChipEl.dataset.mediaType
      || (HERO_STATE.item || {}).media_type;
    applyDownloadingFilter(mediaType);
  };
  downloadChipEl.addEventListener('click', activate);
  downloadChipEl.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      activate();
    }
  });
}

['hero-chip-genre-1', 'hero-chip-genre-2'].forEach(chipId => {
  const chip = document.getElementById(chipId);
  if (!chip) return;
  const activate = () => {
    const genre = String(chip.textContent || '').trim();
    if (!genre) return;
    applyGenreFilter(genre, (HERO_STATE.item || {}).media_type);
  };
  chip.addEventListener('click', activate);
  chip.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      activate();
    }
  });
});

Object.keys(LIBRARY_VIEW_STATE).forEach(sectionId => {
  const input = document.getElementById(`library-search-input-${sectionId}`);
  if (!input) return;
  input.addEventListener('input', () => {
    LIBRARY_VIEW_STATE[sectionId].query = input.value || '';
    syncLibraryToolbar(sectionId);
    renderLibrarySection(sectionId);
  });
});

document.getElementById('trakt-connect-btn').addEventListener('click', () => {
  startTraktConnect();
});

document.getElementById('trakt-open-btn').addEventListener('click', () => {
  if (TRAKT_STATE.deviceFlow && TRAKT_STATE.deviceFlow.verification_url) {
    window.open(TRAKT_STATE.deviceFlow.verification_url, '_blank', 'noopener');
  }
});

document.getElementById('trakt-copy-btn').addEventListener('click', async () => {
  const code = (TRAKT_STATE.deviceFlow && TRAKT_STATE.deviceFlow.user_code) || '';
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    setTraktInlineStatus('Code copied. Open Trakt, paste or enter it, and keep this page open while it checks for approval.');
  } catch (_error) {
    setTraktInlineStatus('Unable to copy automatically. Enter the displayed code manually on Trakt.', true);
  }
});

document.getElementById('trakt-disconnect-btn').addEventListener('click', () => {
  disconnectTrakt();
});

document.getElementById('trakt-test-btn').addEventListener('click', async () => {
  const btn = document.getElementById('trakt-test-btn');
  const status = document.getElementById('trakt-test-status');
  const form = document.getElementById('trakt_username').form;
  const payload = new FormData(form);
  btn.disabled = true;
  status.textContent = 'Checking Trakt OAuth settings...';
  try {
    const response = await fetch('/api/settings/trakt-test', {
      method: 'POST',
      body: payload,
      headers: { 'Accept': 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'trakt_test_failed');
    }
    status.textContent = 'Trakt credentials are valid. Click Connect Trakt to authorize your account.';
  } catch (_error) {
    status.textContent = 'Connection failed. Check the Trakt client ID, client secret, and network access.';
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('tmdb-test-btn').addEventListener('click', async () => {
  const btn = document.getElementById('tmdb-test-btn');
  const status = document.getElementById('tmdb-test-status');
  const form = document.getElementById('tmdb_api_key').form;
  const payload = new FormData(form);
  btn.disabled = true;
  status.textContent = 'Checking TMDB connection...';
  try {
    const response = await fetch('/api/settings/tmdb-test', {
      method: 'POST',
      body: payload,
      headers: { 'Accept': 'application/json' },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'tmdb_test_failed');
    }
    status.textContent = 'Connected to TMDB.';
  } catch (_error) {
    status.textContent = 'Connection failed. Check the TMDB API key and network access.';
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('qbt-test-btn').addEventListener('click', async () => {
  const btn = document.getElementById('qbt-test-btn');
  const status = document.getElementById('qbt-test-status');
  btn.disabled = true;
  status.textContent = 'Checking qB connection...';
  try {
    const response = await fetch('/api/settings/qbt-test', { headers: { 'Accept': 'application/json' } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || 'qbt_test_failed');
    }
    status.textContent = 'Connected. qB reachable (' + String(payload.torrents_seen || 0) + ' torrents visible).';
  } catch (_error) {
    status.textContent = 'Connection failed. Ensure URL/username are saved and QBT_WEBUI_PASSWORD is set in this app session.';
  } finally {
    btn.disabled = false;
  }
});

Object.keys(LIBRARY_VIEW_STATE).forEach(sectionId => {
  syncLibraryToolbar(sectionId);
  renderLibrarySection(sectionId);
});

renderTraktControls();
if (TRAKT_STATE.deviceFlow) {
  scheduleTraktPoll(TRAKT_STATE.deviceFlow.interval || 5);
}

document.addEventListener('click', event => {
  const launchBtn = event.target.closest('[data-torrent-launch="1"]');
  if (launchBtn) {
    event.preventDefault();
    const link = launchBtn.dataset.link || '';
    const descLink = launchBtn.dataset.descLink || '';
    startTorrentDownload(link, descLink).catch(() => {
      const body = document.getElementById('torrent-modal-body');
      body.insertAdjacentHTML('afterbegin', '<div class="torrent-modal-note">Unable to launch download for the selected candidate.</div>');
    });
    return;
  }

  const unignoreBtn = event.target.closest('.ignored-unignore');
  if (unignoreBtn) {
    event.preventDefault();
    const kind = unignoreBtn.dataset.unignoreKind;
    const key = unignoreBtn.dataset.ignoreKey;
    unignoreDiscoverItem(kind, key).catch(() => {
      if (kind === 'collection') {
        document.getElementById('ignored-collections-list').innerHTML = '<div class="ignored-empty">Unable to remove ignore entry.</div>';
      } else {
        document.getElementById('ignored-titles-list').innerHTML = '<div class="ignored-empty">Unable to remove ignore entry.</div>';
      }
    });
    return;
  }
  const ignoreBtn = event.target.closest('.discover-ignore-btn');
  if (ignoreBtn) {
    event.preventDefault();
    event.stopPropagation();
    const kind = ignoreBtn.dataset.ignoreKind;
    const tmdbId = ignoreBtn.dataset.tmdbId || '';
    const collectionId = ignoreBtn.dataset.collectionId || '';
    const title = ignoreBtn.dataset.title || '';
    const collectionName = ignoreBtn.dataset.collectionName || '';
    ignoreDiscoverItem(kind, tmdbId, collectionId, title, collectionName).catch(() => {
      const note = document.getElementById('discover-collections-note');
      note.textContent = 'Unable to save ignore preference.';
      note.style.display = 'block';
    });
    return;
  }
  const heroTrigger = event.target.closest('.discover-hero-trigger');
  if (heroTrigger) {
    event.preventDefault();
    openDiscoverHero(heroTrigger);
    return;
  }
});

document.addEventListener('submit', event => {
  const form = event.target.closest('.discover-add-form');
  if (!form) return;
  event.preventDefault();
  submitDiscoverAdd(form);
});

document.getElementById('ignored-refresh-btn').addEventListener('click', () => {
  loadIgnoredItemsOnce(true);
});

document.getElementById('ignored-clear-titles-btn').addEventListener('click', () => {
  unignoreAll('title').catch(() => {
    document.getElementById('ignored-titles-list').innerHTML = '<div class="ignored-empty">Unable to clear ignored titles.</div>';
  });
});

document.getElementById('ignored-clear-collections-btn').addEventListener('click', () => {
  unignoreAll('collection').catch(() => {
    document.getElementById('ignored-collections-list').innerHTML = '<div class="ignored-empty">Unable to clear ignored collections.</div>';
  });
});

document.getElementById('torrent-modal-close').addEventListener('click', () => {
  closeTorrentModal();
});

document.getElementById('torrent-modal').addEventListener('click', event => {
  if (event.target.id === 'torrent-modal') {
    closeTorrentModal();
  }
});

document.getElementById('metadata-modal-close').addEventListener('click', () => {
  closeMetadataModal();
});

document.getElementById('metadata-modal').addEventListener('click', event => {
  if (event.target.id === 'metadata-modal') {
    closeMetadataModal();
  }
});

document.getElementById('hero-add-btn').addEventListener('click', () => {
  addDiscoverHeroItem();
});

document.getElementById('hero-add-missing-btn').addEventListener('click', () => {
  addDiscoverHeroItem();
});

document.getElementById('hero-retry-download-btn').addEventListener('click', () => {
  retryLibraryDownloadSearch();
});

document.getElementById('hero-watch-btn').addEventListener('click', () => {
  const item = HERO_STATE.item;
  if (item && item.id) {
    window.location.href = watchUrl(item.id, null);
  }
});

async function autoFinalizeDownload() {
  const item = HERO_STATE.item;
  if (!item || HERO_STATE.source !== 'library' || !item.id) return;
  const statusEl = document.getElementById('hero-library-status');
  statusEl.textContent = 'Marking complete…';
  _stopDlPoll();
  try {
    const response = await fetch('/api/library/mark-downloaded/' + item.id, {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || 'mark_failed');
    if (payload.item && ITEMS[item.id]) {
      Object.assign(ITEMS[item.id], payload.item);
    }
    setCardDlBadge(item.id, null, null);
    _hideDlProgress();
    document.getElementById('hero-chip-download').style.display = 'none';
    document.getElementById('hero-library-actions').style.display = 'none';
    statusEl.textContent = '';
    showToast('Download marked complete.');
    // Refresh hero with updated quality/subtitles.
    if (payload.item) openHeroFromItem(payload.item, null, true);
  } catch (_e) {
    statusEl.textContent = 'Failed to mark complete.';
  }
}

// Circumference of the r=15.5 ring in the SVG above; the stroke is drawn by
// offsetting a dash this long, so the two must stay in step.
const DL_RING_CIRCUMFERENCE = 97.39;

function _hideDlProgress() {
  document.getElementById('hero-dl-ring').classList.remove('is-visible');
}

function _showDlProgress(percent, tooltip) {
  const ring = document.getElementById('hero-dl-ring');
  const fill = document.getElementById('hero-dl-ring-fill');
  const lbl  = document.getElementById('hero-dl-ring-label');
  const pct  = Math.min(100, Math.max(0, percent || 0));
  ring.classList.add('is-visible');
  fill.style.strokeDashoffset = DL_RING_CIRCUMFERENCE * (1 - pct / 100);
  lbl.textContent = Math.round(pct) + '%';
  ring.setAttribute('aria-label', 'Download progress ' + Math.round(pct) + '%');
  ring.title = tooltip || '';
}

function _stopDlPoll() {
  if (_dlPollTimer) { clearTimeout(_dlPollTimer); _dlPollTimer = null; }
}

async function _pollDownloadProgress() {
  const item = HERO_STATE.item;
  if (!item || HERO_STATE.source !== 'library' || !item.id) return;
  const statusEl = document.getElementById('hero-library-status');
  try {
    const response = await fetch('/api/library/download-progress/' + item.id, {
      headers: { 'Accept': 'application/json' },
    });
    if (!response.ok) return;
    const data = await response.json();
    if (!data.ok) return;

    if (data.is_complete) {
      statusEl.textContent = 'Download complete!';
      _showDlProgress(100, '100%');
      await autoFinalizeDownload();
      return;
    }

    // No "downloading…" text: the ring carries the progress and the hero chip
    // already says the item is downloading, so a third indicator is noise.
    if (data.source === 'qbittorrent' && data.progress !== null) {
      const pct = data.progress_percent || 0;
      const eta = data.eta != null && data.eta > 0
        ? ' · ETA ' + _formatEta(data.eta)
        : '';
      _showDlProgress(pct, _capitalise(data.state || 'downloading') + ' — ' + pct.toFixed(1) + '%' + eta);
      statusEl.textContent = '';
    } else {
      _hideDlProgress();
      statusEl.textContent = '';
    }
  } catch (_e) { /* silent */ }
  // Schedule next poll in 30 s only if hero still shows this item.
  if (HERO_STATE.item && HERO_STATE.item.id === item.id) {
    _dlPollTimer = setTimeout(_pollDownloadProgress, 30000);
  }
}

function _formatEta(seconds) {
  if (!seconds || seconds < 0) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

function _capitalise(str) {
  return str ? str.charAt(0).toUpperCase() + str.slice(1) : '';
}

function showToast(message, variant = 'success', timeoutMs = 2400) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const toast = document.createElement('div');
  toast.className = 'toast ' + (variant === 'error' ? 'error' : 'success');
  toast.textContent = message;
  stack.appendChild(toast);

  window.requestAnimationFrame(() => {
    toast.classList.add('show');
  });

  window.setTimeout(() => {
    toast.classList.remove('show');
    window.setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 180);
  }, timeoutMs);
}

document.getElementById('hero-fav-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (HERO_STATE.source !== 'library' || !HERO_STATE.item || !HERO_STATE.item.id) return;

  const form = event.currentTarget;
  const btn = document.getElementById('hero-fav-btn');
  btn.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'fetch',
        'Accept': 'application/json',
      },
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.item) {
      throw new Error('favourite_failed');
    }
    const itemId = HERO_STATE.item.id;
    const merged = { ...(ITEMS[itemId] || HERO_STATE.item), ...payload.item };
    ITEMS[itemId] = merged;
    HERO_STATE.item = merged;
    const card = document.getElementById('card-' + itemId);
    openHeroFromItem(merged, card, true);
    showToast(merged.favourite ? 'Added to favourites.' : 'Removed from favourites.');
  } catch (_error) {
    showToast('Unable to update favourite right now.', 'error');
  } finally {
    btn.disabled = false;
  }
});

async function refreshMetadataForItem(itemId, options = {}) {
  const btn = options.button || null;
  const noteEl = options.noteEl || null;
  const originalText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
  }

  try {
    const response = await fetch('/refresh/' + itemId, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'fetch',
        'Accept': 'application/json',
      },
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.item) {
      throw new Error('refresh_failed');
    }

    const merged = { ...(ITEMS[itemId] || HERO_STATE.item || {}), ...payload.item };
    ITEMS[itemId] = merged;
    if (HERO_STATE.item && HERO_STATE.item.id === itemId) {
      HERO_STATE.item = merged;
    }

    const card = document.getElementById('card-' + itemId);
    openHeroFromItem(merged, card, true);
    if (btn) btn.textContent = 'Refreshed';
    if (noteEl) noteEl.textContent = 'Metadata refreshed.';
    showToast('Metadata refreshed.');
    return true;
  } catch (_error) {
    if (btn) btn.textContent = 'Refresh failed';
    if (noteEl) noteEl.textContent = 'Metadata refresh failed.';
    showToast('Metadata refresh failed.', 'error');
    return false;
  } finally {
    if (btn) {
      window.setTimeout(() => {
        btn.disabled = false;
        btn.textContent = originalText;
      }, 1000);
    }
  }
}

// ── Hero panel

async function fetchSubtitles(mediaId) {
  if (SUBTITLE_FETCH_INFLIGHT.has(mediaId)) return;
  SUBTITLE_FETCH_INFLIGHT.add(mediaId);

  const subsCard = document.getElementById('hero-card-subs');
  const subsVal = document.getElementById('hero-subs-val');
  subsCard.classList.remove('clickable');
  subsCard.onclick = null;
  subsVal.textContent = 'Fetching…';
  try {
    const resp = await fetch(`/api/library/fetch-subtitles/${mediaId}`, {
      method: 'POST',
      headers: { 'X-Requested-With': 'fetch' },
    });
    const data = await resp.json();
    if (data.ok && data.item) {
      const merged = { ...(ITEMS[mediaId] || {}), ...data.item };
      ITEMS[mediaId] = merged;
      HERO_STATE.item = merged;
      const card = document.getElementById('card-' + mediaId);
      openHeroFromItem(merged, card, true, { suppressToggle: true });
      const sectionId = (merged.media_type || 'movie') === 'tv' ? 'tv' : 'movies';
      renderLibrarySection(sectionId);
      showToast('Subtitles fetched.');
    } else {
      subsVal.textContent = 'Fetch';
      subsCard.classList.add('clickable');
      subsCard.onclick = () => fetchSubtitles(mediaId);
      showToast('No subtitles found.', 'error');
    }
  } catch (_) {
    subsVal.textContent = 'Fetch';
    subsCard.classList.add('clickable');
    subsCard.onclick = () => fetchSubtitles(mediaId);
    showToast('Subtitle fetch failed.', 'error');
  } finally {
    SUBTITLE_FETCH_INFLIGHT.delete(mediaId);
    if (HERO_STATE.source === 'library' && HERO_STATE.item && HERO_STATE.item.id === mediaId) {
      openHeroFromItem(HERO_STATE.item, document.getElementById('card-' + mediaId), true, { suppressToggle: true });
    }
  }
}

async function runQualityCheck(mediaId) {
  const upgradeCard = document.getElementById('hero-card-upgrade');
  const upgradeVal = document.getElementById('hero-upgrade-val');
  const oldText = upgradeVal.textContent;
  upgradeCard.style.pointerEvents = 'none';
  upgradeVal.textContent = 'Checking...';
  try {
    const response = await fetch('/check/' + mediaId, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'fetch',
        'Accept': 'application/json',
      },
    });
    const payload = await response.json();
    if (payload.error === 'search_unavailable') {
      // Nothing was recorded, so leave the previous result showing rather
      // than implying the title was checked and found up to date.
      upgradeVal.textContent = oldText;
      showToast(payload.message || 'Torrent search engine unreachable.', 'error');
      return;
    }
    if (!response.ok || !payload.ok || !payload.item) {
      throw new Error('check_failed');
    }
    const merged = { ...(ITEMS[mediaId] || {}), ...payload.item };
    ITEMS[mediaId] = merged;
    HERO_STATE.item = merged;
    const card = document.getElementById('card-' + mediaId);
    try {
      openHeroFromItem(merged, card, true, { suppressToggle: true });
    } catch (_renderError) {
      // Keep the check outcome visible even if a non-critical UI refresh step fails.
      setInfoCard(
        'hero-card-upgrade',
        'hero-upgrade-val',
        merged.upgrade_available ? ('↑ ' + (merged.best_found_quality || 'Available')) : '↑ Check'
      );
    }
    showToast(payload.outcome && payload.outcome.found ? 'Higher quality found.' : 'Quality check complete.');
    if (payload.outcome && payload.outcome.found) {
      retryLibraryDownloadSearch({ allowUpgradeSearch: true }).catch(() => {
        // Ignore modal lookup errors here; they are handled in the retry helper status text.
      });
    }
  } catch (error) {
    console.error('runQualityCheck failed', error);
    upgradeVal.textContent = oldText;
    showToast('Quality check failed.', 'error');
  } finally {
    upgradeCard.style.pointerEvents = '';
  }
}

async function scanQualityForItem(mediaId) {
  try {
    const response = await fetch('/api/quality/' + mediaId + '/scan', {
      method: 'POST',
      headers: {
        'X-Requested-With': 'fetch',
        'Accept': 'application/json',
      },
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.item) {
      throw new Error('scan_failed');
    }
    const merged = { ...(ITEMS[mediaId] || {}), ...payload.item };
    ITEMS[mediaId] = merged;
    HERO_STATE.item = merged;
    const card = document.getElementById('card-' + mediaId);
    try {
      openHeroFromItem(merged, card, true, { suppressToggle: true });
    } catch (_renderError) {
      // Keep the scan outcome visible even if a non-critical UI refresh step fails.
      setInfoCard(
        'hero-card-quality',
        'hero-quality-val',
        merged.current_quality || 'Unknown'
      );
    }
    showToast('Quality scan complete: ' + (merged.current_quality || 'detected'));
  } catch (error) {
    console.error('scanQualityForItem failed', error);
    showToast('Quality scan failed.', 'error');
  }
}

function openHero(el) {
  const id = parseInt(el.dataset.id, 10);
  const item = ITEMS[id];
  if (!item) return;

  openHeroFromItem(item, el, true);
}

function openHeroFromItem(item, el, isLibraryItem, options = {}) {
  const favBtn  = document.getElementById('hero-fav-btn');
  const favForm = document.getElementById('hero-fav-form');
  const upgradeCard = document.getElementById('hero-card-upgrade');
  const metadataCard = document.getElementById('hero-card-metadata');
  const discoverActions = document.getElementById('hero-discover-actions');
  const discoverStatus = document.getElementById('hero-discover-status');
  const libraryActions = document.getElementById('hero-library-actions');
  const libraryStatus = document.getElementById('hero-library-status');
  const addBtn = document.getElementById('hero-add-btn');
  const retryBtn = document.getElementById('hero-retry-download-btn');
  const addMissingBtn = document.getElementById('hero-add-missing-btn');

  HERO_STATE.source = isLibraryItem ? 'library' : 'discover';
  HERO_STATE.item = item;

  if (isLibraryItem) {
    const id = item.id;
    const suppressToggle = !!options.suppressToggle;
    // Toggle off if same card clicked again
    if (activeCardId === id && el && !suppressToggle) { activeCardId = null; el.classList.remove('active-card'); return; }
    activeCardId = id;
    document.querySelectorAll('.card').forEach(c => c.classList.remove('active-card'));
    if (el) {
      el.classList.add('active-card');
    } else {
      const currentCard = document.getElementById('card-' + id);
      if (currentCard) currentCard.classList.add('active-card');
    }
    favForm.style.display = 'block';
    metadataCard.style.display = 'flex';
    metadataCard.classList.add('clickable');
    metadataCard.onclick = () => openMetadataModal();
    discoverActions.style.display = 'none';
    libraryActions.style.display = 'none';
    discoverStatus.textContent = '';
    libraryStatus.textContent = '';
    retryBtn.style.display = 'none';
    retryBtn.disabled = false;
    addBtn.disabled = false;
    addMissingBtn.style.display = 'none';
    addMissingBtn.disabled = false;
  } else {
    activeCardId = null;
    document.querySelectorAll('.card').forEach(c => c.classList.remove('active-card'));
    favForm.style.display = 'none';
    metadataCard.style.display = 'none';
    metadataCard.onclick = null;
    discoverActions.style.display = 'flex';
    libraryActions.style.display = 'none';
    discoverStatus.textContent = '';
    libraryStatus.textContent = '';
    addBtn.disabled = false;
  }

  // Background blur
  const bg = document.getElementById('hero-bg');
  bg.style.backgroundImage = item.poster_url
    ? "url('" + item.poster_url.replace(/'/g, "\\'") + "')"
    : 'linear-gradient(135deg, #1b1035 0%, #3a0033 100%)';

  // Poster
  const img = document.getElementById('hero-poster-img');
  const ph  = document.getElementById('hero-poster-ph');
  if (item.poster_url) {
    img.src = item.poster_url;
    img.style.display = 'block';
    ph.style.display  = 'none';
    img.onerror = () => { img.style.display='none'; ph.style.display='flex'; };
  } else {
    img.style.display = 'none';
    ph.textContent    = (item.title || '?')[0].toUpperCase();
    ph.style.display  = 'flex';
  }

  // Text
  document.getElementById('hero-title').textContent = item.title || '';

  const cy = document.getElementById('hero-chip-year');
  cy.textContent   = item.year ? String(item.year) : '';
  cy.style.display = item.year ? 'inline-block' : 'none';
  document.getElementById('hero-chip-type').textContent    = item.media_type === 'tv' ? 'TV Show' : 'Movie';
  // Plenty of titles genuinely carry a single genre on TMDB, so an absent
  // chip is normal rather than missing data - hide it instead of labelling it.
  const g1 = document.getElementById('hero-chip-genre-1');
  const g2 = document.getElementById('hero-chip-genre-2');
  const genre1 = String(item.genre_1 || '').trim();
  const genre2 = String(item.genre_2 || '').trim();
  [[g1, genre1], [g2, genre2]].forEach(([chip, genre]) => {
    chip.textContent = genre;
    chip.style.display = genre ? 'inline-block' : 'none';
    // Only advertise the chip as a control while it actually holds a genre.
    if (genre) {
      chip.setAttribute('role', 'button');
      chip.setAttribute('tabindex', '0');
      chip.title = `Show all ${genre}`;
    } else {
      chip.removeAttribute('role');
      chip.removeAttribute('tabindex');
      chip.title = '';
    }
  });

  const synEl = document.getElementById('hero-synopsis');
  synEl.textContent = item.synopsis || '';
  synEl.style.display = item.synopsis ? 'block' : 'none';

  const actEl = document.getElementById('hero-actors');
  if (item.actors) {
    actEl.innerHTML = '<span class="hero-actors-label">Cast\u00a0</span>' + escHtml(item.actors);
    actEl.style.display = 'block';
  } else { actEl.style.display = 'none'; }

  // Info cards
  const downloadChip = document.getElementById('hero-chip-download');
  const statusMap = {
    starting: 'Starting',
    handed_off: 'Queued',
    downloading: 'Downloading',
  };
  // Reset the download UI for every hero, not just the downloading branch.
  // Switching from a downloading title to any other one left the progress ring
  // frozen on the previous title's percentage, and a pending poll still queued.
  _stopDlPoll();
  _hideDlProgress();

  if (isLibraryItem && item.download_status && statusMap[item.download_status]) {
    downloadChip.textContent = statusMap[item.download_status];
    downloadChip.style.display = 'inline-block';
    downloadChip.setAttribute('role', 'button');
    downloadChip.setAttribute('tabindex', '0');
    downloadChip.dataset.mediaType = item.media_type || 'movie';
    downloadChip.title = (item.download_message || item.download_status)
      + ' — click to see everything downloading';
    libraryActions.style.display = 'flex';
    if ((item.media_type || 'movie') === 'movie') {
      retryBtn.style.display = 'inline-block';
      retryBtn.textContent = 'Retry / Find Torrent';
    } else {
      retryBtn.style.display = 'none';
    }
    // Begin polling if qB WebUI mode with a hash, else just show buttons.
    libraryStatus.textContent = '';
    if (item.download_source === 'qb_webui' && item.download_torrent_hash) {
      _dlPollTimer = setTimeout(_pollDownloadProgress, 800);
    }
    document.getElementById('hero-watch-btn').style.display = 'none';
  } else {
    downloadChip.style.display = 'none';
    downloadChip.title = '';
    // Stop advertising it as a control while it is hidden.
    downloadChip.removeAttribute('role');
    downloadChip.removeAttribute('tabindex');
    // Show watch button if item has a local path that exists
    if (isLibraryItem && item.path && !item.file_missing) {
      libraryActions.style.display = 'flex';
      document.getElementById('hero-watch-btn').style.display = 'inline-block';
      retryBtn.style.display = 'none';
    } else if (isLibraryItem && item.file_missing) {
      document.getElementById('hero-watch-btn').style.display = 'none';
      retryBtn.style.display = 'none';
      const addMissingBtn = document.getElementById('hero-add-missing-btn');
      addMissingBtn.style.display = 'inline-block';
      addMissingBtn.disabled = !TMDB_CONFIGURED || !item.tmdb_id;
      libraryActions.style.display = 'flex';
      discoverActions.style.display = 'none';
    } else {
      libraryActions.style.display = 'none';
      document.getElementById('hero-watch-btn').style.display = 'none';
      retryBtn.style.display = 'none';
    }
  }

  // A TV show's path is a folder of episodes, so Watch alone cannot say which
  // one to play; the season dropdown is what makes a choice possible.
  if (isLibraryItem && item.media_type === 'tv' && item.path && !item.file_missing) {
    libraryActions.style.display = 'flex';
    loadSeasonsForHero(item);
  } else {
    const seasonEl = document.getElementById('hero-season-select');
    if (seasonEl) {
      seasonEl.style.display = 'none';
      seasonEl.innerHTML = '';
    }
    setAiringUi(false, null, 0);
    setDuplicatesUi(0);
  }

  setInfoCard('hero-card-rating', 'hero-rating-val', item.rating ? item.rating.toFixed(1) + ' / 10' : null);
  if (isLibraryItem) {
    setInfoCard('hero-card-resolution', 'hero-resolution-val', item.current_quality || null);
    const subsCard = document.getElementById('hero-card-subs');
    const subsVal = document.getElementById('hero-subs-val');
    if (item.subtitles) {
      subsVal.textContent = item.subtitles;
      subsCard.style.display = 'flex';
      subsCard.classList.remove('clickable');
      subsCard.onclick = null;
    } else if (SUBTITLE_FETCH_INFLIGHT.has(item.id)) {
      subsVal.textContent = 'Fetching…';
      subsCard.style.display = 'flex';
      subsCard.classList.remove('clickable');
      subsCard.onclick = null;
    } else if (item.path && !item.file_missing) {
      subsVal.textContent = 'Fetch';
      subsCard.style.display = 'flex';
      subsCard.classList.add('clickable');
      subsCard.onclick = () => fetchSubtitles(item.id);
    } else {
      subsCard.style.display = 'none';
      subsCard.classList.remove('clickable');
      subsCard.onclick = null;
    }
    const belowPreferred = qualityRank(item.current_quality) < qualityRank(PREFERRED_QUALITY);
    // Not while a download is running. file_missing is deliberately suppressed
    // for an active download, so without the extra check a title with no file
    // yet reads as "below preferred quality" and is offered an upgrade — and
    // clicking it could start a second download for the same title.
    const shouldShowUpgrade = !item.file_missing
      && !libraryItemDownloading(item)
      && (item.upgrade_available || belowPreferred);
    if (shouldShowUpgrade) {
      setInfoCard(
        'hero-card-upgrade',
        'hero-upgrade-val',
        item.upgrade_available ? ('↑ ' + (item.best_found_quality || 'Available')) : '↑ Check'
      );
    } else {
      upgradeCard.style.display = 'none';
    }

    // Upgrade card acts as the search trigger.
    if (upgradeCard.style.display !== 'none') {
      upgradeCard.style.cursor = 'pointer';
      upgradeCard.title = 'Check for upgrade';
      upgradeCard.onclick = () => {
        runQualityCheck(item.id);
      };
    } else {
      upgradeCard.style.cursor = '';
      upgradeCard.title = '';
      upgradeCard.onclick = null;
    }

    favForm.action = '/favourite/' + item.id;
    favBtn.classList.toggle('active', !!item.favourite);
    favBtn.textContent = item.favourite ? '♥' : '♡';
    favBtn.title = item.favourite ? 'Remove from favourites' : 'Add to favourites';
    favBtn.setAttribute('aria-label', favBtn.title);
  } else {
    document.getElementById('hero-card-resolution').style.display = 'none';
    document.getElementById('hero-card-subs').style.display = 'none';
    upgradeCard.style.display = 'none';
    upgradeCard.style.cursor = '';
    upgradeCard.title = '';
    upgradeCard.onclick = null;
  }

  document.getElementById('hero').classList.add('loaded');
}

function closeHero() {
  _stopDlPoll();
  _hideDlProgress();
  closeMetadataModal();
  document.getElementById('hero').classList.remove('loaded');
  document.getElementById('hero-bg').style.backgroundImage = 'linear-gradient(135deg, #1b1035 0%, #3a0033 100%)';
  document.getElementById('hero-fav-form').style.display = 'none';
  const metadataCard = document.getElementById('hero-card-metadata');
  metadataCard.style.display = 'none';
  metadataCard.onclick = null;
  document.getElementById('hero-discover-actions').style.display = 'none';
  document.getElementById('hero-library-actions').style.display = 'none';
  const heroSeasonSelect = document.getElementById('hero-season-select');
  if (heroSeasonSelect) heroSeasonSelect.style.display = 'none';
  // The airing chip lives among the genre chips, outside hero-library-actions,
  // so hiding that container does not hide it.
  const heroAiringChip = document.getElementById('hero-chip-airing');
  if (heroAiringChip) heroAiringChip.style.display = 'none';
  const heroCheckNewBtn = document.getElementById('hero-check-new-btn');
  if (heroCheckNewBtn) heroCheckNewBtn.style.display = 'none';
  document.querySelectorAll('.card').forEach(c => c.classList.remove('active-card'));
  activeCardId = null;
}

// Escape key closes metadata modal first, then hero.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const modal = document.getElementById('metadata-modal');
  if (modal && modal.classList.contains('open')) {
    closeMetadataModal();
    return;
  }
  closeHero();
});

function setInfoCard(cardId, valId, value) {
  const card = document.getElementById(cardId);
  if (value) {
    document.getElementById(valId).textContent = value;
    card.style.display = 'flex';
  } else {
    card.style.display = 'none';
  }
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Bulk action progress
// These actions are plain form posts that can run for a long time on a large
// library. Show an in-progress bar as soon as one starts; the page reload
// that follows carries the completion status and replaces it.
function showRunningStatus(text) {
  let bar = document.getElementById('status-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'status-bar';
    const main = document.querySelector('.main');
    if (!main) return;
    main.insertBefore(bar, main.firstChild);
  }
  bar.className = 'status-bar status-info';
  bar.innerHTML =
    '<span><span class="status-spinner" aria-hidden="true"></span>' +
    escHtml(text) + '&hellip;</span>';
  bar.setAttribute('role', 'status');
  bar.setAttribute('aria-live', 'polite');
}

document.querySelectorAll('form[data-bulk-label]').forEach(form => {
  form.addEventListener('submit', () => {
    showRunningStatus(form.dataset.bulkLabel || 'Working');
    const clicked = form.querySelector('button[type="submit"]');
    if (clicked) clicked.classList.add('is-running');
    // Deferred so disabling cannot interfere with the submission already
    // under way; blocks a second bulk action from being queued mid-run.
    setTimeout(() => {
      document.querySelectorAll('form[data-bulk-label] button[type="submit"]')
        .forEach(btn => { btn.disabled = true; });
    }, 0);
  });
});
