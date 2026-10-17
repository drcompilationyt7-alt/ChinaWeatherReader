"""Import BY-EYE approved sound-hit moments (hit_scan mini clips) straight into a sound pool root.

Why (2026-10-15): 304 of the 333 moments hit_scan kept never reached the pool: the generic library run on the minis
rejects them for look reasons (soft / plain / junk / static / short scene windows), which say nothing about whether
the hit shows its cause. The owner's bar for a sound hit is the by-eye check (one-word action on screen), so the
approved minis skip the look gates. The SAFETY gates stay automatic and per shot, plus the burned-in text check:
  - NudeNet (library.nude_level) on frames every 0.125 s: any exposure / swimwear-like coverage rejects;
  - SigLIP "unsafe rather than safe" (the library's own prompt groups, anime extras): max >= 0.93 or mean >= 0.75
    rejects (library non-strict thresholds; gore allowed for sound hits since 2026-10-12);
  - edit_fx.text_bands: any band > 2 % rejects.
Each imported clip gets its approved moment in core/edit/assets/sound-hit-allowlist.json (clip-relative t).

usage: python core/edit/hit_import.py --work output/edit-pools-grow/work --approved approved.json --root STAGE_ROOT
approved.json = {mini_name: [action_tag, word]} (mini_name = file stem in WORK/mini)
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for d in ('core/edit', 'core/meme', 'core/pools'):
    sys.path.insert(0, os.path.join(ROOT, d))

ALLOW_P = os.path.join(ROOT, 'core', 'edit', 'assets', 'sound-hit-allowlist.json')
CRISP_TAGS = {'clap', 'object', 'chain'}


def frames(path, step=0.125):
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    every = max(1, int(round(fps * step)))
    out, i = [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i % every == 0:
            out.append(f)
        i += 1
    cap.release()
    return out, fps, i / fps


def safety(path):
    """(ok, info): NudeNet every 0.125 s + SigLIP unsafe-vs-safe on 6 frames + burned-in text bands."""
    import numpy as np
    import cv2
    import library as L
    import clip_judge as CJ
    import edit_fx as FX
    from PIL import Image
    if not L.cf.PATHS:
        L.cf.load_models()
    fr, fps, dur = frames(path)
    if not fr:
        return False, {'why': 'decode'}
    nude = max(L.nude_level(f) for f in fr)
    if nude:
        return False, {'why': 'nude', 'level': nude}
    J = CJ.Judge(check_vibe=False)
    un = CJ.UNSAFE + L.UNSAFE_EXTRA['anime']
    sf = CJ.SAFE + L.SAFE_EXTRA['anime']
    pick = [fr[int(k)] for k in np.linspace(0, len(fr) - 1, min(6, len(fr)))]
    imgs = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in pick]
    with J.torch.inference_mode():
        x = J.proc(images=imgs, return_tensors='pt')
        e = CJ._tensor(J.model.get_image_features(**x))
        e = e / e.norm(dim=-1, keepdim=True)
        tt = J.proc(text=un + sf, padding='max_length', return_tensors='pt')
        te = CJ._tensor(J.model.get_text_features(**tt))
        te = te / te.norm(dim=-1, keepdim=True)
        lg = ((e @ te.T) * J.model.logit_scale.exp() + J.model.logit_bias).numpy()
    v = 1 / (1 + np.exp(-(lg[:, :len(un)].max(1) - lg[:, len(un):].max(1))))
    um, ux = float(v.mean()), float(v.max())
    if ux >= 0.93 or um >= 0.75:
        return False, {'why': 'unsafe', 'mean': round(um, 3), 'max': round(ux, 3)}
    t, b = FX.text_bands(path)
    if max(float(t or 0), float(b or 0)) > 0.02:
        return False, {'why': 'text', 'band': [t, b]}
    return True, {'unsafe': [round(um, 3), round(ux, 3)], 'nude': 0, 'text_band': [t, b], 'dur': round(dur, 3), 'fps': fps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--approved', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    import vibe_pool as VP
    side = json.load(open(os.path.join(a.work, 'mini.json'), encoding='utf-8'))
    appr = json.load(open(a.approved, encoding='utf-8'))
    allow = json.load(open(ALLOW_P, encoding='utf-8'))
    led, bl = VP.load_ledger(), VP.load_blocklist()
    res = {}
    mans = {}
    for name, (tag, word) in appr.items():
        m = side.get(name)
        p = os.path.join(a.work, 'mini', name + '.mp4')
        if not m or not os.path.isfile(p):
            res[name] = 'missing'
            continue
        vibe = 'crisp' if tag in CRISP_TAGS else 'impact'
        if vibe not in mans:
            mans[vibe] = VP.load_manifest('sound', vibe, root=a.root)
        man = mans[vibe]
        others = [it for vn, mm in mans.items() if vn != vibe for it in mm['items']]
        sid, st = m['source_id'], float(m['offset'])
        ok, info = safety(p)
        if not ok:
            res[name] = info
            continue
        en = st + info['dur']
        iid = f'c_{sid}_{int(round(st * 1000))}'
        if iid in {it['id'] for it in man['items']}:
            res[name] = 'dup_id'
            continue
        if VP.moment_taken(sid, st, en, led, man['items'] + others):
            res[name] = 'moment_used'
            continue
        if VP.blocked(sid, st, en, bl):
            res[name] = 'blocked'
            continue
        res[name] = {'id': iid, 'vibe': vibe, **info}
        if a.dry:
            continue
        vd = VP.vibe_dir('sound', vibe, a.root)
        os.makedirs(os.path.join(vd, 'clips'), exist_ok=True)
        rel = f'clips/{iid}.mp4'
        shutil.copy2(p, os.path.join(vd, rel))
        man['items'].append({
            'id': iid, 'file': rel, 'source_id': sid, 'start': round(st, 3), 'end': round(en, 3),
            'moment': f'{sid}#{int(st)}', 'source_url': f'https://youtu.be/{sid}' if len(sid) == 11 else None,
            'source_title': m.get('title'), 'source_channel': m.get('channel'), 'views': m.get('views'),
            'duration': round(info['dur'], 3), 'has_audio': True, 'has_text': False, 'text_band': info['text_band'],
            'hit_scan': {'tag': m.get('tag'), 'score': m.get('score')},
            'safety': {'gate': 'pass', 'checked': 'hit_import: NudeNet every 0.125 s + SigLIP unsafe/safe (library prompts) + text bands',
                       'unsafe': info['unsafe']},
            'by_eye': {'action': tag, 'word': word, 't': m['hit_t']},
            'sfx': [{'t': float(m['hit_t'])}], 'query': 'hit_import (by-eye approved)',
            'status': 'fresh', 'added': VP.now()[:10], 'used_at': None, 'used_in': None,
        })
        allow['clips'][iid] = [{'t': round(float(m['hit_t']), 3), 'action': tag, 'word': word,
                                'note': f"by-eye dense strip {VP.now()[:10]} ({m.get('title', '')[:50]})"}]
    if not a.dry:
        for vibe, man in mans.items():
            VP.save_manifest('sound', vibe, man, root=a.root)
        VP.write_index(a.root)
        with open(ALLOW_P, 'w', encoding='utf-8') as f:
            json.dump(allow, f, indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, default=str))


if __name__ == '__main__':
    main()
