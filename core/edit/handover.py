#!/usr/bin/env python3
"""
handover: how two clips hand over to each other - the camera move runs THROUGH the cut - and how every shot keeps
MOVING for its whole duration (drive()).

drive (owner, 2026-09-26: "they have movement and pace, real edits; ours looks like we just put a few clips together"):
whole-video optical-flow motion (core/edit/refs/motion_energy.py, median / p75 of the per-frame flow, %W @30 fps) of the
references is 0.5-1.85 / 1.4-3.8 on the song / character / K-pop edits and ours was 0.32 / 1.48 (still shots between
flashes). drive() gives every shot of a preset with a DRIVE profile a continuous camera move whose speed follows the
references' U-shape (fast out of the cut, easing to a cruising speed, accelerating into the next cut), rises with the
song's energy, gets a small kick on every beat, and whose kind (pan across the plate, push, pull, 3D orbit, 2.5D
parallax) is drawn from the preset's mix; the pan direction is kept from shot to shot at the preset's measured
direction share so the moves chain. The plate always covers the canvas (pans use the source's own spare width first,
then zoom headroom).

Measured on the reference AV kits (core/edit/refs/motion3d-av.json + <group>-av.json): at the cuts of most presets
the picture keeps moving across the switch. The outgoing shot's motion ramps up over its last frames (tau_out), the
incoming shot picks the motion up in (mostly) the same direction at `ratio` times the speed and settles
exponentially (tau_in). Some presets also turn the picture in 3D around the cut (yaw / pitch a few degrees per frame
for 3-5 frames, decaying) - `swing3d`.

What the engine adds per cut (engine.shot_cam calls carry(); nothing happens for a preset without a profile):
  - the TOTAL image motion of the outgoing shot at the cut is measured = its clip content (DIS flow on its last
    source frames, mapped to the canvas) + its camera; direction d (pan x / pan y / zoom);
  - the outgoing camera is topped up along d so the picture reaches the preset's measured speed at the switch
    (v_cut, % width per 30 fps frame), with an exponential ramp (tau_out);
  - the incoming camera starts moving along d (rotated to the measured direction cosine) at ratio * v_cut minus
    what its own content already does along d, decaying with tau_in;
  - zoom that shrinks below the cover scale is re-based so no plate edge shows; pans get zoom headroom.
  - swing3d: p of the cuts turn in 3D (yaw / pitch) - out side accelerating x g per frame into the switch, in side
    decaying with tau - through core/edit/camera3d.py (cover zoom, perspective motion blur).

Profiles are per PRESET (presets are type specific, never mixed): PROFILES[preset]; plan["preset"] selects it,
plan["handover"] = false switches it off, plan["handover_overrides"] = {...} tunes it, shot["handover"] = false
switches one cut off.
"""
import math

import cv2
import numpy as np

R_MEAN = 0.36          # mean radius of the frame in widths (speed = |t| + |ln s| * R, as general_av_motion.py)

