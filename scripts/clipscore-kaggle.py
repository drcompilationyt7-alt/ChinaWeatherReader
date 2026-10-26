"""
Kaggle GPU SCORING of the staged edit-pool candidates (Zero Yen Otaku; owner OK 2026-10-22). GitHub mines, downloads and cuts the
clips and stages them on the release `edit-pools-staging`; this driver sends the kernel `<user>/zyo-clipscore` the PUBLIC URLs of the
staged tars (no dataset, no GitHub token, no cookies, no YouTube on Kaggle), waits, fetches feats.json and turns it into one
`clipscore-<tar stem>.json` per tar (core/pools/clipscore.py verdict: clip id -> score, pass / hard fail, reasons), uploaded back to
the staging release. The pull-staged reviews read them (hard fails listed apart, sorted by score, scores on the sheets); NOTHING is
imported automatically: the final Claude review stays.

  python scripts/clipscore-kaggle.py check                        day guard + weekly GPU budget + tars waiting (JSON)
  python scripts/clipscore-kaggle.py run [--dry-run] [--no-upload] [--tars a.tar,b.tar]
  python scripts/clipscore-kaggle.py calib --labels L.json --sets S.json [--dry-run]   labelled features -> output/clipscore/calib-feats.json
  python scripts/clipscore-kaggle.py rescore                      re-apply the thresholds to the uploaded scores (no GPU)

Rules (hard): Kaggle work starts only Monday-Thursday (UTC AND the owner's US Eastern clock: weekends belong to the owner's other
project); never touches a kernel / dataset outside the zyo-clipscore prefix (cwr-* / apq-* are other projects); weekly cap
ZYO_WEEK_MIN (1200 min = 20 GPU h, so >= 10 h of the ~30 h free quota stay for other uses), one session <= ZYO_SESSION_MIN (40).
The ledger (every session, minutes = push -> complete wall time, an upper bound of the GPU time) lives on the staging release as
clipscore-ledger.json (+ a local copy in memory/), so CI and the PC share it. Auth: the kaggle CLI reads KAGGLE_API_TOKEN (GitHub
secret) or ~/.kaggle/access_token; this script never reads, prints or writes the token.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core', 'pools'))
import clipscore as CS  # noqa: E402

REPO = os.environ.get('GITHUB_REPOSITORY', 'drcompilationyt7-alt/ChinaWeatherReader')
STAGE_TAG = os.environ.get('EDIT_POOLS_STAGING_TAG', 'edit-pools-staging')
KERNEL_SLUG = 'zyo-clipscore'
PREFIX = 'zyo-clipscore'
WEEK_MIN = float(os.environ.get('ZYO_WEEK_MIN', 1200))
SESSION_MIN = float(os.environ.get('ZYO_SESSION_MIN', 40))
LEDGER_ASSET = 'clipscore-ledger.json'
LEDGER_LOCAL = os.path.join(ROOT, os.environ.get('MEMORY_DIR', 'memory'), 'clipscore-kaggle.json')
OUT = os.path.join(ROOT, 'output', 'clipscore')
KAGGLE = os.environ.get('KAGGLE_EXE') or shutil.which('kaggle') or 'kaggle'
if os.name == 'nt' and not os.environ.get('KAGGLE_EXE'):
    _k = os.path.expanduser('~/AppData/Local/Programs/Python/Python311/Scripts/kaggle.exe')
    KAGGLE = _k if os.path.exists(_k) else KAGGLE


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ guards
def day_ok(now=None):
    """Monday-Thursday on BOTH clocks: UTC and the owner's US Eastern time (UTC-5 / -4; -5 is the safe side for the Sunday edge)"""
    now = now or datetime.now(timezone.utc)
    east = now - timedelta(hours=5)
    return now.weekday() <= 3 and east.weekday() <= 3


def week_key(now=None):
    y, w, _ = (now or datetime.now(timezone.utc)).isocalendar()
    return f'{y}-W{w:02d}'


