"""
Kaggle half of the monthly pop-quiz pool refresh (.github/workflows/pop-pool-refresh.yml).

  python scripts/pop-pool-kaggle.py run [--dry-run]     day guard -> budget -> bundle -> push -> wait -> fetch + merge
  python scripts/pop-pool-kaggle.py bundle --dry-run    just build the dataset + kernel folders and show the commands

Kaggle free tier (checked 2026-10-09; Kaggle's docs page did not render, figures from secondary sources, confirm on the
account's settings page): GPU quota ~30 h / week (dynamic, can be higher), resets Saturday 00:00 UTC; one session max 12 h.
The owner's other project uses the same account and has priority on weekends. So:
  * Kaggle work starts only Monday-Thursday (UTC); never Friday-Sunday (a manual run then uses the CPU fallback).
    The schedule is the first Wednesday of the month: late in the quota week, after that weekend's hours were used,
    and the quota resets on the Saturday after.
  * Hard budget: MONTH_GPU_MIN (180 min) of GPU session time per calendar month, one session at most SESSION_MAX_MIN (120 min,
    well under the 12 h limit). Usage is logged in MEMORY_DIR/pop-pool-kaggle.json (GPU quota counts session time, so the
    full session counts). The network work (Danbooru, picture downloads) is done on the GitHub runner beforehand
    (--phase prefetch), so the GPU session only computes; leftovers continue next session / next month (resumable cache).
  * The remaining account quota cannot be read through the Kaggle CLI; a push or run that fails (quota, auth, ...) makes the
    workflow use the CPU fallback on the runner.
Auth: the kaggle CLI reads KAGGLE_API_TOKEN (GitHub secret) or ~/.kaggle/access_token. This script never reads, prints or
writes the token. Account name: KAGGLE_USERNAME.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUIZ = os.path.join(ROOT, 'core', 'quiz')
MODELS = os.environ.get('POP_MODELS') or os.path.join(os.path.expanduser('~'), '.cache', 'pop_quiz_models')
LEDGER = os.path.join(ROOT, os.environ.get('MEMORY_DIR', 'memory'), 'pop-pool-kaggle.json')
MONTH_GPU_MIN = int(os.environ.get('POP_KAGGLE_MONTH_MIN', 180))
SESSION_MAX_MIN = int(os.environ.get('POP_KAGGLE_SESSION_MIN', 120))
DATASET_SLUG, KERNEL_SLUG = 'pop-pool-input', 'pop-pool-refresh'


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def day_ok(now=None):
    """Kaggle only Monday-Thursday UTC (weekday 0-3): never Friday-Sunday (the other project's time)."""
    now = now or datetime.now(timezone.utc)
    return now.weekday() <= 3


def ledger():
    try:
        return json.load(open(LEDGER, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def budget_left(now=None):
    month = (now or datetime.now(timezone.utc)).strftime('%Y-%m')
    used = sum(s.get('minutes', 0) for s in ledger().get(month, {}).get('sessions', []))
    return max(0, MONTH_GPU_MIN - used), used


def record(minutes, status, stats=None):
    lg = ledger()
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    lg.setdefault(month, {'sessions': []})['sessions'].append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                                              'minutes': round(minutes, 1), 'status': status, 'stats': stats})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump(lg, open(LEDGER, 'w', encoding='utf-8'), indent=1)


def kaggle(*args, check=True, capture=True):
    r = subprocess.run(['kaggle', *args], capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args[:3])}: {(r.stderr or r.stdout or '')[-300:]}")
    return r


def bundle(out, minutes, img_cache):
    user = os.environ.get('KAGGLE_USERNAME', '<KAGGLE_USERNAME>')
    if os.path.exists(out):
        shutil.rmtree(out)
    ds, kn = os.path.join(out, 'dataset'), os.path.join(out, 'kernel')
    os.makedirs(ds)
    os.makedirs(kn)
    skip = ('pop2', 'pop2_next', '__pycache__', 'flags', 'emoji', 'voice')
    with tarfile.open(os.path.join(ds, 'code.tar.gz'), 'w:gz') as t:
        t.add(os.path.join(ROOT, 'scripts', 'build-pop-media.py'), 'scripts/build-pop-media.py')
        t.add(os.path.join(ROOT, 'core', 'edit', 'fx_eyes.py'), 'core/edit/fx_eyes.py')
        for dp, dns, fs in os.walk(QUIZ):
            dns[:] = [d for d in dns if d not in skip]
            for f in fs:
                if f.endswith(('.py', '.json')):
                    p = os.path.join(dp, f)
                    t.add(p, os.path.relpath(p, ROOT).replace(os.sep, '/'))
    with tarfile.open(os.path.join(ds, 'state.tar.gz'), 'w:gz') as t:
        t.add(os.path.join(MODELS, 'extras-cache.json'), 'extras-cache.json')
    if img_cache and os.path.isdir(img_cache):
        shutil.copytree(img_cache, os.path.join(ds, 'imgcache'))
    json.dump({'minutes': minutes}, open(os.path.join(ds, 'budget.json'), 'w'))
    json.dump({'title': 'pop-pool-input', 'id': f'{user}/{DATASET_SLUG}', 'licenses': [{'name': 'other'}]},
              open(os.path.join(ds, 'dataset-metadata.json'), 'w'), indent=1)
    shutil.copyfile(os.path.join(ROOT, 'scripts', 'kaggle', 'pop_pool_kernel.py'), os.path.join(kn, 'pop_pool_kernel.py'))
    json.dump({'id': f'{user}/{KERNEL_SLUG}', 'title': 'pop-pool-refresh', 'code_file': 'pop_pool_kernel.py', 'language': 'python',
               'kernel_type': 'script', 'is_private': True, 'enable_gpu': True, 'enable_internet': True,
               'dataset_sources': [f'{user}/{DATASET_SLUG}'], 'competition_sources': [], 'kernel_sources': []},
              open(os.path.join(kn, 'kernel-metadata.json'), 'w'), indent=1)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(ds) for f in fs)
    log(f'bundle: {out} ({size / 1e6:.0f} MB, session time box {minutes} min)')
    return ds, kn, user