# Numbers: core/edit/refs/motion3d-av.json (presets.<p>.handover), the AV reports' continuity tables, and the curve
# fits of the proof loop (core/edit/refs/motion_av_proof.py). v in % width per 30 fps frame; taus in seconds.
PROFILES = {
    # Blur Flow (general-av 3.1, 42 valid hand-overs): 1.37 %W / frame through the switch, |v_in|/|v_out| 1.06, cos .55
    # (57-73 % keep the direction). The pooled incoming speed stays at 1.2-1.5 for ~10 frames and the outgoing is
    # already ~1 from -8 frames: tau_in 0.25 s / tau_out 0.08 s fitted in the proof loop (motion_av_proof.py blur_flow
    # r1 -> r3; the per-cut 72 / 27 ms fits under-drove the curves). 3D: whip_slide cuts
    # turn ~11 deg in yaw over 4 frames on the incoming side (motion3d-av: 1 of 10); 2.5D parallax on 15 % of cuts.
    'blur_flow': dict(v_cut=1.37, ratio=1.0, cos=0.8, tau_out=0.08, tau_in=0.25, T_out=0.25,
                      swing3d=dict(p=0.35, on=['whip_slide'], deg=(8.0, 13.0), side='in', tau=0.1, yaw_share=1.0)),
    # (no in-shot 2.5D parallax: the references' parallax flags are the anime's own layered motion, not the editor's -
    #  motion3d-av.md section 2 - and the owner rejected depth-layer parallax; `parallax=dict(p, amount)` stays available)
    # Hype Typography (general-av 3.2): slow at the switch (0.44), the incoming accelerates after it (1.15 at +100 ms),
    # ratio 2.48, cos -0.05 (a NEW direction: continuity is the flash), in tau 165 ms, out ramp 127 ms.
    # 3D: 2 of 19 blur bridges turn 12-16 deg in yaw over 3 frames at the switch (i4f 5.6 / 8.0 s).
    'hype_typography': dict(v_cut=0.44, ratio=2.5, cos=0.0, tau_out=0.127, tau_in=0.165, T_out=0.2,
                            swing3d=dict(p=0.12, on=['blur_bridge_hype', 'blur_bridge_flash'], deg=(10.0, 16.0), side='both',
                                         tau=0.12, in_k=0.4, frames_out=5, g=1.4, yaw_share=1.0)),
    # Character Quick Velocity (character-av 2): flash_zoom out 1.85 %W / frame ramp 49 ms, in at 0.62 of it decaying
    # 137 ms; mb_cut 1.33 / 15 ms -> 1.15 / 23 ms; whip 2.17 / 44 ms -> 0.19 of it; zoom_blur_cut 0.84 / 236 ms ->
    # 1.02 / 159 ms. 3D: flash_zoom 5 / 44 and mb_cut 4 / 20 turn 7-12 deg over ~3 frames INTO the switch (KSjp, mGz9)
    'char_quick_velocity': dict(v_cut=1.85, ratio=0.62, cos=0.87, tau_out=0.049, tau_in=0.137, T_out=0.13,
                                by={'mb_cut': dict(v_cut=1.33, ratio=0.86, tau_out=0.015, tau_in=0.023, T_out=0.07),
                                    'whip_horizontal': dict(v_cut=2.17, ratio=0.19, tau_out=0.044, tau_in=0.1),
                                    'impact_zoom_1frame': dict(v_cut=0.84, ratio=1.2, tau_out=0.236, tau_in=0.159, T_out=0.25)},
                                swing3d=dict(p=0.16, on=['flash_zoom', 'mb_cut'], deg=(7.0, 12.0), side='both', tau=0.08,
                                             in_k=0.7, frames_out=5, g=1.4, yaw_share=0.6)),
    # Character Soft Romance: IN-heavy - the incoming carries the move at 1.9-2.8x the outgoing speed, softer ease
    # (vertical whip out 0.33 / 56 ms, in 1.09 / 148 ms; white_out out 0.65 / 123 ms, in 0.45), direction cos ~0.
    'char_soft_romance': dict(v_cut=0.33, ratio=2.0, cos=0.0, tau_out=0.056, tau_in=0.148, T_out=0.1,
                              by={'white_out': dict(v_cut=0.65, ratio=0.7, tau_out=0.123, T_out=0.2)}),
    # Dance Smooth Velocity (dance-av A): 6 of 7 blur cuts carry the motion across, slow settle tau 211 ms.
    'dance_smooth_velocity': dict(v_cut=0.32, ratio=1.0, cos=0.8, tau_out=0.05, tau_in=0.211, T_out=0.12),
    # Dance Long AMV: 11 / 14 blur cuts and 8 / 11 zoom-blur entrances carry the motion, half the hard cuts do.
    'dance_long_amv': dict(v_cut=0.46, ratio=1.2, cos=0.85, tau_out=0.1, tau_in=0.1, T_out=0.15),
    # Dance Meme Payoff: hard cuts keep the direction (cos 0.999), in / out 0.7.
    'dance_meme': dict(v_cut=0.48, ratio=0.7, cos=0.95, tau_out=0.068, tau_in=0.095, T_out=0.12),
    # K-pop Postcard Drop (kpop-av): out 1.4-3.1 %W / frame, in 1.1-2.9 (median ratio ~0.75), the motion carries
    # through the blur; the zoom flips pull-back -> push-in (the blur-cut family's own envelopes keep that).
    'kpop_postcard_drop': dict(v_cut=2.0, ratio=0.75, cos=0.96, tau_out=0.1, tau_in=0.18, T_out=0.15, zoom_carry=False,
                               default_dir=(1.0, 0.0, 0.0)),
    # K-pop Stage Push: hard punches - the new shot moves harder than the old one left (entry tau 78 ms).
    'kpop_stage_push': dict(v_cut=0.72, ratio=0.8, cos=0.95, tau_out=0.05, tau_in=0.078, T_out=0.1, zoom_carry=False,
                            default_dir=(1.0, 0.0, 0.0)),
}
# no profile (no carry by design, measured): cinematic_slowmo (still camera, 0.03 %W / frame), band_parade (the slide
# preview owns the motion), char_raw_crisp (native 60 fps hard cuts, no camera), dance_raw (no effects),
# dance_burst_parade (the punches are accents), kpop_meme_reveal / kpop_single_take_smear / kpop_text_strobe (wrap
# slides, no cuts, words on black), sound_hit_sync (fresh source action at every cut, cos 0.42), fx_typography (its
# own whip / settle transitions already carry the direction, fx-av 2.2).
# DRIVE: in-shot camera speed (%W per 30 fps frame): lo = cruising mid-shot, hi = extra at the cuts (U-shape, taus in s),
# target = the references' whole-video flow median (motion_energy.py) that content + camera aim for,
# beat = kick on every beat (x of lo, decaying 0.1 s), e = how much the song's energy scales it (0 = not at all),
# mix = move kinds, keep = share of shots that keep the previous shot's pan direction.
# Calibrated with motion_energy.py on 15-20 s renders against the references' whole-video numbers (see the proof).
DRIVE = {
    'blur_flow': dict(target=0.6, lo_min=0.06, lo=0.6, hi=1.3, hi_min=0.8, ti=0.1, to=0.08, beat=0.9, e=0.5, keep=0.73,
                      mix={'pan': 0.3, 'push': 0.35, 'pull': 0.2, 'parallax': 0.1, 'orbit': 0.05}),
    'hype_typography': dict(target=1.25, lo=0.95, hi=1.1, ti=0.2, to=0.12, beat=0.5, e=0.5, keep=0.5,
                            mix={'pan': 0.4, 'push': 0.3, 'pull': 0.1, 'orbit': 0.1, 'tilt': 0.1}),
    'char_quick_velocity': dict(target=1.0, lo=0.8, hi=1.4, ti=0.14, to=0.05, beat=0.5, e=0.6, keep=0.78,
                                mix={'pan': 0.4, 'push': 0.35, 'pull': 0.1, 'orbit': 0.1, 'tilt': 0.05}),
    'char_soft_romance': dict(target=0.2, lo_min=0.08, lo=0.22, hi=0.35, ti=0.15, to=0.06, beat=0.2, e=0.4, keep=0.3,
                              mix={'pull': 0.45, 'pan': 0.35, 'parallax': 0.2}),
    'kpop_postcard_drop': dict(target=0.7, lo_min=0.08, lo=0.7, hi=0.9, ti=0.18, to=0.1, beat=0.3, e=0.5, keep=0.9,
                               mix={'pan': 0.45, 'push': 0.35, 'parallax': 0.2}),
    'kpop_stage_push': dict(target=0.67, lo=0.5, hi=0.7, ti=0.08, to=0.05, beat=0.3, e=0.5, keep=0.8,
                            mix={'push': 0.5, 'pan': 0.35, 'parallax': 0.15}),
    'dance_long_amv': dict(target=0.6, lo=0.3, hi=0.5, ti=0.1, to=0.1, beat=0.3, e=0.5, keep=0.5, mix={'push': 0.5, 'pan': 0.5}),
    'dance_smooth_velocity': dict(target=0.6, lo=0.3, hi=0.4, ti=0.21, to=0.05, beat=0.0, e=0.4, keep=0.8, mix={'pan': 0.7, 'push': 0.3}),
    'band_parade': dict(target=0.4, lo=0.15, hi=0.1, ti=0.2, to=0.1, beat=0.0, e=0.3, keep=0.9, mix={'pan': 1.0}),
    'fx_typography': dict(target=0.8, lo=0.55, hi=0.8, ti=0.15, to=0.1, beat=0.2, e=0.4, keep=0.5,
                          mix={'push': 0.5, 'pan': 0.3, 'orbit': 0.2}),
}
# no DRIVE (their references barely move the camera, measured): cinematic_slowmo (0.06-0.13 median), char_raw_crisp,
# dance_burst_parade / dance_raw / dance_meme (the dancers carry the motion), kpop_meme_reveal / single_take_smear /
# text_strobe, sound_hit_sync (native source motion only).
V_CAP = 3.0             # % W / frame: the carried speed never exceeds this (a whip's own streak speed is not re-used)

