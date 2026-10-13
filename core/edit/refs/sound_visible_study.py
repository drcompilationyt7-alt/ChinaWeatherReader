#!/usr/bin/env python3
"""
Visible-source study of a sound edit (reference or ours): for every hit, what is on screen when the sound plays.

  python core/edit/refs/sound_visible_study.py --ref FB15TNFAHVU --out DIR            # a reference (sound-av.json events)
  python core/edit/refs/sound_visible_study.py --video ours.mp4 --plan plan.json --out DIR [--rot90]

Writes DIR/<name>_visible.json (per hit: core/edit/sound_visible.measure + tags + gate) and DIR/<name>_strips_NN.jpg:
one row per shot, frames from the cut to the next cut (max 14), the hit frame outlined red, the measured action box
green, the 9:16 window at focus_x blue.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import sound_visible as SV  # noqa: E402

REF_DIR = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp', 'edit-refs', 'sound')


def unrotate(src, dst, threads=4):
    if not os.path.exists(dst):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-threads', str(threads), '-i', src, '-vf', 'transpose=2',
                        '-c:v', 'libx264', '-crf', '12', '-preset', 'fast', '-c:a', 'aac', '-b:a', '256k', dst], check=True)
    return dst


def ref_events(rid):
    d = json.load(open(os.path.join(HERE, 'sound-av.json'), encoding='utf-8'))['references'][rid]
    ev = d['events']
    out = []
    for i, e in enumerate(ev):
        nxt = ev[i + 1]['t'] if i + 1 < len(ev) else d['len_s']
        out.append({'i': i, 'cut': e['t'], 'next': nxt, 'hit': (e.get('hit') or {}).get('t'),
                    'heard': 'clip_hit' if e.get('hit') else 'song_only'})
    return out


def plan_events(plan):
    out = []
    for i, s in enumerate(plan['shots']):
        h = s.get('hit') or {}
        audible = bool(s.get('sfx')) and not h.get('silent')
        out.append({'i': i, 'cut': s['t0'], 'next': s['t1'], 'hit': h.get('at') if audible else None,
                    'heard': 'clip_hit' if audible else 'song_only', 'path': s['path'], 'clip_t': h.get('clip_t'),
                    'vis': s.get('visible')})
    return out


def strips(video, evs, res, out_prefix, fps):
    import cv2
    cap = cv2.VideoCapture(video)
    vw, vh = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    CW, CH = (192, 108) if vw >= vh else (108, 192)
    rows = []
    for e, r in zip(evs, res):
        f0 = int(round(e['cut'] * fps))
        f1 = min(int(round(e['next'] * fps)), f0 + 14)
        fh = int(round(e['hit'] * fps)) if e['hit'] is not None else None
        cells = []
        for f in range(f0, max(f0 + 1, f1)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, im = cap.read()
            if not ok:
                break
            im = cv2.resize(im, (CW, CH))
            m = r.get('measure') or {}
            if fh is not None and f == fh:
                cv2.rectangle(im, (0, 0), (CW - 1, CH - 1), (0, 0, 255), 3)
                b = m.get('bbox')
                if b and b[2] > b[0]:
                    cv2.rectangle(im, (int(b[0] * CW), int(b[1] * CH)), (int(b[2] * CW), int(b[3] * CH)), (0, 255, 0), 1)
                if m.get('focus_x') is not None:
                    cx = m['focus_x'] * CW
                    cv2.rectangle(im, (int(cx - CH * 9 / 32), 0), (int(cx + CH * 9 / 32), CH - 1), (255, 128, 0), 1)
            cv2.putText(im, str(f), (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
            cells.append(im)
        while len(cells) < 14:
            cells.append(np.zeros((CH, CW, 3), np.uint8))
        lab = np.zeros((CH, 220, 3), np.uint8)
        g = r.get('gate') or [None, None]
        lines = [f"#{e['i']} {e['heard']}", f"cut {e['cut']:.3f} hit {e['hit'] if e['hit'] is not None else '-'}"]
        m = r.get('measure') or {}
        if m:
            lines += [f"off {m.get('offset_f24')} prom {m.get('prom')}", f"area {m.get('area')} fit {m.get('fit')}",
                      f"gate {'OK' if g[0] else g[1]}", f"tag {r.get('tag_src')}/{r.get('tag_non')} {r.get('tag_margin')}"]
        for k, ln in enumerate(lines):
            cv2.putText(lab, ln, (4, 14 + 16 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
        rows.append(np.hstack([lab] + cells))
    cap.release()
    paths = []
    for k in range(0, len(rows), 8):
        p = f'{out_prefix}_strips_{k // 8:02d}.jpg'
        cv2.imwrite(p, np.vstack(rows[k:k + 8]), [cv2.IMWRITE_JPEG_QUALITY, 88])
        paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default=None)
    ap.add_argument('--video', default=None)
    ap.add_argument('--plan', default=None)
    ap.add_argument('--rot90', action='store_true')
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-tags', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.ref:
        name = a.ref
        video = unrotate(os.path.join(REF_DIR, f'{a.ref}.mp4'), os.path.join(a.out, f'{a.ref}_16x9.mp4'))
        evs = ref_events(a.ref)
    else:
        name = os.path.splitext(os.path.basename(a.video))[0]
        video = unrotate(a.video, os.path.join(a.out, f'{name}_16x9.mp4')) if a.rot90 else a.video
        evs = plan_events(json.load(open(a.plan, encoding='utf-8')))
    fps = SV._probe(video)[2]
    res = []
    for e in evs:
        r = {'i': e['i'], 'heard': e['heard'], 'cut': e['cut'], 'hit': e['hit']}
        if e['hit'] is not None:
            m = SV.measure(video, e['hit'], use_cache=False, t_min=e['cut'], t_max=e['next'])
            # measure() scans the window before the hit; frames before the cut belong to the previous shot: re-run the
            # calm baseline only inside this shot by clipping the event to frames after the cut
            r['measure'] = m
            tg = None
            if not a.no_tags and m.get('event_t') is not None:
                tg = SV.tags(video, [max(e['cut'] + 0.01, m['event_t'] - 1 / fps), m['event_t']], region=m.get('bbox'))
                r['tags'] = tg
                r['tag_src'], r['tag_non'], r['tag_margin'] = SV.tag_summary(tg)
            r['gate'] = SV.gate(m, tg)
            r['score'] = SV.score(m, tg)
            r['action_after_cut_f24'] = round((m['event_t'] - e['cut']) * 24, 2) if m.get('event_t') is not None else None
            r['windup_after_cut_f24'] = round((m['windup_t'] - e['cut']) * 24, 2) if m.get('windup_t') is not None else None
        res.append(r)
    hits = [r for r in res if r['hit'] is not None]
    ok = [r for r in hits if r['gate'][0]]
    summ = {'name': name, 'shots': len(res), 'hits': len(hits), 'visible_ok': len(ok),
            'visible_share': round(len(ok) / max(1, len(hits)), 3),
            'reasons': {k: sum(1 for r in hits if r['gate'][1] == k) for k in {r['gate'][1] for r in hits if r['gate'][1]}},
            'offset_f24_abs_median': round(float(np.median([abs(r['measure']['offset_f24']) for r in hits
                                                             if r['measure'].get('offset_f24') is not None] or [0])), 2),
            'area_median': round(float(np.median([r['measure'].get('area', 0) for r in hits] or [0])), 3),
            'fit_median': round(float(np.median([r['measure'].get('fit', 0) for r in hits] or [0])), 3)}
    sp = strips(video, evs, res, os.path.join(a.out, name), fps)
    with open(os.path.join(a.out, f'{name}_visible.json'), 'w', encoding='utf-8') as f:
        json.dump({'summary': summ, 'hits': res, 'sheets': sp}, f, indent=1)
    print(json.dumps(summ))


if __name__ == '__main__':
    main()
