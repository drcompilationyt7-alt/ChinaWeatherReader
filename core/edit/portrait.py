"""Calm-portrait QA of one take (cinematic_slowmo remake, round 6): what a viewer sees on the RENDERED frame.

The planners' library tags (framing / big_face / p_talk) let a phone call, an eye macro, a lip-flap hero, a milky wide
shot and a soft head through (critics, round 2 of edit-studio-6). This measures the take itself, on the frames the
engine will show, graded with the plan's look:

  face_h / face_w / face_frac   anime face box height (share of the frame height) on the frames with a face, the share
                                of sampled frames with a face (deepghs anime face detector, library.AnimeFaces)
  mouth                         lip-flap share: consecutive native frames where the mouth region changes while the upper
                                face holds (library.mouth_flap's rule, at native fps, 2 frames apart)
  lap / lap_head / head_skip    Laplacian variance at 960 px on the graded frame: median, first 3 frames, and how many
                                native frames to skip until 3 frames in a row reach 0.8 x the median (a blurred head)
  p2 / p50 / p98 / sat          graded luma percentiles and HSV saturation median (0-255)
  drift                         native whole-frame flow (median %W per 1/30 s, planners.clip_flow)
  sig                           SigLIP zero-shot pairs in 0..1: phone, talk, eye (eye macro), wide, female, calm

    python portrait.py clip.mp4 [clip2 ...] [--t0 s --t1 s] [--look golden_soft]
Cached next to the clip as <clip>.portrait.json (keyed by t0 / t1 / look).
"""
import json
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VERSION = 3
_FACE = {}
_SIG = {}

SIG_PAIRS = {
    'phone': (['an anime character talking on a telephone', 'a person holding a phone handset to the ear',
               'an anime girl making a phone call', 'a character holding a smartphone to the ear'],
              ['an anime character portrait', 'a calm anime close-up of a face', 'an anime character looking ahead']),
    'talk': (['an anime character talking with the mouth open', 'an anime character speaking in a conversation',
              'an anime character shouting', 'an anime character laughing loudly with the mouth wide open'],
             ['an anime character with a closed mouth', 'a calm silent anime face', 'a gentle closed-mouth smile']),
    'eye': (['an extreme close-up of an anime eye', 'a macro shot of anime eyes filling the frame',
             'a close-up of one eye with a sparkling iris'],
            ['an anime portrait showing the whole face', 'an anime character head and shoulders',
             'an anime face with hair visible']),
    'wide': (['a wide shot with small distant figures', 'an anime landscape scene', 'a room interior with tiny people'],
             ['a close-up portrait of an anime character', 'an anime face filling the frame']),
    'female': (['an anime girl', 'a beautiful anime woman', 'a cute anime girl with long hair'],
               ['an anime boy', 'an anime man', 'a handsome anime guy with short hair']),
    'calm': (['a beautiful calm anime portrait in soft light', 'an iconic emotional anime close-up',
              'a gorgeous anime character with hair blowing in the wind'],
             ['a blurry low quality anime frame', 'a chaotic anime action frame', 'a dull washed-out anime frame']),
}


def _faces():
    if 'det' not in _FACE:
        try:
            import library as LIB
            p = LIB.model_path('anime_face')
            _FACE['det'] = LIB.AnimeFaces(p) if p else None
        except Exception:  # noqa: BLE001
            _FACE['det'] = None
    return _FACE['det']