# transitions whose own envelope owns the camera through the cut completely (composites / wipes / dips): no carry
NO_CARRY = {'hard', 'photo_collage', 'card_tilt_in', 'white_bar_wipe', 'shape_wipe', 'chevron_wipe', 'iris_wipe',
            'wave_wipe', 'blob_wipe', 'iris_heart', 'darken_slide_preview', 'cutout_prereveal', 'peek_window',
            'slices_reveal', 'photo_clones', 'first_shot_entry', 'fade_in', 'bar_open_reveal', 'vignette_fade_in',
            'breath', 'dip_black', 'dip_black_strobe', 'glass_shatter', 'mirror_tile_scale_in', 'cross_dissolve',
            'dissolve_blur', 'defocus_dissolve', 'swing_turn', 'cube_turn', 'flip_turn', 'orbit_through',
            'zoom_through_depth'}


def profile(eng):
    if eng.plan.get('handover') is False:
        return None
    p = PROFILES.get(eng.plan.get('preset') or '')
    if p is None:
        return None
    p = dict(p)
    p.update(eng.plan.get('handover_overrides') or {})
    return p


def _base_name(tr):
    import transitions as TR
    n = (getattr(tr, 'name', '') or 'hard').split('+')[0]
    return TR.ALIASES.get(n, n)


