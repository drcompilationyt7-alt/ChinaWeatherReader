"""
Kaggle half of the AI sharpening of the sound pool (owner 2026-10-13; runs inside the monthly edit-pool refresh, NOT enabled / pushed).

  python scripts/edit-upscale-kaggle.py check                     day guard + monthly GPU budget (JSON)
  python scripts/edit-upscale-kaggle.py bundle --pool DIR [--dry-run]    build the dataset + kernel folders, print the commands
  python scripts/edit-upscale-kaggle.py run --pool DIR [--dry-run]       day guard -> budget -> bundle -> push -> wait -> output -> merge into DIR

What it does: the NEW sound HIT clips of the refresh staging pool (vis.hit, no `<clip>.up.json` sidecar yet) are packed into a Kaggle dataset
version (<user>/edit-upscale-input); the kernel <user>/edit-upscale-refresh (kernel prefix edit-upscale-) runs core/edit/upscale_pool.py on them
(Real-ESRGAN realesr-animevideov3 x2 on the GPU; the 2.4 MB model is downloaded from the Real-ESRGAN release inside the kernel, never stored in
the repo); the output (result.tar: the sharp clips + sidecars + stats.json) comes back with `kaggle kernels output` and is merged into the pool
in place, each clip replaced only after a size / duration / audio check. The GitHub job then uploads the pool to the `edit-pools` release, so the
daily render finds sharp clips and pays nothing.

Why Kaggle and not a CPU runner (Option A): the model is cheap (SRVGGNetCompact) but 22-83 s per 1080p frame at 2 threads (lab/prep.py notes);
a month of ~50 hit clips x ~60 frames = 3 000 frames = 18-70 CPU hours (6 h job limit -> 3-12 matrix jobs of 2 vCPU, > the free minutes of the
repo); on a T4 / P100 it is ~10-25 frames/s = 2-5 minutes of GPU. So: Kaggle first; the fallback when Kaggle is unavailable (Friday-Sunday = the
owner's other project, no token, budget used, failure) is to leave the clips at 1080p (the renderer still works, just softer) and retry at the next
refresh. A CPU matrix is possible (upscale_pool.py --device cpu --limit N) but not enabled.

Same rules as scripts/pop-pool-kaggle.py: Monday-Thursday UTC only, MONTH_GPU_MIN (90 min) of session time per month, one session <= SESSION_MAX_MIN
(60). Auth: the kaggle CLI reads KAGGLE_API_TOKEN (GitHub secret, to be added by the owner) / KAGGLE_USERNAME (variable); this script never reads, prints
or writes the token. Usage ledger: MEMORY_DIR/edit-upscale-kaggle.json.
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
LEDGER = os.path.join(ROOT, os.environ.get('MEMORY_DIR', 'memory'), 'edit-upscale-kaggle.json')
MONTH_GPU_MIN = int(os.environ.get('EDIT_UPSCALE_MONTH_MIN', 90))
SESSION_MAX_MIN = int(os.environ.get('EDIT_UPSCALE_SESSION_MIN', 60))
DATASET_SLUG, KERNEL_SLUG = 'edit-upscale-input', 'edit-upscale-refresh'
MAX_CLIPS = int(os.environ.get('EDIT_UPSCALE_MAX_CLIPS', 80))
# 2026-10-24: EDIT_UPSCALE_ALL=1 (sound_refresh.py sharpen-staged): every clip of the given pool dir without a sidecar, no
# fresh / vis.hit filter (the staged near misses carry quality.needs_sharpen instead of the visible-source stamp)
TAKE_ALL = os.environ.get('EDIT_UPSCALE_ALL') == '1'


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def day_ok(now=None):
    return (now or datetime.now(timezone.utc)).weekday() <= 3          # Monday-Thursday UTC


def ledger():
    try:
        return json.load(open(LEDGER, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def budget_left(now=None):
    month = (now or datetime.now(timezone.utc)).strftime('%Y-%m')
    used = sum(s.get('minutes', 0) for s in ledger().get(month, {}).get('sessions', []))
    return MONTH_GPU_MIN - used, used


def record(minutes, status, stats=None):
    lg = ledger()
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    lg.setdefault(month, {'sessions': []})['sessions'].append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                                              'minutes': round(minutes, 1), 'status': status, 'stats': stats})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump(lg, open(LEDGER, 'w', encoding='utf-8'), indent=1)


def kaggle(*args, check=True):
    r = subprocess.run(['kaggle', *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args[:3])}: {(r.stderr or r.stdout or '')[-300:]}")
    return r


def pick_clips(pool, limit=MAX_CLIPS):
    """[(relative path under pool, id)] of fresh HIT clips (visible-source stamp) with media on disk and no sharpening sidecar yet."""
    out = []
    for preset in ('sound',):
        pd = os.path.join(pool, preset)
        if not os.path.isdir(pd):
            continue
        for vibe in sorted(os.listdir(pd)):
            mp = os.path.join(pd, vibe, 'clips.json')
            if not os.path.isfile(mp):
                continue
            for it in json.load(open(mp, encoding='utf-8')).get('items', []):
                rel = f"{preset}/{vibe}/{it.get('file')}"
                f = os.path.join(pool, rel)
                want = TAKE_ALL or (it.get('status', 'fresh') == 'fresh' and (it.get('vis') or {}).get('hit'))
                if want and os.path.isfile(f) and not os.path.isfile(f + '.up.json'):
                    out.append((rel, it.get('id')))
    return out[:limit]


def bundle(pool, out, minutes):
    user = os.environ.get('KAGGLE_USERNAME', '<KAGGLE_USERNAME>')
    shutil.rmtree(out, ignore_errors=True)
    ds, kn = os.path.join(out, 'dataset'), os.path.join(out, 'kernel')
    os.makedirs(ds)
    os.makedirs(kn)
    clips = pick_clips(pool)
    with tarfile.open(os.path.join(ds, 'code.tar.gz'), 'w:gz') as t:
        t.add(os.path.join(ROOT, 'core', 'edit', 'upscale_pool.py'), 'core/edit/upscale_pool.py')
    with tarfile.open(os.path.join(ds, 'clips.tar'), 'w') as t:
        for rel, _ in clips:
            t.add(os.path.join(pool, rel), rel)
    json.dump({'minutes': minutes, 'clips': [r for r, _ in clips]}, open(os.path.join(ds, 'budget.json'), 'w'))
    json.dump({'title': 'edit-upscale-input', 'id': f'{user}/{DATASET_SLUG}', 'licenses': [{'name': 'other'}]},
              open(os.path.join(ds, 'dataset-metadata.json'), 'w'), indent=1)
    shutil.copyfile(os.path.join(ROOT, 'scripts', 'kaggle', 'edit_upscale_kernel.py'), os.path.join(kn, 'edit_upscale_kernel.py'))
    json.dump({'id': f'{user}/{KERNEL_SLUG}', 'title': 'edit-upscale-refresh', 'code_file': 'edit_upscale_kernel.py', 'language': 'python',
               'kernel_type': 'script', 'is_private': True, 'enable_gpu': True, 'enable_internet': True,
               'dataset_sources': [f'{user}/{DATASET_SLUG}'], 'competition_sources': [], 'kernel_sources': []},
              open(os.path.join(kn, 'kernel-metadata.json'), 'w'), indent=1)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(ds) for f in fs)
    log(f'bundle: {len(clips)} clips, {size / 1e6:.0f} MB, session time box {minutes} min -> {out}')
    return ds, kn, user, clips


def wait(user, timeout_min):
    t_end = time.time() + timeout_min * 60
    while time.time() < t_end:
        out = (kaggle('kernels', 'status', f'{user}/{KERNEL_SLUG}', check=False).stdout or '').lower()
        st = next((s for s in ('complete', 'error', 'cancel') if s in out), 'running')
        if st != 'running':
            return st
        time.sleep(60)
    return 'timeout'


def _probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height:format=duration', '-of', 'json', path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout or '{}')
    s = (d.get('streams') or [{}])[0]
    a = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path],
                       capture_output=True, text=True).stdout.strip()
    return int(s.get('width') or 0), int(s.get('height') or 0), float((d.get('format') or {}).get('duration') or 0), bool(a)


def merge(result_tar, pool):
    """Replace pool clips by the sharp ones from the kernel's result.tar after a size / duration / audio check (a failed one stays as it was)."""
    tmp = os.path.join(os.path.dirname(os.path.abspath(result_tar)), 'result-extract')
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(result_tar) as t:
        t.extractall(tmp)
    ok = bad = 0
    for dp, _, fs in os.walk(tmp):
        for f in fs:
            if not f.endswith('.mp4'):
                continue
            new = os.path.join(dp, f)
            rel = os.path.relpath(new, tmp).replace(os.sep, '/')
            old = os.path.join(pool, rel)
            if not os.path.isfile(old):
                bad += 1
                continue
            (w0, h0, d0, a0), (w1, h1, d1, a1) = _probe(old), _probe(new)
            if w1 == 2 * w0 and h1 == 2 * h0 and abs(d1 - d0) < 0.25 and (a1 or not a0):
                os.replace(new, old)
                side = new + '.up.json'
                with open(old + '.up.json', 'w') as fh:
                    json.dump({'src': [w0, h0], 'dst': [w1, h1], 'model': 'realesr-animevideov3', 'where': 'kaggle'}, fh)
                ok += 1
            else:
                bad += 1
                log('merge: rejected', rel, (w0, h0, d0), (w1, h1, d1))
    shutil.rmtree(tmp, ignore_errors=True)
    log(f'merge: {ok} clips replaced by their sharp version, {bad} rejected / missing')
    return {'replaced': ok, 'rejected': bad}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['run', 'bundle', 'check'])
    ap.add_argument('--pool', default=os.path.join(ROOT, 'output', 'edit-pools-next'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--out', default=os.path.join(ROOT, 'output', 'kaggle-upscale-bundle'))
    a = ap.parse_args()
    left, used = budget_left()
    minutes = min(SESSION_MAX_MIN, left - 5)
    if a.cmd == 'check':
        print(json.dumps({'day_ok': day_ok(), 'budget_left_min': left, 'used_this_month_min': used, 'session_min': max(0, minutes),
                          'clips': len(pick_clips(a.pool))}))
        return
    if a.cmd == 'run' and not a.dry_run:
        if not day_ok():
            sys.exit('kaggle: Friday-Sunday belong to the owner\'s other project: clips stay 1080p until the next refresh')
        if minutes < 10:
            sys.exit(f'kaggle: monthly GPU budget used ({used} of {MONTH_GPU_MIN} min): clips stay 1080p')
        if not os.environ.get('KAGGLE_USERNAME'):
            sys.exit('kaggle: KAGGLE_USERNAME not set: clips stay 1080p')
    ds, kn, user, clips = bundle(a.pool, a.out, max(10, minutes))
    if not clips:
        print('nothing to sharpen (no new hit clips without a sidecar)')
        return
    cmds = [f'kaggle datasets version -p {ds} -m "upscale" --dir-mode tar  (or: datasets create the first time)', f'kaggle kernels push -p {kn}',
            f'kaggle kernels status {user}/{KERNEL_SLUG}  (every 60 s)', f'kaggle kernels output {user}/{KERNEL_SLUG} -p output/kaggle-upscale-out  -> merge result.tar'
            f' into {a.pool}']
    if a.cmd == 'bundle' or a.dry_run:
        print('DRY RUN: nothing pushed. Would run:\n  ' + '\n  '.join(cmds))
        return
    t0 = time.time()
    status = 'push-failed'
    try:
        if kaggle('datasets', 'version', '-p', ds, '-m', 'upscale', '--dir-mode', 'tar', check=False).returncode != 0:
            kaggle('datasets', 'create', '-p', ds, '--dir-mode', 'tar')
        time.sleep(90)
        kaggle('kernels', 'push', '-p', kn)
        status = wait(user, minutes + 20)
        if status != 'complete':
            raise RuntimeError(f'kernel {status}')
        outdir = os.path.join(ROOT, 'output', 'kaggle-upscale-out')
        shutil.rmtree(outdir, ignore_errors=True)
        kaggle('kernels', 'output', f'{user}/{KERNEL_SLUG}', '-p', outdir)
        stats = merge(os.path.join(outdir, 'result.tar'), a.pool)
        record((time.time() - t0) / 60, 'complete', stats)
    except Exception as e:  # noqa: BLE001
        record((time.time() - t0) / 60 if status != 'push-failed' else 0, status, {'error': str(e)[:200]})
        sys.exit(f'kaggle failed ({str(e)[:200]}): the clips stay 1080p')


if __name__ == '__main__':
    main()
