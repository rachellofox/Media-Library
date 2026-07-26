// Runs the REAL bulk-progress code extracted from index.html against a minimal
// DOM stub, so the behaviour is verified rather than eyeballed.
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'templates', 'index.html');
const html = fs.readFileSync(path, 'utf8');

const start = html.indexOf('// ── Bulk action progress');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) { console.error('FAIL: could not locate the bulk-progress block'); process.exit(1); }

// escHtml lives earlier in the same script; pull the real one in rather than
// stubbing it, so escaping is exercised as it actually ships.
const escStart = html.indexOf('function escHtml(str)');
const escEnd = html.indexOf('}', html.indexOf('return String(str)')) + 1;
if (escStart < 0) { console.error('FAIL: could not locate escHtml'); process.exit(1); }
const source = html.slice(escStart, escEnd) + '\n' + html.slice(start, end);

// ---- minimal DOM ----------------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      contains: (c) => this.classList._s.has(c),
    };
    this._className = '';
    this.innerHTML = '';
    this.disabled = false;
    this._listeners = {};
    this.id = '';
    this.type = '';
  }
  set className(v) { this._className = v; }
  get className() { return this._className; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  insertBefore(c) { this.children.unshift(c); return c; }
  addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); }
  dispatch(ev) { (this._listeners[ev] || []).forEach(fn => fn({ type: ev })); }
  querySelector(sel) {
    if (sel === 'button[type="submit"]') return this.children.find(c => c.tagName === 'BUTTON' && c.type === 'submit') || null;
    return null;
  }
}

const registry = { forms: [], byId: {}, main: new El('div') };

function makeForm(action, label) {
  const f = new El('form');
  f.dataset.bulkLabel = label;
  f.action = action;
  const b = new El('button');
  b.type = 'submit';
  f.appendChild(b);
  registry.forms.push(f);
  return f;
}

makeForm('/sync-library', 'Scanning library folders for new titles');
makeForm('/scan-quality', 'Scanning media quality');
makeForm('/scan-subtitles', 'Scanning subtitles');
makeForm('/refresh-all', 'Refreshing metadata from TMDB');

global.document = {
  getElementById: (id) => registry.byId[id] || null,
  createElement: (t) => new El(t),
  querySelector: (sel) => (sel === '.main' ? registry.main : null),
  querySelectorAll: (sel) => {
    if (sel === 'form[data-bulk-label]') return registry.forms;
    if (sel === 'form[data-bulk-label] button[type="submit"]') {
      return registry.forms.map(f => f.querySelector('button[type="submit"]'));
    }
    return [];
  },
};
// insertBefore on main should also register the bar by id
const origInsert = registry.main.insertBefore.bind(registry.main);
registry.main.insertBefore = (c) => { const r = origInsert(c); if (c.id) registry.byId[c.id] = c; return r; };

// ---- load the real code ---------------------------------------------------
eval(source);

// ---- assertions -----------------------------------------------------------
const results = [];
const check = (name, cond, detail = '') => {
  results.push([name, cond]);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond || !detail ? '' : '  -- ' + detail}`);
};

check('no status bar exists before submit', document.getElementById('status-bar') === null);

const qualityForm = registry.forms[1];
qualityForm.dispatch('submit');

const bar = document.getElementById('status-bar');
check('status bar created on submit', !!bar);
check('bar uses the info style', bar && bar.className === 'status-bar status-info', bar && bar.className);
check('bar shows a spinner', bar && bar.innerHTML.includes('status-spinner'));
check('bar shows this action\'s label', bar && bar.innerHTML.includes('Scanning media quality'), bar && bar.innerHTML);
check('label is ellipsised', bar && bar.innerHTML.includes('&hellip;'));
check('bar is announced to screen readers',
  bar && bar.getAttribute('role') === 'status' && bar.getAttribute('aria-live') === 'polite');
check('clicked button marked running',
  qualityForm.querySelector('button[type="submit"]').classList.contains('is-running'));

const btns = document.querySelectorAll('form[data-bulk-label] button[type="submit"]');
check('buttons NOT disabled synchronously (submission must proceed)',
  btns.every(b => b.disabled === false));

setTimeout(() => {
  check('all bulk buttons disabled after the tick', btns.every(b => b.disabled === true));
  check('only the clicked button shows running',
    btns.filter(b => b.classList.contains('is-running')).length === 1);

  // A second submit must reuse the existing bar rather than stacking bars.
  registry.forms[3].dispatch('submit');
  check('re-uses the single status bar', registry.main.children.length === 1,
    'children=' + registry.main.children.length);
  check('bar text updated to the newer action',
    document.getElementById('status-bar').innerHTML.includes('Refreshing metadata from TMDB'));

  const failed = results.filter(([, ok]) => !ok);
  console.log(`\n${'='.repeat(56)}\nPASSED ${results.length - failed.length}   FAILED ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
}, 0);
