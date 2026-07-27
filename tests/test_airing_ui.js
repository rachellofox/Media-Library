// The airing chip and "Check for new content" button must appear only for shows
// still in production, and must not linger when the hero closes or changes item.
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

const source = html.slice(html.indexOf('function setAiringUi'),
                          html.indexOf('async function loadSeasonsForHero'));

class El {
  constructor() { this.style = {}; this.textContent = ''; this.title = ''; }
}
const els = { 'hero-chip-airing': new El(), 'hero-check-new-btn': new El() };
global.document = { getElementById: (id) => els[id] || null };
eval(source);

const chip = els['hero-chip-airing'];
const btn = els['hero-check-new-btn'];

console.log('=== airing, with gaps ===');
setAiringUi(true, 'Returning Series', 6);
check('chip shown', chip.style.display === 'inline-block', chip.style.display);
check('chip reads Airing', chip.textContent === 'Airing', chip.textContent);
check('chip tooltip carries the TMDB wording',
  chip.title === 'TMDB status: Returning Series', chip.title);
check('button shown', btn.style.display === 'inline-block', btn.style.display);
check('button counts the gaps', btn.textContent === 'Find 6 missing', btn.textContent);

console.log('\n=== ENDED but with gaps: the Hacks case ===');
setAiringUi(false, 'Ended', 10);
check('chip hidden for an ended show', chip.style.display === 'none', chip.style.display);
check('button STILL offered', btn.style.display === 'inline-block', btn.style.display);
check('button counts the gaps', btn.textContent === 'Find 10 missing', btn.textContent);
check('tooltip explains the count',
  btn.title === '10 aired episodes you do not have', btn.title);

console.log('\n=== ended and complete: nothing to offer ===');
setAiringUi(false, 'Ended', 0);
check('chip hidden', chip.style.display === 'none');
check('button hidden', btn.style.display === 'none', btn.style.display);

console.log('\n=== airing but nothing missing today ===');
setAiringUi(true, 'Returning Series', 0);
check('chip shown', chip.style.display === 'inline-block');
check('button still offered, worded as a check',
  btn.style.display === 'inline-block' && btn.textContent === 'Check for new content',
  btn.textContent);

console.log('\n=== singular wording and odd inputs ===');
setAiringUi(false, 'Ended', 1);
check('one missing reads singular', btn.title === '1 aired episode you do not have', btn.title);
setAiringUi(true, null, undefined);
check('no tooltip when status is unknown', chip.title === '', JSON.stringify(chip.title));
check('undefined count treated as zero', btn.textContent === 'Check for new content', btn.textContent);
setAiringUi(false, 'Ended', null);
check('null count hides the button for an ended show', btn.style.display === 'none');

console.log('\n=== wiring in the template ===');
check('chip sits among the hero chips',
  /hero-chip-genre-2[\s\S]{0,400}hero-chip-airing/.test(html));
check('button sits in the hero library actions',
  /hero-season-select[\s\S]{0,300}hero-check-new-btn/.test(html));
check('closeHero hides the chip', /closeHero[\s\S]*?hero-chip-airing/.test(html));
check('closeHero hides the button', /closeHero[\s\S]*?hero-check-new-btn/.test(html));
check('non-TV items reset the airing UI',
  /seasonEl\.innerHTML = ''[\s\S]{0,120}setAiringUi\(false, null, 0\)/.test(html));
check('loadSeasonsForHero resets before fetching',
  /async function loadSeasonsForHero[\s\S]{0,200}setAiringUi\(false, null, 0\)/.test(html));
check('airing state and gap count come from the seasons endpoint',
  /setAiringUi\(!!data\.in_production, data\.status, data\.missing_count\)/.test(html));

const handler = html.slice(html.indexOf('checkNewBtn.addEventListener'),
                           html.indexOf('async function findEpisodeTorrents'));
check('button keeps the hero open (activateSection)', handler.includes("activateSection('tv')"), handler.slice(0, 90));
check('button does not use showSection', !handler.includes("showSection('tv')"));

const render = html.slice(html.indexOf('async function showMissingForShow'),
                          html.indexOf('// Bonus features carry no episode number'));
check('reuses the Discover markup', render.includes('showMissingEpisodesMarkup(data.show)'));
check('says so plainly when nothing is missing', render.includes('Nothing missing'));
check('button re-enabled in finally', /finally \{[\s\S]{0,80}btn\.disabled = false/.test(render));

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(58)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
if (failed.length) failed.forEach(([n]) => console.log('  - ' + n));
process.exit(failed.length ? 1 : 0);
