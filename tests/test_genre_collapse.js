// Exercises the shipped genre-filter and discover-collapse logic against a DOM stub.
const fs = require('fs');
// Markup lives in the template, behaviour in the static script; these
// assertions span both, so both are read.
const html = [
  require('path').join(__dirname, '..', 'templates', 'index.html'),
  require('path').join(__dirname, '..', 'static', 'js', 'library.js'),
].map((p) => fs.readFileSync(p, 'utf8')).join('\n');

function slice(startMarker, endMarker) {
  const s = html.indexOf(startMarker);
  const e = html.indexOf(endMarker, s);
  if (s < 0 || e < 0) throw new Error('could not locate ' + startMarker);
  return html.slice(s, e);
}

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

// ---------- genre filtering ----------
const genreSrc = slice('function libraryItemHasGenre', 'function renderLibrarySection');

let shown = [];
const LIBRARY_VIEW_STATE = {
  movies: { query: '', genre: '' },
  tv: { query: '', genre: '' },
};
let currentSection = 'discover';
function showSection(name) { currentSection = name; }
function renderLibrarySection(id) { shown.push(id); }
function syncLibraryToolbar() {}
eval(genreSrc);

console.log('=== genre matching ===');
const item = { genre_1: 'Science Fiction', genre_2: 'Action' };
check('matches first genre', libraryItemHasGenre(item, 'Science Fiction'));
check('matches second genre', libraryItemHasGenre(item, 'Action'));
check('case insensitive', libraryItemHasGenre(item, 'sCiEnCe FiCtIoN'));
check('trims whitespace', libraryItemHasGenre(item, '  Action  '));
check('rejects a non-match', !libraryItemHasGenre(item, 'Horror'));
check('empty genre matches everything', libraryItemHasGenre(item, ''));
check('no partial matching (Action != Act)', !libraryItemHasGenre(item, 'Act'));
check('single-genre item, absent second', libraryItemHasGenre({ genre_1: 'Comedy', genre_2: null }, 'Comedy'));
check('null genres reject', !libraryItemHasGenre({ genre_1: null, genre_2: null }, 'Comedy'));

console.log('\n=== applying and clearing ===');
applyGenreFilter('Horror', 'movie');
check('movie genre stored', LIBRARY_VIEW_STATE.movies.genre === 'Horror');
check('switched to movies section', currentSection === 'movies', currentSection);
check('re-rendered', shown.includes('movies'));

applyGenreFilter('Drama', 'tv');
check('tv routed to the tv section', LIBRARY_VIEW_STATE.tv.genre === 'Drama' && currentSection === 'tv');
check('movies filter untouched', LIBRARY_VIEW_STATE.movies.genre === 'Horror');

applyGenreFilter('  Comedy  ', 'movie');
check('stored genre is trimmed', LIBRARY_VIEW_STATE.movies.genre === 'Comedy',
  JSON.stringify(LIBRARY_VIEW_STATE.movies.genre));

clearGenreFilter('movies');
check('cleared', LIBRARY_VIEW_STATE.movies.genre === '');
check('unknown media_type falls back to movies',
  (applyGenreFilter('Thriller', undefined), currentSection === 'movies'));

// ---------- discover collapse ----------
console.log('\n=== discover collapse ===');
const collapseSrc = slice('const DISCOVER_COLLAPSED_KEY', 'document.querySelectorAll(\'[data-discover-toggle]\')');

const store = {};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
};

class El {
  constructor(cls) {
    this._cls = new Set(cls ? [cls] : []);
    this.attrs = {};
    this.textContent = '';
    this.children = [];
    this.classList = {
      toggle: (c, on) => { if (on) this._cls.add(c); else this._cls.delete(c); },
      contains: (c) => this._cls.has(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  querySelector(sel) {
    if (sel === '.discover-collapse-btn') return this.btn;
    if (sel === '.discover-block-title') return this.titleEl;
    return null;
  }
}

const blocks = {};
for (const [key, title] of [['watchlist', 'Your Watchlist'], ['trending', 'Trending']]) {
  const b = new El('discover-block');
  b.btn = new El();
  b.titleEl = new El();
  b.titleEl.textContent = title;
  blocks[key] = b;
}
global.document = {
  querySelector: (sel) => {
    const m = /\[data-discover-block="(.+?)"\]/.exec(sel);
    return m ? blocks[m[1]] || null : null;
  },
};
eval(collapseSrc);

check('default is expanded', !blocks.watchlist.classList.contains('collapsed'));

setDiscoverBlockCollapsed('watchlist', true);
check('collapsing adds the class', blocks.watchlist.classList.contains('collapsed'));
check('aria-expanded false', blocks.watchlist.btn.getAttribute('aria-expanded') === 'false');
check('aria-label says Expand', blocks.watchlist.btn.getAttribute('aria-label') === 'Expand Your Watchlist',
  blocks.watchlist.btn.getAttribute('aria-label'));

setDiscoverBlockCollapsed('watchlist', false);
check('expanding removes the class', !blocks.watchlist.classList.contains('collapsed'));
check('aria-expanded true', blocks.watchlist.btn.getAttribute('aria-expanded') === 'true');
check('aria-label says Collapse', blocks.watchlist.btn.getAttribute('aria-label') === 'Collapse Your Watchlist');

check('blocks are independent',
  (setDiscoverBlockCollapsed('trending', true),
   blocks.trending.classList.contains('collapsed') && !blocks.watchlist.classList.contains('collapsed')));

store['media-library.discover-collapsed'] = JSON.stringify(['trending']);
check('persisted state reads back', loadCollapsedDiscoverBlocks().has('trending'));
check('unpersisted key absent', !loadCollapsedDiscoverBlocks().has('watchlist'));

store['media-library.discover-collapsed'] = 'not json';
check('corrupt storage degrades to empty', loadCollapsedDiscoverBlocks().size === 0);

check('unknown block key is a no-op',
  (setDiscoverBlockCollapsed('nope', true), true));

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(58)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
if (failed.length) failed.forEach(([n]) => console.log('  - ' + n));
process.exit(failed.length ? 1 : 0);
