// The show's hero card must survive opening a season's episode list, while a
// manual nav click must still reset back to the poster grid.
const fs = require('fs');
// Markup lives in the template, behaviour in the static script; these
// assertions span both, so both are read.
const html = [
  require('path').join(__dirname, '..', 'templates', 'index.html'),
  require('path').join(__dirname, '..', 'static', 'js', 'library.js'),
].map((p) => fs.readFileSync(p, 'utf8')).join('\n');

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

const start = html.indexOf('function activateSection(name)');
const end = html.indexOf('// Show the section selected by the server.', start);
if (start < 0 || end < 0) { console.error('FAIL: could not locate the section functions'); process.exit(1); }
const source = html.slice(start, end);

let closeHeroCalls = 0;
let closeEpisodeViewCalls = 0;
const shownSections = [];
const store = {};

function closeHero() { closeHeroCalls++; }
function closeEpisodeView() { closeEpisodeViewCalls++; }
function loadDiscoverOnce() {}
function loadIgnoredItemsOnce() {}
const SECTIONS = ['discover', 'movies', 'tv', 'favourites', 'settings'];
const LAST_SECTION_KEY = 'media-library.last-section';
global.localStorage = { setItem: (k, v) => { store[k] = v; }, getItem: (k) => store[k] ?? null };

const els = {};
for (const s of SECTIONS) {
  els['section-' + s] = { style: {} };
  els['nav-' + s] = { classList: { add() { this._a = true; }, remove() { this._a = false; } } };
}
global.document = { getElementById: (id) => els[id] || null };

eval(source);

console.log('=== activateSection: hero must survive ===');
activateSection('tv');
check('does NOT close the hero', closeHeroCalls === 0, closeHeroCalls);
check('does NOT reset the episode view', closeEpisodeViewCalls === 0, closeEpisodeViewCalls);
check('tv section shown', els['section-tv'].style.display === 'block');
check('other sections hidden', els['section-movies'].style.display === 'none');
check('last section remembered', store[LAST_SECTION_KEY] === 'tv');

console.log('\n=== showSection: manual nav must reset ===');
showSection('movies');
check('closes the hero', closeHeroCalls === 1, closeHeroCalls);
check('resets the episode view', closeEpisodeViewCalls === 1, closeEpisodeViewCalls);
check('movies section shown', els['section-movies'].style.display === 'block');
check('tv section hidden', els['section-tv'].style.display === 'none');

console.log('\n=== the season handler uses activateSection, not showSection ===');
const handler = html.slice(
  html.indexOf("seasonSelect.addEventListener('change'"),
  html.indexOf('const backToShowsBtn'));
check('handler calls activateSection', handler.includes("activateSection('tv')"), handler.slice(0, 80));
check('handler does not call showSection', !handler.includes("showSection('tv')"));

console.log('\n=== episode rows render a thumbnail slot ===');
const render = html.slice(html.indexOf('async function showSeasonEpisodes'),
                          html.indexOf('async function renderFeaturettes'));
check('uses the TMDB still when present', render.includes('episode.still_url'));
check('falls back to a placeholder div', render.includes('<div class="tv-episode-thumb"></div>'));
check('images lazy-load', render.includes('loading="lazy"'));
check('featurettes are appended after the episodes', render.includes('renderFeaturettes(mediaId'));

const feat = html.slice(html.indexOf('async function renderFeaturettes'),
                        html.indexOf('function libraryItemHasGenre'));
check('featurette section hidden when there are none', feat.includes('if (!files.length) return false;'));
check('featurette label is escaped', feat.includes('escHtml(file.label || file.name)'));
check('raw filename kept as the tooltip', feat.includes('escAttr(file.name)'));
check('featurettes are requested for a specific season',
      feat.includes('unmatched?season=${encodeURIComponent(season)}'), feat.slice(0, 120));
check('caller is told whether anything was added', feat.includes('return true;'));

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(56)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
if (failed.length) failed.forEach(([n]) => console.log('  - ' + n));
process.exit(failed.length ? 1 : 0);