def _gh(args, check=False, timeout=600):
    return subprocess.run(['gh'] + args, capture_output=True, text=True, check=check, timeout=timeout)


def ledger():
    tmp = os.path.join(OUT, '_ledger')
    os.makedirs(tmp, exist_ok=True)
    r = _gh(['release', 'download', STAGE_TAG, '--repo', REPO, '--pattern', LEDGER_ASSET, '--dir', tmp, '--clobber'])
    for p in (os.path.join(tmp, LEDGER_ASSET), LEDGER_LOCAL):
        if (r.returncode == 0 or p == LEDGER_LOCAL) and os.path.isfile(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except ValueError:
                pass
    return {}


def other_minutes(now=None, shared=None):
    """GPU minutes the AI-art driver logged only in its local file (memory/ai-art-kaggle.json) this week; skipped when the shared
    ledger already carries apq-ai-art sessions (no double count). The owner's cwr-* use is outside our cap by design."""
    if any(s.get('job') == 'apq-ai-art' for s in (shared or [])):
        return 0.0
    try:
        return sum(s.get('minutes', 0) for s in json.load(open(os.path.join(ROOT, 'memory', 'ai-art-kaggle.json'))).get(week_key(now), []))
    except (OSError, ValueError, AttributeError):
        return 0.0


def budget(now=None):
    """the SHARED weekly ledger: every job (zyo-clipscore, sound-sharpen, apq-ai-art) counts against ZYO_WEEK_MIN"""
    wk = ledger().get(week_key(now), [])
    used = sum(s.get('minutes', 0) for s in wk) + other_minutes(now, wk)
    return WEEK_MIN - used, used


def record(minutes, status, stats=None, upload=True):
    lg = ledger()
    lg.setdefault(week_key(), []).append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'job': 'zyo-clipscore', 'minutes': round(minutes, 1),
                                          'gpu_hours': round(minutes / 60, 3), 'status': status, 'stats': stats})
    lg['_week_totals'] = {k: round(sum(s.get('minutes', 0) for s in v) / 60, 2) for k, v in lg.items() if k.startswith('20')}
    os.makedirs(os.path.dirname(LEDGER_LOCAL), exist_ok=True)
    json.dump(lg, open(LEDGER_LOCAL, 'w', encoding='utf-8'), indent=1)
    if upload:
        p = os.path.join(OUT, LEDGER_ASSET)
        os.makedirs(OUT, exist_ok=True)
        json.dump(lg, open(p, 'w', encoding='utf-8'), indent=1)
        _gh(['release', 'upload', STAGE_TAG, p, '--clobber', '--repo', REPO])


