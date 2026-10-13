#!/usr/bin/env python3
"""
Style presets for the edit engine (core/edit/engine.py), built from the Study specs in
core/edit/refs/{general,sound,dance,character,kpop}.json.

A style says how a plan renders when the plan leaves something out: the colour look, the house
transition and hero transitions (auto-picked on cuts whose transition_in is missing or "auto"),
how far cuts lead the beat, camera behaviour, speed curves, per-beat accents, overlays, text,
watermark, audio fades and the ending. Anything in plan.style_overrides replaces the preset's keys.

    import styles
    st = styles.resolve('velocity_slowmo')
    t_cut = styles.cut_time(beat_t, 'blur_bridge', 'flow_blur')   # when the new shot's first frame shows

Presets
  velocity_slowmo   the owner's favourite (Golden Hour, _sX2EUexbW8): crisp holds and smooth optical-
                    flow slow motion, hard cuts with a bloom exposure bump every pulse, warm glow, no
                    handheld, lyric / title words on black, 1.2 s fade out
  cinematic_warm    long-form cinematic: soft blur bridges and dissolves, slow motion, letterbox
                    sections, warm glow, light leaks, black-and-white selective outro
  flow_blur         After Effects flow edit (ZkWQ / cj12 / S2rz): blur bridge on every cut (40% with a
                    flash), handheld camera, U-shaped velocity, particles
  hype_flash        hype edit (i4f / m3lv / zawB): 4-5 frame zoom-blur bridges, flashes on 60% of cuts,
                    hero cards / splits / wipes / radial bursts, title words
  glitch            hype variant: RGB split, invert / dip strobes, halftone flashes
  beat_band         "one character per beat" collections (GHB): 16:9 band in 9:16, darken-slide preview
  dance_sync        cute dance parade (Uf_4iSx6r3g): burst-land velocity per beat, one punch accent per
                    beat, static crop, cute grade
  dance_smooth      smooth velocity dance (ONlH): cosine velocity, follow crop, dark velocity grade
  dance_amv         16:9 dance AMV (1L7mzyGhXCA): transitions straddling the kick, bloom, 3D title
  character_aura    single-character quick velocity edit: flash-zoom / motion-blur cuts, 0.5-0.8x
                    chorus with impact / dip ramps, per-shot zoom, 2:3 window, sparkles
  character_soft    soft romance: white-outs, defocus dissolves, 1-frame impact zooms, pastel / glow
  character_raw     raw crisp 4K: native frames, hard + white-flash cuts, crisp vivid grade
  postcard_kpop     K-pop member edit (mFHp6Dd2ISk): postcard intro, blur-in punch, cutout / slices /
                    clones hero reveals, crisp K-pop grade
  kpop_stage        stage / MV member edit (upHh): establishing push-in, hard punches, kinetic name
  kpop_trend        single-clip trend edit (fSMk / pB7P): strobe smears and vertical smears, tint
  sound_edit        sound edit: hard cuts on the clip's own hits, native frames, neutral grade, 16:9
                    master rotated or letterboxed in 9:16
"""
import copy
import math