# ------------------------------------------------------------------ measuring the pictures

def content_velocity(eng, j, t, side='end'):
    """
    Image motion of shot j's CLIP content near output time t, in canvas units per output second:
    (vx, vy) canvas-width fractions / s, vz ln-scale / s. side 'end' looks back from t, 'start' forward.
    """
    s = eng.shots[j]
    foot = eng.foot(s)
    if foot.is_image:
        return np.zeros(3)
    tm = s['_tm']
    sp = tm.speed(t)
    if abs(sp) < 1e-3:
        return np.zeros(3)
    gap = 3
    ct = tm(t - 0.5 / eng.fps) if side == 'end' else tm(t + 0.5 / eng.fps)
    i1 = foot.index(ct)
    i0 = i1 - gap if side == 'end' else i1
    i1 = i1 if side == 'end' else i1 + gap
    i0 = max(0, i0)
    i1 = min(foot.n_total - 1, i1)
    if i1 <= i0:
        return np.zeros(3)
    try:
        a = foot.native(i0 / foot.fps)
        b = foot.native(i1 / foot.fps)
    except Exception:  # noqa: BLE001
        return np.zeros(3)
    w = 192
    h = max(8, int(round(a.shape[0] * w / a.shape[1])))
    ga = cv2.cvtColor(cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(cv2.resize(b, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    if float(cv2.absdiff(ga, gb).mean()) > 40:                 # a cut inside the clip: no motion to carry
        return np.zeros(3)
    fl = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST).calc(ga, gb, None)
    ys, xs = np.mgrid[4:h:6, 4:w:6]
    p0 = np.stack([xs.ravel() - w / 2.0, ys.ravel() - h / 2.0], 1).astype(np.float32)
    p1 = p0 + fl[ys.ravel(), xs.ravel()]
    M, _ = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0, maxIters=200)
    if M is None:
        return np.zeros(3)
    sc = math.hypot(M[0, 0], M[1, 0])
    # work px -> canvas: the shot's matrix scale; per source-frame step -> per output second
    Mc = eng._matrix(s, foot, eng.canvas, (0.0, 0.0, 0.0, 0.0), t)
    k = math.sqrt(abs(np.linalg.det(Mc[:, :2]))) * (foot.size[0] / float(w))
    per_s = foot.fps * abs(sp) / float(i1 - i0)
    cw = float(eng.canvas[0])
    return np.array([M[0, 2] * k / cw * per_s, M[1, 2] * k / cw * per_s, math.log(max(1e-3, sc)) * per_s])


def camera_velocity(eng, j, t):
    """The shot camera's own motion (without the carry) at t, canvas units / s."""
    dt = 1.0 / eng.fps
    a = eng.shot_cam(j, t - dt, _carry=False)
    b = eng.shot_cam(j, t, _carry=False)
    return np.array([(b[2] - a[2]) / dt, (b[3] - a[3]) / dt, (b[0] - a[0]) / dt])


def speed_of(v):
    """|v| in % width per 30 fps frame (the unit of the AV reports)."""
    return (math.hypot(v[0], v[1]) + abs(v[2]) * R_MEAN) * 100 / 30.0


def _unit(v):
    n = math.hypot(v[0], v[1]) + abs(v[2]) * R_MEAN
    return v / n if n > 1e-9 else None


# ------------------------------------------------------------------ the plan of one cut

def _plan(eng, j, prof):
    """Resolved carry of the cut INTO shot j (cached on the shot)."""
    s = eng.shots[j]
    if '_carry' in s:
        return s['_carry']
    s['_carry'] = None
    if j == 0 or s.get('handover') is False:
        return None
    tr = eng.trans[j]
    name = _base_name(tr)
    prof = dict(prof, **((prof.get('by') or {}).get(name) or {}))
    if name in NO_CARRY or name in (prof.get('no_carry') or ()):
        c = None
    else:
        t0 = s['t0']
        vo = content_velocity(eng, j - 1, t0, 'end') + camera_velocity(eng, j - 1, t0 - 1.0 / eng.fps)
        if prof.get('zoom_carry') is False:     # the zoom FLIPS at these cuts (pull-back -> punch-in): carry pans only
            vo = np.array([vo[0], vo[1], 0.0])
        d = _unit(vo)
        if d is None:
            dd = prof.get('default_dir', (0.0, 0.0, 1.0))
            d = _unit(np.array(dd, float))
        rng = np.random.default_rng(s['seed'] + 41)
        v_cut = float(prof.get('v_cut', 1.0)) * float(rng.uniform(0.85, 1.15))          # % W / frame @30
        have = speed_of(vo)
        a_out = max(0.0, v_cut - have) * float(prof.get('out_share', 1.0))
        # the incoming direction: the outgoing one turned to the measured cosine (a small random side)
        cosv = float(np.clip(prof.get('cos', 1.0), -1, 1))
        ang = math.acos(cosv) * (1 if rng.random() < 0.5 else -1)
        dx, dy, dz = d
        rx, ry = dx * math.cos(ang) - dy * math.sin(ang), dx * math.sin(ang) + dy * math.cos(ang)
        din = np.array([rx, ry, dz * cosv])
        din = _unit(din) if _unit(din) is not None else d
        vi = content_velocity(eng, j, t0, 'start')
        if prof.get('zoom_carry') is False:
            din = np.array([din[0], din[1], 0.0])
            din = _unit(din) if _unit(din) is not None else np.array([1.0, 0.0, 0.0])
        along = (vi[0] * din[0] + vi[1] * din[1] + vi[2] * din[2] * R_MEAN * R_MEAN) * 100 / 30.0
        v_ref = min(V_CAP, max(have, v_cut))                    # the outgoing speed AT the switch after the top-up
        ratio = float(prof.get('ratio', 1.0))
        tgt = min(ratio * v_ref, max(ratio, 1.0) * v_cut)          # never beyond the preset's measured incoming speed
        a_in = max(0.0, tgt - max(0.0, along)) * float(prof.get('in_share', 1.0))
        c = dict(d=d, din=din, a_out=a_out * 0.3, a_in=a_in * 0.3,       # -> canvas widths per second
                 tau_out=float(prof.get('tau_out', 0.03)), tau_in=float(prof.get('tau_in', 0.08)),
                 T_out=float(prof.get('T_out', 0.12)), v_have=have, v_cut=v_cut)
        tr_pre = getattr(tr, 'pre', 0.0)
        c['T_out'] = max(c['T_out'], tr_pre)
    # 3D turn around the cut
    sw = prof.get('swing3d')
    if sw and name not in NO_CARRY and (not sw.get('on') or name in sw['on']):
        rng = np.random.default_rng(s['seed'] + 43)
        if rng.random() < float(sw.get('p', 0.0)):
            axis = 'yaw' if rng.random() < float(sw.get('yaw_share', 0.6)) else 'pitch'
            lo, hi = sw.get('deg', (6.0, 14.0))
            deg = float(rng.uniform(lo, hi)) * (1 if rng.random() < 0.5 else -1)
            c = dict(c or {}, swing=dict(axis=axis, deg=deg, side=sw.get('side', 'in'), tau=float(sw.get('tau', 0.06)),
                                         g=float(sw.get('g', 1.35)), frames_out=int(sw.get('frames_out', 3)),
                                         in_k=float(sw.get('in_k', 1.0))))
    s['_carry'] = c
    return c


# ------------------------------------------------------------------ the camera terms

def _disp_out(u, a, tau, T):
    """Outgoing displacement at u = t_cut - t (velocity a e^(-u/tau), zero at u = T)."""
    if u > T or a <= 0:
        return 0.0
    return a * tau * (math.exp(-u / tau) - math.exp(-T / tau))


def _disp_in(v, a, tau):
    if a <= 0:
        return 0.0
    return a * tau * (1 - math.exp(-max(0.0, v) / tau))


def _terms(vec, D, cw_ch):
    """(lns, dx, dy) for a displacement D along a unit direction vec, with cover headroom for pans."""
    dx, dy, dz = vec[0] * D, vec[1] * D, vec[2] * D
    head = math.log(1 + 2 * abs(dx)) + math.log(1 + 2 * abs(dy) * cw_ch)
    return dz, dx, dy, head


def _parallax(eng, j, prof):
    """(amount, dir) of shot j's in-shot 2.5D parallax drift, or None (seeded per shot, cached)."""
    s = eng.shots[j]
    if '_par25' in s:
        return s['_par25']
    s['_par25'] = None
    pr = prof.get('parallax')
    cam = s.get('_cam') or {}
    if pr and not cam.get('move3d') and s['t1'] - s['t0'] >= float(pr.get('min_len', 0.6)) and s.get('speed') != 'freeze' \
            and s.get('layout', 'fill') == 'fill':
        rng = np.random.default_rng(s['seed'] + 47)
        if rng.random() < float(pr.get('p', 0.0)):
            lo, hi = pr.get('amount', (0.02, 0.04))
            s['_par25'] = (float(rng.uniform(lo, hi)), 1.0 if rng.random() < 0.5 else -1.0)
    return s['_par25']


def carry(eng, j, t, c):
    """Camera c of shot j at t plus the hand-over terms of its two cuts (into j, out of j)."""
    prof = profile(eng)
    if not prof:
        return c
    cw, ch = eng.canvas
    cw_ch = cw / float(ch)
    add = [0.0] * 9
    par = _parallax(eng, j, prof)
    if par:
        s = eng.shots[j]
        u = min(1.0, max(0.0, (t - s['t0']) / max(0.2, s['t1'] - s['t0'])))
        add[7] += par[0] * par[1] * (u - 0.5)             # lateral travel: the layers slide, the subject stays put
    # incoming side of the cut into j
    pl = _plan(eng, j, prof) if j > 0 else None
    v = t - eng.shots[j]['t0']
    if pl and v >= 0:
        if 'din' in pl:
            D = _disp_in(v, pl['a_in'], pl['tau_in'])
            dz, dx, dy, head = _terms(pl['din'], D, cw_ch)
            if dz < 0:              # a pull-back: start pushed in by the whole travel and settle to 0 (never under cover)
                dz = abs(pl['din'][2]) * (pl['a_in'] * pl['tau_in'] - D)
            add[0] += dz + head
            add[2] += dx
            add[3] += dy
        sw = pl.get('swing')
        if sw and sw['side'] in ('in', 'both'):
            # the incoming continues the turn from part of the angle and settles (visible after the bridge)
            a = sw['deg'] * sw.get('in_k', 1.0) * math.exp(-v / sw['tau'])
            add[4 if sw['axis'] == 'yaw' else 5] += a
    # outgoing side of the cut out of j
    if j + 1 < len(eng.shots):
        pn = _plan(eng, j + 1, prof)
        u = eng.shots[j + 1]['t0'] - t
        if pn and u > 0:
            if 'd' in pn:
                D = _disp_out(u, pn['a_out'], pn['tau_out'], pn['T_out'])
                Dmax = _disp_out(0.0, pn['a_out'], pn['tau_out'], pn['T_out'])
                dz, dx, dy, head = _terms(pn['d'], D, cw_ch)
                if dz < 0:          # pulling back into the cut: carry the headroom from the start of the ramp
                    dz = abs(pn['d'][2]) * (Dmax - D)
                add[0] += dz + head
                add[2] += dx
                add[3] += dy
            sw = pn.get('swing')
            if sw and sw['side'] in ('out', 'both'):
                k = u * 30.0                                  # frames @30 before the switch
                n = float(sw['frames_out'])
                if k <= n:
                    # the whole turn happens on the outgoing's last n frames (KSjp / i4f / mGz9: 8-12 deg over ~3
                    # frames, starting ~5 frames @30 before the switch), accelerating geometrically x g per frame
                    g = sw['g']
                    a = sw['deg'] * (g ** (-k) - g ** (-n)) / (1 - g ** (-n))
                    add[4 if sw['axis'] == 'yaw' else 5] -= a
    if not any(add):
        return c
    if any(add[4:]):
        c = tuple(c) + (0.0,) * (9 - len(c))
        return tuple(x + y for x, y in zip(c, add))
    return (c[0] + add[0], c[1], c[2] + add[2], c[3] + add[3]) + tuple(c[4:])


# ------------------------------------------------------------------ in-shot drive

def drive_profile(eng):
    if eng.plan.get('drive') is False:
        return None
    p = DRIVE.get(eng.plan.get('preset') or '')
    if p is None:
        return None
    p = dict(p)
    p.update(eng.plan.get('drive_overrides') or {})
    return p


def _median_radius(canvas):
    cw, ch = canvas
    ys, xs = np.mgrid[0:ch:8, 0:cw:8]
    return float(np.median(np.hypot(xs - cw / 2.0, ys - ch / 2.0))) / cw


def _drive_plan(eng, j, dp):
    s = eng.shots[j]
    if '_drive' in s:
        return s['_drive']
    s['_drive'] = None
    cam = s.get('_cam') or {}
    if s.get('drive') is False or cam.get('move3d') or s.get('layout', 'fill') != 'fill' or s.get('speed') == 'freeze':
        return None
    rng = np.random.default_rng(s['seed'] + 53)
    mix = dp.get('mix') or {'pan': 1.0}
    kinds = list(mix)
    w = np.array([mix[k] for k in kinds], float)
    kind = kinds[int(rng.choice(len(kinds), p=w / w.sum()))]
    T0, T1 = s['t0'], s['t1']
    dur = max(0.1, T1 - T0)
    # speed curve over the shot (and a margin either side for the transitions' shutters)
    ts = np.linspace(T0 - 0.3, T1 + 0.3, max(32, int((dur + 0.6) * 120)))
    x = np.clip(ts - T0, 0, dur)
    lo, hi = float(dp['lo']), float(dp['hi'])
    if s.get('_content_flow') is not None and dp.get('target'):
        # the camera adds what the footage lacks (a still plate gets the full cruise, a fast twixtor shot a drift)
        lo = max(float(dp.get('lo_min', 0.12)), min(lo, float(dp['target']) - 0.8 * float(s['_content_flow'])))
        hi = max(float(dp.get('hi_min', 0.0)), hi * max(0.4, lo / max(1e-6, float(dp['lo']))))
    v = lo + hi * (np.exp(-x / float(dp['ti'])) + np.exp(-(dur - x) / float(dp['to'])))
    if dp.get('beat') and eng.beats:
        b = np.array(eng.beats)
        k = np.searchsorted(b, ts, side='right') - 1
        since = np.where(k >= 0, ts - b[np.clip(k, 0, len(b) - 1)], 9.0)
        v = v + float(dp['beat']) * max(lo, float(dp.get('beat_min', 0.3))) * np.exp(-np.clip(since, 0, None) / 0.1)
    ek = float(dp.get('e', 0.0))
    if ek and eng.energy:
        e = np.array([eng.energy_at(t) for t in ts])
        v = v * ((1 - ek) + ek * (0.35 + 0.9 * e))
    v = v * float(rng.uniform(0.85, 1.15))
    vcs = v * 0.3                                    # %W / frame @30 -> canvas widths / s
    D = np.concatenate([[0.0], np.cumsum((vcs[1:] + vcs[:-1]) / 2 * np.diff(ts))])
    i0, i1 = int(np.searchsorted(ts, T0)), int(np.searchsorted(ts, T1))
    D = D - D[i0]
    Dt = float(D[min(i1, len(D) - 1)])
    # pan direction: kept from the previous shot at the preset's share
    prev = eng.shots[j - 1].get('_drive') if j > 0 else None
    if prev and prev.get('pan') is not None and rng.random() < float(dp.get('keep', 0.5)):
        pan = prev['pan']
    else:
        ang = rng.uniform(0, 2 * math.pi)
        pan = (math.cos(ang), 0.35 * math.sin(ang))           # mostly horizontal, as the references
    R = _median_radius(eng.canvas)
    sg = 1.0 if rng.random() < 0.5 else -1.0
    plan = dict(kind=kind, ts=ts, D=D, Dt=Dt, pan=pan if kind == 'pan' else (prev or {}).get('pan'), R=R, sg=sg, head=0.0)
    if kind == 'pan':
        n = math.hypot(*pan)
        ux, uy = pan[0] / n, pan[1] / n
        plan['u'] = (ux, uy)
        # zoom headroom beyond the plate's own spare width (the focus clamp keeps the plate inside at scale b)
        foot = eng.foot(s)
        cw, ch = eng.canvas
        M = eng._matrix(s, foot, eng.canvas, (0.0, 0.0, 0.0, 0.0), T0)
        b = math.sqrt(abs(np.linalg.det(M[:, :2])))
        px = (cw / 2.0 - M[0, 2]) / b
        py = (ch / 2.0 - M[1, 2]) / b
        ww, wh = foot.size
        need = 1.0
        pk = float(dp.get('pan_k', 0.4))
        hx, hy = abs(ux) * Dt / 2 * cw * pk, abs(uy) * Dt / 2 * cw * pk
        for a, room in ((cw / 2.0 + hx, px), (cw / 2.0 + hx, ww - px), (ch / 2.0 + hy, py), (ch / 2.0 + hy, wh - py)):
            need = max(need, a / max(1.0, b * room))
        plan['head'] = math.log(need) * 1.02
    s['_drive'] = plan
    return plan


def drive(eng, j, t, c):
    """Shot j's continuous in-shot move at t added to camera c (a no-op for presets without a DRIVE profile)."""
    dp = drive_profile(eng)
    if not dp:
        return c
    pl = _drive_plan(eng, j, dp)
    if not pl:
        return c
    D = float(np.interp(t, pl['ts'], pl['D']))
    k, Dt = pl['kind'], pl['Dt']
    add = [0.0] * 9
    if k == 'pan':
        # proof round (motion_av_proof, per-channel): the references' pan is only 0.2-0.3 %W / frame around the cuts,
        # their camera speed is mostly zoom + the footage - a pan drive runs at pan_k of the drive speed
        ux, uy = pl['u']
        pk = float(dp.get('pan_k', 0.4))
        add[2] += ux * (D - Dt / 2) * pk
        add[3] += uy * (D - Dt / 2) * pk
        add[0] += pl['head']
    elif k == 'push':
        add[0] += D / pl['R']
    elif k == 'pull':
        add[0] += (Dt - D) / pl['R']
    elif k == 'orbit':
        # yaw about the subject: deg per canvas width of travel ~ the 40 deg FOV (a flat 1 deg ~ 0.03 W at the centre)
        add[4] += pl['sg'] * (D - Dt / 2) * 30.0
        add[0] += 0.02
    elif k == 'tilt':
        add[5] += pl['sg'] * (D - Dt / 2) * 30.0
        add[0] += 0.02
    elif k == 'parallax':
        # layers slide against each other; the focus layer does not move, so add a slow push to carry the flow
        add[7] += pl['sg'] * (D - Dt / 2) * 1.6
        add[0] += 0.4 * D / pl['R']
    if any(add[4:]):
        c = tuple(c) + (0.0,) * (9 - len(c))
        return tuple(x + y for x, y in zip(c, add))
    return (c[0] + add[0], c[1], c[2] + add[2], c[3] + add[3]) + tuple(c[4:])


# ------------------------------------------------------------------ source moments with subject motion

def _window_motion(path, a, b, aspect, fps=10.0, w=160):
    """Per-step flow median (% width per 1/fps s) and a cut flag between samples of a clip range."""
    import subprocess
    h = max(8, int(round(w * aspect / 2)) * 2)
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '%.3f' % max(0.0, a), '-i', path, '-t', '%.3f' % (b - a), '-an',
                        '-vf', 'fps=%g,scale=%d:%d:flags=area' % (fps, w, h), '-pix_fmt', 'gray', '-f', 'rawvideo', '-'],
                       capture_output=True)
    buf = np.frombuffer(r.stdout, np.uint8)
    if buf.size < w * h * 2:
        return None, None
    n = buf.size // (w * h)
    fr = buf[:n * w * h].reshape(n, h, w)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
    mot, cut = [], []
    for i in range(1, n):
        d = float(cv2.absdiff(fr[i], fr[i - 1]).mean())
        cut.append(d > 38)
        fl = dis.calc(fr[i - 1], fr[i], None)
        mot.append(float(np.median(np.hypot(fl[..., 0], fl[..., 1]))) / w * 100)
    return np.array(mot), np.array(cut)


