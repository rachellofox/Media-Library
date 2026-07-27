# Maintenance scripts

Operator tools, run by hand. They are not imported by the app.

Run from the repo root so the imports resolve:

```bash
python scripts/_plan_tv_naming.py            # dry run
python scripts/_plan_tv_naming.py --apply    # make the changes
```

The leading underscore marks these as internal to the repo rather than a public
interface. It does not mean "do not run".

## Rules these follow

Earned from real incidents, so they are worth keeping:

- **Dry run by default.** Nothing changes without `--apply`. Read the plan first.
- **`os.rename`, never `shutil.move`.** Across a locked file `shutil.move`
  silently degrades to copy-then-delete, which once duplicated 10 GB. A move that
  cannot be done must fail loudly.
- **Recycle, never unlink.** Anything removed goes to the Recycle Bin via
  `send2trash`, so a wrong call is recoverable.
- **Refuse rather than guess.** Where two files could both be the answer, both
  are left alone and the case is reported.

## What each one is for

| Script | Changes files? | Purpose |
| --- | --- | --- |
| `_plan_tv_naming.py` | yes, `--apply` | Brings TV episodes to `Show/Season NN/Show - SxxExx - Title.ext`, carrying subtitles and bonus material with them. Detects which episode ordering a release uses. |
| `_plan_canonical_names.py` | yes, `--apply` | The same for films. |
| `_split_packs.py` | yes, `--apply` | Splits a folder holding several films into one folder per film. |
| `_consolidate_duplicates.py` | yes, `--apply` | Merges duplicate folders for one title, keeping the best copy. |
| `_cleanup_movies.py` | yes, `--apply` | Renames a film's video to match its folder, promotes videos out of nested folders, recycles junk. Leaves `Featurettes`, `Subs`, `Subtitles`, `Extras`, `Bonus` alone. |
| `_qbt_prune_broken.py` | yes, `--apply` | Removes torrents from qBittorrent whose files are gone. Touches qBittorrent, not the library. |
| `_audit_movies.py` | no | Reports odd film folders — misnamed videos, nested videos, junk. |
| `_dbcheck.py` | no | Compares a folder on disk against `library.db`. |
| `_debug_missing.py` | no | Finds library entries by title or path keyword. |
| `_debug_query.py` | no | Poster-fetch diagnostics for a title. |

The four read-only scripts are safe to run at any time. The six that change
files should be read in dry run first — they act on the whole library.

`_cleanup_movies.py` also accepts `--execute`, the flag it originally used, so an
older invocation still applies rather than silently doing nothing.
