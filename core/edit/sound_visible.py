#!/usr/bin/env python3
"""
Visible-source gate for sound-edit hits (owner 2026-10-12: "I need to know WHAT IS MAKING THE SOUND").

A clip hit may be AUDIBLE in a sound edit only when the viewer SEES its cause in the same frames: the gun cocked on the
reload click, the muzzle flash on the shot, the blade on the clash, the fist landing on the punch. Study of the reference
(FB15TNFAHVU, Shape of You): core/edit/refs/sound-visible-source.md.

measure(path, t) looks at the clip around the hit (source frames, held drawings removed):
  - residual motion per frame: Farneback flow at 192 px with the camera move (median flow) removed, plus the frame
    difference; A[f] = mean of the strongest 8 % of the residual magnitude (a local action, not a global pan / fade);
  - the action EVENT: the frame within +-EVENT_WIN (24 fps frames) of the transient with the biggest change of A
    (onset = rise, contact = sudden stop) -> its offset to the hit in 24-fps frames and its prominence over the
    shot's calm baseline (A median 0.5-0.2 s before the hit);
  - the action REGION at the event: the residual-motion mask -> bbox, area share, the best 9:16 window (centre x
    `focus_x`, energy share inside it `fit`);
  - a pure FX flash: a global luma jump with motion everywhere and no local region;
  - the wind-up start: the first frame of the motion rise before the event (the cut should come a few frames earlier);
  - optional SigLIP tags (core/meme/clip_judge.py model, CPU) on the event frame: what is in frame at the transient
    (gun / reload / sword / fist / kick / clap / ... vs face / eyes / scenery / effect-only).

gate(m) -> (ok, reason): ok when the action is in sync (|offset| <= 2 frames@24), prominent, large enough (close-up /
medium), mostly inside one 9:16 window, not a flash-only frame, and (with tags) the frame shows an object / action
class rather than a face / eyes / scenery / effect only.

    python core/edit/sound_visible.py --clip clip.mp4 --t 1.23 [--tags]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
VERSION = 7
TAG_VERSION = 6           # tags cache (prompts); measure changes do not invalidate it
AW = 192                     # analysis width (px)
EVENT_WIN = 2.0              # frames@24 either side of the transient
SYNC_MAX = 2.0               # |event - hit| frames@24 for a visible source (refs: action within +-2 frames)
PROM_MIN = float(os.environ.get('SOUND_VIS_PROM', '1.8'))
AREA_MIN = float(os.environ.get('SOUND_VIS_AREA', '0.05'))      # bbox area share of the frame (close-up / medium)
FIT_MIN = float(os.environ.get('SOUND_VIS_FIT', '0.5'))        # residual-motion energy inside the best 9:16 window
STRONG_MARGIN, STRONG_SRC, PROM_MIN_STRONG = 4.0, -2.5, 1.0
TAG_MARGIN = float(os.environ.get('SOUND_VIS_MARGIN', '1.9'))   # best source tag minus best non-source tag
SRC_MIN = float(os.environ.get('SOUND_VIS_SRC', '-3.8'))         # best source tag logit (absolute)
FACE_MARGIN = float(os.environ.get('SOUND_VIS_FACE', '-0.5'))   # source tag minus face tag (logits)
CROP_W = (1080 / 1920) * (9 / 16)                               # 9:16 window width as a share of a 16:9 frame (0.316)

SOURCE_TAGS = {
    'gun': ['a close-up of a hand holding a gun', 'a gun being fired with a muzzle flash', 'a pistol aimed at the camera',
            'a rifle or shotgun firing'],
    'reload': ['a close-up of hands reloading a gun', 'a hand cocking a pistol slide', 'a magazine inserted into a gun',
               'a shotgun being pumped'],
    'sword': ['two swords clashing with sparks', 'a sword slash', 'a character drawing a katana from its sheath',
              'a blade swung in a fight'],
    'fist': ['a fist punching a face', 'a punch landing on a body', 'a close-up of a clenched fist hitting'],
    'kick': ['a kick hitting an opponent', 'a foot stomping the ground'],
    'clap': ['hands clapping', 'fingers snapping', 'a hand slamming a table'],
    'object': ['an object being struck', 'a door slammed shut', 'a lighter flicked open', 'a coin flipped by a thumb',
               'a chess piece placed on a board', 'a cup set down on a table'],
    'impact': ['a character smashed into a wall', 'a body hitting the ground hard', 'a car crash'],
    # owner 2026-10-12: breaking / cracking sounds, as in the reference (crystal crushed in a fist, an earring shattering)
    'glass': ['glass shattering into shards', 'a crystal crushed in a hand', 'a window breaking', 'a glass cup breaking'],
    'mask': ['a mask cracking on a face', 'a white mask shattering into pieces', 'a cracked mask falling off a face'],
    'crack': ['ice cracking', 'a stone wall cracking', 'the ground cracking apart', 'an object breaking into pieces'],
    'chain': ['metal chains rattling', 'a chain wrapping around something', 'a chain pulled tight'],
    'metal': ['a metal clang with sparks', 'a weapon hitting armour', 'a shield blocking a blow', 'a metal can hit by a bullet'],
}
NONSOURCE_TAGS = {
    'face': ['a close-up of an anime face', 'a character looking at the camera', 'a character shouting'],
    'eyes': ['an extreme close-up of eyes'],
    'scenery': ['a landscape or building with no people', 'the sky', 'an empty room'],
    'wide': ['a wide shot with tiny distant characters', 'a dark wide battlefield seen from far away'],
    'fx': ['an abstract flash of light', 'an energy aura effect filling the frame', 'a white screen flash',
           'an explosion filling the whole frame'],
}


def log(*a):
    print('[sound_visible]', *a, file=sys.stderr, flush=True)


def _probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate:format=duration', '-of', 'json', path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    s = d['streams'][0]
    n, k = s['r_frame_rate'].split('/')
    return int(s['width']), int(s['height']), float(n) / max(1.0, float(k)), float(d['format'].get('duration') or 0)


def _decode(path, t0, t1, w, h, scale_w=AW, rgb=False):
    sh = int(round(scale_w * h / w / 2) * 2)
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{max(0.0, t0):.4f}', '-i', path, '-t', f'{t1 - max(0.0, t0):.4f}',
                        '-an', '-vf', f'scale={scale_w}:{sh}', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                       capture_output=True, timeout=120)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (scale_w * sh * 3)
    return a[:n * scale_w * sh * 3].reshape(n, sh, scale_w, 3)


def _cache_path(path, t, extra='', ver=None):
    st = os.stat(path)
    d = os.environ.get('SOUND_VIS_CACHE') or os.path.join(os.environ.get('TEMP', '/tmp'), 'sound-visible')
    os.makedirs(d, exist_ok=True)
    k = hashlib.md5(f'{os.path.abspath(path)}|{st.st_size}|{st.st_mtime}|{round(float(t), 3)}|{extra}|{VERSION if ver is None else ver}'.encode()).hexdigest()
    return os.path.join(d, k + '.json')


def measure(path, t, box=None, use_cache=True, t_min=None, t_max=None):
    """Visible action at the clip hit t (s in the file). box = the clip's content box [x0, y0, x1, y1] (0-1) or None.
    t_min / t_max: the shot's own range (scene / picture cuts): frames outside it are never looked at."""
    import cv2
    cp = _cache_path(path, t, f'{t_min}|{t_max}|{box}')
    if use_cache and os.path.exists(cp):
        try:
            return json.load(open(cp, encoding='utf-8'))
        except Exception:  # noqa: BLE001
            pass
    w, h, fps, dur = _probe(path)
    t0 = max(0.0, t - 0.6, (t_min + 0.5 / fps) if t_min is not None else 0.0)
    t1 = min(dur, t + 0.3, (t_max - 0.5 / fps) if t_max is not None else 1e9)
    fr = _decode(path, t0, t1, w, h)
    out = {'t': round(float(t), 4), 'fps': round(fps, 3), 'ok_decode': len(fr) >= 4}
    if len(fr) < 4:
        return out
    times = t0 + np.arange(len(fr)) / fps
    if box:
        x0, y0, x1, y1 = box
        H_, W_ = fr.shape[1:3]
        fr = fr[:, int(y0 * H_):max(int(y0 * H_) + 8, int(y1 * H_)), int(x0 * W_):max(int(x0 * W_) + 8, int(x1 * W_))]
    g = fr.astype(np.float32).mean(axis=3)
    # held drawings: keep only frames that differ from the previous kept one (anime on 2s / 3s, 60 fps files)
    keep = [0]
    for k in range(1, len(g)):
        if np.abs(g[k] - g[keep[-1]]).mean() > 0.6:
            keep.append(k)
    n = len(g)
    A = np.zeros(n)          # local residual motion (strongest 8 % of the pixels); held frames = 0 (no new motion)
    S = np.zeros(n)          # local brightening (99.5th pct of the positive luma change): sparks, muzzle flashes
    G = np.zeros(n)          # global share of moving pixels
    L = g.mean(axis=(1, 2)) / 255.0
    maps = {}
    for a, b in zip(keep[:-1], keep[1:]):
        f = cv2.calcOpticalFlowFarneback(g[a].astype(np.uint8), g[b].astype(np.uint8), None, 0.5, 3, 13, 3, 5, 1.1, 0)
        f = f - np.median(f.reshape(-1, 2), axis=0)                  # remove the camera pan
        m = np.hypot(f[..., 0], f[..., 1])
        dd = g[b] - g[a]
        d = np.abs(dd) / 16.0
        m = m + 0.5 * d
        thr = np.percentile(m, 92)
        A[b] = float(m[m >= thr].mean())
        S[b] = float(np.percentile(np.clip(dd, 0, None), 99.5)) / 255.0
        G[b] = float((m > max(1.0, 0.5 * thr)).mean())
        maps[b] = m
    k_hit = int(np.argmin(np.abs(times - t)))
    scale24 = fps / 24.0
    kf = sorted(maps)
    shot_med = float(np.median(A[kf])) if kf else 0.0
    base = shot_med
    win = [k for k in keep[1:] if abs(k - k_hit) <= EVENT_WIN * scale24 + 0.5]
    if not win:
        out.update(prom=0.0, offset_f24=None, reason_hint='held_drawing')
        _save(cp, out)
        return out
    prev_of = {b: a for a, b in zip(keep[:-1], keep[1:])}
    back = int(round(3 * scale24))

    def strength(k):
        """(score, kind): onset = motion / brightening over the frames just before (holds count as still);
        contact = a move that stops at k; shot = motion over the shot's typical motion."""
        pre = A[max(0, k - back):k]
        pre_l = float(pre.max()) if len(pre) else shot_med
        pre_s = S[max(0, k - back):k]
        pre_sl = float(pre_s.max()) if len(pre_s) else 0.0
        onset = A[k] / (pre_l + 0.5)
        spark = (S[k] - pre_sl) / 0.12 if S[k] >= 0.35 else 0.0      # a new bright spot (>= 35 % luma rise)
        post = A[k + 1:k + 1 + back]
        contact = (A[k] / (float(post.max()) + 0.5)) * 0.8 if len(post) and A[k] > shot_med else 0.0
        shot = A[k] / (shot_med + 0.35)
        c = {'onset': onset, 'spark': spark, 'contact': contact, 'shot': shot}
        kk = max(c, key=c.get)
        return float(c[kk]), kk
    best, best_s, kind = None, -1.0, None
    for k in win:
        s_, kk = strength(k)
        s_ *= 1.0 - 0.06 * abs(k - k_hit) / scale24                    # ties go to the frame nearest the hit
        if s_ > best_s:
            best, best_s, kind = k, s_, kk
    k_ev = best
    peak = float(A[k_ev])
    prom = float(best_s)
    off24 = float((k_ev - k_hit) / scale24)
    # wind-up start: walk back from the event while the motion is above the calm level
    k_w = k_ev
    for k in reversed(keep[:keep.index(k_ev)]):
        if A[k] <= base * 1.25 + 0.2 or (t - times[k]) > 0.45:
            break
        k_w = k
    windup_s = float(times[k_ev] - times[k_w])
    # region at the event: union of the event frame and the one before (a contact is the end of a move)
    m = maps[k_ev].copy()
    pk = prev_of.get(k_ev)
    if pk in maps:
        m = np.maximum(m, maps[pk])
    hh, ww = m.shape
    thr = max(np.percentile(m, 90), 1.2 * float(np.median(m)) + 0.8)
    mask = (m >= thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    energy = m * mask
    tot = float(energy.sum()) + 1e-6
    col = energy.sum(axis=0)
    cw = max(4, int(round(hh * 9 / 16)))                            # 9:16 window (full height) in analysis px
    cw = min(ww, cw)
    cs = np.concatenate([[0.0], np.cumsum(col)])
    win_e = cs[cw:] - cs[:-cw]
    jx = int(np.argmax(win_e))
    fit = float(win_e[jx] / tot)
    focus_x = (jx + cw / 2) / ww
    # bbox of the components holding 85 % of the energy
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    comps = sorted([(float(energy[lab == i].sum()), i) for i in range(1, nlab)], reverse=True)
    acc, xs0, ys0, xs1, ys1 = 0.0, ww, hh, 0, 0
    for e_, i in comps:
        x, y, bw, bh, _a = stats[i]
        xs0, ys0, xs1, ys1 = min(xs0, x), min(ys0, y), max(xs1, x + bw), max(ys1, y + bh)
        acc += e_
        if acc >= 0.85 * tot:
            break
    bbox = [xs0 / ww, ys0 / hh, xs1 / ww, ys1 / hh] if xs1 > xs0 else [0, 0, 0, 0]
    area = float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    mask_share = float(mask.mean())
    # pure FX flash: a luma jump with motion everywhere at the event
    kp = prev_of.get(k_ev, max(0, k_ev - 1))
    luma_jump = float(L[k_ev] - L[kp])
    flash = bool(abs(luma_jump) >= 0.12 and G[k_ev] >= 0.6)
    # how lit the action is (dark smears / silhouettes read as nothing on a phone)
    bx = [int(bbox[0] * ww), int(bbox[1] * hh), max(int(bbox[2] * ww), int(bbox[0] * ww) + 2), max(int(bbox[3] * hh), int(bbox[1] * hh) + 2)]
    reg = g[k_ev][bx[1]:bx[3], bx[0]:bx[2]] / 255.0
    out.update(region_luma=round(float(reg.mean()), 3) if reg.size else None, frame_luma=round(float(L[k_ev]), 3))
    out.update(spark=round(float(S[k_ev]), 3), base=round(base, 3), peak=round(peak, 3), prom=round(prom, 2), offset_f24=round(off24, 2), kind=kind,
               event_t=round(float(times[k_ev]), 4), windup_t=round(float(times[k_w]), 4), windup_s=round(windup_s, 3),
               bbox=[round(x, 3) for x in bbox], area=round(area, 3), mask_share=round(mask_share, 3),
               fit=round(fit, 3), focus_x=round(focus_x, 3), global_share=round(float(G[k_ev]), 3),
               luma_jump=round(luma_jump, 3), flash=flash, held=len(keep) < 0.5 * n)
    _save(cp, out)
    return out


def _save(cp, d):
    try:
        with open(cp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ SigLIP tags (CPU)

_J = {}


def _judge():
    if 'j' not in _J:
        sys.path.insert(0, os.path.join(ROOT, 'core', 'meme'))
        import clip_judge as CJ
        import torch
        torch.set_num_threads(int(os.environ.get('EDIT_THREADS') or 4))
        J = CJ.Judge(check_vibe=False)
        names, prompts = [], []
        for grp in (SOURCE_TAGS, NONSOURCE_TAGS):
            for k, ps in grp.items():
                for p in ps:
                    names.append(k)
                    prompts.append(p)
        with J.torch.inference_mode():
            tt = J.proc(text=prompts, padding='max_length', return_tensors='pt')
            te = CJ._tensor(J.model.get_text_features(**tt))
            te = te / te.norm(dim=-1, keepdim=True)
        _J['j'] = (J, CJ, names, te)
    return _J['j']


def tags(path, t_frames, box=None, region=None):
    """Max logit per tag class over the frames at times t_frames (the event frame and the one before)."""
    from PIL import Image
    tag_c = os.path.splitext(_cache_path(path, round(sum(t_frames), 3), f'{box}|{region}', ver=TAG_VERSION))[0] + '.tags.json'
    if os.path.exists(tag_c):
        try:
            return json.load(open(tag_c, encoding='utf-8'))
        except Exception:  # noqa: BLE001
            pass
    J, CJ, names, te = _judge()
    w, h, fps, dur = _probe(path)
    imgs = []
    for tt in t_frames:
        fr = _decode(path, max(0.0, tt), max(0.0, tt) + 1.0 / fps, w, h, scale_w=448)
        if len(fr):
            im = fr[0]
            if box:
                H_, W_ = im.shape[:2]
                im = im[int(box[1] * H_):int(box[3] * H_) or H_, int(box[0] * W_):int(box[2] * W_) or W_]
            if region and region[2] > region[0]:
                # the action region at the event (sound_visible bbox), grown 30 % and to >= 40 % of the frame
                H_, W_ = im.shape[:2]
                cx, cy = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
                hw = max(0.2, (region[2] - region[0]) * 0.65)
                hh = max(0.2, (region[3] - region[1]) * 0.65)
                im = im[int(max(0, cy - hh) * H_):int(min(1, cy + hh) * H_), int(max(0, cx - hw) * W_):int(min(1, cx + hw) * W_)]
            imgs.append(Image.fromarray(np.ascontiguousarray(im)))
    if not imgs:
        return {}
    with J.torch.inference_mode():
        x = J.proc(images=imgs, return_tensors='pt')
        e = CJ._tensor(J.model.get_image_features(**x))
        e = e / e.norm(dim=-1, keepdim=True)
        lg = ((e @ te.T) * J.model.logit_scale.exp() + J.model.logit_bias).numpy().max(axis=0)
    res = {}
    for k, v in zip(names, lg):
        res[k] = round(max(res.get(k, -99.0), float(v)), 2)
    _save(tag_c, res)
    return res


def tag_summary(tg):
    if not tg:
        return None, None, 0.0
    src = max(SOURCE_TAGS, key=lambda k: tg.get(k, -99))
    non = max(NONSOURCE_TAGS, key=lambda k: tg.get(k, -99))
    return src, non, round(tg.get(src, -99) - tg.get(non, -99), 2)


def gate(m, tg=None):
    """(ok, reason) for an AUDIBLE hit: the viewer must see what makes the sound."""
    if not m or not m.get('ok_decode', True) or m.get('offset_f24') is None:
        return False, 'no_visible_event'
    if m.get('flash'):
        return False, 'fx_flash'
    if abs(m['offset_f24']) > SYNC_MAX + 0.01:
        return False, 'event_off_sync'
    strong = False
    if tg:
        _src, _non, _mg = tag_summary(tg)
        # a clearly tagged source (blade, gun, ...) in continuous fight motion: no still frames before the hit, so the
        # onset ratio stays low although the action is plain to see (pool check 2026-10-12: Sword of the Stranger,
        # Greed vs Wrath clashes at prom 1.1-1.7)
        strong = _mg >= STRONG_MARGIN and tg.get(_src, -99) >= STRONG_SRC
    if m['prom'] < (PROM_MIN_STRONG if strong else PROM_MIN):
        return False, 'nothing_visible'
    if m['area'] < AREA_MIN:
        return False, 'source_too_small'
    if m['fit'] < FIT_MIN:
        return False, 'source_off_crop'
    if tg:
        src, non, margin = tag_summary(tg)
        if non in ('face', 'eyes') and margin < FACE_MARGIN:
            return False, 'face_only'
        if non in ('scenery', 'fx', 'wide') and margin < -1.0:
            return False, f'{non}_only'
        # sound_4 by-eye check (28 hits): the clear sources had margin >= 1.9 and a source logit >= -3.8; FX / faces /
        # smears sat at margin -0.3..1.6 (core/edit/refs/sound-visible-source.md)
        if margin < TAG_MARGIN or tg.get(src, -99) < SRC_MIN:
            return False, 'source_unclear'
    if (m.get('region_luma') is not None and m['region_luma'] < 0.16 and float(m.get('spark') or 0) < 0.4)             or (m.get('frame_luma') is not None and m['frame_luma'] < 0.12):
        return False, 'dark'
    return True, None


def score(m, tg=None):
    """0-1 visible-source quality (for ranking among passing events)."""
    if not m or m.get('offset_f24') is None:
        return 0.0
    s = (0.35 * float(np.clip((m['prom'] - 1) / 4, 0, 1)) + 0.25 * float(np.clip(m['area'] / 0.25, 0, 1))
         + 0.2 * float(np.clip((m['fit'] - 0.4) / 0.5, 0, 1)) + 0.2 * float(np.clip(1 - abs(m['offset_f24']) / 3, 0, 1)))
    if tg:
        _, _, margin = tag_summary(tg)
        s += 0.15 * float(np.clip(margin / 4, -1, 1))
    return round(float(np.clip(s, 0, 1)), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--clip', required=True)
    ap.add_argument('--t', type=float, required=True)
    ap.add_argument('--tags', action='store_true')
    a = ap.parse_args()
    m = measure(a.clip, a.t, use_cache=False)
    tg = tags(a.clip, [m.get('event_t', a.t)]) if a.tags else None
    print(json.dumps({'measure': m, 'tags': tg, 'gate': gate(m, tg), 'score': score(m, tg)}))


if __name__ == '__main__':
    main()
