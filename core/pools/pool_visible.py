"""
Pool-level VISIBLE-SOURCE stamp for the sound pools (owner 2026-10-12: "I need to know WHAT IS MAKING THE SOUND").

The gate itself is core/edit/sound_visible.py (motion onset / contact / spark within +-2 frames@24 of the transient, big
enough, inside one 9:16 window, not an FX flash, SigLIP source tag vs face / scenery) and its use per audible hit,
sound_edit.visible_pass(); this file does NOT build a second gate: it runs exactly sound_edit.vibe_events() (HPSS /
transient events + visible_pass) once per pool clip and writes the verdict into the manifest, so that the monthly refresh can
count, gate and de-duplicate clips without rendering anything.

Per fresh sound clip, item['vis'] (additive, pool-format.md v1.2):
  {"v": 1, "hit": true|false, "hit_t": s in the clip, "cls": kick|snare|hat|whoosh|voice, "sound_type": gun|blade|punch|break|object|other,
   "src": raw SigLIP source tag, "score": 0-1 visible-source score, "offset_f24", "prom", "area", "fit", "kind", "event_t",
   "windup_t", "voiced": bool, "reason": why it is a bed (picture-only) clip, "n_events", "sig": picture signature (base64),
   "checked": date}
hit = an AUDIBLE clip hit whose cause is visible; everything else stays in the pool as a BED shot (song-only cuts need a picture).

    python core/pools/pool_visible.py stamp  [--dir DIR] [--vibe V] [--force] [--limit N] [--no-tags]
    python core/pools/pool_visible.py counts [--dir DIR] [--json]
    python core/pools/pool_visible.py sheet  [--dir DIR] [--vibe V] [--out PREFIX] [--only-new IDS.json] [--per 48]
"""
import argparse
import base64
import datetime as _dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'core', 'edit'))
sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))

VERSION = 1
SIG_FRAMES, SIG_W, SIG_H = 5, 16, 9
DUP_CORR = float(os.environ.get('POOL_DUP_CORR', '0.93'))
TYPE_OF_TAG = {'gun': 'gun', 'reload': 'gun', 'sword': 'blade', 'metal': 'blade', 'fist': 'punch', 'kick': 'punch',
               'impact': 'punch', 'glass': 'break', 'mask': 'break', 'crack': 'break', 'clap': 'object', 'object': 'object',
               'chain': 'object'}
SOUND_TYPES = ('gun', 'blade', 'punch', 'break', 'object', 'other')


def log(*a):
    print('[pool_visible]', *a, file=sys.stderr, flush=True)


