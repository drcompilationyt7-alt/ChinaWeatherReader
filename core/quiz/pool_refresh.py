"""
Safe monthly swap of the silhouette / eyes / outfit / theme pools (owner rules 2026-10-09).

  stage      fresh staging copy: assets/pop2_next/ + assets/character-extras.next.json (the build writes there:
             POP2_DIR / POP_EXTRAS_FILE point the builders and the renderer at the staging copy)
  validate   staging vs the live pool and the quality BASELINE: every file exists and loads, gate results present,
             counts per format / theme >= minimum and >= 80% of the live pool, no duplicate ids, age / content rules
             re-checked (render_pop.extra_check + the theme age rule), items below the baseline's 10th percentile
             dropped, batch medians within tolerance of the baseline, one test frame per format / theme renders.
             Writes <staging>/refresh-report.json; exit 1 = failed (the live pool stays untouched).
  promote    new pool = the staged batch + the live pool's items that were NEVER used (used log = MEMORY_DIR/pop-bank.json);
             used old items retire (files removed), duplicates keep the newer item. Only after validate passed.
  baseline   --approve: store the CURRENT live pool's quality stats as the baseline (assets/pool-baseline.json). Run it only
             when the owner has approved the samples / gate sheets. Without a baseline nothing can be promoted.
  run        validate, then promote when it passed (the workflow's step).

Quality metrics (higher is better unless noted): short side of the stored picture, fan art source short side, on-model margin /
hair / eye agreement (fan art), silhouette distinctiveness (1 - solidity), eye-crop sharpness (Laplacian variance) and source
eye size, outfit framing (share of the picture the crop shows), the WD14 rating mix (share 'sensitive') and the source mix
(share fan art). No free aesthetic model is cached, so there is no aesthetic score.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets')
LIVE_DIR, LIVE_JSON = os.path.join(ASSETS, 'pop2'), os.path.join(ASSETS, 'character-extras.json')
NEXT_DIR, NEXT_JSON = os.path.join(ASSETS, 'pop2_next'), os.path.join(ASSETS, 'character-extras.next.json')
BASELINE = os.path.join(ASSETS, 'pool-baseline.json')
FORMATS = ('silhouette', 'eyes', 'outfit')
MIN_COUNT = {'silhouette': 20, 'eyes': 100, 'outfit': 15, 'theme': 6}
KEEP_SHARE = 0.8          # a refresh may not shrink a pool below 80% of the live one
TOL = 0.15                # batch medians within 15% of the baseline's
HIGHER = ('short_side', 'src_short', 'margin', 'hair', 'eye_agree', 'distinct', 'sharpness', 'eye_px', 'framing')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def default_dir(it, fmt):
    return it.get('dir') or {'silhouette': 'sil', 'eyes': 'eyes', 'outfit': 'outfit', 'character': None}[fmt]


def item_files(fmt, it):
    d = default_dir(it, fmt)
    if d is None:
        return []
    i = it['id']
    return {'silhouette': [f'{d}/{i}.jpg', f'{d}/{i}.png'], 'eyes': [f'{d}/{i}.jpg', f'{d}/{i}_p.jpg'],
            'outfit': [f'{d}/{i}.jpg'], 'character': [f'{d}/{i}.jpg']}[fmt]


def pools(ex):
    """[(pool name, format, items)]: the three base pools + every theme pool."""
    out = [(f, f, ex.get(f) or []) for f in FORMATS]
    for tid, p in (ex.get('themes') or {}).items():
        out += [(f'theme:{tid}:{f}', f, items) for f, items in p.items()]
    return out


def used_key(fmt, it):   # pipeline/pop-quiz-content.js key()
    return f"{fmt}:{str(it.get('name')).lower()}"


# ------------------------------------------------------------------ quality metrics
def metrics(fmt, it, root):
    m = {}
    files = item_files(fmt, it)
    if files:
        try:
            with Image.open(os.path.join(root, files[0])) as im:
                m['short_side'] = float(min(im.size))
                if fmt == 'eyes':
                    import cv2
                    g = np.asarray(im.convert('L'), np.float32)
                    m['sharpness'] = float(cv2.Laplacian(g, cv2.CV_32F).var())
        except OSError:
            pass
    src = it.get('source') or {}
    if src.get('kind') == 'danbooru':
        try:
            m['src_short'] = float(min(int(x) for x in str(src.get('res', '0x0')).split('x')))
        except ValueError:
            pass
        om = src.get('onmodel') or {}
        for k, mk in (('margin', 'margin'), ('hair', 'hair'), ('eye', 'eye_agree')):
            if isinstance(om, dict) and om.get(k) is not None:
                m[mk] = float(om[k])
    if fmt == 'silhouette' and (it.get('stats') or {}).get('solidity') is not None:
        m['distinct'] = 1.0 - float(it['stats']['solidity'])
    if fmt == 'eyes' and it.get('eye_px'):
        m['eye_px'] = float(it['eye_px'])
    if fmt == 'outfit' and it.get('crop'):
        c = it['crop']
        m['framing'] = float((c[2] - c[0]) * (c[3] - c[1]))
    return m


def batch_stats(fmt, items, root):
    ms = [metrics(fmt, it, root) for it in items]
    out = {'n': len(items), 'metrics': {}}
    for k in HIGHER:
        v = [m[k] for m in ms if k in m]
        if len(v) >= 3:
            out['metrics'][k] = {'p10': float(np.percentile(v, 10)), 'median': float(np.median(v)), 'n': len(v)}
    r = [(it.get('gate') or {}).get('rating') or {} for it in items]
    out['sensitive_share'] = float(np.mean([1.0 if x.get('sensitive', 0) > x.get('general', 0) else 0.0 for x in r])) if r else 0.0
    out['fan_share'] = float(np.mean([1.0 if (it.get('source') or {}).get('kind') == 'danbooru' else 0.0 for it in items])) if items else 0.0
    return out


def baseline_for(base, name, fmt):
    """The baseline entry of a pool: its own if approved, else the format's (a theme pool new since the approval)."""
    b = base['pools']
    return b.get(name) or b.get(fmt) or None