def _siglip():
    if 'm' not in _SIG:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
            from transformers.utils import logging as tlog
            tlog.set_verbosity_error()
            torch.set_num_threads(min(6, os.cpu_count() or 1))
            name = 'google/siglip-base-patch16-224'
            model, proc = AutoModel.from_pretrained(name).eval(), AutoProcessor.from_pretrained(name)
            texts, idx = [], {}
            for k, (a, b) in SIG_PAIRS.items():
                idx[k] = (list(range(len(texts), len(texts) + len(a))), list(range(len(texts) + len(a),
                                                                                   len(texts) + len(a) + len(b))))
                texts += a + b
            with torch.inference_mode():
                te = model.get_text_features(**proc(text=texts, padding='max_length', return_tensors='pt'))
                te = getattr(te, 'pooler_output', te)
                te = te / te.norm(dim=-1, keepdim=True)
            _SIG['m'] = (torch, model, proc, te, idx)
        except Exception:  # noqa: BLE001
            _SIG['m'] = None
    return _SIG['m']


def _sig_scores(imgs):
    M = _siglip()
    if M is None or not imgs:
        return {}
    torch, model, proc, te, idx = M
    from PIL import Image
    pil = [Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) for im in imgs]
    with torch.inference_mode():
        e = model.get_image_features(**proc(images=pil, return_tensors='pt'))
        e = getattr(e, 'pooler_output', e)
        e = e / e.norm(dim=-1, keepdim=True)
        lg = ((e @ te.T) * model.logit_scale.exp() + model.logit_bias).numpy()
    out = {}
    for k, (a, b) in idx.items():
        y, z = lg[:, a].max(1), lg[:, b].max(1)
        v = 1 / (1 + np.exp(-(y - z) / 3.0))
        out[k] = round(float(np.median(v)), 3)
        out[k + '_max'] = round(float(v.max()), 3)
    return out


def _graded(path, look, t0, t1, fps, w=960):
    """Frames t0..t1 of the clip at native fps, through the engine's grade for `look`, at w px: BGR list."""
    import footage as FT
    import styles as S
    prep = FT.analyze(path)
    h = int(round(w * prep.h / max(1, prep.w) / 2) * 2)
    fc = f'[0:v]scale={w}:{h}[g0];'
    if look:
        lk = S.adapt_look(S.LOOKS.get(look, S.LOOKS['source']), prep)
        gamma, bloom_k = prep.exposure(lk.get('exposure') or 0.42) if lk.get('exposure') else (1.0, 1.0)
        fc += S.grade_graph(lk, w, gamma, bloom_k, (prep.p02, prep.p98) if lk.get('levels') else None)
        fc += ',format=bgr24'
    else:
        fc += '[g0]format=bgr24'
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{t0:.3f}', '-t', f'{max(0.05, t1 - t0):.3f}', '-i', path, '-an',
           '-filter_complex', fc, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-threads', '6', '-']
    b = subprocess.run(cmd, capture_output=True).stdout
    n = len(b) // (w * h * 3)
    return [np.frombuffer(b, np.uint8)[i * w * h * 3:(i + 1) * w * h * 3].reshape(h, w, 3) for i in range(n)]


