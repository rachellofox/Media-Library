// Exercises the shipped _showDlProgress/_hideDlProgress against a DOM stub.
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'templates', 'index.html');
const html = fs.readFileSync(path, 'utf8');

const start = html.indexOf('const DL_RING_CIRCUMFERENCE');
const end = html.indexOf('function _stopDlPoll', start);
if (start < 0 || end < 0) { console.error('FAIL: could not locate the ring code'); process.exit(1); }
const source = html.slice(start, end);

class El {
  constructor(id) {
    this.id = id;
    this.style = {};
    this.attrs = {};
    this.title = '';
    this.textContent = '';
    this._cls = new Set();
    this.classList = {
      add: (c) => this._cls.add(c),
      remove: (c) => this._cls.delete(c),
      contains: (c) => this._cls.has(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
}

const els = {
  'hero-dl-ring': new El('hero-dl-ring'),
  'hero-dl-ring-fill': new El('hero-dl-ring-fill'),
  'hero-dl-ring-label': new El('hero-dl-ring-label'),
};
global.document = { getElementById: (id) => els[id] || null };

eval(source);

const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

const ring = els['hero-dl-ring'];
const fill = els['hero-dl-ring-fill'];
const label = els['hero-dl-ring-label'];
const CIRC = 97.39;

check('hidden before any progress', !ring.classList.contains('is-visible'));

_showDlProgress(24, 'Downloading — 24.0% · ETA 5m');
check('ring becomes visible', ring.classList.contains('is-visible'));
check('percentage shown in the middle', label.textContent === '24%', label.textContent);
check('arc offset correct for 24%',
  Math.abs(parseFloat(fill.style.strokeDashoffset) - CIRC * 0.76) < 0.01, fill.style.strokeDashoffset);
check('eta moved to the tooltip', ring.title.includes('ETA 5m'), ring.title);
check('accessible label updated', ring.getAttribute('aria-label') === 'Download progress 24%',
  ring.getAttribute('aria-label'));

_showDlProgress(9.3, '');
check('rounds 9.3 to 9%', label.textContent === '9%', label.textContent);

_showDlProgress(100, '100%');
check('full ring at 100%', Math.abs(parseFloat(fill.style.strokeDashoffset)) < 0.01,
  fill.style.strokeDashoffset);
check('label reads 100%', label.textContent === '100%');

_showDlProgress(0, '');
check('empty ring at 0%', Math.abs(parseFloat(fill.style.strokeDashoffset) - CIRC) < 0.01,
  fill.style.strokeDashoffset);

_showDlProgress(140, '');
check('over 100 clamped', Math.abs(parseFloat(fill.style.strokeDashoffset)) < 0.01);
check('clamped label reads 100%', label.textContent === '100%', label.textContent);

_showDlProgress(-20, '');
check('negative clamped to empty', Math.abs(parseFloat(fill.style.strokeDashoffset) - CIRC) < 0.01);
check('clamped label reads 0%', label.textContent === '0%', label.textContent);

_showDlProgress(null, '');
check('null progress treated as 0%', label.textContent === '0%', label.textContent);

_hideDlProgress();
check('hidden again after _hideDlProgress', !ring.classList.contains('is-visible'));

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${'='.repeat(56)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
process.exit(failed.length ? 1 : 0);