def pool_dir(d=None):
    return os.path.abspath(d or os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools'))


def sound_vibes(root):
    p = os.path.join(root, 'sound')
    return sorted(v for v in os.listdir(p) if os.path.isfile(os.path.join(p, v, 'clips.json'))) if os.path.isdir(p) else []


def load(root, vibe):
    with open(os.path.join(root, 'sound', vibe, 'clips.json'), encoding='utf-8') as f:
        return json.load(f)


def save(root, vibe, man):
    p = os.path.join(root, 'sound', vibe, 'clips.json')
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    os.replace(p + '.tmp', p)


# ------------------------------------------------------------------ picture signature (same scene, two uploads)

def signature(path):
    """5 grey 16x9 frames across the clip, zero-mean / unit-norm, base64 of uint8 (compact, comparable by correlation)."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fr = []
    for q in np.linspace(0.1, 0.9, SIG_FRAMES):
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * q)))
        ok, f = cap.read()
        if ok:
            fr.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (SIG_W, SIG_H), interpolation=cv2.INTER_AREA))
    cap.release()
    if len(fr) < SIG_FRAMES:
        return None
    return base64.b64encode(np.stack(fr).astype(np.uint8).tobytes()).decode()


def _vec(sig):
    import numpy as np
    a = np.frombuffer(base64.b64decode(sig), np.uint8).astype(np.float32).reshape(SIG_FRAMES, -1)
    a = a - a.mean(axis=1, keepdims=True)
    return (a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-6)).ravel() / (SIG_FRAMES ** 0.5)


def same_picture(sig_a, sig_b, thr=None):
    if not sig_a or not sig_b:
        return False
    return float((_vec(sig_a) * _vec(sig_b)).sum()) >= (thr or DUP_CORR)


def dup_of(item, others, thr=None):
    """id of the first item of `others` showing the same picture as `item` (None if none)."""
    s = (item.get('vis') or {}).get('sig')
    if not s:
        return None
    a = _vec(s)
    thr = thr or DUP_CORR
    for o in others:
        so = (o.get('vis') or {}).get('sig')
        if so and o.get('id') != item.get('id') and float((a * _vec(so)).sum()) >= thr:
            return o.get('id')
    return None


# ------------------------------------------------------------------ the stamp

def _verdict(evs, n_events):
    """vis dict of one clip from its sound_edit events (audible = not silent_only)."""
    aud = [e for e in evs if not e.get('silent_only') and not e.get('fill')]
    aud = [e for e in aud if (e.get('visible') or {}).get('ok')]
    if aud:
        e = max(aud, key=lambda x: (x.get('visible') or {}).get('score', 0.0) + 0.3 * float(x.get('score', 0.0)))
        v = e['visible']
        voiced = e.get('cls') == 'voice' or float(e.get('harm', 0.0)) >= float(os.environ.get('SOUND_VOICED_HARM', '0.5'))
        return {'hit': True, 'hit_t': round(float(e['t']), 3), 'cls': e.get('cls'),
                'sound_type': TYPE_OF_TAG.get(v.get('src') or '', 'other'), 'src': v.get('src'), 'score': v.get('score'),
                'offset_f24': v.get('offset_f24'), 'prom': v.get('prom'), 'area': v.get('area'), 'fit': v.get('fit'),
                'kind': v.get('kind'), 'event_t': v.get('event_t'), 'windup_t': v.get('windup_t'),
                'voiced': bool(voiced), 'n_events': n_events}
    why = [e.get('vis_reject') or e.get('reject') for e in evs if e.get('vis_reject')]
    reason = max(set(why), key=why.count) if why else ('no_event' if not evs else 'not_crisp')
    return {'hit': False, 'reason': reason, 'n_events': n_events}


def stamp(root=None, vibe=None, force=False, limit=None, tags=True, ids=None):
    """Stamp every fresh sound clip without a current vis record. Returns {vibe: {stamped, hit, bed}}."""
    os.environ.setdefault('SOUND_PICTURE_FILL', '0')
    if not tags:
        os.environ['SOUND_VIS_TAGS'] = '0'
    import sound_edit as SE
    root = pool_dir(root)
    cache = os.environ.get('SOUND_EV_CACHE') or os.path.join(tempfile.gettempdir(), 'sound-edit-events')
    os.makedirs(cache, exist_ok=True)
    out = {}
    done = 0
    for vb in ([vibe] if vibe else sound_vibes(root)):
        man = load(root, vb)
        vd = os.path.join(root, 'sound', vb)
        todo = [it for it in man['items'] if it.get('status', 'fresh') == 'fresh'
                and (force or (it.get('vis') or {}).get('v') != VERSION) and (ids is None or it['id'] in ids)]
        res = {'stamped': 0, 'hit': 0, 'bed': 0}
        for it in todo:
            if limit and done >= limit:
                break
            p = os.path.join(vd, it['file'])
            if not os.path.isfile(p):
                continue
            shot = SE._pool_shot(dict(it, path=p))
            try:
                evs = SE.vibe_events([shot], cache_dir=cache)
            except Exception as ex:  # noqa: BLE001
                log(f'{it["id"]}: events failed: {str(ex)[:100]}')
                evs = []
            vis = _verdict(evs, len(evs))
            vis.update(v=VERSION, checked=_dt.date.today().isoformat(), sig=signature(p))
            it['vis'] = vis
            res['stamped'] += 1
            res['hit' if vis['hit'] else 'bed'] += 1
            done += 1
            if done % 10 == 0:
                save(root, vb, man)           # resumable
                log(f'{vb}: {res}')
        save(root, vb, man)
        out[vb] = res
    return out


# ------------------------------------------------------------------ counts

def counts(root=None):
    """Per vibe: fresh / hit / bed / unstamped, hit moments per sound type, distinct sources, voiced share."""
    root = pool_dir(root)
    out = {}
    for vb in sound_vibes(root):
        fr = [it for it in load(root, vb)['items'] if it.get('status', 'fresh') == 'fresh']
        hits = [it for it in fr if (it.get('vis') or {}).get('hit')]
        c = {t: 0 for t in SOUND_TYPES}
        for it in hits:
            c[it['vis'].get('sound_type') if it['vis'].get('sound_type') in c else 'other'] += 1
        out[f'sound/{vb}'] = {
            'fresh': len(fr), 'hit': len(hits), 'bed': sum(1 for it in fr if it.get('vis') and not it['vis'].get('hit')),
            'unstamped': sum(1 for it in fr if (it.get('vis') or {}).get('v') != VERSION), 'by_type': c,
            'sources': len({it.get('source_id') for it in hits}), 'voiced': sum(1 for it in hits if it['vis'].get('voiced')),
            'median_vis_score': round(sorted(it['vis'].get('score') or 0 for it in hits)[len(hits) // 2], 3) if hits else None}
    return out


# ------------------------------------------------------------------ contact sheet of hit moments

def sheet(root=None, vibe=None, out_prefix=None, only=None, per=48, cols=8, cell=(240, 135)):
    """Contact sheets (JPEG) of hit moments: the frame at the visible event, label = sound type / score / hit time."""
    import cv2
    import numpy as np
    root = pool_dir(root)
    items = []
    for vb in ([vibe] if vibe else sound_vibes(root)):
        for it in load(root, vb)['items']:
            if it.get('status', 'fresh') == 'fresh' and (it.get('vis') or {}).get('hit') and (only is None or it['id'] in only):
                items.append((vb, it))
    items.sort(key=lambda x: (x[1]['vis'].get('sound_type') or '', -(x[1]['vis'].get('score') or 0)))
    out_prefix = out_prefix or os.path.join(root, 'pool-sheet')
    os.makedirs(os.path.dirname(os.path.abspath(out_prefix)), exist_ok=True)
    files = []
    for si in range(0, len(items), per):
        chunk = items[si:si + per]
        rows = -(-len(chunk) // cols)
        W, H = cell
        img = np.zeros((rows * (H + 16), cols * W, 3), np.uint8)
        for k, (vb, it) in enumerate(chunk):
            cap = cv2.VideoCapture(os.path.join(root, 'sound', vb, it['file']))
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            t = float(it['vis'].get('event_t') or it['vis'].get('hit_t') or 0.0)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
            ok, f = cap.read()
            cap.release()
            if not ok:
                continue
            f = cv2.resize(f, (W, H))
            y, x = (k // cols) * (H + 16), (k % cols) * W
            img[y:y + H, x:x + W] = f
            lab = f"{it['vis'].get('sound_type')}/{it['vis'].get('src')} {it['vis'].get('score') or 0:.2f}"
            cv2.putText(img, lab, (x + 3, y + H + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
        fn = f'{out_prefix}-{si // per + 1:02d}.jpg'
        cv2.imwrite(fn, img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        files.append(fn)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['stamp', 'counts', 'sheet'])
    ap.add_argument('--dir')
    ap.add_argument('--vibe')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--no-tags', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--only-new')
    ap.add_argument('--per', type=int, default=48)
    ap.add_argument('--ids', help='JSON list of clip ids: stamp only these (the refresh stamps its new clips)')
    a = ap.parse_args()
    if a.cmd == 'stamp':
        ids = set(json.load(open(a.ids))) if a.ids else None
        print(json.dumps(stamp(a.dir, a.vibe, a.force, a.limit, not a.no_tags, ids)))
    elif a.cmd == 'counts':
        c = counts(a.dir)
        if a.json:
            print(json.dumps(c))
        else:
            for k, v in c.items():
                print(k, json.dumps(v))
    else:
        only = set(json.load(open(a.only_new))) if a.only_new else None
        print(json.dumps(sheet(a.dir, a.vibe, a.out, only, a.per)))


if __name__ == '__main__':
    main()