# ---------------------------------------------------------------------------------------------- looks
# curve / sat / gamma / balance / bloom (opacity) / bloom_at (threshold) / bloom_sigma (px at 1080 w)
# are applied by ffmpeg while decoding (in RGB); cas / unsharp / grain run in the encoder at output
# size (sharpening after the single resample); vignette is a multiply map; exposure = per-shot
# exposure normalisation toward that luma; levels = per-shot auto-levels (p02 -> 0.015, p98 -> 0.98);
# soft = gaussian px after the grade; orton = Orton glow amount; gradient = palette soft-light strength.
LOOKS = {
    'cinematic': dict(curve="0/0 0.10/0.03 0.35/0.26 0.7/0.74 0.92/0.97 1/1", sat=1.42, gamma=0.93,
                      balance='rs=-0.04:gs=-0.01:bs=0.09:rh=0.07:gh=0.02:bh=-0.06', bloom=0.45, bloom_at=0.74, cas=0.6,
                      vignette=0.32, grain=4, exposure=0.42),
    'dreamy': dict(curve="0/0.03 0.25/0.22 0.6/0.66 0.9/0.96 1/1", sat=1.32, gamma=0.98,
                   balance='rs=0.04:bs=0.06:rm=0.04:bm=-0.02:rh=0.07:bh=-0.03', bloom=0.6, bloom_at=0.66, cas=0.45,
                   vignette=0.32, grain=4, exposure=0.42),
    'hype': dict(curve="0/0 0.15/0.04 0.5/0.5 0.85/0.95 1/1", sat=1.6, gamma=0.88,
                 balance='rs=-0.05:bs=0.09:rh=0.07:bh=-0.07', bloom=0.3, bloom_at=0.82, cas=0.75, vignette=0.35, grain=4,
                 exposure=0.42),
    # general (AMV) looks
    'warm_glow': dict(curve="0/0.02 0.25/0.21 0.6/0.66 0.9/0.96 1/1", sat=1.3, gamma=0.98,
                      balance='rs=-0.01:bs=0.03:rm=0.03:bm=-0.02:rh=0.07:bh=-0.05', bloom=0.7, bloom_at=0.6, cas=0.5,
                      vignette=0.42, grain=4, exposure=0.46),
    # blur_flow remake of ZkWQ (measured on the reference at 4 fps, bars excluded: HSV sat p50 69/255, luma p50 151, p05 47,
    # p95 236, R-B +30, (R+B)/2-G +7.5): soft pastel warm daylight with a gentle glow. sat_target / warm_target / magenta_target
    # are per-clip normalisations (adapt_look): every clip lands on the same palette, whatever its source grade
    'zkw_soft_warm': dict(curve="0/0.02 0.25/0.25 0.6/0.69 0.88/0.93 1/0.97", sat=1.0, gamma=1.0,
                          balance='rs=0.0:bs=0.0:rh=0.03:bh=-0.03', bloom=0.22, bloom_at=0.72, cas=0.55,
                          vignette=0.12, grain=3, exposure=0.58, sat_target=0.47, warm_target=0.17, magenta_target=0.03),
    'show_palette': dict(curve="0/0 0.10/0.03 0.35/0.26 0.7/0.74 0.92/0.97 1/1", sat=1.5, gamma=0.93,
                         balance='rs=-0.04:gs=-0.01:bs=0.08:rh=0.05:gh=0.02:bh=-0.04', bloom=0.45, bloom_at=0.74, cas=0.6,
                         vignette=0.42, grain=4, exposure=0.40),
    'high_key_pastel': dict(curve="0/0.05 0.3/0.42 0.7/0.84 0.9/0.99 1/1", sat=0.9, gamma=1.0,
                            balance='rh=0.06:gh=-0.02:bh=0.05:rm=0.02:bm=0.02', bloom=0.5, bloom_at=0.7, cas=0.5,
                            vignette=0.15, grain=3, exposure=0.5),
    # cinematic_slowmo (_sX2 remake, measured on the body at 4 fps: L* median 47, p05 9 (lifted blacks), p95 82 (rolled
    # highlights), saturation 30 %, Lab b +13 in the highlights / -4.7 in the shadows, crisp (Laplacian var ~1100 @960))
    'golden_soft': dict(curve="0/0.04 0.12/0.135 0.35/0.385 0.65/0.685 0.9/0.865 1/0.9", sat=0.92, gamma=1.0,
                        balance='rs=-0.03:gs=0.0:bs=0.05:rm=0.02:bm=-0.02:rh=0.03:gh=0.01:bh=-0.035', bloom=0.4, bloom_at=0.7,
                        cas=0.65, unsharp='5:5:0.5:5:5:0.0', orton=0.15, vignette=0.12, grain=0, exposure=0.52,   # round 10: soft dreamy diffusion (critics: ours 3-4x crisper than _sX2's 700-1600 Laplacian)
                        # remake round 3 (critics: hero p2 50 vs ref 20, sat 47 vs 76, 'milky wash'): per-clip guard
                        guard=dict(p02_in=0.06, p02_to=0.025, p98_in=0.90, p98_to=0.87, top_to=0.94, sat_ref=0.28,
                                   sat_lo=0.90, sat_hi=1.9,      # 2026-10-12 (vibe edits, owner: rich, not milky): 0.26 / 1.55 -> 0.28 / 1.9
                                   # remake round 4 (critics: every shot at luma 115-133, 'evenly filtered'; _sX2
                                   # alternates 111 / 204 / 50 / 95 / 45 / 199): the exposure pull may move a clip's
                                   # mean by at most keep_key; bright clips roll only above p98_in2; HSV sat capped
                                   keep_key=0.02, p98_in2=0.965, sat_max=0.60, anchors=(0.25, 0.75))),   # round 5: 0.06 -> 0.02 (Shinobu 99 -> 133, the key alternation was lost)
    # character looks (grade presets measured on the references)
    'teal_mono': dict(curve="0/0 0.12/0.05 0.45/0.42 0.8/0.86 1/1", sat=1.35, gamma=0.95,
                      balance='rs=-0.14:gs=0.02:bs=0.20:rm=-0.08:gm=0.03:bm=0.12:rh=0.05:gh=0.0:bh=-0.03', bloom=0.35,
                      bloom_at=0.78, cas=0.7, vignette=0.45, grain=4, exposure=0.42),
    'faded_film': dict(curve="0/0.11 0.25/0.29 0.5/0.53 0.75/0.76 1/0.93", sat=0.72, gamma=1.0,
                       balance='rs=0.02:bs=0.03:rh=0.05:gh=0.02:bh=-0.06', bloom=0.3, bloom_at=0.7, cas=0.2, soft=0.6,
                       vignette=0.3, grain=7, exposure=0.5),
    'golden_bloom': dict(curve="0/0.17 0.3/0.38 0.6/0.68 0.85/0.9 1/0.96", sat=1.05, gamma=1.02,
                         balance='rs=0.03:bs=-0.02:rm=0.04:bm=-0.05:rh=0.08:gh=0.03:bh=-0.12', bloom=0.65, bloom_at=0.62,
                         cas=0.5, vignette=0.34, grain=3, exposure=0.52),
    'pastel_pink': dict(curve="0/0.09 0.5/0.57 1/0.93", sat=0.62, gamma=1.0, balance='rm=0.05:gm=-0.03:bm=0.04:rh=0.03:bh=0.02',
                        bloom=0.25, bloom_at=0.8, cas=0.7, vignette=0.0, grain=0, exposure=0.55),
    'crisp_vivid': dict(curve="0/0 0.25/0.22 0.75/0.8 1/1", sat=1.35, gamma=0.98, balance=None, bloom=0.12, bloom_at=0.9,
                        cas=0.95, unsharp='5:5:0.6:5:5:0.0', vignette=0.0, grain=0, exposure=0.47),
    'dark_contrast': dict(curve="0/0 0.18/0.1 0.5/0.49 0.85/0.88 1/0.93", sat=1.1, gamma=0.94,
                          balance='rs=0.02:bs=0.04:rh=0.03:bh=-0.02', bloom=0.3, bloom_at=0.8, cas=0.6, vignette=0.4, grain=5,
                          exposure=0.44),
    'soft_glow': dict(curve="0/0.02 0.5/0.49 0.8/0.74 1/0.84", sat=0.82, gamma=1.0, balance='rs=-0.02:bs=0.03:rh=0.02:bh=0.03',
                      bloom=0.8, bloom_at=0.55, cas=0.2, orton=0.45, vignette=0.27, grain=0, exposure=0.46),
    # dance looks
    'cute': dict(curve=None, sat=1.33, gamma=1.0, balance=None, bloom=0.0, cas=0.6, vignette=0.0, grain=0, exposure=None),
    'velocity_dark': dict(curve="0/0 0.1/0.01 0.25/0.05 0.55/0.4 0.9/1 1/1", sat=1.7, gamma=1.0, balance='bs=0.03',
                          bloom=0.0, cas=0.8, vignette=0.16, grain=0, exposure=None),
    'amv_bloom': dict(curve="0/0.06 0.5/0.52 1/0.99", sat=1.2, gamma=1.0, balance='rh=0.03:bh=-0.02', bloom=0.4, bloom_at=0.7,
                      cas=0.6, vignette=0.0, grain=0, exposure=0.46),
    # K-pop looks
    'kpop_crisp': dict(curve="0/0 0.05/0 0.25/0.19 0.5/0.5 0.75/0.83 0.93/1 1/1", sat=1.12, gamma=1.0, balance=None,
                       bloom=0.12, bloom_at=0.85, cas=0.9, unsharp='5:5:1.1:5:5:0.0', levels=True, vignette=0.0, grain=0,
                       exposure=None),
    'tint_hue_push': dict(curve="0/0.02 0.5/0.48 1/0.97", sat=1.5, gamma=1.0,
                          balance='rs=0.12:bs=-0.06:rm=0.08:bm=-0.03:rh=0.08:bh=-0.06', bloom=0.3, bloom_at=0.7, cas=0.6,
                          vignette=0.2, grain=0, exposure=0.45),
    'tint_pastel_cool': dict(curve="0/0.1 0.5/0.56 1/0.97", sat=0.38, gamma=1.0, balance='rs=-0.02:bs=0.08:bm=0.05:bh=0.03',
                             bloom=0.25, bloom_at=0.75, cas=0.5, vignette=0.0, grain=0, exposure=0.5),
    # sound edits: source colours
    'neutral': dict(curve=None, sat=1.03, gamma=1.0, balance=None, bloom=0.0, cas=0.3, vignette=0.0, grain=0, exposure=None),
    'source': dict(curve=None, sat=1.0, gamma=1.0, balance=None, bloom=0.0, cas=0.0, vignette=0.0, grain=0, exposure=None),
    # blur_flow / S2rz remake: crisp saturated anime colour, dark shots stay dark (no exposure lift), a cool lilac
    # highlight bloom and a light vignette (reference frames 5.0 / 14.0 / 22.5 / 25.3 s)
    's2rz_cool': dict(curve="0/0 0.06/0.015 0.25/0.2 0.75/0.83 1/1", sat=1.5, gamma=1.0, balance='bs=0.03:rh=-0.01:bh=0.03',
                      bloom=0.3, bloom_at=0.78, cas=0.95, unsharp='5:5:0.6:5:5:0.0', vignette=0.22, grain=0, exposure=None),
    # remake round 2: the planner's s2rz variant normalises each clip's saturation to the reference's (already-vivid Oshi
    # no Ko clips went hot magenta under the flat x1.5): sat_target 0.72 -> multiplier 0.45-1.45 per clip (adapt_look)
    's2rz_cool_adapt': dict(curve="0/0 0.06/0.015 0.25/0.2 0.75/0.83 1/1", sat=1.0, sat_target=0.72, gamma=1.0,
                            balance='bs=0.03:rh=-0.01:bh=0.03', bloom=0.3, bloom_at=0.78, cas=0.95, unsharp='5:5:0.6:5:5:0.0',
                            vignette=0.22, grain=0, exposure=None),
    # remake round 3 (critics: 7 body shots at luma 150-172 / HSV sat 45-62 where the reference sits at 86-107 / 85-123,
    # a pink haze after the flash, crunchy edges at Laplacian 2488 vs 456): a per-clip exposure normaliser (target mean
    # 0.40, kept within 0.08 of the clip's own key), a highlight roll-off on bright clips (p98 > 0.93 -> 0.88), the
    # saturation brought toward the reference's (HSV mean ~0.42, never above 0.55), a gentle curve, a small bloom and
    # half the sharpening (no unsharp)
    's2rz_guard': dict(curve="0/0 0.06/0.02 0.25/0.21 0.5/0.5 0.75/0.78 1/0.98", sat=1.0, gamma=1.0,
                       balance='bs=0.03:rh=-0.01:bh=0.03', bloom=0.14, bloom_at=0.85, cas=0.45, vignette=0.22, grain=0,
                       exposure=0.40,
                       guard=dict(keep_key=0.08, p02_in=0.12, p02_to=0.04, p98_in=0.93, p98_to=0.88, top_to=0.96,
                                  anchors=[0.25], sat_ref=0.42, sat_lo=0.85, sat_hi=1.4, sat_max=0.55)),
    # S2rz remake round 4 (critics: sat median 85 vs the reference's 107, p98 236 vs >= 245, dull Yor shots): the same
    # guard with a punchier target - HSV sat toward 0.46 (x up to 1.8), a real white point, a slight S curve
    's2rz_punch': dict(curve="0/0 0.06/0.02 0.25/0.22 0.5/0.5 0.75/0.79 1/1", sat=1.0, gamma=1.0,
                       balance='rs=-0.03:bs=0.07:rm=-0.025:bm=0.04:bh=0.03', bloom=0.10, bloom_at=0.85, cas=0.45, vignette=0.2, grain=0,
                       exposure=0.40,
                       guard=dict(keep_key=0.08, p02_in=0.08, p02_to=0.025, p98_in=0.95, p98_to=0.95, top_to=0.99,
                                  anchors=[0.25], sat_ref=0.42, sat_lo=0.85, sat_hi=1.7, sat_max=0.58)),
    # hype_typography i4f (PJUNKIE), measured on the reference (luma / p5 / p95 / r-b / Laplacian var, 480 px):
    # intro 94 / 30 / 242 / +27 / 1240 (crisp, warm); chorus 113 / 40 / 212 / +44 / 190 (lifted blacks, soft
    # highlights, warm, heavy glow). Shots pick one via shot['look'].
    'hype_i4f': dict(curve="0/0.06 0.3/0.28 0.7/0.74 1/0.97", sat=1.2, gamma=0.98,
                     balance='rs=0.03:bs=-0.03:rm=0.03:bm=-0.03:rh=0.05:bh=-0.05', bloom=0.45, bloom_at=0.7, cas=0.5,
                     vignette=0.25, grain=3, exposure=0.42),
    'hype_i4f_glow': dict(curve="0/0.08 0.3/0.29 0.7/0.72 1/0.9", sat=1.3, gamma=1.0,
                          balance='rs=0.05:bs=-0.05:rm=0.05:bm=-0.05:rh=0.05:bh=-0.06', bloom=0.45, bloom_at=0.62, cas=0.0,
                          orton=0.18, vignette=0.2, grain=3, exposure=0.40),
    # hype_typography remake round 3: the i4f chorus is one warm sepia family (r-b +48 on the 480 px meter) whatever
    # the footage; cool / blue plates (r-b < ~15) take this stronger warm pull so the cut sequence stays one colour.
    'hype_i4f_glow_warm': dict(curve="0/0.08 0.3/0.28 0.7/0.72 1/0.9", sat=1.05, gamma=1.0,
                               balance='rs=0.16:gs=0.02:bs=-0.16:rm=0.14:gm=0.01:bm=-0.15:rh=0.08:bh=-0.10', bloom=0.45,
                               bloom_at=0.62, cas=0.0, orton=0.18, vignette=0.25, grain=3, exposure=0.40,
                               mixer='rr=0.8:rg=0.22:rb=0.08:gr=0.12:gg=0.8:gb=0.06:br=0.16:bg=0.16:bb=0.55'),
    'hype_i4f_warm': dict(curve="0/0.06 0.3/0.28 0.7/0.74 1/0.97", sat=1.05, gamma=0.98,
                          balance='rs=0.12:bs=-0.13:rm=0.11:bm=-0.12:rh=0.07:bh=-0.08', bloom=0.45, bloom_at=0.7, cas=0.5,
                          vignette=0.3, grain=3, exposure=0.42,
                          mixer='rr=0.85:rg=0.18:rb=0.05:gr=0.1:gg=0.85:gb=0.05:br=0.12:bg=0.12:bb=0.68'),
}

