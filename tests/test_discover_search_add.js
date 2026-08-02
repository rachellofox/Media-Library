// B-0108.07: a Discover search result must open the hero and pull a torrent,
// the same as every other Discover surface (watchlist, trending, collections)
// — not the standalone form with typed-in quality and a local path that used
// to render here, a leftover from before the hero+torrent flow existed.
//
// Runs the real renderSearchResults() from the shipped script, not a
// reimplementation, so this fails if the markup regresses back to the form.
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'library.js'), 'utf8');

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

// Extracts one function body by counting braces from its opening `{`, rather
// than searching for a second marker — robust regardless of what comment or
// character happens to follow the function in the file.
function extractFunction(signature) {
  const start = source.indexOf(signature);
  if (start < 0) throw new Error(`could not locate ${JSON.stringify(signature)}`);
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces extracting ${JSON.stringify(signature)}`);
}

const body = [
  extractFunction('function escAttr(str)'),
  extractFunction('function escHtml(str)'),
  extractFunction('function renderSearchResults(query, results)'),
].join('\n');

const els = {
  'discover-search-note': { textContent: '' },
  'discover-search-results': { style: {}, innerHTML: '' },
};
global.document = { getElementById: (id) => els[id] || null };

// eslint-disable-next-line no-eval
eval(body + '\nglobal.renderSearchResults = renderSearchResults;');

const addable = {
  tmdb_id: 603, media_type: 'movie', title: 'The Matrix', year: 1999,
  thumbnail: 'https://image.tmdb.org/poster.jpg', in_library: false,
};
const owned = {
  tmdb_id: 604, media_type: 'movie', title: 'Already Here', year: 2001,
  thumbnail: '', in_library: true,
};

global.renderSearchResults('grogu', [addable, owned]);
const html = els['discover-search-results'].innerHTML;

console.log('\n=== 1. No trace of the old manual form ===');
check('no <form> element', !/<form/i.test(html), html);
check('no "Quality" input placeholder', !/Quality \(e\.g/.test(html), html);
check('no "Local path" input placeholder', !/Local path/.test(html), html);
check('no reference to the retired discover-add-form class', !/discover-add-form/.test(html), html);

console.log('\n=== 2. An addable result opens the hero, like every other Discover surface ===');
check('carries the hero-trigger class', /discover-hero-trigger/.test(html));
check('carries the tmdb id', /data-tmdb-id="603"/.test(html), html);
check('carries the media type', /data-media-type="movie"/.test(html), html);
check('carries the title', /data-title="The Matrix"/.test(html), html);
check('carries the year', /data-year="1999"/.test(html), html);
check('is a real button, not a link or bare div (keyboard accessible)', /<button[^>]*discover-hero-trigger/.test(html), html);

console.log('\n=== 3. An already-owned result is not clickable, and says so ===');
const ownedSection = html.slice(html.indexOf('Already Here') - 400, html.indexOf('Already Here') + 200);
check('no hero-trigger on the owned card', !ownedSection.includes('discover-hero-trigger') || !html.includes('data-tmdb-id="604"'));
check('tells the user it is already in the library', /Already in library/.test(html), html);

console.log('\n=== 4. A title with markup-significant characters is escaped ===');
els['discover-search-results'].innerHTML = '';
global.renderSearchResults('test', [
  { tmdb_id: 1, media_type: 'movie', title: '<img src=x onerror=alert(1)>', year: 2020, in_library: false },
]);
const escaped = els['discover-search-results'].innerHTML;
check('the raw tag never appears unescaped', !escaped.includes('<img src=x'), escaped);
check('it was escaped instead', escaped.includes('&lt;img'), escaped);

const failed = results.filter(([, cond]) => !cond);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
