// Switching the hero from a downloading title to any other must clear the
// progress ring and cancel the pending poll, not leave the old percentage frozen.
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'templates', 'index.html');
const html = fs.readFileSync(path, 'utf8');

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

// The stretch of openHero that decides the download UI.
const regionStart = html.indexOf("const downloadChip = document.getElementById('hero-chip-download')");
const regionEnd = html.indexOf('setInfoCard(', regionStart);
const region = html.slice(regionStart, regionEnd);
if (regionStart < 0 || regionEnd < 0) {
  console.error('FAIL: could not locate the download UI region of openHero');
  process.exit(1);
}

const iReset = region.indexOf('_hideDlProgress()');
const iStop = region.indexOf('_stopDlPoll()');
const iBranch = region.indexOf('if (isLibraryItem && item.download_status');
const iTimer = region.indexOf('setTimeout(_pollDownloadProgress');
const iElse = region.indexOf('} else {');

console.log('=== ordering inside openHero ===');
check('the ring is hidden somewhere in the region', iReset >= 0);
check('the poll is stopped somewhere in the region', iStop >= 0);
check('ring hidden BEFORE the downloading branch', iReset >= 0 && iReset < iBranch,
  `hide@${iReset} branch@${iBranch}`);
check('poll stopped BEFORE the downloading branch', iStop >= 0 && iStop < iBranch,
  `stop@${iStop} branch@${iBranch}`);
check('the new poll timer is armed AFTER the reset', iTimer > iStop && iTimer > iReset,
  `timer@${iTimer} stop@${iStop}`);
check('so the reset cannot cancel the timer it just armed', iTimer > iStop);

console.log('\n=== the reset is unconditional ===');
const beforeBranch = region.slice(0, iBranch);
check('reset sits outside any if/else', !/\bif\s*\(/.test(beforeBranch.slice(beforeBranch.indexOf('_stopDlPoll()'))),
  beforeBranch.slice(beforeBranch.indexOf('_stopDlPoll()')).slice(0, 60));
check('the else branch no longer needs its own reset',
  region.slice(iElse).indexOf('_hideDlProgress()') === -1,
  'else branch still calls _hideDlProgress');
check('no duplicate reset inside the downloading branch',
  region.slice(iBranch, iElse).indexOf('_hideDlProgress()') === -1,
  'downloading branch still resets, which is now redundant');

console.log('\n=== the reset actually clears the ring ===');
class El {
  constructor() {
    this.style = {};
    this.textContent = '';
    this.title = '';
    this._cls = new Set(['is-visible']);
    this.classList = {
      add: (c) => this._cls.add(c),
      remove: (c) => this._cls.delete(c),
      contains: (c) => this._cls.has(c),
    };
  }
  setAttribute(k, v) { this[k] = v; }
}
const els = {
  'hero-dl-ring': new El(),
  'hero-dl-ring-fill': new El(),
  'hero-dl-ring-label': new El(),
};
global.document = { getElementById: (id) => els[id] || null };
let _dlPollTimer = 'pending-timer';
global.clearTimeout = () => { cleared = true; };
let cleared = false;

const ringSrc = html.slice(html.indexOf('const DL_RING_CIRCUMFERENCE'),
                           html.indexOf('async function _pollDownloadProgress'));
eval(ringSrc);

els['hero-dl-ring-label'].textContent = '24%';
check('ring starts visible with a stale percentage',
  els['hero-dl-ring'].classList.contains('is-visible'));
_hideDlProgress();
check('_hideDlProgress removes it from view',
  !els['hero-dl-ring'].classList.contains('is-visible'));
_stopDlPoll();
check('_stopDlPoll clears the pending timer', cleared);
check('timer handle nulled so it cannot be cleared twice', _dlPollTimer === null, _dlPollTimer);

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(58)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
if (failed.length) failed.forEach(([n]) => console.log('  - ' + n));
process.exit(failed.length ? 1 : 0);