# S2rz remake round 6: the reference's shots sit anywhere from luma 55 to 180 (the one exposure target of 0.40 made every shot
# ~100): per-shot exposure variants of the punch look, picked by the generator from the reference shot's own luma
for _e in range(20, 84, 4):
    LOOKS['s2rz_punch_x%d' % _e] = dict(LOOKS['s2rz_punch'], exposure=_e / 100.0, curve='0/0 0.06/0.018 0.25/0.20 0.5/0.465 0.75/0.765 1/1')
    # round 5: the reference's night shots are teal-cyan (shadows RGB 10 29 41 on a 480 px meter; ours were blue-purple 6 17 38)
    LOOKS['s2rz_night_x%d' % _e] = dict(LOOKS['s2rz_punch_x%d' % _e], balance='rs=-0.05:gs=0.035:bs=0.025:rm=-0.04:gm=0.02:bm=0.02:bh=0.03')



def _monotone_pts(pts):
    """ffmpeg curves draws a natural cubic spline through the points: a steep last segment (p98 -> 0.84, 1.0 -> 0.93)
    makes it dip between the points and SOLARISES the highlights (remake round 2: blue skies turned olive, Gojo
    neon). Relax the points until the spline is monotone and inside [0, 1] (checked on a 1000-step grid)."""
    try:
        import numpy as np
        from scipy.interpolate import CubicSpline
    except Exception:  # noqa: BLE001
        return [pts[0], (0.5, 0.5), pts[-1]]
    xx = np.linspace(0.0, 1.0, 1001)
    # strictly increasing x (a clip with p98 = 1.0 put two points at x = 1 and crashed the spline: cinematic round 6)
    srt, pts = sorted(tuple(p) for p in pts), []
    for p in srt:
        if not pts or p[0] - pts[-1][0] >= 0.005:
            pts.append(p)
        elif p[0] >= 0.999:
            pts[-1] = p

    def ok(pp):
        yy = CubicSpline([a for a, _ in pp], [b for _, b in pp], bc_type='natural')(xx)
        return bool((np.diff(yy) >= -1e-6).all() and yy.min() >= -0.005 and yy.max() <= 1.005)
    pp = [tuple(p) for p in pts]
    for _ in range(12):
        if ok(pp):
            return pp
        # pull every inner point halfway toward the straight line between its neighbours' mapping (identity-ish)
        q = [pp[0]]
        for i in range(1, len(pp) - 1):
            a, b = pp[i]
            if abs(a - 0.5) < 1e-6:
                q.append((a, b))
                continue
            x0, y0 = pp[i - 1] if a < 0.5 else q[-1]
            x1, y1 = pp[i + 1]
            lin = y0 + (y1 - y0) * (a - x0) / max(1e-6, x1 - x0)
            q.append((a, 0.5 * (b + lin)))
        q.append(pp[-1])
        pp = q
    return pp if ok(pp) else [pts[0], (0.5, 0.5), pts[-1]]


