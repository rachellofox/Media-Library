"""
Run the whole test suite.
Usage: python tests/run_all.py [-v]

Each test file is a standalone script that exits non-zero on failure, so this
runs them as subprocesses and reports a summary. JavaScript tests need node on
PATH and are skipped with a notice if it is missing, rather than failing the run.
"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
VERBOSE = '-v' in sys.argv


def run(command, path):
    result = subprocess.run(command + [path], capture_output=True, text=True,
                            cwd=REPO_ROOT, timeout=600)
    return result.returncode, (result.stdout or '') + (result.stderr or '')


def main():
    node = shutil.which('node')
    names = sorted(n for n in os.listdir(TESTS_DIR)
                   if n.startswith('test_') and n.endswith(('.py', '.js')))

    passed, failed, skipped = [], [], []
    for name in names:
        path = os.path.join(TESTS_DIR, name)
        if name.endswith('.js') and not node:
            skipped.append((name, 'node not on PATH'))
            print(f'SKIP  {name}  (node not on PATH)')
            continue

        command = [node] if name.endswith('.js') else [sys.executable]
        code, output = run(command, path)
        # The suites print their own "PASSED n FAILED n" tally; surface it.
        tally = next((line.strip() for line in reversed(output.splitlines())
                      if 'PASSED' in line or 'FAILED' in line), '')
        if code == 0:
            passed.append(name)
            print(f'PASS  {name:38} {tally}')
        else:
            failed.append((name, output))
            print(f'FAIL  {name:38} {tally}')
        if VERBOSE:
            print(output)

    print(f'\n{"=" * 70}')
    print(f'passed {len(passed)}   failed {len(failed)}   skipped {len(skipped)}')
    for name, reason in skipped:
        print(f'  skipped: {name} — {reason}')
    if failed and not VERBOSE:
        print('\nre-run with -v for output. Failing files:')
        for name, _output in failed:
            print(f'  {name}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
