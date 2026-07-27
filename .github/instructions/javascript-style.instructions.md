---
applyTo: '**/*.js,**/*.html'
description: 'Front-end conventions for Media-Library.'
---

# Front-end Conventions

## Where code lives

- Behaviour goes in `static/js/`. `library.js` for the library page, `player.js`
  for the player.
- Templates hold **markup and the server-injected bootstrap only**. The
  bootstrap is the small inline `<script>` that carries Jinja values — `ITEMS`,
  `PREFERRED_QUALITY`, `TRAKT_STATE`, `mediaId` and so on — because those cannot
  live in a static file.
- Do not put logic back in a template. It cannot be linted, parsed in CI, or
  cached by the browser, and it makes the template unreadable.

The static files are plain classic scripts, loaded after the bootstrap. That
ordering matters: the bootstrap's top-level `const` declarations are what the
static script reads, and the static script's function declarations are what the
inline `onclick` handlers in the markup call.

## Escaping

All values that reach markup go through the helpers, without exception:

- `escHtml(value)` for text — escapes `&`, `<`, `>`
- `escAttr(value)` for attribute values — adds `"`

Attributes are double-quoted, which is what makes `escAttr` sufficient. If you
ever single-quote an attribute, `escAttr` will not protect it.

## Comments

The same rules as Python: explain **why**, not what. No decorative dividers or
section banners.

## Checks

```bash
node --check static/js/library.js     # parses
python tests/run_all.py               # includes the front-end tests
```

The front-end tests read the shipped template *and* the shipped script and
evaluate the real code against a DOM stub, so they break when either is edited
carelessly. That is deliberate — see `tests/README.md`.
