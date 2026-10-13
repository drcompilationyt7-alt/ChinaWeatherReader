"""
Kaggle driver for AI-generated quiz art (owner 2026-10-11). Generation runs on Kaggle (free GPU), the gates run locally.
  python scripts/pop-ai-art-kaggle.py plan   --scopes beach,halloween --chars 150 --seeds 3     -> output/ai-art/prompts.jsonl
  python scripts/pop-ai-art-kaggle.py bundle                                                   -> dataset + kernel folders (nothing pushed)
  python scripts/pop-ai-art-kaggle.py run    [--minutes 120]                                   -> day guard, budget, push, wait, fetch into the gen/out folder
  python scripts/pop-ai-art-kaggle.py score  --scopes beach,halloween                          -> local gates + merge (ai-cache.json), then finish_theme
  python scripts/pop-ai-art-kaggle.py check                                                    -> weekday / weekly budget
Rules: Kaggle only Monday-Thursday UTC (the owner's other project has Friday-Sunday), <= 180 GPU min per week for us, one session
time-boxed (default 120). Our names all start with apq-ai-art-; other kernels of the account are never touched.
Auth: the kaggle CLI reads ~/.kaggle/access_token or KAGGLE_API_TOKEN; this script never reads, prints or writes it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core', 'quiz'))
OUTDIR = os.path.join(ROOT, 'output', 'ai-art')
LEDGER = os.path.join(ROOT, os.environ.get('MEMORY_DIR', 'memory'), 'ai-art-kaggle.json')
WEEK_MIN = int(os.environ.get('APQ_AI_WEEK_MIN', 180))
DATASET, KERNEL = 'apq-ai-art-input', 'apq-ai-art-gen'
KAGGLE = os.environ.get('KAGGLE_EXE') or 'C:/Users/Home/Downloads/StockBot/.venv/Scripts/kaggle.exe'
if not os.path.exists(KAGGLE):
    KAGGLE = 'kaggle'


def kaggle(*args, check=True):
    r = subprocess.run([KAGGLE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args[:2])}: {(r.stderr or r.stdout or '')[-300:]}")
    return r


def day_blocked():
    """Friday-Sunday in UTC OR in local time (the owner's other project; both clocks must say Monday-Thursday)."""
    return datetime.now(timezone.utc).weekday() > 3 or datetime.now().weekday() > 3


def week_key(now=None):
    y, w, _ = (now or datetime.now(timezone.utc)).isocalendar()
    return f'{y}-W{w:02d}'


def ledger():
    try:
        return json.load(open(LEDGER, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def used_min():
    return sum(s.get('minutes', 0) for s in ledger().get(week_key(), []))


def record(minutes, status, stats=None):
    lg = ledger()
    lg.setdefault(week_key(), []).append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'minutes': round(minutes, 1), 'status': status, 'stats': stats})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump(lg, open(LEDGER, 'w', encoding='utf-8'), indent=1)


def username():
    r = kaggle('config', 'view', check=False)
    for ln in (r.stdout or '').splitlines():
        if 'username' in ln.lower():
            return ln.split(':', 1)[1].strip()
    return os.environ.get('KAGGLE_USERNAME', '')


def bundle(minutes):
    user = username() or '<KAGGLE_USERNAME>'
    ds, kn = os.path.join(OUTDIR, 'dataset'), os.path.join(OUTDIR, 'kernel')
    for d in (ds, kn):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    shutil.copyfile(os.path.join(OUTDIR, 'prompts.jsonl'), os.path.join(ds, 'prompts.jsonl'))
    json.dump({'minutes': minutes}, open(os.path.join(ds, 'budget.json'), 'w'))
    json.dump({'title': DATASET, 'id': f'{user}/{DATASET}', 'licenses': [{'name': 'other'}]}, open(os.path.join(ds, 'dataset-metadata.json'), 'w'), indent=1)
    shutil.copyfile(os.path.join(ROOT, 'scripts', 'kaggle', 'ai_art_kernel.py'), os.path.join(kn, 'ai_art_kernel.py'))
    json.dump({'id': f'{user}/{KERNEL}', 'title': KERNEL, 'code_file': 'ai_art_kernel.py', 'language': 'python', 'kernel_type': 'script',
               'is_private': True, 'enable_gpu': True, 'enable_internet': True, 'dataset_sources': [f'{user}/{DATASET}'],
               'competition_sources': [], 'kernel_sources': []}, open(os.path.join(kn, 'kernel-metadata.json'), 'w'), indent=1)
    return ds, kn, user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['plan', 'bundle', 'run', 'score', 'check'])
    ap.add_argument('--scopes', default='beach,halloween')
    ap.add_argument('--chars', type=int, default=150)
    ap.add_argument('--seeds', type=int, default=3)
    ap.add_argument('--minutes', type=int, default=120)
    a = ap.parse_args()
    import pop_gen
    scopes = a.scopes.split(',')
    if a.cmd == 'check':
        print(json.dumps({'weekday_ok': not day_blocked(), 'week_used_min': used_min(), 'week_left_min': max(0, WEEK_MIN - used_min())}))
    elif a.cmd == 'plan':
        os.makedirs(OUTDIR, exist_ok=True)
        lines = pop_gen.plan_prompts(scopes, a.chars, a.seeds, os.path.join(OUTDIR, 'prompts.jsonl'))
        print(f'{len(lines)} prompts ->', os.path.join(OUTDIR, 'prompts.jsonl'))
    elif a.cmd == 'bundle':
        ds, kn, user = bundle(a.minutes)
        print('bundle ready:', ds, kn, '(nothing pushed)')
    elif a.cmd == 'run':
        if day_blocked():
            sys.exit("kaggle: not on Friday-Sunday (the owner's other project has the account)")
        minutes = min(a.minutes, WEEK_MIN - used_min())
        if minutes < 20:
            sys.exit(f'kaggle: weekly budget used ({used_min()} of {WEEK_MIN} min)')
        ds, kn, user = bundle(minutes)
        t0 = time.time()
        status = 'push-failed'
        try:
            if kaggle('datasets', 'version', '-p', ds, '-m', 'prompts', '--dir-mode', 'tar', check=False).returncode != 0:
                kaggle('datasets', 'create', '-p', ds, '--dir-mode', 'tar')
            time.sleep(90)
            kaggle('kernels', 'push', '-p', kn)
            t_end = time.time() + (minutes + 25) * 60
            status = 'running'
            time.sleep(75)   # the previous run's status lingers for a moment after a push
            while time.time() < t_end:
                out = (kaggle('kernels', 'status', f'{user}/{KERNEL}', check=False).stdout or '').lower()
                status = next((s for s in ('complete', 'error', 'cancel') if s in out), 'running')
                if status != 'running':
                    break
                time.sleep(60)
            if status != 'complete':
                raise RuntimeError(f'kernel {status}')
            tmp = os.path.join(OUTDIR, 'result')
            shutil.rmtree(tmp, ignore_errors=True)
            kaggle('kernels', 'output', f'{user}/{KERNEL}', '-p', tmp)
            n = 0
            for dp, _, fs in os.walk(os.path.join(tmp, 'out')):
                for f in fs:
                    dst = os.path.join(pop_gen.OUT, os.path.relpath(os.path.join(dp, f), os.path.join(tmp, 'out')))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(os.path.join(dp, f), dst)
                    n += f.endswith('.png')
            stats = json.load(open(os.path.join(tmp, 'stats.json')))
            record((time.time() - t0) / 60, 'complete', stats)
            print('fetched', n, 'pictures;', stats)
        except Exception as e:  # noqa: BLE001
            record((time.time() - t0) / 60 if status != 'push-failed' else 0, status, {'error': str(e)[:200]})
            sys.exit(f'kaggle failed: {str(e)[:200]}')
    elif a.cmd == 'score':
        print(json.dumps(pop_gen.score_dir(scopes, minutes=a.minutes), indent=1))


if __name__ == '__main__':
    main()