def _guard_look(lk, prep):
    """Optional per-clip guard (look key 'guard', cinematic_slowmo remake round 3): a milky / bright-glow clip keeps a
    real black point (clip p02 above p02_in is pulled down to p02_to before the look's curve), bright clips get their
    top rolled so the graded p98 stays under p98_max, and the saturation multiplier brings the clip's HSV saturation
    toward sat_ref within (sat_lo, sat_hi) instead of one fixed multiplier."""
    g = lk['guard']
    lk = dict(lk)
    p02, p98 = float(getattr(prep, 'p02', 0.02)), float(getattr(prep, 'p98', 0.98))
    lo = p02 > g.get('p02_in', 0.10) and p02 < 0.4
    hi = p98 > g.get('p98_in2', g.get('p98_in', 0.90)) and p98 > 0.6
    if g.get('keep_key') and lk.get('exposure'):
        # keep the clip's own key: the exposure target is moved to within keep_key of the clip's mean
        m = float(getattr(prep, 'mean', 0.42))
        lk['exposure'] = round(min(m + g['keep_key'], max(m - g['keep_key'], float(lk['exposure']))), 4)
    if lo or hi:
        # ffmpeg curves is a spline through the points: a mid anchor keeps the midtones where they were (only the
        # black point / the top move)
        pts = [(0.0, 0.0)] + ([(p02, g.get('p02_to', 0.035))] if lo else []) + [(0.5, 0.5)] +             ([(p98, min(p98, g.get('p98_to', 0.86))), (1.0, g.get('top_to', 0.94))] if hi else [(1.0, 1.0)])
        if g.get('anchors'):
            # round 5 (cinematic_slowmo): a natural spline through (0,0)(.5,.5)(p98,..)(1,..) bulges ABOVE identity
            # below the mid (Shinobu: p02 18 -> 23, p50 99 -> 108, HSV sat 74 -> 58: the 'milky wash'); identity
            # anchors at 0.25 / 0.75 keep the untouched range untouched (p50 99, sat 75)
            xs = [a for a, _ in pts]
            for ax in g['anchors']:
                if all(abs(ax - x) > 0.15 for x in xs):
                    pts.append((ax, ax))
            pts.sort()
        lk['pre_curve'] = ' '.join(f'{a:.3f}/{b:.3f}' for a, b in _monotone_pts(pts))
    sat = getattr(prep, 'sat', None)
    if sat is not None and g.get('sat_ref'):
        k = min(g.get('sat_hi', 1.35), max(g.get('sat_lo', 0.75), g['sat_ref'] / max(0.05, sat)))
        if g.get('sat_max'):
            k = min(k, float(g['sat_max']) / max(0.05, sat))
        lk['sat'] = round(float(k), 3)
    return lk


def adapt_look(look, prep):
    """Per-clip copy of a look with sat_target / warm_target / magenta_target: the saturation multiplier and the
    colour balance that bring this clip's measured palette (footage.Prep sat / warm / magenta) to the targets.
    Looks without targets (and clips without the measurement) come back unchanged."""
    lk = LOOKS.get(look, LOOKS['source']) if isinstance(look, str) else look
    if lk and prep is not None and lk.get('guard'):
        lk = _guard_look(lk, prep)
    if not lk or prep is None or 'sat_target' not in lk or getattr(prep, 'sat', None) is None:
        return lk
    lk = dict(lk)
    k = lk['sat_target'] / max(0.05, prep.sat)
    lk['sat'] = round(float(min(1.45, max(0.45, k)) * lk.get('sat', 1.0)), 3)
    # colour balance (midtones + highlights) toward the warmth / magenta targets; colorbalance units ~ 0.5 per 0.1 shift
    dw = float(lk.get('warm_target', prep.warm) - prep.warm)
    dm = float(lk.get('magenta_target', prep.magenta) - (prep.magenta or 0.0))
    rm = max(-0.25, min(0.25, 1.8 * dw / 2 + 0.6 * dm))
    bm = max(-0.25, min(0.25, -1.8 * dw / 2 + 0.6 * dm))
    gm = max(-0.25, min(0.25, -1.2 * dm))
    extra = f'rm={rm:.3f}:gm={gm:.3f}:bm={bm:.3f}'
    base = lk.get('balance') or ''
    base = ':'.join(x for x in base.split(':') if x and x.split('=')[0] not in ('rm', 'gm', 'bm'))
    lk['balance'] = (base + ':' + extra) if base else extra
    return lk


def grade_graph(look, work_w, gamma=1.0, bloom_k=1.0, levels=None):
    """
    ffmpeg filter_complex fragment starting at [g0]: the colour look at work resolution, in planar
    RGB (so the bloom's screen blend never touches chroma planes). Unlabelled output.
    """
    lk = LOOKS.get(look, LOOKS['source']) if isinstance(look, str) else look
    chain = ['format=gbrp']
    if lk.get('levels') and levels:
        lo, hi = levels
        lo = min(0.25, max(0.005, lo))          # 0.005 / 0.995: ffmpeg curves rejects '0/0 0.000/0.015' (clip with p02 = 0)
        hi = max(lo + 0.3, min(0.995, hi))
        chain.append(f"curves=all='0/0 {lo:.3f}/0.015 {hi:.3f}/0.98 1/1'")
    if lk.get('pre_curve'):          # optional per-clip guard curve (styles._guard_look)
        chain.append(f"curves=all='{lk['pre_curve']}'")
    if abs(gamma - 1.0) > 0.01:
        chain.append(f'eq=gamma={gamma:.3f}')
    if lk.get('curve'):
        chain.append(f"curves=all='{lk['curve']}'")
    sat, gm = lk.get('sat', 1.0), lk.get('gamma', 1.0)
    if abs(sat - 1) > 0.005 or abs(gm - 1) > 0.005:
        chain.append(f'eq=saturation={sat}:gamma={gm}')
    if lk.get('balance'):
        chain.append(f"colorbalance={lk['balance']}")
    if lk.get('mixer'):     # optional colorchannelmixer (hype_i4f warm looks: cool plates pulled into the sepia family)
        chain.append(f"colorchannelmixer={lk['mixer']}")
    if lk.get('soft'):
        chain.append(f"gblur=sigma={lk['soft'] * work_w / 1080:.2f}")
    g = '[g0]' + ','.join(chain)
    bloom = lk.get('bloom', 0.0) * bloom_k
    if bloom > 0.02:
        sig = lk.get('bloom_sigma', 24) * work_w / 1080
        g += (f",split[ga][gb];[gb]curves=all='0/0 {lk.get('bloom_at', 0.75)}/0 1/1',gblur=sigma={sig:.1f}[gg];"
              f"[ga][gg]blend=all_mode=screen:all_opacity={bloom:.3f}")
    if lk.get('orton'):
        sig = 0.015 * work_w
        g += (f",split[oa][ob];[ob]gblur=sigma={sig:.1f}[og];[oa][og]blend=all_mode=screen:all_opacity={lk['orton']:.3f}")
    return g