# ------------------------------------------------------------------ commands
def stage():
    if os.path.exists(NEXT_DIR):
        shutil.rmtree(NEXT_DIR)
    shutil.copytree(LIVE_DIR, NEXT_DIR)
    shutil.copyfile(LIVE_JSON, NEXT_JSON)
    log(f'staged: {NEXT_DIR}, {NEXT_JSON}')


def approve_baseline(by='owner', live_json=None, live_dir=None, out=None):
    ex = json.load(open(live_json or LIVE_JSON, encoding='utf-8'))
    res = {'approved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'approved_by': by, 'pools': {}}
    for name, fmt, items in pools(ex):
        if items:
            res['pools'][name] = batch_stats(fmt, items, live_dir or LIVE_DIR)
    json.dump(res, open(out or BASELINE, 'w', encoding='utf-8'), indent=1)
    log(f'baseline stored: {out or BASELINE} ({len(res["pools"])} pools)')


def content_ok(fmt, it, name, overrides):
    """The age / content rules again (render-time check + the theme age rule)."""
    sys.path.insert(0, HERE)
    from render_pop import extra_check
    if fmt in FORMATS:
        why = extra_check(fmt, it)
        if why:
            return why
    g = it.get('gate') or {}
    if not g.get('why') or not g.get('rating'):
        return 'no stored gate result'
    if g['rating'].get('questionable', 0) > 0.2 or g['rating'].get('explicit', 0) > 0.1:
        return 'rating'
    strict = it.get('age_class') != 'adult' or g.get('class', 'adult') not in ('adult', 'adult-modest')
    if strict and (g['rating'].get('general', 0) < 0.6 or g.get('swimwear')):
        return 'strict gate'
    if name.startswith('theme:'):
        tid = name.split(':')[1]
        th = json.load(open(os.path.join(HERE, 'themes.json'), encoding='utf-8'))['themes'].get(tid) or {}
        if th.get('age_rule') == 'adults' and (it.get('age_class') != 'adult' or str(it['id']) in overrides):
            return 'theme is adults only'
    return None


def test_frame(fmt, edition, env):
    """One round of the staged pool renders (a still at the reveal; no TTS)."""
    out = os.path.join(NEXT_DIR, f'_test_{edition or "base"}_{fmt}.png')
    cmd = [sys.executable, os.path.join(HERE, 'render_quiz.py'), '--format', fmt, '--seed', '7', '--rounds', '3', '--frame', '4.6',
           '--no-voice', '--no-music', '--out', out] + (['--edition', edition] if edition else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=HERE)
    ok = r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 10000
    if os.path.exists(out):
        os.remove(out)
    return ok, (r.stderr or r.stdout)[-300:]


def validate(render=True, next_dir=None, next_json=None, live_json=None, baseline=None, min_count=None):
    next_dir, next_json, live_json, baseline = next_dir or NEXT_DIR, next_json or NEXT_JSON, live_json or LIVE_JSON, baseline or BASELINE
    report = {'ok': False, 'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'problems': [], 'dropped': [], 'pools': {}}
    if not os.path.exists(baseline):
        report['problems'].append('no quality baseline yet: the owner has not approved a batch (pool_refresh.py baseline --approve)')
        return report
    base = json.load(open(baseline, encoding='utf-8'))
    try:
        new = json.load(open(next_json, encoding='utf-8'))
    except (OSError, ValueError) as e:
        report['problems'].append(f'staged pool unreadable: {e}')
        return report
    live = json.load(open(live_json, encoding='utf-8')) if os.path.exists(live_json) else {}
    live_n = {name: len(items) for name, _, items in pools(live)}
    try:
        overrides = json.load(open(os.path.join(ASSETS, 'appearance-overrides.json'), encoding='utf-8'))['characters']
    except (OSError, ValueError):
        overrides = {}
    os.environ['POP2_DIR'] = next_dir          # extra_check reads the files from the staging copy
    for name, fmt, items in pools(new):
        b = baseline_for(base, name, fmt)
        keep, seen = [], set()
        for it in items:
            why = None
            if it['id'] in seen:
                why = 'duplicate id'
            for f in item_files(fmt, it):
                p = os.path.join(next_dir, f)
                try:
                    with Image.open(p) as im:
                        im.verify()
                except (OSError, SyntaxError):
                    why = why or f'missing / unreadable {f}'
            why = why or content_ok(fmt, it, name, overrides)
            if not why and b:   # per item: at least the approved batch's 10th percentile on every metric it has
                m = metrics(fmt, it, next_dir)
                low = [k for k, v in m.items() if k in b['metrics'] and v < b['metrics'][k]['p10']]
                if low:
                    why = 'below baseline p10: ' + ','.join(low)
            if why:
                report['dropped'].append({'pool': name, 'id': it['id'], 'name': it.get('name'), 'why': why})
                if why == 'duplicate id' or why.startswith('missing'):
                    report['problems'].append(f'{name}: {it.get("name")}: {why}')
                continue
            seen.add(it['id'])
            keep.append(it)
        st = batch_stats(fmt, keep, next_dir)
        report['pools'][name] = {'n': len(keep), 'live_n': live_n.get(name, 0), 'stats': st}
        mc = min_count or MIN_COUNT
        need = mc['theme'] if name.startswith('theme:') else mc[fmt]
        if len(keep) < need:
            report['problems'].append(f'{name}: {len(keep)} items < minimum {need}')
        if live_n.get(name) and len(keep) < KEEP_SHARE * live_n[name]:
            report['problems'].append(f'{name}: {len(keep)} items, well below the live pool ({live_n[name]})')
        if b and keep:   # the whole batch matches the baseline
            for k, v in st['metrics'].items():
                if k in b['metrics'] and v['median'] < (1 - TOL) * b['metrics'][k]['median']:
                    report['problems'].append(f"{name}: median {k} {v['median']:.3g} < baseline {b['metrics'][k]['median']:.3g} - {int(TOL * 100)}%")
            if st['sensitive_share'] > b.get('sensitive_share', 0) + 0.15:
                report['problems'].append(f"{name}: sensitive share {st['sensitive_share']:.2f} vs baseline {b.get('sensitive_share', 0):.2f}")
            if abs(st['fan_share'] - b.get('fan_share', 0)) > 0.25:
                report['problems'].append(f"{name}: fan-art share {st['fan_share']:.2f} vs baseline {b.get('fan_share', 0):.2f}")
        if name.startswith('theme:'):
            new['themes'][name.split(':')[1]][fmt] = keep
        else:
            new[fmt] = keep
    # filtered staging pool (drops removed) for the test renders and the promotion
    json.dump(new, open(next_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if render and not report['problems']:
        env = {**os.environ, 'POP2_DIR': next_dir, 'POP_EXTRAS_FILE': next_json}
        for name, fmt, items in pools(new):
            if not items:
                continue
            ok, err = test_frame(fmt, name.split(':')[1] if name.startswith('theme:') else None, env)
            if not ok:
                report['problems'].append(f'{name}: test render failed: {err}')
    report['ok'] = not report['problems']
    json.dump(report, open(os.path.join(next_dir, 'refresh-report.json'), 'w', encoding='utf-8'), indent=1)
    return report


def promote(used_file, next_dir=None, next_json=None, live_dir=None, live_json=None):
    """new pool = staged batch + the live pool's never-used items; used old items retire; duplicates keep the newer."""
    next_dir, next_json, live_dir, live_json = next_dir or NEXT_DIR, next_json or NEXT_JSON, live_dir or LIVE_DIR, live_json or LIVE_JSON
    used = {}
    try:
        used = json.load(open(used_file, encoding='utf-8')).get('used') or {}
    except (OSError, ValueError):
        log(f'no used log at {used_file}: every old item counts as unused')
    new = json.load(open(next_json, encoding='utf-8'))
    old = json.load(open(live_json, encoding='utf-8'))
    final = dict(new)
    final['themes'] = {k: dict(v) for k, v in (new.get('themes') or {}).items()}
    retired, kept_old = [], 0
    for name, fmt, old_items in pools(old):
        tid = name.split(':')[1] if name.startswith('theme:') else None
        cur = (final['themes'].setdefault(tid, {}).setdefault(fmt, []) if tid else final.setdefault(fmt, []))
        ids = {it['id'] for it in cur}
        for it in old_items:
            if used_key(fmt, it) in used:
                retired.append((fmt, it))
            elif it['id'] in ids:
                pass                       # duplicate: the newer item stays
            else:
                cur.append({**it, '_from': 'live'})
                kept_old += 1
    # files: new items from staging, kept old items from the live folder (copied first so nothing referenced disappears)
    tmp_dir = live_dir + '_promote'
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir)
    for name, fmt, items in pools(final):
        for it in items:
            src_root = live_dir if it.pop('_from', None) == 'live' else next_dir
            for f in item_files(fmt, it):
                os.makedirs(os.path.dirname(os.path.join(tmp_dir, f)), exist_ok=True)
                shutil.copy2(os.path.join(src_root, f), os.path.join(tmp_dir, f))
    json.dump(final, open(live_json + '.tmp', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # swap: the old folder is only removed once the new one is complete
    old_dir = live_dir + '_old'
    if os.path.exists(old_dir):
        shutil.rmtree(old_dir)
    os.replace(live_dir, old_dir)
    os.replace(tmp_dir, live_dir)
    os.replace(live_json + '.tmp', live_json)
    shutil.rmtree(old_dir)
    shutil.rmtree(next_dir, ignore_errors=True)
    if os.path.exists(next_json):
        os.remove(next_json)
    log(f'promoted: {sum(len(i) for _, _, i in pools(final))} items ({kept_old} unused old items kept, {len(retired)} used old items retired)')
    return {'kept_old': kept_old, 'retired': len(retired), 'total': sum(len(i) for _, _, i in pools(final))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['stage', 'validate', 'promote', 'run', 'baseline'])
    ap.add_argument('--approve', action='store_true', help='baseline: the owner approved the current live pool')
    ap.add_argument('--used', default=os.path.join(os.path.dirname(os.path.dirname(HERE)), os.environ.get('MEMORY_DIR', 'memory'), 'pop-bank.json'))
    ap.add_argument('--no-render', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'stage':
        stage()
    elif a.cmd == 'baseline':
        if not a.approve:
            sys.exit('baseline needs --approve (only after the owner approved the samples and gate sheets)')
        approve_baseline()
    elif a.cmd == 'promote':
        promote(a.used)
    else:
        rep = validate(render=not a.no_render)
        log(json.dumps({'ok': rep['ok'], 'problems': rep['problems'][:20], 'dropped': len(rep['dropped'])}, indent=1))
        if not rep['ok']:
            log('VALIDATION FAILED: the live pool stays as it is')
            sys.exit(1)
        if a.cmd == 'run':
            promote(a.used)


if __name__ == '__main__':
    main()