def lap_rendered(path, look, t0, t1, n=8):
    """Laplacian variance as the critics measure it on the RENDER (graded at the clip's own width, the look's
    encoder-side cas / unsharp, then INTER_AREA to 960 px): median over n frames spread over [t0, t1] and the first
    3 native frames. -> (median, [head3])"""
    import footage as FT
    import styles as S
    prep = FT.analyze(path)
    W = int(prep.w) // 2 * 2
    Hh = int(round(prep.h / 2)) * 2
    fc = f'[0:v]scale={W}:{Hh}[g0];'
    lk = S.adapt_look(S.LOOKS.get(look, S.LOOKS['source']), prep) if look else None
    if lk:
        gamma, bloom_k = prep.exposure(lk.get('exposure') or 0.42) if lk.get('exposure') else (1.0, 1.0)
        fc += S.grade_graph(lk, W, gamma, bloom_k, (prep.p02, prep.p98) if lk.get('levels') else None)
        post = []
        if lk.get('cas'):
            post.append(f"cas={float(lk['cas']):.2f}")
        if lk.get('unsharp'):
            post.append(f"unsharp={lk['unsharp']}")
        fc += (',' + ','.join(post) if post else '') + ',format=bgr24'
    else:
        fc += '[g0]format=bgr24'
    def run(pre, extra):
        cmd = ['ffmpeg', '-v', 'error', '-ss', f'{t0:.3f}', '-t', f'{max(0.05, t1 - t0):.3f}', '-i', path, '-an',
               '-filter_complex', pre + fc, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-threads', '6'] + extra + ['-']
        b = subprocess.run(cmd, capture_output=True).stdout
        m = len(b) // (W * Hh * 3)
        out = []
        for i in range(m):
            f = np.frombuffer(b, np.uint8)[i * W * Hh * 3:(i + 1) * W * Hh * 3].reshape(Hh, W, 3)
            g = cv2.cvtColor(cv2.resize(f, (960, int(round(960 * Hh / W))), interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY)
            out.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        return out
    head = run('', ['-frames:v', '3'])
    fps_s = max(1.0, n / max(0.3, t1 - t0))
    fc2 = fc.replace('[0:v]scale=', f'[0:v]fps={fps_s:.3f},scale=', 1)
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{t0:.3f}', '-t', f'{max(0.05, t1 - t0):.3f}', '-i', path, '-an',
           '-filter_complex', fc2, '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-threads', '6', '-']
    b = subprocess.run(cmd, capture_output=True).stdout
    m = len(b) // (W * Hh * 3)
    body = []
    for i in range(m):
        f = np.frombuffer(b, np.uint8)[i * W * Hh * 3:(i + 1) * W * Hh * 3].reshape(Hh, W, 3)
        g = cv2.cvtColor(cv2.resize(f, (960, int(round(960 * Hh / W))), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        body.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
    if not body and not head:
        return 0.0, []
    body = body or head
    return round(float(np.median(body))), [round(x) for x in head]


def qa(path, t0=0.0, t1=None, look='golden_soft', max_s=3.0, use_cache=True):
    path = str(path)
    import footage as FT
    prep = FT.analyze(path)
    dur = float(prep.duration or 0.0) or 3.0
    t1 = min(dur, t0 + max_s) if t1 is None else min(dur, t1)
    key = f'{VERSION}|{t0:.3f}|{t1:.3f}|{look}'
    cache = path + '.portrait.json'
    store = {}
    if use_cache and os.path.exists(cache):
        try:
            store = json.load(open(cache, encoding='utf-8'))
            if key in store:
                res = store[key]
                if 'lap_r' not in res and not res.get('bad'):
                    # round 6: the rendered-frame sharpness (what the critics measure), added to old cache entries
                    res['lap_r'], res['lap_r_head'] = lap_rendered(path, look, t0, t1)
                    store[key] = res
                    try:
                        json.dump(store, open(cache, 'w', encoding='utf-8'))
                    except OSError:
                        pass
                return res
        except Exception:  # noqa: BLE001
            store = {}
    fr = _graded(path, look, t0, t1, prep.fps)
    res = dict(n=len(fr), t0=round(t0, 3), t1=round(t1, 3))
    if len(fr) < 3:
        res['bad'] = 'decode'
        return res
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]
    lap = np.array([cv2.Laplacian(g, cv2.CV_64F).var() for g in gray])
    med = float(np.median(lap))
    skip = 0
    for i in range(0, min(len(lap) - 3, 24)):
        if np.all(lap[i:i + 3] >= 0.8 * med):
            skip = i
            break
    else:
        skip = 0
    hsv = [cv2.cvtColor(f[::2, ::2], cv2.COLOR_BGR2HSV) for f in fr[::max(1, len(fr) // 12)]]
    gs = np.stack([g[::2, ::2] for g in gray[::max(1, len(fr) // 12)]])
    res.update(lap=round(med), lap_head=[round(float(x)) for x in lap[:3]], head_skip=int(skip),
               lap_p10=round(float(np.percentile(lap, 10))),
               p2=round(float(np.percentile(gs, 2)), 1), p50=round(float(np.percentile(gs, 50)), 1),
               p98=round(float(np.percentile(gs, 98)), 1),
               sat=round(float(np.median(np.concatenate([h[..., 1].ravel() for h in hsv]))), 1))
    # faces on ~10 frames; mouth flap on native consecutive pairs (2 apart) near those frames
    det = _faces()
    step = max(1, len(fr) // 10)
    fidx = list(range(0, len(fr), step))
    boxes = {}
    if det is not None:
        for i in fidx:
            bs = [b for b in det(fr[i]) if b[4] >= 0.45]
            if bs:
                boxes[i] = max(bs, key=lambda b: b[2] * b[3])
    H, W = gray[0].shape
    if boxes:
        hs = [b[3] for b in boxes.values()]
        ws = [b[2] for b in boxes.values()]
        cx = [b[0] + b[2] / 2 for b in boxes.values()]
        res.update(face_h=round(float(np.median(hs)), 3), face_w=round(float(np.median(ws)), 3),
                   face_cx=round(float(np.median(cx)), 3), face_frac=round(len(boxes) / len(fidx), 2),
                   face_edge=round(float(np.mean([(b[0] < 0.01 or b[0] + b[2] > 0.99 or b[1] + b[3] > 0.99)
                                                  for b in boxes.values()])), 2))
    else:
        res.update(face_h=0.0, face_w=0.0, face_cx=None, face_frac=0.0, face_edge=0.0)
    hits = cnt = 0
    dms = []
    if boxes:
        keys = sorted(boxes)
        for j in range(2, len(gray)):
            k = min(keys, key=lambda q: abs(q - j))
            x, y, w, h = boxes[k][:4]
            x, y, w, h = x * W, y * H, w * W, h * H
            mx0, mx1, my0, my1 = int(x + 0.22 * w), int(x + 0.78 * w) + 1, int(y + 0.6 * h), int(y + 0.97 * h) + 1
            ux0, ux1, uy0, uy1 = int(x + 0.1 * w), int(x + 0.9 * w) + 1, int(y + 0.15 * h), int(y + 0.5 * h) + 1
            a, b = gray[j].astype(np.int16), gray[j - 2].astype(np.int16)
            mm, uu = a[max(0, my0):my1, max(0, mx0):mx1], a[max(0, uy0):uy1, max(0, ux0):ux1]
            if mm.size < 30 or uu.size < 30:
                continue
            dm = float(np.abs(mm - b[max(0, my0):my1, max(0, mx0):mx1]).mean())
            du = float(np.abs(uu - b[max(0, uy0):uy1, max(0, ux0):ux1]).mean())
            cnt += 1
            dms.append(dm - du)
            if dm >= 2.5 and dm >= 1.8 * du + 0.8:
                hits += 1
    res['mouth'] = round(hits / cnt, 3) if cnt else 0.0
    res['mouth_dm'] = round(float(np.percentile(dms, 90)), 2) if dms else 0.0
    try:
        import planners as PL
        ct, ce = PL.clip_flow(path)
        m = (ct >= t0) & (ct <= t1)
        e = ce[m] if m.sum() >= 3 else ce
        res['drift'] = round(float(np.median(e)), 4)
        res['drift_p90'] = round(float(np.percentile(e, 90)), 4)
    except Exception:  # noqa: BLE001
        pass
    res['lap_r'], res['lap_r_head'] = lap_rendered(path, look, t0, t1)
    pick = [fr[int(q * (len(fr) - 1))] for q in (0.15, 0.5, 0.85)]
    res['sig'] = _sig_scores([cv2.resize(p, (480, int(480 * H / W))) for p in pick])
    store[key] = res
    try:
        json.dump(store, open(cache, 'w', encoding='utf-8'))
    except OSError:
        pass
    return res



# ---------------------------------------------------------------------------------------------------------------
# remake round 8 (critics, edit-studio-6 round 3: a Yor + Loid gag two-shot, an over-the-shoulder conversation and a
# Mitsuri + Obanai fist-pose two-shot filled _sX2's solo portrait slots; a cut landed on a blink): who is in the frame.
PEOPLE_VERSION = 1
_SIG2 = {}
SIG2_PAIRS = {
    'twoshot': (['two anime characters in the same frame', 'a couple of anime characters side by side',
                 'an over-the-shoulder shot of two people talking', 'the back of a head in the foreground facing a girl',
                 'two people having a conversation'],
                ['a single anime character alone in the frame', 'a solo portrait of one anime girl',
                 'one character with an empty background']),
    'gag': (['a comedic anime reaction face', 'a surprised anime face with a wide open mouth',
             'a chibi gag expression', 'an embarrassed blushing anime face screaming', 'a funny exaggerated anime face'],
            ['a calm beautiful anime face', 'a serene anime portrait', 'a gentle emotional anime close-up']),
}


def _siglip2():
    if 'm' not in _SIG2:
        M = _siglip()
        if M is None:
            _SIG2['m'] = None
        else:
            torch, model, proc, _te, _idx = M
            texts, idx = [], {}
            pairs = dict(SIG2_PAIRS)
            pairs['female'] = SIG_PAIRS['female']
            for k, (a, b) in pairs.items():
                idx[k] = (list(range(len(texts), len(texts) + len(a))),
                          list(range(len(texts) + len(a), len(texts) + len(a) + len(b))))
                texts += a + b
            with torch.inference_mode():
                te = model.get_text_features(**proc(text=texts, padding='max_length', return_tensors='pt'))
                te = getattr(te, 'pooler_output', te)
                te = te / te.norm(dim=-1, keepdim=True)
            _SIG2['m'] = (torch, model, proc, te, idx)
    return _SIG2['m']


def _sig2(imgs, keys=None):
    M = _siglip2()
    if M is None or not imgs:
        return {}
    torch, model, proc, te, idx = M
    from PIL import Image
    pil = [Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) for im in imgs]
    with torch.inference_mode():
        e = model.get_image_features(**proc(images=pil, return_tensors='pt'))
        e = getattr(e, 'pooler_output', e)
        e = e / e.norm(dim=-1, keepdim=True)
        lg = ((e @ te.T) * model.logit_scale.exp() + model.logit_bias).numpy()
    out = {}
    for k, (a, b) in idx.items():
        if keys and k not in keys:
            continue
        y, z = lg[:, a].max(1), lg[:, b].max(1)
        out[k] = [round(float(x), 3) for x in 1 / (1 + np.exp(-(y - z) / 3.0))]
    return out


def people(path, t0=0.0, t1=None, use_cache=True):
    """Who is in the take: faces per frame (anime face detector), the second face's size, each big face's SigLIP
    'female' score on its own crop (a man's face in the frame), SigLIP twoshot / over-the-shoulder and gag scores on
    the whole frame, and a blink check near the in-point (the eye band loses its dark iris pixels for a few frames).
      n_faces_max, face2_h (second-largest face height, share of H), male_face (max over faces >= 0.12 H of
      1 - female), twoshot, gag (medians over 3 frames), blink_s (first time in 0..0.4 s after t0 where the eye band's
      dark share drops under 0.55 x the take's median, or None). Cached as <clip>.people.json."""
    path = str(path)
    import footage as FT
    prep = FT.analyze(path)
    dur = float(prep.duration or 0.0) or 3.0
    t1 = min(dur, t0 + 3.0) if t1 is None else min(dur, t1)
    key = f'{PEOPLE_VERSION}|{t0:.3f}|{t1:.3f}'
    cache = path + '.people.json'
    store = {}
    if use_cache and os.path.exists(cache):
        try:
            store = json.load(open(cache, encoding='utf-8'))
            if key in store:
                return store[key]
        except Exception:  # noqa: BLE001
            store = {}
    fr = _graded(path, None, t0, t1, prep.fps)
    res = dict(t0=round(t0, 3), t1=round(t1, 3), n=len(fr))
    if len(fr) < 3:
        res['bad'] = 'decode'
        return res
    H, W = fr[0].shape[:2]
    det = _faces()
    step = max(1, len(fr) // 8)
    fidx = list(range(0, len(fr), step))
    nf, f2, crops, main = [], [], [], {}
    for i in fidx:
        bs = [b for b in (det(fr[i]) if det is not None else []) if b[4] >= 0.45 and b[3] >= 0.06]
        nf.append(len(bs))
        f2.append(float(bs[1][3]) if len(bs) > 1 else 0.0)
        if bs:
            main[i] = bs[0]
        for b in bs[:3]:
            if b[3] >= 0.12:
                x, y, w, h = b[0] * W, b[1] * H, b[2] * W, b[3] * H
                x0, y0 = int(max(0, x - 0.25 * w)), int(max(0, y - 0.25 * h))
                x1, y1 = int(min(W, x + 1.25 * w)), int(min(H, y + 1.3 * h))
                if x1 - x0 > 16 and y1 - y0 > 16:
                    crops.append(cv2.resize(fr[i][y0:y1, x0:x1], (224, 224), interpolation=cv2.INTER_AREA))
    res.update(n_faces_max=int(max(nf) if nf else 0), n_faces_med=float(np.median(nf)) if nf else 0.0,
               face2_h=round(float(np.median(f2)) if f2 else 0.0, 3), face2_h_max=round(float(max(f2)) if f2 else 0.0, 3))
    crops = crops[:12]
    if crops:
        s = _sig2(crops, keys=('female',)).get('female') or []
        res['male_face'] = round(float(1.0 - min(s)), 3) if s else 0.0
    else:
        res['male_face'] = 0.0
    pick = [cv2.resize(fr[int(q * (len(fr) - 1))], (480, int(480 * H / W))) for q in (0.15, 0.5, 0.85)]
    s2 = _sig2(pick, keys=('twoshot', 'gag'))
    for k in ('twoshot', 'gag'):
        v = s2.get(k) or []
        res[k] = round(float(np.median(v)), 3) if v else 0.0
        res[k + '_max'] = round(float(max(v)), 3) if v else 0.0
    # blink: dark share of the eye band (face box rows 0.35-0.62) per native frame over the first 0.4 s
    res['blink_s'] = None
    if main:
        keys_ = sorted(main)
        dark = []
        for j in range(len(fr)):
            k = min(keys_, key=lambda q: abs(q - j))
            x, y, w, h = main[k][:4]
            band = cv2.cvtColor(fr[j][int((y + 0.35 * h) * H):int((y + 0.62 * h) * H) + 1,
                                      int((x + 0.1 * w) * W):int((x + 0.9 * w) * W) + 1], cv2.COLOR_BGR2GRAY)
            dark.append(float((band < 70).mean()) if band.size > 50 else np.nan)
        dark = np.array(dark)
        med = float(np.nanmedian(dark)) if np.isfinite(dark).any() else 0.0
        if med > 0.02:
            n04 = int(min(len(dark), round(0.4 * prep.fps)))
            low = np.where(dark[:n04] < 0.55 * med)[0]
            if len(low):
                res['blink_s'] = round(float(low[0]) / prep.fps, 3)
        res['eye_dark_med'] = round(med, 3)
    store[key] = res
    try:
        json.dump(store, open(cache, 'w', encoding='utf-8'))
    except OSError:
        pass
    return res

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--t0', type=float, default=0.0)
    ap.add_argument('--t1', type=float)
    ap.add_argument('--look', default='golden_soft')
    ap.add_argument('--nocache', action='store_true')
    a = ap.parse_args()
    for p in a.paths:
        print(os.path.basename(os.path.dirname(p)) + '/' + os.path.basename(p),
              json.dumps(qa(p, a.t0, a.t1, None if a.look == 'none' else a.look, use_cache=not a.nocache)))


# ---------------------------------------------------------------------------------------------------------------
# cinematic_slowmo round 10 (critics, BLOCKING x2: Kaguya's mouth opens and closes five times in a slowed shot, Yor talks
# through a whole slot; the flap-share test above (mouth_dm) missed both): count the open / close CYCLES of the mouth box.
MOUTH_VERSION = 1


def mouth_cycles(path, t0=0.0, t1=3.0, use_cache=True):
    """dict(cycles, amp, n): the mouth region's luma (relative to the upper face, so exposure changes cancel) over the take
    at the native frame rate, face box per ~4 frames (anime_face detector), smoothed over 3 frames; `amp` = p90 - p10 of
    the series (grey levels); cycles = full excursions across the mid line with a 25 % hysteresis (0 when amp < 7).
    A talking / laughing take reads >= 2, a closed-mouth take with drifting hair 0-1. Cached next to the clip."""
    path = str(path)
    cache = path + '.mouth.json'
    key = f'{MOUTH_VERSION}|{t0:.2f}|{t1:.2f}'
    store = {}
    if use_cache and os.path.exists(cache):
        try:
            store = json.load(open(cache, encoding='utf-8'))
            if key in store:
                return store[key]
        except Exception:  # noqa: BLE001
            store = {}
    det = _faces()
    res = dict(cycles=0, amp=0.0, n=0, faces=0)
    try:
        import footage as FT
        prep = FT.analyze(path)
        fr = _graded(path, None, t0, min(float(prep.duration or t1), t1), prep.fps, w=640)
    except Exception:  # noqa: BLE001
        fr = []
    if det is None or len(fr) < 8:
        return res
    H, W = fr[0].shape[:2]
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in fr]
    boxes = {}
    for i in range(0, len(fr), 4):
        bs = [b for b in det(fr[i]) if b[4] >= 0.45]
        if bs:
            boxes[i] = max(bs, key=lambda b: b[2] * b[3])
    res.update(n=len(fr), faces=len(boxes))
    if len(boxes) < 3:
        return res
    keys = sorted(boxes)
    ser = []
    for j in range(len(gray)):
        k = min(keys, key=lambda q: abs(q - j))
        x, y, w, h = [float(v) for v in boxes[k][:4]]
        x, y, w, h = x * W, y * H, w * W, h * H
        m = gray[j][max(0, int(y + 0.62 * h)):int(y + 0.92 * h) + 1, max(0, int(x + 0.3 * w)):int(x + 0.7 * w) + 1]
        u = gray[j][max(0, int(y + 0.2 * h)):int(y + 0.5 * h) + 1, max(0, int(x + 0.15 * w)):int(x + 0.85 * w) + 1]
        if m.size < 20 or u.size < 20:
            ser.append(ser[-1] if ser else 0.0)
            continue
        ser.append(float(m.std()) + 0.0 * float(u.mean()))        # an open mouth adds dark / red interior: texture in the box
    s = np.convolve(np.array(ser), np.ones(3) / 3.0, mode='same')
    lo, hi = float(np.percentile(s, 10)), float(np.percentile(s, 90))
    amp = hi - lo
    cyc, state, hyst = 0, 0, 0.25 * amp
    mid = 0.5 * (lo + hi)
    for v in s:
        if state <= 0 and v > mid + hyst:
            state = 1
            cyc += 0.5
        elif state >= 0 and v < mid - hyst:
            if state == 1:
                cyc += 0.5
            state = -1
    res.update(cycles=round(float(cyc), 1), amp=round(amp, 2) if amp >= 0 else 0.0)
    if amp < 3.0:
        res['cycles'] = 0
    store[key] = res
    try:
        json.dump(store, open(cache, 'w', encoding='utf-8'))
    except OSError:
        pass
    return res