def encoder_vf(look, grain_override=None):
    """Filters run by the encoder at output size: CAS / unsharp sharpening, then grain (never sharpened)."""
    lk = LOOKS.get(look, LOOKS['source']) if isinstance(look, str) else look
    vf = []
    if lk.get('cas'):
        vf.append(f"cas={lk['cas']:.2f}")
    if lk.get('unsharp'):
        vf.append(f"unsharp={lk['unsharp']}")
    grain = lk.get('grain', 0) if grain_override is None else grain_override
    if grain:
        vf.append(f'noise=alls={int(grain)}:allf=t')
    return vf


# ---------------------------------------------------------------------------------------------- leads
# How long before the beat the new shot's first frame shows (ms, negative = before), per family.
#
# THIS IS THE ONLY LEAD TABLE. core/edit/planners.py imports it (planners.LEAD_MS is this dict) so a plan
# can no longer render with different cut placement depending on whether an import succeeded. The general
# numbers are general.json pooled_measurements.cut_lead_by_transition (blur_bridge -80 ms n=107, flash
# -90 ms n=78, hard -80 ms n=15); every transition that has no measurement of its own falls back to
# 'default' for its family.
LEAD_MS = {
    'general': {'blur_bridge': -80, 'blur_bridge_soft': -80, 'blur_bridge_flash': -90, 'flash_white': -90, 'hard': -67,
                'exposure_bump_hard': -67, 'zoom_through': -100, 'punch_settle': -67, 'radial_zoom_burst': -120,
                'whip_slide': -80, 'dissolve_blur': 0, 'invert_strobe': -67, 'dip_black_strobe': -67, 'rgb_split_glitch': -67,
                'darken_slide_preview': 33, 'white_bar_wipe': -33, 'shape_wipe': -67, 'card_tilt_in': -150,
                'photo_collage': -80, 'split_screen': -67, 'halftone_flash': -67, 'hype': -147, 'first_shot_entry': 0,
                'hard_settle': -67, 'none': 0, 'default': -80},
    'dance': {'hard': -60, 'hard_settle': -60, 'defocus_in': -60, 'zoom_blur_in': -60, 'flash_white': -33, 'flash_in': -40,
              'cross_dissolve': -60, 'whip_diag': -60, 'pattern_flash': -33, 'dark_blur_in': -60, 'lens_zoomthrough': -60,
              'none': 0, 'default': -60},
    'character': {'flash_zoom': -90, 'mb_cut': -85, 'whip_horizontal': -90, 'whip_vertical': -60, 'impact_zoom_1frame': -60,
                  'zoom_through_color_field': -10, 'hard': -33, 'white_flash_cut': 0, 'white_out': -75, 'defocus_dissolve': -120,
                  'dip_black': -100, 'spin_blur': -30, 'spin_settle': -30, 'mirror_tile_scale_in': -90, 'tint_flash': -33,
                  'shape_wipe': -67, 'glow_cut': -33, 'card_tilt_in': -90, 'iris_wipe': -60, 'chevron_wipe': -60,
                  'wave_wipe': -60, 'blob_wipe': -60, 'bar_open_reveal': 0, 'none': 0, 'default': -65},
    'kpop': {'blur_in_punch': -40, 'shake_blur': -50, 'blur_rotate': -50, 'blur_zoom': -50, 'hard': -33, 'hard_punch': -25,
             'vslide_wrap': -95, 'dip_black': -10, 'zoom_blur_flash': 0, 'zoom_through': 0, 'cutout_prereveal': 0,
             'slices_reveal': 0, 'photo_clones': -40, 'peek_window': -40, 'strobe_smear': 0, 'vsmear': -30, 'none': 0,
             'default': -40},
    'sound': {'hard': 0, 'cross_dissolve': 0, 'none': 0, 'default': 0},
    # fx.json: the joins deliberately sit OFF the percussion beat (join phase 0.64 +- 0.12 of the 160 BPM
    # beat), so the lead here is a fraction of a beat, applied by the planner - these are only the small
    # in-transition offsets that keep the extreme frame on the musical target.
    'fx': {'whip_exp_white': -100, 'whip_exp_black': -100, 'whip_exp_soft': -100, 'rack_focus': 0, 'spin_settle': 0,
           'zoom_settle': -60, 'glass_shatter': -120, 'dolly_out_white': -120, 'hard': -67, 'none': 0, 'default': -67},
}


# ---------------------------------------------------------------------------------------------- presets
_BASE = dict(
    family='general',
    look='warm_glow',
    frame={'9:16': 'full', '16:9': 'full'},     # full | rot90 | band16x9 | band1x1 | window2x3 | window3x4 | letterbox_black | letterbox_blur
    layout='fill',                              # default shot layout
    interp='flow',                              # flow | native | blend
    speed=dict(default='normal', slowmo_rate=0.5, velocity=dict(kind='u_shape', lo=0.55, hi_in=1.6, hi_out=1.0, tau_in=0.12,
                                                                  tau_out=0.1), shutter=True, shutter_above=1.5),
    camera=dict(handheld=0.003, handheld_hz=3.0, handheld_roll=0.18, handheld_zoom=0.01,
                moves={'push_in': 0.35, 'pull_out': 0.35, 'drift': 0.3}, move_ln=(0.04, 0.10), drift_frac=(0.03, 0.07),
                ease='io', bounce=0.0, strong_bounce=0.0, phrase_shake=0.0, drop_punch=0.0, motion_blur=6.0, entry_roll=0.35,
                pre_cut_pull=None),   # (ln amount, seconds): the pull-back over the last frames of a shot
    transitions=dict(house='blur_bridge', p={'blur_bridge': 0.62, 'blur_bridge_flash': 0.25, 'zoom_through': 0.06,
                                             'whip_slide': 0.03, 'dissolve_blur': 0.04},
                     hero={'flash_white': 0.3, 'radial_zoom_burst': 0.15, 'card_tilt_in': 0.1, 'photo_collage': 0.08,
                           'shape_wipe': 0.07, 'white_bar_wipe': 0.1, 'split_screen': 0.0, 'dip_black_strobe': 0.07,
                           'invert_strobe': 0.05},
                     hero_on='phrase', first='first_shot_entry', max_hero_per_4_bars=1),
    accents=dict(auto=False, mix={}, phase=0.6),
    overlays=dict(gradient=0.0, particles=None, light_leak_on_flash=True, letterbox=0.0, flash_color=(255, 255, 255)),
    text=dict(title='bold_italic', title_when='drop', title_size=0.075, title_dur=1.6, subtitle='wide_thin', subtitle_size=0.03,
              intro='neon'),
    watermark=dict(style='watermark', pos=(0.93, 0.955), anchor='right', size=0.026, opacity=0.5, rotate_with_master=True),
    audio=dict(fade_in=0.0, fade_out=1.5, clip_gain_db=-8.0, sfx_over_music_db=5.0, sfx_hp=100.0, lufs=-14.0, ceiling_db=-2.0,
               snap_attacks=True),
    end=dict(kind='fade_black', dur=1.2),
    lead='general',
)