def pick_sources(eng):
    """
    For presets with a DRIVE profile: measure each shot's CONTENT motion (flow median of its clip span, converted to
    canvas %W per 30 fps frame at the shot's rate and framing) - drive() only adds the camera motion the content lacks -
    and, when the content is below the preset's target, move src_in (at most +-0.8 s, never across a cut inside the
    clip) to the stretch with the most subject motion. plan "motion_pick": false / shot "pick": false keep the planner's
    moment (the content is still measured); shots with a speed_curve / clip hits (sfx) / stills are never moved.
    """
    dp = drive_profile(eng)
    if not dp:
        return
    FT = __import__('footage')
    target = float(dp.get('target', 0.7))
    for s in eng.shots:
        if s['path'].lower().endswith(FT.IMAGE_EXT) or s.get('layout', 'fill') != 'fill':
            continue
        prep = eng.preps[s['path']]
        rate = float(s.get('rate') or 1.0)
        span = max(0.3, (s['t1'] - s['t0']) * rate)
        movable = not (eng.plan.get('motion_pick') is False or s.get('pick') is False or s.get('speed_curve') or s.get('sfx'))
        m_ = 0.8 if movable else 0.0
        a, b = max(0.0, s['src_in'] - m_), min(prep.duration - 0.05, s['src_in'] + span + m_)
        if b - a < min(span, 0.3):
            continue
        mot, cut = _window_motion(s['path'], a, b, prep.h / float(prep.w))
        if mot is None or len(mot) < 2:
            continue
        # source %W per 0.1 s -> canvas %W per 30 fps output frame
        foot = eng.foot(s)
        M = eng._matrix(s, foot, eng.canvas, (0.0, 0.0, 0.0, 0.0), s['t0'])
        k = math.sqrt(abs(np.linalg.det(M[:, :2]))) * foot.size[0] / float(eng.canvas[0]) / 3.0 * rate
        n = max(1, min(len(mot), int(round(span * 10))))
        best, cur = None, None
        for i in range(0, len(mot) - n + 1):
            m = float(np.mean(mot[i:i + n])) * k
            st = a + i / 10.0
            if abs(st - s['src_in']) < 0.051:
                cur = m
            if cut[i:i + n].any():
                continue
            if best is None or m > best[0]:
                best = (m, st)
        if cur is None:
            cur = float(np.mean(mot[:n])) * k
        s['_content_flow'] = cur
        if movable and best and cur < target and best[0] > 1.3 * cur + 0.05:
            s['_src_in_planner'] = s['src_in']
            s['src_in'] = round(best[1], 3)
            s['_content_flow'] = best[0]
        elif movable and cur > 1.6 * target:
            # far busier than the preset's references (a whirl of effects / a speed-lined smear): the nearest calmer
            # stretch of the same take that still moves (>= the target), so the edit keeps the references' pace
            calm = None
            for i in range(0, len(mot) - n + 1):
                if cut[i:i + n].any():
                    continue
                m = float(np.mean(mot[i:i + n])) * k
                if m >= 0.9 * target and (calm is None or abs(m - 1.1 * target) < abs(calm[0] - 1.1 * target)):
                    calm = (m, a + i / 10.0)
            if calm and calm[0] < 0.8 * cur:
                s['_src_in_planner'] = s['src_in']
                s['src_in'] = round(calm[1], 3)
                s['_content_flow'] = calm[0]
