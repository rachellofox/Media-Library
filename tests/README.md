# Tests

```bash
python tests/run_all.py        # whole suite
python tests/run_all.py -v     # with output from each file
python tests/test_ordering.py  # one file
```

19 files, ~424 assertions. Every file is a standalone script that exits non-zero
on failure, which is why `run_all.py` runs them as subprocesses rather than
importing them.

## What is covered

| Area | Files |
| --- | --- |
| Episode identification and naming | `test_episode_match`, `test_intruder_guard`, `test_ordering`, `test_episodes` |
| Download finalisation and upgrades | `test_finalize`, `test_tv_finalize`, `test_tv_upgrade_hazard`, `test_upgrade_during_download` |
| qBittorrent outages | `test_qbt_outage` |
| Discover and metadata | `test_missing_episodes`, `test_posters` |
| Authentication | `test_auth` |
| Front end (`.js`) | `test_airing_ui`, `test_bulk_progress`, `test_dl_ring`, `test_downloading_chip`, `test_genre_collapse`, `test_hero_persists`, `test_hero_switch_reset` |

The Python tests concentrate on the paths that have caused real data loss —
episode identification, and the finalisation step that moves and retires files.
Those are the ones to keep green.

## Conventions

**Never touch the network.** TMDB is stubbed with a fake client exposing only the
methods under test:

```python
class FakeTmdb:
    def episode_orderings(self, _tv_id):
        return [...]
```

**Never touch the real library or database.** Filesystem tests build a sandbox
under `tempfile.TemporaryDirectory()` and a throwaway SQLite file. Nothing reads
`media.db` or `D:\TV Shows`.

**Front-end tests run the shipped JavaScript.** There is no separate copy: the
`.js` tests read `templates/index.html`, extract the `<script>` block, and `eval`
it against a minimal DOM stub. This means they test what actually ships, and they
break if the template is edited carelessly — which is the point. They need `node`
on PATH; `run_all.py` skips them with a notice if it is missing.

**Assertions are plain.** Each file defines a small `check(name, condition)`
helper and prints `PASS`/`FAIL` lines, then a `PASSED n FAILED n` tally that
`run_all.py` reads. No framework is required to run the suite.

## Known gaps

- Not yet on `pytest` — see Section H of `Common/CodeReview.md`. The current form
  works and is dependency-free; converting is a separate, deliberate step.
- `test_tv_upgrade_hazard.py` does not print the standard tally line, so it shows
  blank in the runner summary despite passing.
- No coverage measurement.