STYLES = {
    'velocity_slowmo': dict(
        family='general', look='warm_glow',
        speed=dict(default='slowmo', slowmo_rate=0.5, velocity=dict(kind='u_shape', lo=0.55, hi_in=1.6, hi_out=1.0, tau_in=0.12,
                                                                     tau_out=0.1)),
        camera=dict(handheld=0.0, moves={'push_in': 0.6, 'pull_out': 0.4}, move_ln=(0.025, 0.06), ease='io', motion_blur=6.0,
                    entry_roll=0.0),
        transitions=dict(house='exposure_bump_hard', p={'exposure_bump_hard': 0.85, 'blur_bridge_soft': 0.15},
                         hero={'flash_white': 0.5, 'dissolve_blur': 0.5}, hero_on='phrase', first='first_shot_entry'),
        overlays=dict(gradient=0.0, particles=dict(kind='gold_dust', n=45, opacity=0.5), light_leak_on_flash=True, letterbox=0.0),
        text=dict(title='neon', title_when='intro', title_size=0.07, title_dur=2.0, subtitle='wide_thin', subtitle_size=0.028,
                  intro='neon'),
        watermark=dict(style='watermark', pos=(0.04, 0.5), anchor='vertical_left', size=0.022, opacity=0.4),
        end=dict(kind='fade_black', dur=1.2), lead='general',
    ),
    'cinematic_warm': dict(
        family='general', look='warm_glow',
        speed=dict(default='slowmo', slowmo_rate=0.55),
        camera=dict(handheld=0.0015, moves={'push_in': 0.5, 'pull_out': 0.3, 'drift': 0.2}, move_ln=(0.03, 0.07)),
        transitions=dict(house='blur_bridge_soft', p={'blur_bridge_soft': 0.55, 'dissolve_blur': 0.25, 'exposure_bump_hard': 0.2},
                         hero={'flash_white': 0.4, 'radial_zoom_burst': 0.2, 'card_tilt_in': 0.2, 'dip_black_strobe': 0.2},
                         first='first_shot_entry'),
        overlays=dict(gradient=0.1, particles=dict(kind='bokeh', n=30, opacity=0.35), light_leak_on_flash=True, letterbox=0.09),
        text=dict(title='wide_thin', title_when='intro', title_size=0.06, title_dur=2.4, subtitle='tiny', subtitle_size=0.028,
                  intro='wide_thin'),
        end=dict(kind='bw_fade', dur=3.0),
    ),
    'flow_blur': dict(
        family='general', look='warm_glow',
        speed=dict(default='velocity', velocity=dict(kind='u_shape', lo=0.55, hi_in=1.6, hi_out=1.0, tau_in=0.12, tau_out=0.1)),
        overlays=dict(gradient=0.0, particles=dict(kind='sparkles', n=55, opacity=0.55), light_leak_on_flash=True),
        text=dict(title='bold_glow', title_when='drop', title_size=0.07, title_dur=1.6, subtitle='wide_thin', subtitle_size=0.028,
                  intro='neon'),
    ),
    'hype_flash': dict(
        family='general', look='show_palette',
        speed=dict(default='velocity', velocity=dict(kind='u_shape', lo=0.5, hi_in=1.8, hi_out=1.3, tau_in=0.1, tau_out=0.1)),
        camera=dict(handheld=0.003, entry_roll=0.4, phrase_shake=0.012),
        transitions=dict(house='blur_bridge_hype', p={'blur_bridge_hype': 0.45, 'blur_bridge_flash': 0.35, 'zoom_through': 0.1,
                                                      'rgb_split_glitch': 0.05, 'whip_slide': 0.05},
                         hero={'radial_zoom_burst': 0.2, 'card_tilt_in': 0.2, 'split_screen': 0.15, 'white_bar_wipe': 0.15,
                               'flash_white': 0.2, 'shape_wipe': 0.1}, hero_on='phrase', first='first_shot_entry'),
        overlays=dict(gradient=0.12, particles=dict(kind='embers', n=60, opacity=0.5), light_leak_on_flash=True),
        text=dict(title='bold_italic', title_when='drop', title_size=0.085, title_dur=1.4, subtitle='wide_thin',
                  subtitle_size=0.03, intro='wide_thin'),
        lead='general',
    ),
    'glitch': dict(
        family='general', look='hype',
        speed=dict(default='velocity', velocity=dict(kind='u_shape', lo=0.5, hi_in=1.8, hi_out=1.3, tau_in=0.1, tau_out=0.1)),
        camera=dict(handheld=0.004, phrase_shake=0.015),
        transitions=dict(house='rgb_split_glitch', p={'rgb_split_glitch': 0.35, 'blur_bridge_flash': 0.25, 'invert_strobe': 0.1,
                                                      'halftone_flash': 0.08, 'dip_black_strobe': 0.07, 'blur_bridge': 0.15},
                         hero={'invert_strobe': 0.3, 'radial_zoom_burst': 0.3, 'white_bar_wipe': 0.2, 'halftone_flash': 0.2},
                         first='first_shot_entry'),
        overlays=dict(gradient=0.15, particles=dict(kind='embers', n=50, opacity=0.45), light_leak_on_flash=False),
    ),
    'beat_band': dict(
        family='general', look='source', frame={'9:16': 'band16x9', '16:9': 'full'},
        speed=dict(default='normal'), camera=dict(handheld=0.0, moves={'static': 1.0}, move_ln=(0.0, 0.0), entry_roll=0.0),
        transitions=dict(house='darken_slide_preview', p={'darken_slide_preview': 0.92, 'hard': 0.08}, hero={}, first='hard'),
        overlays=dict(particles=None, light_leak_on_flash=False),
        text=dict(title='bold_italic', title_when='band_caption', title_size=0.03, subtitle=None),
        end=dict(kind='cut', dur=0.0),
    ),
    'dance_sync': dict(
        family='dance', look='cute', layout='fill',
        speed=dict(default='velocity', velocity=dict(kind='burst_land', lo=0.5, hi=3.0, peak=0.85, sigma=0.12, advance=1.1),
                   shutter=True, shutter_above=1.5),
        camera=dict(handheld=0.0, moves={'static': 1.0}, move_ln=(0.0, 0.0), bounce=0.0, entry_roll=0.0, motion_blur=6.0),
        # dance.json: 30 % of the hard cuts land ENLARGED and settle with s_k = 1.18^(0.55^k) - that is
        # hard_settle, which used to be in no pool at all, so those cuts rendered flat.
        transitions=dict(house='hard', p={'hard': 0.20, 'hard_settle': 0.08, 'defocus_in': 0.2, 'zoom_blur_in': 0.14,
                                          'flash_white': 0.1, 'flash_in': 0.08, 'cross_dissolve': 0.08, 'whip_diag': 0.04,
                                          'pattern_flash': 0.03, 'dark_blur_in': 0.03},
                         hero={'flash_white': 0.4, 'zoom_blur_in': 0.3, 'pattern_flash': 0.3}, hero_on='phrase', first='hard'),
        accents=dict(auto=True, mix={'zoom_in_punch': 0.45, 'vslide_wrap': 0.2, 'zoom_out_punch': 0.15, 'rotate_punch': 0.07,
                                     'blur_pulse': 0.08, 'flash_pulse': 0.05}, phase=0.6, min_energy=0.45),
        overlays=dict(gradient=0.0, particles=None, light_leak_on_flash=False),
        text=dict(title='hook_3d', title_when='none', title_size=0.08, subtitle=None),
        watermark=dict(style='watermark_glow', pos=(0.5, 0.93), anchor='center', size=0.03, opacity=0.8),
        end=dict(kind='slowmo', dur=None), lead='dance',
    ),
    'dance_smooth': dict(
        family='dance', look='velocity_dark', layout='fill',
        speed=dict(default='velocity', velocity=dict(kind='smooth_wave', mean=1.2, amp=0.5, center=-0.1, advance=1.15)),
        camera=dict(handheld=0.0, moves={'static': 1.0}, move_ln=(0.0, 0.0), follow=True, entry_roll=0.0),
        transitions=dict(house='defocus_in', p={'defocus_in': 0.4, 'zoom_blur_in': 0.3, 'hard': 0.2, 'cross_dissolve': 0.1},
                         hero={'dark_blur_in': 0.5, 'flash_white': 0.5}, first='breath'),
        overlays=dict(gradient=0.0, particles=None, light_leak_on_flash=False),
        text=dict(title=None, title_when='none'),
        end=dict(kind='freeze', dur=1.8), lead='dance',
    ),
    'dance_amv': dict(
        family='dance', look='amv_bloom',
        speed=dict(default='velocity', velocity=dict(kind='burst_land', lo=0.6, hi=2.2, peak=0.85, sigma=0.15, advance=1.05)),
        camera=dict(handheld=0.0, moves={'static': 0.5, 'push_in': 0.5}, move_ln=(0.0, 0.05), entry_roll=0.0),
        transitions=dict(house='defocus_in', p={'defocus_in': 0.3, 'zoom_blur_in': 0.25, 'hard': 0.14, 'hard_settle': 0.06,
                                                'flash_in': 0.1, 'cross_dissolve': 0.1, 'whip_diag': 0.05},
                         hero={'flash_white': 0.7, 'lens_zoomthrough': 0.3}, first='vignette_fade_in'),
        accents=dict(auto=True, mix={'zoom_in_punch': 0.6, 'blur_pulse': 0.2, 'flash_pulse': 0.2}, phase=0.6, min_energy=0.6,
                     every=2),
        overlays=dict(particles=dict(kind='bokeh', n=30, opacity=0.35, every=4), light_leak_on_flash=False),
        text=dict(title='hook_3d', title_when='drop', title_anim='letters_3d', title_size=0.1, title_dur=1.5, subtitle=None),
        watermark=dict(style='watermark_glow', pos=(0.5, 0.93), anchor='center', size=0.03, opacity=0.8),
        end=dict(kind='fade_black', dur=1.5), lead='dance',
    ),
    'character_aura': dict(
        family='character', look='dark_contrast', frame={'9:16': 'window2x3', '16:9': 'full'},
        speed=dict(default='ramp', ramp='impact', slowmo_rate=0.45, rate=0.65),
        camera=dict(handheld=0.003, moves={'push_in': 0.5, 'pull_out': 0.5}, move_ln=(0.08, 0.3), ease='out', alternate=0.7,
                    phrase_shake=0.015, entry_roll=0.0),
        transitions=dict(house='flash_zoom', p={'flash_zoom': 0.26, 'mb_cut': 0.22, 'whip_horizontal': 0.1,
                                                'zoom_through_color_field': 0.08, 'impact_zoom_1frame': 0.07, 'spin_blur': 0.06,
                                                'whip_vertical': 0.05, 'hard': 0.05, 'mirror_tile_scale_in': 0.04, 'tint_flash': 0.04,
                                                'shape_wipe': 0.03}, house_share=0.6,
                         hero={'zoom_through_color_field': 0.4, 'flash_zoom': 0.4, 'impact_zoom_1frame': 0.2}, first='fade_in'),
        overlays=dict(particles=dict(kind='sparkles', n=55, opacity=0.7), light_leak_on_flash=True),
        text=dict(title='hook_3d', title_when='drop', title_size=0.1, title_dur=1.2, subtitle='lyric', subtitle_size=0.03),
        watermark=dict(style='watermark', pos=(0.5, 0.965), anchor='center', size=0.02, opacity=0.4),
        end=dict(kind='fade_black', dur=0.8), lead='character',
    ),
    'character_soft': dict(
        family='character', look='pastel_pink', frame={'9:16': 'window3x4', '16:9': 'full'},
        speed=dict(default='slowmo', slowmo_rate=0.6),
        camera=dict(handheld=0.0, moves={'pull_out': 0.75, 'push_in': 0.25}, move_ln=(0.06, 0.2), ease='out', entry_roll=0.0),
        transitions=dict(house='white_out', p={'white_out': 0.22, 'defocus_dissolve': 0.25, 'impact_zoom_1frame': 0.15,
                                               'whip_vertical': 0.12, 'hard': 0.12, 'dip_black': 0.08, 'glow_cut': 0.06},
                         hero={'white_out': 0.5, 'iris_wipe': 0.25, 'shape_wipe': 0.25}, first='bar_open_reveal'),
        overlays=dict(particles=dict(kind='hearts', n=26, opacity=0.5), light_leak_on_flash=True),
        text=dict(title='script', title_when='intro', title_size=0.08, title_dur=2.0, subtitle='tiny', subtitle_size=0.025),
        watermark=dict(style='watermark', pos=(0.5, 0.965), anchor='center', size=0.02, opacity=0.4),
        end=dict(kind='white_black', dur=1.2), lead='character',
    ),
    'character_raw': dict(
        family='character', look='crisp_vivid', interp='native', frame={'9:16': 'window2x3', '16:9': 'full'},
        speed=dict(default='normal'),
        camera=dict(handheld=0.0, moves={'static': 0.7, 'push_in': 0.3}, move_ln=(0.0, 0.05), entry_roll=0.0),
        transitions=dict(house='hard', p={'hard': 0.45, 'white_flash_cut': 0.45, 'dip_black': 0.05, 'mb_cut': 0.05},
                         hero={'white_flash_cut': 1.0}, first='hard'),
        overlays=dict(particles=None, light_leak_on_flash=False),
        text=dict(title=None, title_when='none'),
        end=dict(kind='fade_black', dur=0.8), lead='character',
    ),
    'postcard_kpop': dict(
        family='kpop', look='kpop_crisp', frame={'9:16': 'band1x1', '16:9': 'full'},
        speed=dict(default='velocity', velocity=dict(kind='kpop_u', lo=0.45, hi=1.2, tau=0.18), rate=0.65),
        camera=dict(handheld=0.0, moves={'hold': 1.0}, move_ln=(-0.02, 0.02), entry='settle', entry_A=(0.15, 0.35),
                    entry_in_p=0.7, entry_tau=0.085, entry_roll=0.0, pre_cut_pull=(0.11, 0.13)),
        transitions=dict(house='blur_in_punch', p={'blur_in_punch': 0.7, 'shake_blur': 0.1, 'blur_rotate': 0.08, 'blur_zoom': 0.07,
                                                   'hard_punch': 0.05},
                         hero={'cutout_prereveal': 0.34, 'slices_reveal': 0.14, 'photo_clones': 0.14, 'zoom_through': 0.13,
                               'zoom_blur_flash': 0.13, 'peek_window': 0.12}, hero_on='phrase', first='fade_in',
                         bleach_on_hero=0.6),
        overlays=dict(gradient=0.0, particles=None, light_leak_on_flash=False),
        text=dict(title='kinetic', title_when='none', title_size=0.083, subtitle=None),
        watermark=dict(style='watermark_script', pos=(0.5, 0.84), anchor='center', size=0.035, opacity=0.85),
        end=dict(kind='dip_black_chain', dur=0.8), lead='kpop',
    ),
    'kpop_stage': dict(
        family='kpop', look='kpop_crisp',
        speed=dict(default='velocity', velocity=dict(kind='kpop_u', lo=0.45, hi=1.2, tau=0.18), rate=0.7),
        camera=dict(handheld=0.0, moves={'hold': 1.0}, move_ln=(-0.02, 0.02), entry='settle', entry_A=(0.08, 0.3),
                    entry_in_p=0.7, entry_tau=0.085, entry_roll=0.0, pre_cut_pull=(0.11, 0.13)),
        transitions=dict(house='hard_punch', p={'hard_punch': 0.55, 'blur_in_punch': 0.35, 'shake_blur': 0.1},
                         hero={'cutout_prereveal': 0.42, 'zoom_through': 0.21, 'zoom_blur_flash': 0.21, 'peek_window': 0.16},
                         hero_on='phrase', first='fade_in', bleach_on_hero=0.6),
        overlays=dict(particles=None, light_leak_on_flash=False),
        text=dict(title='kinetic', title_when='kinetic', title_size=0.083, subtitle=None),
        watermark=dict(style='watermark_script', pos=(0.5, 0.84), anchor='center', size=0.035, opacity=0.85),
        end=dict(kind='dip_black_chain', dur=1.0), lead='kpop',
    ),
    'kpop_trend': dict(
        family='kpop', look='tint_hue_push',
        speed=dict(default='normal', rate=0.8),
        camera=dict(handheld=0.0, moves={'hold': 1.0}, move_ln=(0.0, 0.02), entry_roll=0.0),
        transitions=dict(house='hard', p={'hard': 1.0}, hero={}, first='fade_in'),
        accents=dict(auto=True, kind_only='strobe_smear', mix={'strobe_smear': 0.6, 'vsmear': 0.4}, phase=1.0, per_bar=3,
                     min_energy=0.4),
        overlays=dict(particles=None, light_leak_on_flash=False),
        text=dict(title='lower', title_when='none', subtitle=None),
        watermark=dict(style='watermark_script', pos=(0.5, 0.84), anchor='center', size=0.035, opacity=0.85),
        end=dict(kind='fade_black', dur=0.6), lead='kpop',
    ),
    'sound_edit': dict(
        family='sound', look='neutral', interp='native', frame={'9:16': 'rot90', '16:9': 'full'},
        speed=dict(default='normal', shutter=False),
        camera=dict(handheld=0.0, moves={'static': 1.0}, move_ln=(0.0, 0.0), motion_blur=1e9, entry_roll=0.0),
        transitions=dict(house='hard', p={'hard': 0.97, 'cross_dissolve': 0.03}, hero={}, first='hard'),
        overlays=dict(gradient=0.0, particles=None, light_leak_on_flash=False, vignette=0.0),
        text=dict(title=None, title_when='none', subtitle=None),
        watermark=dict(style='watermark_italic', pos=(0.45, 0.07), anchor='center', size=0.025, opacity=0.3, rotate_with_master=True),
        audio=dict(fade_in=0.0, fade_out=0.0, clip_gain_db=0.0, sfx_over_music_db=5.0, sfx_hp=100.0),
        end=dict(kind='cut', dur=0.0), lead='sound',
    ),
    # core/edit/refs/fx.json (WGvtliE1298): the text is the subject and the footage is a backdrop.
    # Slow wide shallow-focus plates held 1-3 s, 0.42 cuts/s placed OFF the beat, every join hidden inside an
    # extreme state (an exponential whip, a rack focus, a spin settle), no RGB split, no strobe, no dissolve,
    # no shake, no speed ramps and NO added SFX (spectral flatness <= 0.003 across the whole reference).
    'fx_typography': dict(
        family='general', look='neutral', interp='native',
        speed=dict(default='normal', slowmo_rate=0.7, shutter=True, shutter_above=1.5),
        camera=dict(handheld=0.0, moves={'push_in': 0.55, 'drift': 0.25, 'static': 0.2}, move_ln=(0.05, 0.14),
                    ease='linear', motion_blur=6.0, entry_roll=0.0, bounce=0.0, phrase_shake=0.0),
        transitions=dict(house='whip_exp_white',
                         p={'whip_exp_white': 0.34, 'whip_exp_black': 0.22, 'rack_focus': 0.18, 'spin_settle': 0.12,
                            'zoom_settle': 0.14},
                         hero={'whip_exp_soft': 0.34, 'glass_shatter': 0.22, 'spin_settle': 0.22, 'dolly_out_white': 0.22},
                         hero_on='phrase', first='fade_in', max_hero_per_4_bars=1),
        accents=dict(auto=False, mix={}, phase=0.6),
        overlays=dict(gradient=0.0, particles=dict(kind='bokeh', n=57, opacity=0.45), light_leak_on_flash=False,
                      letterbox=0.0, flash_color=(252, 254, 255)),
        text=dict(title='hero_fx', title_when='none', title_size=0.16, title_dur=0.6, subtitle='set_fx',
                  subtitle_size=0.134, intro='hero_fx'),
        watermark=dict(style='watermark', pos=(0.93, 0.955), anchor='right', size=0.024, opacity=0.45),
        audio=dict(fade_in=0.0, fade_out=0.8, clip_gain_db=-99.0, sfx_over_music_db=-99.0, sfx_hp=100.0, lufs=-14.0,
                   ceiling_db=-2.0, snap_attacks=False),
        end=dict(kind='fade_black', dur=0.8), lead='fx',
    ),
}
ALIASES = {'golden_hour': 'velocity_slowmo', 'cinematic_slowmo': 'velocity_slowmo', 'hype': 'hype_flash',
           'hype_typography': 'hype_flash', 'dance': 'dance_sync', 'character': 'character_aura', 'kpop': 'postcard_kpop',
           'sound': 'sound_edit', 'quick_velocity': 'character_aura', 'soft_romance': 'character_soft', 'raw_crisp': 'character_raw',
           'beat_collection_band': 'beat_band', 'long_amv': 'dance_amv', 'fx_heavy': 'fx_typography',
           'typography': 'fx_typography'}


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ('p', 'hero', 'mix', 'moves', 'frame'):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve(name, overrides=None):
    """The full style dict for a preset name (aliases allowed), with plan overrides merged in."""
    name = ALIASES.get(name, name)
    st = _merge(_BASE, STYLES.get(name, STYLES['flow_blur']))
    st['name'] = name if name in STYLES else 'flow_blur'
    if overrides:
        st = _merge(st, overrides)
    return st