# ------------------------------------------------------------------ kaggle
def kaggle(*args, check=True):
    assert not any(str(a).startswith(('cwr-', 'apq-')) or '/cwr-' in str(a) or '/apq-' in str(a) for a in args), 'not our kernel'
    r = subprocess.run([KAGGLE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(map(str, args[:2]))}: {(r.stderr or r.stdout or '')[-300:]}")
    return r


def username():
    u = os.environ.get('KAGGLE_USERNAME')
    if u:
        return u
    r = kaggle('kernels', 'list', '--mine', '--page-size', '5', check=False)
    m = re.search(r'^([\w-]+)/', r.stdout or '', re.M)
    return m.group(1) if m else None


def staged_tars():
    r = _gh(['release', 'view', STAGE_TAG, '--repo', REPO, '--json', 'assets', '-q', '.assets[].name'])
    names = r.stdout.split() if r.returncode == 0 else []
    tars = [n for n in names if re.match(r'^(slowmo|sound)-cand-.*\.tar$', n)]
    scored = {n for n in names if n.startswith(CS.SCORE_PREFIX)}
    return tars, scored, names


def asset_url(name, tag=STAGE_TAG):
    return f'https://github.com/{REPO}/releases/download/{tag}/{name}'


def build_kernel(config, out_dir, user):
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    src = open(os.path.join(ROOT, 'scripts', 'kaggle', 'clipscore_kernel.py'), encoding='utf-8').read()
    blob = json.dumps(config, ensure_ascii=True)
    assert "'''" not in blob
    open(os.path.join(out_dir, 'clipscore_kernel.py'), 'w', encoding='utf-8').write(src.replace('__CONFIG__', blob, 1))
    json.dump({'id': f'{user}/{KERNEL_SLUG}', 'title': KERNEL_SLUG, 'code_file': 'clipscore_kernel.py', 'language': 'python',
               'kernel_type': 'script', 'is_private': True, 'enable_gpu': True, 'enable_internet': True, 'enable_tpu': False,
               'dataset_sources': [], 'competition_sources': [], 'kernel_sources': [], 'model_sources': []},
              open(os.path.join(out_dir, 'kernel-metadata.json'), 'w'), indent=1)
    return out_dir


def wait(user, timeout_min):
    t_end = time.time() + timeout_min * 60
    time.sleep(60)                                     # the previous version's status lingers right after a push
    while time.time() < t_end:
        out = (kaggle('kernels', 'status', f'{user}/{KERNEL_SLUG}', check=False).stdout or '').lower()
        st = next((s for s in ('complete', 'error', 'cancel') if s in out), 'running')
        if st != 'running':
            return st
        time.sleep(45)
    return 'timeout'


def session(config, tag, dry=False):
    """push the kernel with CONFIG, wait, fetch feats.json; returns (feats, stats, minutes)"""
    user = username()
    if not user:
        raise RuntimeError('no Kaggle username (KAGGLE_USERNAME or a working token)')
    kn = build_kernel(config, os.path.join(OUT, 'kernel'), user)
    if dry:
        print(f'DRY RUN: kernel folder {kn}; would run: kaggle kernels push -p {kn}; status / output {user}/{KERNEL_SLUG}')
        return None, None, 0.0
    t0 = time.time()
    status = 'push-failed'
    try:
        kaggle('kernels', 'push', '-p', kn)
        status = wait(user, config['minutes'] + 25)
        od = os.path.join(OUT, 'kernel-out')
        shutil.rmtree(od, ignore_errors=True)
        kaggle('kernels', 'output', f'{user}/{KERNEL_SLUG}', '-p', od, check=False)
        feats = json.load(open(os.path.join(od, 'feats.json'))) if os.path.isfile(os.path.join(od, 'feats.json')) else None
        stats = json.load(open(os.path.join(od, 'stats.json'))) if os.path.isfile(os.path.join(od, 'stats.json')) else {}
        minutes = (time.time() - t0) / 60
        record(minutes, status, {'tag': tag, 'clips': len(feats or {}), 'kernel_min': stats.get('total_min'), 'gpu': stats.get('gpu'),
                                 'errors': (stats.get('errors') or [])[:3]})
        if status != 'complete' or feats is None:
            log((open(os.path.join(od, f'{KERNEL_SLUG}.log'), encoding='utf-8', errors='replace').read()[-3000:]
                 if os.path.isfile(os.path.join(od, f'{KERNEL_SLUG}.log')) else 'no log'))
            raise RuntimeError(f'kernel {status}')
        return feats, stats, minutes
    except Exception:
        if status == 'push-failed':
            record(0, status, {'tag': tag})
        raise


def gate(minutes_wanted):
    if not day_ok():
        sys.exit('clipscore: Friday-Sunday (UTC or US Eastern) belong to the owner\'s other project: no Kaggle job')
    left, used = budget()
    if left < 15:
        sys.exit(f'clipscore: weekly GPU cap reached ({used:.0f} of {WEEK_MIN:.0f} min used): no Kaggle job')
    return max(10.0, min(minutes_wanted, left - 5))


# ------------------------------------------------------------------ commands
def cmd_check(a):
    left, used = budget()
    tars, scored, _ = staged_tars()
    todo = [t for t in tars if CS.score_name(t) not in scored]
    print(json.dumps({'day_ok': day_ok(), 'week': week_key(), 'used_min': round(used, 1), 'left_min': round(left, 1),
                      'cap_min': WEEK_MIN, 'session_min': SESSION_MIN, 'tars_waiting': todo}))


def cmd_run(a):
    tars, scored, _ = staged_tars()
    todo = [t for t in tars if CS.score_name(t) not in scored] if not a.tars else [t for t in a.tars.split(',') if t in tars]
    if not todo:
        print(json.dumps({'ok': True, 'scored': [], 'note': 'nothing waiting'}))
        return
    minutes = SESSION_MIN if a.dry_run else gate(SESSION_MIN)
    config = {'mode': 'score', 'minutes': minutes,
              'tars': [{'name': t, 'url': asset_url(t), 'kind': 'sound' if t.startswith('sound-') else 'slowmo'} for t in todo]}
    feats, stats, mins = session(config, 'score', dry=a.dry_run)
    if feats is None:
        return
    os.makedirs(OUT, exist_ok=True)
    json.dump(feats, open(os.path.join(OUT, 'last-feats.json'), 'w'), indent=0)
    files = CS.write_scores(feats, OUT, gpu_min=mins)
    if not a.no_upload and files:
        _gh(['release', 'upload', STAGE_TAG, *files, '--clobber', '--repo', REPO], check=True)
        prune(scored)
    summ = {os.path.basename(f): CS.summary(json.load(open(f))) for f in files}
    print(json.dumps({'ok': True, 'minutes': round(mins, 1), 'kernel_min': stats.get('total_min'), 'gpu': stats.get('gpu'), 'scores': summ}))


def prune(scored):
    """drop clipscore-*.json whose tar is gone (imported / deleted)"""
    tars, scored_now, _ = staged_tars()
    for s in scored_now:
        if not any(CS.score_name(t) == s for t in tars):
            _gh(['release', 'delete-asset', STAGE_TAG, s, '-y', '--repo', REPO])


def cmd_rescore(a):
    tars, scored, _ = staged_tars()
    d = os.path.join(OUT, 'rescore')
    os.makedirs(d, exist_ok=True)
    files = []
    for s in scored:
        _gh(['release', 'download', STAGE_TAG, '--repo', REPO, '--pattern', s, '--dir', d, '--clobber'])
        p = os.path.join(d, s)
        if os.path.isfile(p):
            files.append(CS.rescore_file(p))
    if files and not a.no_upload:
        _gh(['release', 'upload', STAGE_TAG, *files, '--clobber', '--repo', REPO], check=True)
    print(json.dumps({'ok': True, 'rescored': [os.path.basename(f) for f in files]}))


def cmd_calib(a):
    labels = json.load(open(a.labels, encoding='utf-8'))
    sets = json.load(open(a.sets, encoding='utf-8'))
    minutes = SESSION_MIN if a.dry_run else gate(float(a.minutes))
    feats, stats, mins = session({'mode': 'calib', 'minutes': minutes, 'labels': labels, 'sets': sets}, 'calib', dry=a.dry_run)
    if feats is None:
        return
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'calib-feats.json')
    json.dump(feats, open(p, 'w'), indent=0)
    print(json.dumps({'ok': True, 'feats': p, 'clips': len(feats), 'minutes': round(mins, 1), 'kernel_min': stats.get('total_min'),
                      'phases': stats.get('phases'), 'errors': stats.get('errors')}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['check', 'run', 'calib', 'rescore'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-upload', action='store_true')
    ap.add_argument('--tars')
    ap.add_argument('--labels')
    ap.add_argument('--sets')
    ap.add_argument('--minutes', default=60)
    a = ap.parse_args()
    {'check': cmd_check, 'run': cmd_run, 'calib': cmd_calib, 'rescore': cmd_rescore}[a.cmd](a)


if __name__ == '__main__':
    main()
