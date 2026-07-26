// The Downloading chip on the hero must filter the library to what is downloading.
const fs = require('fs');
const html = fs.readFileSync(
  require('path').join(__dirname, '..', 'templates', 'index.html'), 'utf8');

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

const start = html.indexOf('function applyDownloadingFilter');
const source = html.slice(start, html.indexOf('function renderLibrarySection', start));
if (start < 0 || !source.includes('state.downloading')) {
  console.error('FAIL: could not extract applyDownloadingFilter');
  process.exit(1);
}

const LIBRARY_VIEW_STATE = {
  movies: { downloading: false, filtersOpen: false, genre: '' },
  tv: { downloading: false, filtersOpen: false, genre: '' },
};
let section = null;
const rendered = [];
const synced = [];
function showSection(name) { section = name; }
function renderLibrarySection(id) { rendered.push(id); }
function syncLibraryToolbar(id) { synced.push(id); }
eval(source);

console.log('=== filtering ===');
applyDownloadingFilter('movie');
check('sets the movies downloading filter', LIBRARY_VIEW_STATE.movies.downloading === true);
check('switches to the movies section', section === 'movies', section);
check('opens the filter row so it is visible and clearable',
  LIBRARY_VIEW_STATE.movies.filtersOpen === true);
check('re-renders the section', rendered.includes('movies'));
check('syncs the toolbar so the button lights up', synced.includes('movies'));
check('leaves tv untouched', LIBRARY_VIEW_STATE.tv.downloading === false);
check('leaves the genre filter alone', LIBRARY_VIEW_STATE.movies.genre === '');

applyDownloadingFilter('tv');
check('a tv show routes to the tv section',
  LIBRARY_VIEW_STATE.tv.downloading === true && section === 'tv', section);

section = null;
applyDownloadingFilter(undefined);
check('an unknown media type falls back to movies', section === 'movies', section);

console.log('\n=== wiring in the template ===');
check('chip is advertised as a control only while shown',
  /downloadChip\.style\.display = 'inline-block';[\s\S]{0,200}setAttribute\('role', 'button'\)/.test(html));
check('role removed when hidden',
  /downloadChip\.style\.display = 'none';[\s\S]{0,200}removeAttribute\('role'\)/.test(html));
check('media type stashed on the element',
  html.includes("downloadChip.dataset.mediaType = item.media_type || 'movie'"));
check('tooltip explains the click',
  html.includes('click to see everything downloading'));
check('keyboard accessible', /downloadChipEl\.addEventListener\('keydown'/.test(html));
check('reads the type from the element, not stale hero state',
  /downloadChipEl\.dataset\.mediaType[\s\S]{0,80}HERO_STATE/.test(html));
check('only chips with role=button look clickable',
  html.includes('.chip-type[role="button"] { cursor: pointer; }'));

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(56)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
if (failed.length) failed.forEach(([n]) => console.log('  - ' + n));
process.exit(failed.length ? 1 : 0);