def lead_s(style, transition):
    """Seconds the switch frame comes before the beat (negative) for a transition in a style (name or dict)."""
    st = resolve(style) if isinstance(style, str) else style
    table = LEAD_MS.get(st.get('lead', 'general'), LEAD_MS['general'])
    base = (transition or 'hard').split('+')[0]
    if base == 'blur_bridge_hype':
        return LEAD_MS['general']['hype'] / 1000.0
    return table.get(base, table.get('default', -60)) / 1000.0


def cut_time(beat, transition, style, fps=60):
    """Time of the new shot's first frame for a cut that belongs on `beat`, snapped to the output frame grid."""
    t = beat + lead_s(style, transition)
    return round(t * fps) / fps


def names():
    return sorted(STYLES)


def ease(u, kind='io'):
    u = min(1.0, max(0.0, u))
    if kind == 'out':
        return 1 - (1 - u) ** 3
    if kind == 'in':
        return u * u * u
    if kind == 'linear':
        return u
    return u * u * (3 - 2 * u)


def smoothstep(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def expo(t, tau):
    return math.exp(-max(0.0, t) / tau) if tau > 0 else 0.0


# cinematic_slowmo round 10: the same grade without the Orton glow, for shots whose _sX2 slot is crisp (a per-shot 'look')
LOOKS['golden_clear'] = {k: v for k, v in LOOKS['golden_soft'].items() if k != 'orton'}
