#!/usr/bin/env python3
"""Run the GitHub workflow's jobs locally, as GitHub runs them -- before you push.

Why this exists
---------------
CI was red for many commits (fixed in b4ed9f1) while every local run was green, because the two were not the
same run:

    what we ran locally                     what GitHub runs (.github/workflows/core.yml)
    -------------------------------------   ----------------------------------------------------------
    pytest tests/                           pytest -m "core or base"   (and -m core in `minimal`)
    the dev env: napari, Qt, bioio, h5py,   a fresh venv with ONLY the packages the workflow installs:
      cellpose, torch ... all installed       no napari, no Qt, no bioio, no h5py, no GPU
    the editable source tree                the editable source AND, separately, the built wheel
    the working tree, untracked files too   a clean checkout of the commit (no gitignored or untracked files)
    Windows, cp1252 console                 Linux, UTF-8

A `base` test that reached a Qt module (directly, or through a science module importing the Qt file-I/O
module) passed in the dev env and failed on GitHub. Nothing local could see it.

So this script does not keep its own copy of the CI recipe. It READS core.yml and runs each job's own `run:`
steps, in that job's order, with that job's `env:`. Each job gets its own venv (only what the job installs)
and its own copy of the repo made from the git index (what a checkout of your next commit would contain).
If the workflow changes, this changes with it.

What it still cannot reproduce: Linux. It runs the steps under bash with UTF-8 forced on, which covers the
console-encoding difference, but a path-separator or case-sensitivity bug only shows on GitHub.

Usage::

    python tools/run_ci_locally.py                 # every job
    python tools/run_ci_locally.py core wheel      # just these jobs
    python tools/run_ci_locally.py --fresh         # rebuild the venvs from scratch

Venvs live in ``~/.pycat-ci/<job>`` (a short path: Windows long-path limits break scikit-learn's install in a
deep temp directory). Needs PyYAML and bash (Git Bash on Windows).
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / '.github' / 'workflows' / 'core.yml'
VENV_ROOT = pathlib.Path.home() / '.pycat-ci'


def _load_jobs():
    try:
        import yaml
    except ImportError:
        sys.exit('PyYAML is needed to read the workflow: pip install pyyaml')
    return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']


def _bash():
    found = shutil.which('bash')
    if os.name == 'nt':
        git_bash = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'bin' / 'bash.exe'
        if git_bash.exists():
            found = str(git_bash)        # not WSL's bash.exe, which would run in a different OS
    if not found:
        sys.exit('bash is needed to run the workflow steps (Git Bash on Windows).')
    return found


def _checkout(dest):
    """A copy of what `actions/checkout` of your next commit would contain: the files in the git index, with
    their working-tree content. Gitignored files (dist/, validation/) are absent, as they are on GitHub."""
    tracked = subprocess.run(['git', 'ls-files', '-z', '--cached'], cwd=REPO, capture_output=True,
                             check=True).stdout.decode('utf-8').split('\0')
    for rel in filter(None, tracked):
        src = REPO / rel
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard', 'src', 'tests'], cwd=REPO,
                               capture_output=True, text=True, check=True).stdout.split()
    if untracked:
        print('  NOTE: not in git, so NOT in this run (and not in CI) -- `git add` them if they belong:')
        for rel in untracked:
            print(f'    {rel}')


def _python_version(job):
    for step in job.get('steps', []):
        if str(step.get('uses', '')).startswith('actions/setup-python'):
            return str((step.get('with') or {}).get('python-version', ''))
    return ''


def _venv(name, fresh):
    path = VENV_ROOT / name
    if fresh and path.exists():
        shutil.rmtree(path)
    if not path.exists():
        VENV_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, '-m', 'venv', str(path)], check=True)
    return path


def run_job(name, job, fresh):
    print(f'\n=== {name}: {job.get("name", name)}')
    want = _python_version(job)
    have = f'{sys.version_info.major}.{sys.version_info.minor}'
    if want and want != have:
        print(f'  WARNING: the workflow uses Python {want}; this is {have}. Run this script with {want}.')

    venv = _venv(name, fresh)
    bindir = venv / ('Scripts' if os.name == 'nt' else 'bin')
    with tempfile.TemporaryDirectory(prefix=f'pycat-ci-{name}-') as tmp:
        work = pathlib.Path(tmp)
        _checkout(work)
        env = dict(os.environ, VIRTUAL_ENV=str(venv), PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8', CI='true')
        env.pop('PYTHONPATH', None)
        env.pop('PYCAT_IMAGE_READER', None)
        for key, value in (job.get('env') or {}).items():
            env[key] = str(value)
        # A reused venv still holds the last run's pycat, and pip skips an "already satisfied" wheel of the same
        # version -- the wheel job would test a stale build. CI's venv is always new.
        subprocess.run([str(bindir / 'python'), '-m', 'pip', 'uninstall', '-q', '-y', 'pycat-napari'],
                       env=env, capture_output=True)

        for step in job.get('steps', []):
            if 'run' not in step:
                continue                 # `uses:` steps: checkout and setup-python are what this script replaces
            label = step.get('name', step['run'].splitlines()[0])
            step_env = dict(env, **{k: str(v) for k, v in (step.get('env') or {}).items()})
            t0 = time.time()
            print(f'--- {label}', flush=True)
            # GitHub runs `run:` with `bash --noprofile --norc -eo pipefail`.
            proc = subprocess.run([_bash(), '--noprofile', '--norc', '-eo', 'pipefail', '-c', step['run']],
                                  cwd=work, env=step_env)
            print(f'--- {label}: {"ok" if proc.returncode == 0 else "FAILED"} ({time.time() - t0:.0f} s)')
            if proc.returncode != 0:
                return False
    return True


def main():
    jobs = _load_jobs()
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('jobs', nargs='*', help=f'jobs to run (default all: {", ".join(jobs)})')
    parser.add_argument('--fresh', action='store_true', help='rebuild each job venv from scratch')
    args = parser.parse_args()

    unknown = set(args.jobs) - set(jobs)
    if unknown:
        sys.exit(f'unknown job(s) {sorted(unknown)}; the workflow has {list(jobs)}')
    results = {name: run_job(name, jobs[name], args.fresh) for name in (args.jobs or jobs)}

    print('\n=== summary')
    for name, ok in results.items():
        print(f'  {name:10} {"pass" if ok else "FAIL"}')
    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