def wait(user, timeout_min):
    t_end = time.time() + timeout_min * 60
    status = 'unknown'
    while time.time() < t_end:
        r = kaggle('kernels', 'status', f'{user}/{KERNEL_SLUG}', check=False)
        out = (r.stdout or '').lower()
        status = next((s for s in ('complete', 'error', 'cancel') if s in out), 'running')
        if status != 'running':
            return status
        time.sleep(60)
    return 'timeout'


def merge(result_tar, staging_pop2):
    """Kernel results -> the local cache (kernel keys win: it started from our cache) + new pictures -> staging pop2."""
    tmp = os.path.join(ROOT, 'output', 'kaggle-result')
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(result_tar) as t:
        t.extractall(tmp)
    mine = os.path.join(MODELS, 'extras-cache.json')
    d = json.load(open(mine, encoding='utf-8'))
    d.update(json.load(open(os.path.join(tmp, 'extras-cache.json'), encoding='utf-8')))
    json.dump(d, open(mine + '.tmp', 'w', encoding='utf-8'))
    os.replace(mine + '.tmp', mine)
    n = 0
    for dp, _, fs in os.walk(os.path.join(tmp, 'pop2_out')):
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), os.path.join(tmp, 'pop2_out'))
            os.makedirs(os.path.dirname(os.path.join(staging_pop2, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(dp, f), os.path.join(staging_pop2, rel))
            n += 1
    stats = json.load(open(os.path.join(tmp, 'stats.json')))
    log(f'merged: {len(d)} cache keys, {n} pictures; stats {json.dumps(stats)[:400]}')
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'bundle', 'check'])
    ap.add_argument('--dry-run', action='store_true', help='build the folders, print the commands, push nothing')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output', 'kaggle-bundle'))
    ap.add_argument('--img-cache', default=os.environ.get('POP_IMG_CACHE'))
    ap.add_argument('--staging', default=os.environ.get('POP2_DIR') or os.path.join(QUIZ, 'assets', 'pop2_next'))
    a = ap.parse_args()
    left, used = budget_left()
    minutes = min(SESSION_MAX_MIN, left - 10)
    if a.cmd == 'check':
        print(json.dumps({'day_ok': day_ok(), 'budget_left_min': left, 'used_this_month_min': used, 'session_min': max(0, minutes)}))
        return
    if a.cmd == 'run' and not a.dry_run:
        if not day_ok():
            sys.exit('kaggle: not on Friday-Sunday (the other project has the account): use the CPU fallback')
        if minutes < 20:
            sys.exit(f'kaggle: monthly GPU budget used ({used} of {MONTH_GPU_MIN} min): use the CPU fallback')
        if not os.environ.get('KAGGLE_USERNAME'):
            sys.exit('kaggle: KAGGLE_USERNAME not set: use the CPU fallback')
    ds, kn, user = bundle(a.out, max(20, minutes), a.img_cache)
    cmds = [f'kaggle datasets version -p {ds} -m "pool refresh" --dir-mode tar  (or: datasets create -p {ds} --dir-mode tar the first time)',
            f'kaggle kernels push -p {kn}', f'kaggle kernels status {user}/{KERNEL_SLUG}  (every 60 s)',
            f'kaggle kernels output {user}/{KERNEL_SLUG} -p output/kaggle-out  -> merge result.tar.gz']
    if a.cmd == 'bundle' or a.dry_run:
        print('DRY RUN: nothing pushed. Would run:\n  ' + '\n  '.join(cmds))
        return
    t0 = time.time()
    status = 'push-failed'
    try:
        if kaggle('datasets', 'version', '-p', ds, '-m', 'pool refresh', '--dir-mode', 'tar', check=False).returncode != 0:
            kaggle('datasets', 'create', '-p', ds, '--dir-mode', 'tar')
        time.sleep(90)   # the new dataset version has to be ready before the kernel mounts it
        kaggle('kernels', 'push', '-p', kn)
        status = wait(user, minutes + 30)
        if status != 'complete':
            raise RuntimeError(f'kernel {status}')
        outdir = os.path.join(ROOT, 'output', 'kaggle-out')
        shutil.rmtree(outdir, ignore_errors=True)
        kaggle('kernels', 'output', f'{user}/{KERNEL_SLUG}', '-p', outdir)
        stats = merge(os.path.join(outdir, 'result.tar.gz'), a.staging)
        record((time.time() - t0) / 60, 'complete', stats)
    except Exception as e:  # noqa: BLE001
        record((time.time() - t0) / 60 if status != 'push-failed' else 0, status, {'error': str(e)[:200]})
        sys.exit(f'kaggle failed ({str(e)[:200]}): use the CPU fallback')


if __name__ == '__main__':
    main()
