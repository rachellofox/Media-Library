// The front end builds markup by string concatenation, so escaping is the only
// thing standing between a film title and script execution. Titles come from
// TMDB and filenames come off disk, so neither is trusted.
//
// This runs the shipped escHtml/escAttr and asserts the invariants the audit
// (CodeReview I7) relied on, so they stay true rather than being true once.
const fs = require('fs');
const path = require('path');

const JS_DIR = path.join(__dirname, '..', 'static', 'js');
const library = fs.readFileSync(path.join(JS_DIR, 'library.js'), 'utf8');
const player = fs.readFileSync(path.join(JS_DIR, 'player.js'), 'utf8');
const templates = ['index.html', 'player.html', 'login.html']
  .map((f) => fs.readFileSync(path.join(__dirname, '..', 'templates', f), 'utf8'))
  .join('\n');

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

// Pull the real helpers out of the shipped file and run them.
const escStart = library.indexOf('function escHtml(str)');
const escAttrStart = library.indexOf('function escAttr(str)');
if (escStart < 0 || escAttrStart < 0) {
  console.error('FAIL: could not find escHtml/escAttr in library.js');
  process.exit(1);
}
const escHtmlSrc = library.slice(escStart, library.indexOf('}', escStart) + 1);
const escAttrSrc = library.slice(escAttrStart, library.indexOf('}', escAttrStart) + 1);
eval(escHtmlSrc);
eval(escAttrSrc);

console.log('\n=== the helpers neutralise a script tag ===');
const attack = '<script>alert(1)</script>';
check('escHtml removes the angle brackets', !escHtml(attack).includes('<script>'), escHtml(attack));
check('escHtml escapes &', escHtml('a & b') === 'a &amp; b', escHtml('a & b'));
check('escHtml escapes < and >', escHtml('<i>') === '&lt;i&gt;', escHtml('<i>'));
check('escAttr also escapes the double quote', escAttr('a"b') === 'a&quot;b', escAttr('a"b'));
check(
  'a title that closes an attribute cannot add one',
  !escAttr('" onerror="alert(1)').includes('"'),
  escAttr('" onerror="alert(1)'),
);
check('escHtml copes with null', escHtml(null) === 'null', escHtml(null));
check('escAttr copes with undefined', typeof escAttr(undefined) === 'string');

console.log('\n=== escAttr is only sufficient while attributes are double-quoted ===');
// escAttr does not escape the single quote, so a single-quoted attribute taking
// an interpolation would be an escape hatch. There must be none.
const singleQuoted = [library, player, templates]
  .join('\n')
  .match(/[a-zA-Z-]+='\$\{[^}]{1,80}\}/g) || [];
check('no interpolated attribute is single-quoted', singleQuoted.length === 0, singleQuoted.slice(0, 3));

console.log('\n=== no sink that bypasses escaping ===');
for (const sink of ['document.write', 'outerHTML =', 'new Function(', 'srcdoc']) {
  const hits = (library + player).split(sink).length - 1;
  check(`${sink} is not used`, hits === 0, `${hits} occurrences`);
}
// insertAdjacentHTML is allowed only with a literal, never an interpolation.
const dynamicInsert = (library + player).match(/insertAdjacentHTML\([^)]*\$\{/g) || [];
check('insertAdjacentHTML is never given an interpolated string', dynamicInsert.length === 0, dynamicInsert);

// A javascript: URL built from data would execute on click.
const jsUrl = (library + player).match(/["'`]javascript:/g) || [];
check('no javascript: URLs', jsUrl.length === 0, jsUrl);


console.log('\n=== watch URLs encode the episode filename ===');
// End at the closing brace on its own line: the body contains ${...}, so the
// first '}' after the return sits inside a template literal, not at the end.
const wuStart = library.indexOf('function watchUrl(');
const wuEnd = library.indexOf('\n}', wuStart) + 2;
eval(library.slice(wuStart, wuEnd));

check('a plain film has no query', watchUrl(7, null) === '/video/7', watchUrl(7, null));
check(
  'an episode is encoded',
  watchUrl(7, 'Season 01/Show & Co - S01E01.mkv').includes('%26'),
  watchUrl(7, 'Season 01/Show & Co - S01E01.mkv'),
);
check(
  'a hash cannot truncate the URL',
  !watchUrl(7, 'a#b.mkv').includes('#'),
  watchUrl(7, 'a#b.mkv'),
);

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(60)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
failed.forEach(([name]) => console.log('  -', name));
process.exit(failed.length ? 1 : 0);
