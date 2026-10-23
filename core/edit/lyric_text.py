#!/usr/bin/env python3
"""
Lyric text that follows the sung words, per preset, exactly as that preset's references show it.

The owner's rule 4: "there should be text too and the text should follow the lyrics". Only presets whose
references SHOW lyric text get a track; the others get none (their references show a watermark at most).
Everything here is traced to the audio-visual studies core/edit/refs/{general,character,kpop,fx}-av.md/.json:

  preset               refs (with lyric text)        track
  hype_typography      zawB, m3lv, i4f               key words ON the syllable (first ink at the sung onset, refs
                                                      |offset| p50 ~30 ms), 1-2 words per item, about one item per
                                                      beat, condensed caps with a strong white glow, letter spread-in
                                                      200-300 ms (width 0.2 -> 1.1 -> 1.0 x), carried out by the cut
  blur_flow            S2rz (1 of 3 refs)             sparse beat words (33 % of sung words), pastel lilac caps with a
                                                      halo, spread-in 333 ms, out 133 ms; on for ~1 render in 3
  cinematic_slowmo     _sX2                           lyric words on black in the intro only, one word at a time on
                                                      the half-bar pulse, purple neon Poppins Black, spread-in 280 ms
  char_quick_velocity  yUui, TG, KSjp, YH             one sung word per item within +-50 ms of the syllable, thin
                                                      caps type-on with a sideways smear trail (or YH's 3D cyan hook
                                                      words), held about 2 beats, carried out by the cut
  char_soft_romance    rfsj                           small lowercase words trailing the sung word by ~266 ms,
                                                      soft glow, light-streak write-on, fade out
  kpop_text_strobe     pB7P                           every sung word of the intro phrases on black, lowercase
                                                      rounded sans at 85 % white, first word fades 0.28 s, the next
                                                      ones wipe in left to right in 4 frames, the phrase fades 0.3 s
  fx_typography        WGvt                           the word3d fly-through (engine / fxsets) fed with the sung
                                                      words: first ink on the syllable, ~60 % of the words, never in
                                                      the 17 frames after a whip join, never over a built set
  every other preset   -                              none (band_parade / cinematic_long / raw crisp / dance / other
                                                      K-pop / sound: their references carry no lyric text)

Size rule (documented because most references are 16:9 and our shorts are 9:16): a reference's text box is
given as % of ITS picture height; we keep the word's size relative to the picture WIDTH (the dimension that
bounds a word), i.e. box_out = box_ref x (W_out / W_ref) x (H_ref / H_out) of H. A 9:16 reference (pB7P,
_sX2) keeps its % of H as is.

    python core/edit/lyric_text.py --attach --plan plan.json --song "Espresso" --artist "Sabrina Carpenter" \
        [--out plan_lyrics.json] [--threads 2]
    python core/edit/lyric_text.py --selftest              (offline, seconds)
    python core/edit/lyric_text.py --measure --video with.mp4 --notext without.mp4 --plan plan.json \
        [--start s] [--report out.json]   (first-ink time per item vs the sung onset, and the geometry)

API
    attach(plan, opts=None, song=None, artist=None, words=None, preset=None, threads=2) -> summary dict
        adds the preset's lyric track to plan['overlays'] in place (words: edit-time [{text, t_start, t_end}]
        to skip lyrics.py)
    sung_words(plan, song, artist, ...) -> edit-time words from core/edit/lyrics.py
    build(preset, words, plan, rng) -> (overlays, summary)
    HANDLERS = {'lyric_text': draw}   the engine overlay type (engine.py Overlay.apply dispatches here)

Overlay 'lyric_text' (canvas space):
    {"type": "lyric_text", "t0", "t1", "preset", "style" (text.STYLES), "look": {render overrides},
     "items": [{"text", "t0" (first ink), "t_out" (exit starts), "t1" (gone), "x", "y", "box" (fraction of H),
                "anim_in": spread | type_smear | pop3d | write | fade | wipe, "dur_in", "anim_out": cut | fade,
                "dur_out", "scale0", "rot0", "phrase", "sung"}],
     "phrases": [{"t0", "t1", "recentre"}]  (kpop_text_strobe: words laid out as a centred phrase)}
"""
import argparse
import json
import math
import os
import random
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# --------------------------------------------------------------------------------------------- the rules


RULES = {
    'hype_typography': dict(
        refs=['zawBCooJwLw', 'm3lv7H6cu_Y', 'i4fSruIkkxk'], where='body', mode='overlay', prob=1.0,
        lag=0.0, pick='key', max_words=3, max_chars=11, min_gap_beats=0.85, share=0.55,
        style='lyric_hype', box=(0.14, 0.24), ref_aspect='16:9', y=0.70, x_jit=0.05, y_jit=0.05, flip=0.30,
        anim_in='spread', dur_in=(0.20, 0.30), scale0=(1.0, 1.35), rot0=6.0, rot1=(0.6, 3.0, 9.0),
        anim_out='cut', dur_out=0.10, hold=(0.28, 1.7), min_before_cut=0.22, replaces_slams=True,
        note='general-av 3.2: text-in within 120 ms of the vocal onset at 12/13 (m3lv) and 11/13 (zawB) lyric items; '
             'box 17-30 % H (zawB), 8 % (m3lv), 12 % (i4f); cream / white with halo +20..+77 luma; spread-in from '
             '0.05-0.6 to 1.0-1.4 x width over 200-300 ms; out 100-134 ms'),
    'blur_flow': dict(
        refs=['S2rzMsl0N7k'], where='body', mode='overlay', prob=0.34,
        lag=0.03, pick='key', max_words=2, max_chars=10, min_gap_beats=1.0, share=0.33,
        style='lyric_pastel', box=(0.08, 0.16), ref_aspect='16:9', y=0.60, x_jit=0.06, y_jit=0.04, flip=0.34,
        anim_in='spread', dur_in=(0.30, 0.36), scale0=(1.0, 1.1), rot0=0.0,
        anim_out='fade', dur_out=0.133, hold=(0.5, 1.4), min_before_cut=0.35,
        # remake round 2 (refs/remake-blur_flow-S2rz.md): S2rz's held words never sit still - a slide of ~1 %W / frame
        # decelerating (tau 0.35 s), a +-0.5 deg roll wobble at ~3 Hz and a 2 % / s scale creep (30 fps strips)
        hold_slide=[0.24, 0.0], hold_tau=0.35, hold_wobble=0.5, hold_hz=3.0, hold_creep=0.02, hold_flip=True,
        hold_drift=[0.02, 0.0], hold_bob=12.0,     # round 4: a 2 %W / s drift + a 12 px bob for the whole life
        note='general-av 3.1: only S2rz of the 3 refs shows lyric words (ZkWQ / cj12 none), so the track is on for '
             '~1 render in 3; 26/80 sung words, 1-2 words per item, 0.5-1.4 s, box 6-28 % H (p50 12), x 0.45 '
             'y 0.6, pastel lilac with a strong halo, in p50 333 ms spread, out p50 133 ms'),
    'cinematic_slowmo': dict(
        refs=['_sX2EUexbW8'], where='intro_black', mode='on_black', prob=1.0,
        lag=0.0, pick='all', max_words=1, max_chars=12, min_gap_beats=0.0, share=1.0, quantise='lead', lead=0.03,
        ensemble=True, beat_lead=0.09, min_item_s=0.20, abut=True, end_before_drop=0.24, xcheck=True, drop_tail_s=0.7, merge_s=0.20,
        max_hold=1.1, no_repeat=True, complete_phrase=True,   # owner 2026-10-18: no re-triggered word (TO TO), no missing word
        # 2026-10-23: min_item_s 0.26 -> 0.20, merge_s 0.30 -> 0.20 (_sX2 holds words 0.28-0.32 s; a 0.25 s pair was merged and the second word shown 0.25 s early)
        style='lyric_neon', box=(0.063, 0.068), ref_aspect='9:16', y=0.505, x_jit=0.0, y_jit=0.0, flip=None,
        anim_in='smear_shrink', dur_in=(0.04, 0.04), scale0=(1.0, 1.0), rot0=0.0,
        anim_out='fade', dur_out=0.12, hold=(0.35, 2.0), min_before_cut=0.0,
        stretch0=0.30, squeeze=0.23, smear0=0.32, smear_tau=0.035, smear_hold=0.0, stretch_blur=1.0,
        # remake round 2 (critics + textbox.py per frame vs _sX2): the swap frame is a streak ~0.5 W long with a white
        # hot right end (2-3 frames), the white end outlives it (hot_tau), the height shrinks with the width over the
        # hold and collapses in the last 3 frames, +-1 px per-frame jitter; cap height 0.054-0.060 H (was 0.065-0.069)
        hot_tau=0.08, shrink_h=0.23, jitter_px=3.0, collapse_s=0.12, streak_x=0.0,  # round 2: _sX2 streak frame sits +114 px right of the settled word
        # remake round 3 (critics + per-frame boxes / colours vs _sX2 in landscape): ONE streak frame (the reference's
        # frame 1), then legible at 1.3x -> 1.14x -> 1.0x width (stretch_tau 53 ms), white-hot on that first legible
        # frame (fill ~70 % white, halo doubled) cooling over 80 ms; no glint sweep; the height holds (shrink_h 0) and
        # only words held > 0.6 s collapse in their last 3 frames; heavier cut (Poppins Black); fill violet #8a00f0 ->
        # magenta #f05cff; halo ring median RGB (32, 0, 76), p90 ~100 luma (ours was (3, 0, 7) / 43)
        stretch_tau=0.053, glint=True, hot_amt=0.9, hot_pow=4.0, hot_glow=1.0, hot_edge=(0.42, 0.62),
        glow_gain=1.05, glow_sig=(0.05, 0.18), grad0=(0xc0, 0x00, 0xff), grad1=(0xda, 0x10, 0xff), hot_hold=1.0,
        glow_rgb=(0x7a, 0x10, 0xe0), collapse_min_hold=0.6, wave=None, wave_hz=0.9, jitter_sx=0.018, jitter_py=0.25, flicker=(0.14, 0.06),
        squeeze_pow=1.0,
        # remake round 4 (both critics vs fr2/words_big.jpg: ours wave-warped, bubbly, darker violet, flat glow): straight
        # baseline (no wave; the wiggle is whole-word x / y jitter + width breathing only), heavy straight sans
        # (text.lyric_neon -> Montserrat Black), 5 deg forward skew, magenta fill #c539ec -> #fa79ff, a white specular
        # band (~25 % of the word) sweeping left -> right over 0.4 s from the first legible frame with the glow x1.5
        # under it; the right end cools to 0.35 white instead of holding 0.62, so the gradient reads
        # remake round 5 (per-column colour of the settled word, 6 words, ref vs ours): the reference's fill has NO
        # green (median RGB ~(193, 0, 236)); ours was lavender (G 60-120). Its white sits on the right fifth of every
        # word through the whole hold ((250, 213, 253)), not a sweeping band: hot right end held at 0.8, pow 4
        # remake round 6 (critic: the reference's word shrinks 79 -> 61 px (23 %) and dims ~25 % over the hold, letters
        # staggered 2-3 deg; ours squeezed the width only and held its luma): uniform 1.0 -> 0.77, dim 0.25, stagger
        dim=0.12, letter_stagger=(2.5, 0.035),
        # remake round 8 (critics, per frame vs _sX2: letters 4.7-5.5 % of the short side vs 5.3-6.6 %, glow without a
        # hot core (glow/core 68 vs 12), edges ~2 px soft, no tilt sway, word switches 40-80 ms late): box x1.17, glow
        # radius halved, the right end pure white through the hold, drawn at 2x and area-downsampled, a +-4 deg sway
        # and a 2.5 px per-letter bob at ~1.5 Hz, words lead the beat by 90 ms (the cuts' lead)
        ss=2.0, rot_wobble=(4.0, 0.55), letter_bob=(2.5, 1.5),
        # per-column colour of 4 settled words: _sX2's left half is a saturated violet #b400f8-#cb00f7, its right half a
        # light pink-magenta #f25dfc-#fa91fe (p99 luma ~190), never grey-white (ours was #d7d9d9)
        hot_rgb=(0xff, 0x8c, 0xff),
        skew_deg=5.0, glint_t0=0.18, glint_dur=0.45, glint_w=0.07, glint_amt=1.0, glint_glow=0.5,
        # round 10 (critics, text vs _sX2): the entry swipe is centred and wide (about 1.3x the word) with a white core
        # and a 2-frame blur-out; the exit is a symmetric ~2.5x stretch with a white core; a narrow hot sweep turns 1-2
        # letters near-white (#fff0ff)
        streak_white=0.8, exit_smear=2.5, exit_smear_s=0.08, glint_rgb=(0xff, 0xf0, 0xff),
        # remake round 1 (remake-cinematic_slowmo.md): measured per frame in landscape - words land on the tracked beat
        # 20-90 ms early (words 1-8 at 0.00 .32 .72 1.00 1.32 2.32 3.32 4.44 vs beats .36 .74
        # 1.04 1.38 2.41 3.39 4.36), each replaces the previous on its own frame (abut), cap height ~6.7 % of the
        # landscape canvas, a streak-in that collapses in 3-4 frames @25, a shrink to ~0.5-0.6 x over a 1 s hold, the
        # last word fades 3 frames and the picture cuts in 3 frames later (end_before_drop 0.24 s incl. the fade)
        note='general-av 3.3: the sung line (7-8 words) one word at a time on black, dead centre, box 8-17 % H, '
             'purple #a21fe3 glow +18..+35, in p50 280 ms (letters spread from the centre), out p50 120 ms; the '
             'words sit on the half-bar pulse, not on the syllable'),
    'char_quick_velocity': dict(
        refs=['yUuiSey5kmE', 'TG_hmVvoWC4', 'KSjpckUTItU', 'YH_GH1nk1c0'], where='body', mode='overlay', prob=1.0,
        lag=-0.01, pick='key', max_words=1, max_chars=10, min_gap_beats=1.8, share=0.4,
        style='lyric_thin', box=(0.17, 0.24), ref_aspect='16:9', y=0.70, x_jit=0.04, y_jit=0.03, flip=0.30,
        anim_in='type_smear', dur_in=(0.30, 0.30), scale0=(1.0, 1.0), rot0=0.0,
        anim_out='cut', dur_out=0.10, hold=(0.45, 1.3), min_before_cut=0.35,
        variants={'hook3d': dict(p=0.2, style='lyric_hook3d', box=(0.14, 0.22), anim_in='pop3d', dur_in=(0.30, 0.30),
                                 y=0.71, scale0=(1.35, 1.35))},
        note='character-av 3: one sung word per item within +-50 ms of the syllable (yUui -10, TG -47, KSjp +66, '
             'YH -10 ms), box 14-24 % H, centre (0.51-0.55, 0.70), off-white #b0aa9a-#ccc9bb / white, no glow, '
             'type-on with a smear trail 100-300 ms, ~2 beats on screen; YH: 3D cyan #89e0f8 hook words'),
    'char_soft_romance': dict(
        refs=['rfsjEohxVaw'], where='body', mode='overlay', prob=1.0,
        lag=0.266, pick='key', max_words=1, max_chars=12, min_gap_beats=1.5, share=0.4,
        style='lyric_soft', box=(0.08, 0.10), ref_aspect='16:9', y=0.62, x_jit=0.03, y_jit=0.03, flip=0.36,
        anim_in='write', dur_in=(0.266, 0.266), scale0=(1.0, 1.0), rot0=0.0,
        anim_out='fade', dur_out=0.25, hold=(0.8, 1.2), min_before_cut=0.3,
        note='character-av 3: rfsj text trails the sung word by +266 ms (IQR 146..500), box ~9.4 % H, centre, light '
             'with a soft glow (+6 luma), light-streak write-on 266 ms, ~1 s on screen'),
    'kpop_text_strobe': dict(
        refs=['pB7PRQKPUuA'], where='intro_black', mode='on_black', prob=1.0,
        lag=0.0, pick='all', max_words=1, max_chars=14, min_gap_beats=0.0, share=1.0, phrases=True,
        style='lyric_round', box=(0.025, 0.025), ref_aspect='9:16', y=0.50, x_jit=0.0, y_jit=0.0, flip=None,
        anim_in='wipe', dur_in=(0.16, 0.16), first_in=('fade', 0.28), scale0=(1.0, 1.0), rot0=0.0,
        anim_out='fade', dur_out=0.30, hold=(0.3, 9.0), min_before_cut=0.0, end_before_drop=0.06,
        note='kpop-av pB7P: every sung word of the intro phrases, lower case, t10 +17..+89 ms (median +27) after the '
             'onset; first word fades over 6-8 f, the next ones wipe L->R in 3-5 f; phrase fades 0.28-0.32 s; '
             'rounded bold sans, asc-desc 2.2-2.8 % H, 85 % white, centre; last phrase ends 60 ms before the drop'),
    'fx_typography': dict(
        refs=['WGvtliE1298'], where='plates', mode='word3d', prob=1.0,
        lag=0.0, pick='key', max_words=1, max_chars=12, min_gap_beats=0.0, min_gap_s=0.28, share=0.6,
        after_join=17 / 30.0, entry_f=4,
        note='fx-av 3: a word appears on the first frame of its syllable (median lag 0 frames), ~60 % of the sung '
             'words, one per syllable group, preferring the snare; never straddles a join; the first word of a '
             'shot comes 17-18 frames after a whip join; words never fade (they pass the lens)'),
}
# presets whose references show NO lyric text (a watermark / a title card / a name at most)
# 2026-09-29 text style library (core/edit/text_fx.py RECIPES, refs/text-animation.md): per edit type, the recipes a
# planner picks from per edit (weights; the type's own reference look first). A recipe is a text.STYLES look + its
# animation; it never crosses types. plan['planner']['text_recipe'] (or attach opts 'text_recipe') forces one.
RECIPE_WEIGHTS = {
    'blur_flow': {'s2rz_zoomdrift': 0.40, 'la_wide_scatter': 0.25, 'la_script_scatter': 0.20, 'la_word_camera': 0.15},
    'cinematic_slowmo': {'sx2_neon_sway': 1.0},
    'hype_typography': {'zawb_halo_spread': 0.45, 'i4f_persp_type': 0.35, 'npsom_page_flip': 0.20},
    'char_quick_velocity': {'yuui_ride_smear': 0.75, 'beach_title3d': 0.25},
    'char_soft_romance': {'rfsj_soft_write': 1.0},
    'kpop_text_strobe': {'pb7p_static_wipe': 1.0},
    'fx_typography': {'wgvt_word3d': 0.55, 'tri_line_fade': 0.15, 'tri_fly3d': 0.15, 'tri_twitch': 0.075,
                      'tri_camera_push': 0.075},
}


def pick_recipe(preset, plan, variant='base'):
    """The text recipe of this edit (seeded by the plan, independent of the layout rng), or None."""
    forced = (plan.get('planner') or {}).get('text_recipe')
    if forced:
        return forced
    if variant == 'hook3d':
        return 'yh_hook3d_pop'
    ws = RECIPE_WEIGHTS.get(preset) or {}
    if not ws:
        return None
    rr = random.Random('%s|%s|text_recipe' % (plan.get('seed'), preset))
    names = list(ws)
    return rr.choices(names, [ws[n] for n in names])[0]


NO_TEXT = ['band_parade', 'cinematic_long', 'char_raw_crisp', 'dance_burst_parade', 'dance_smooth_velocity',
           'dance_raw', 'dance_meme', 'dance_long_amv', 'kpop_postcard_drop', 'kpop_stage_push', 'kpop_meme_reveal',
           'kpop_single_take_smear', 'sound_hit_sync']

TEMPLATE_PRESET = {'flow_blur': 'blur_flow', 'hype_typography': 'hype_typography', 'cinematic_slowmo': 'cinematic_slowmo',
                   'fx_heavy': 'fx_typography', 'quick_velocity': 'char_quick_velocity',
                   'soft_romance': 'char_soft_romance', 'single_clip_trend': 'kpop_text_strobe'}

BACK = {'it', 'me', 'you', 'so', 'now', 'up', 'out', 'too'}
STOP = set("""a an the and or but so to of in on at for by with from as is are was were be been am i'm im it it's its
i me my you your you're we us our they them he him his she her hers this that these those do does did not no yeah oh
ooh ah uh la na da hey just like if then there here what when where who how all can could would should will i'll
i've i'd don't dont can't cant won't wont gonna wanna gotta ya yo up out about over into onto than 'cause cause
been being some any""".split())


def log(*a):
    print('[lyric_text]', *a, file=sys.stderr, flush=True)


def preset_of(plan, opts=None):
    """The preset id of a plan (planner.preset, opts preset, or its template / family)."""
    opts = opts or {}
    pl = plan.get('planner') or {}
    pid = opts.get('preset') or pl.get('preset')
    if pid:
        return pid
    tp = plan.get('template') or ''
    if tp in TEMPLATE_PRESET:
        if tp == 'single_clip_trend' and not any(o.get('type') == 'text_on_black' for o in plan.get('overlays') or []):
            return 'kpop_single_take_smear'
        return TEMPLATE_PRESET[tp]
    fam = pl.get('family')
    if fam:
        return {'quick_velocity': 'char_quick_velocity', 'soft_romance': 'char_soft_romance',
                'raw_crisp': 'char_raw_crisp'}.get(fam)
    return None


# --------------------------------------------------------------------------------------------- the words

def sung_words(plan, song, artist, song_audio=None, rate=1.0, threads=2, offline=False, stats=None):
    """Edit-time sung words for the plan's music window (core/edit/lyrics.py)."""
    import lyrics as LY
    ms = float(plan.get('music_start') or 0.0)
    D = float(plan.get('duration') or 0.0)
    rate = float(plan.get('music_rate') or rate or 1.0)
    ws = LY.words(song, artist or '', plan['music'], t0=ms, t1=ms + D * rate, rate=rate, threads=threads,
                  song_audio=song_audio, offline=offline, stats=stats)
    pid = ((plan.get('planner') or {}).get('preset') or plan.get('preset')
           or TEMPLATE_PRESET.get(plan.get('template') or ''))     # planner-only plans carry the template, not the preset
    if RULES.get(pid, {}).get('xcheck') and ws and not offline:
        # remake round 3 (cinematic_slowmo): a second opinion on every onset (faster-whisper + harmonic onsets,
        # core/edit/lyric_check.py) - the aligner can land a word 0.4-0.7 s late after a held syllable
        try:
            import lyric_check as LC
            ws, xlog = LC.crosscheck(ws, plan['music'], ms, ms + D * rate, threads=max(2, threads))
            if stats is not None:
                stats['xcheck'] = [dict(r, t=round((r['t'] - ms) / rate, 3)) if 't' in r else r for r in xlog]
        except Exception as e:  # noqa: BLE001
            if stats is not None:
                stats['xcheck'] = f'skipped: {e}'
    if RULES.get(pid, {}).get('ensemble') and ws and not offline and os.environ.get('LYRICS_ENSEMBLE', '1') != '0':
        # 2026-10-23 (owner on blue v2: "the lyrics is still a bit off"; measured +0.4..+0.8 s on 3 of 5 words): the words on
        # black are re-timed by lyric_check.intro_ensemble (two CTC models on the exact words + the stem's word-start events,
        # a late-biased monotone DP). Times only in the log.
        try:
            import lyric_check as LC
            drop = float(plan.get('drop') or 6.0)
            ws, elog = LC.intro_ensemble(ws, plan['music'], ms, ms + (drop + 0.3) * rate, threads=max(2, threads))
            if stats is not None:
                stats['ensemble'] = dict(elog, moves=[dict(m_, was=round((m_['was'] - ms) / rate, 3), now=round((m_['now'] - ms) / rate, 3))
                                                      for m_ in elog.get('moves') or []])
        except Exception as e:  # noqa: BLE001
            if stats is not None:
                stats['ensemble'] = f'skipped: {type(e).__name__}: {str(e)[:160]}'
    out = []
    for w in ws:
        a, b = (float(w['t_start']) - ms) / rate, (float(w['t_end']) - ms) / rate
        if b <= 0 or a >= D:
            continue
        out.append(dict(w, t_start=round(a, 4), t_end=round(b, 4)))
    return out


def _norm(s):
    return re.sub(r"[^a-z']", '', str(s).lower().replace('’', "'"))


def _clean(s, case=None):
    import text as T
    s = T.latin_only(s).strip(' ,.;:!?"()[]')
    if case == 'upper':
        s = s.upper()
    elif case == 'lower':
        s = s.lower()
    return s


def _cuts(plan):
    return [float(s['t0']) for s in (plan.get('shots') or [])[1:]]


def _next_after(xs, t):
    for x in xs:
        if x > t + 1e-6:
            return x
    return None


def _snares(plan):
    """Backbeat times (beats 2 and 4 of each bar) - the fx reference prefers syllables on the snare."""
    beats = [float(b) for b in plan.get('beats') or []]
    downs = [float(d) for d in plan.get('downbeats') or []]
    out = []
    for b in beats:
        prev = [d for d in downs if d <= b + 1e-3]
        if not prev:
            continue
        k = round((b - prev[-1]) / max(0.2, float(plan.get('beat_period') or 0.5)))
        if k in (1, 3):
            out.append(b)
    return out


def _group(words, rule):
    """Sung words -> every candidate item of 1..max_words consecutive words (a function word leans on its
    neighbour: 'OH ISN'T', 'EVERY NIGHT', 'KNOW IT'). Each candidate carries the indices of its words so the
    chosen items never share a word."""
    out = []
    mw, mc = int(rule.get('max_words', 1)), int(rule.get('max_chars', 12))
    for i, w in enumerate(words):
        grp = [w]
        out.append((grp[:], (i,)))
        j = i + 1
        while mw > 1 and j < len(words) and len(grp) < mw:
            nx = words[j]
            gap = float(nx['t_start']) - float(grp[-1]['t_end'])
            txt = ' '.join(x['text'] for x in grp + [nx])
            # a function word leans FORWARD onto the next word ('I GUESS', 'OH ISN'T'); an object pronoun leans
            # back ('KNOW IT'); two content words go together only when sung in one breath ('EVERY NIGHT')
            lean = (_norm(grp[-1]['text']) in STOP and gap < 0.22) or (_norm(nx['text']) in BACK and gap < 0.22)                 or (gap < 0.15 and _norm(nx['text']) not in STOP and _norm(grp[-1]['text']) not in STOP)
            if re.search(r'[,.?!;:]$', str(grp[-1]['text']).strip()):
                break                       # never across the end of a sung phrase ('SWEET? | I GUESS')
            if len(txt) <= mc and lean and nx.get('line_id') == w.get('line_id'):
                grp.append(nx)
                out.append((grp[:], tuple(range(i, j + 1))))
                j += 1
            else:
                break
    return out


def _complete_phrase(cand, ws, rule):
    """owner 2026-10-18 (slowmo_3 showed 'TO TO' where the lyric is 'TO SEE'): the words on black follow the fetched lyric text
    in its true order with no sung word missing between the first and the last shown word, and never the same word twice in a
    row (a repeated / held word stays on screen longer instead). cand = [(t, words, ws indices)] sorted by time."""
    mi = float(rule.get('min_item_s', 0.26))
    mc = int(rule.get('max_chars', 12))
    cand = sorted(cand, key=lambda c: min(c[2]))
    used = {i for _, _, idx in cand for i in idx}
    lo, hi = min(used), max(used)
    for i in [i for i in range(lo, hi + 1) if i not in used]:
        w = ws[i]
        k = max(j for j, c in enumerate(cand) if min(c[2]) < i)          # the item sung before it
        t_prev = cand[k][0]
        t_next = cand[k + 1][0] if k + 1 < len(cand) else None
        t = max(float(w['t_start']), t_prev + mi)
        if t_next is None or t <= t_next - mi:
            cand.insert(k + 1, (t, [w], [i]))                           # its own item at its sung time
        else:
            tk, gk, ik = cand[k]                                         # no room: it shares the previous item
            cand[k] = (tk, gk + [w], list(ik) + [i])
            if len(' '.join(x['text'] for x in cand[k][1])) > mc + 6 and t_next is not None:
                tn, gn, iN = cand[k + 1]                                 # too long: lead the next item instead
                cand[k] = (tk, gk, ik)
                cand[k + 1] = (tn, [w] + gn, [i] + list(iN))
        cand = sorted(cand, key=lambda c: min(c[2]))
    out = []
    for t, g, idx in cand:
        if out and _norm(' '.join(x['text'] for x in g)) == _norm(' '.join(x['text'] for x in out[-1][1])):
            pt, pg, pidx = out[-1]
            out[-1] = (pt, pg, list(pidx) + list(idx))                   # the same word again: held longer, not shown twice
            continue
        out.append((t, g, idx))
    return out


def phrase_check(items_text, lyric_words):
    """(ok, why) for a shown word sequence against the lyric words of the window, both in order: no word twice in a row, no
    sung word missing inside the shown span, no word that is not in the lyrics (used by judge_slowmo at render time)"""
    shown = [_norm(x) for it in items_text for x in str(it).split() if _norm(x)]
    lyr = [_norm(w) for w in lyric_words if _norm(w)]
    if not shown:
        return False, 'no words shown'
    for a_, b_ in zip(shown, shown[1:]):
        if a_ == b_:
            return False, f'"{a_}" twice in a row'
    for start in range(len(lyr)):
        if lyr[start] != shown[0]:
            continue
        seg = lyr[start:start + len(shown)]
        dedup = [x for j, x in enumerate(seg) if not j or x != seg[j - 1]]
        if dedup == shown or seg == shown:
            return True, 'in order, complete'
    return False, 'shown words are not one contiguous run of the lyric line(s) (a word missing, extra or out of order)'


def _score(grp, snares):
    content = [x for x in grp if _norm(x['text']) not in STOP]
    s = sum(min(8, len(_norm(x['text']))) for x in content) + 3.0 * len(content)
    s += 1.5 * (len(grp) - 1)
    if not content:
        s -= 6.0            # a function word alone ('SO', 'I') only when nothing better is sung near it
    s += 2.0 * float(np.mean([float(x.get('confidence') or 0.5) for x in grp]))
    t = float(grp[0]['t_start'])
    if snares and min(abs(t - b) for b in snares) < 0.06:
        s += 2.0
    if grp[0].get('partial'):
        s -= 3.0
    return s


def _blocked(plan):
    """Windows where lyric text would fight other text / a scene that replaces the picture."""
    out = []
    for o in plan.get('overlays') or []:
        ty = o.get('type')
        if ty in ('text', 'title_card', 'music_card', 'music_card3d', 'postcard_intro', 'photo_scene_intro', 'caption_bar',
                  'center_label', 'glass_set', 'white_set', 'letters_fly', 'closing_3d', 'kinetic_name', 'light_streak',
                  'text_on_black'):
            out.append((float(o.get('t0', 0)), float(o.get('t1', 0))))
    # the style's own title card on the drop (engine.py Engine._init_overlays)
    pl = plan.get('planner') or {}
    so = ((plan.get('style_overrides') or {}).get('text') or {})
    if pl.get('show_title') and plan.get('title') and so.get('title_when') != 'none':
        try:
            import styles as S
            tx = (S.STYLES.get(plan.get('style')) or {}).get('text') or {}
            if tx.get('title') and tx.get('title_when', 'drop') == 'drop':
                d = float(plan.get('drop') or 0.0)
                out.append((d - 0.1, d + float(tx.get('title_dur', 1.6)) + 0.1))
        except Exception:  # noqa: BLE001
            pass
    return out


def build(preset, words, plan, rng=None):
    """-> (overlays to add, summary). words: edit-time [{text, t_start, t_end, line_id, confidence, lyric}]."""
    rng = rng or random.Random(0)
    rule = RULES.get(preset)
    if not rule:
        return [], {'preset': preset, 'track': 'none', 'why': 'its references show no lyric text'}
    if rule['mode'] == 'word3d':
        rec = pick_recipe(preset, plan)
        ovs, summ = _build_word3d(rule, words, plan, rng)
        if rec and rec != 'wgvt_word3d':
            ovs = _word3d_to_recipe(plan, ovs, rec, rng)
            summ['text_recipe'] = rec
        elif rec:
            summ['text_recipe'] = rec
        return ovs, summ
    if preset == 'cinematic_slowmo':
        # 2026-10-21 (CI dry run 3, "blue"): a Whisper word outside the LRC (lyrics._foreign, lyric=False) went on screen and
        # the judge failed it (3_lyrics 0, NON-LYRIC). The slow-mo edit shows the song's lyrics only (owner 2026-10-18).
        words = [w for w in words or [] if w.get('lyric', True)]
    rule = dict(rule)
    var = 'base'
    for vn, v in (rule.get('variants') or {}).items():
        if rng.random() < float(v.get('p', 0)):
            rule.update({k: x for k, x in v.items() if k != 'p'})
            var = vn
            break
    D = float(plan['duration'])
    P = float(plan.get('beat_period') or 0.5)
    drop = float(plan.get('drop') or 0.0)
    cuts = _cuts(plan)
    W, H = int(plan.get('width') or 1080), int(plan.get('height') or 1920)
    import text as T
    recipe = pick_recipe(preset, plan, var)
    try:
        import text_fx as TFX
        if recipe not in TFX.RECIPES:
            recipe = None
    except Exception:  # noqa: BLE001
        recipe = None
    rstyle = TFX.RECIPES[recipe].get('style') if recipe else None
    case = T.STYLES.get(rstyle or rule['style'], {}).get('case')
    ws = [w for w in words if _clean(w['text'], case)]
    if rule['where'] == 'intro_black':
        lim = drop - float(rule.get('end_before_drop', 0.0))
        ws = [w for w in ws if -0.1 <= float(w['t_start']) < lim - 0.15]      # a word sung before frame 0 cannot show
    elif rule['where'] == 'body':
        lo_ = max(0.0, drop - 0.05) if rule.get('from_s') is None else float(rule['from_s'])     # from_s: composer override
        ws = [w for w in ws if float(w['t_start']) >= lo_ and float(w['t_start']) < D - 0.4]
    blocked = _blocked(plan) if rule['where'] != 'intro_black' else []
    groups = _group(ws, rule)
    snares = _snares(plan)
    lag = float(rule['lag'])
    # which items: all (on black) or key words, about one per min_gap beats, the best-scoring first
    cand = []
    for g, idx in groups:
        t = float(g[0]['t_start']) + lag
        if rule.get('quantise') == 'half_beat':
            grid = np.arange(0.0, D, P / 2.0) if P > 0 else np.array([t])
            t = float(grid[int(np.argmin(np.abs(grid - t)))]) if len(grid) else t
        elif rule.get('quantise') == 'lead':
            # 2026-10-23: _sX2 measured per word (CTC + stem events): first ink 0.00-0.06 s before the sung onset (median 0.035),
            # legible one streak frame later; no beat snap (the 'beat_lead' snap moved blue v2's first word 0.11 s early)
            t = max(0.0, t - float(rule.get('lead', 0.03)))
            if cand and t < cand[-1][0] + float(rule.get('min_item_s', 0.0)):
                t = cand[-1][0] + float(rule.get('min_item_s', 0.0))
        elif rule.get('quantise') == 'beat_lead':
            # _sX2: a word lands on the song's tracked beat nearest its syllable, a frame or two early
            rb = np.asarray(plan.get('raw_beats') or plan.get('beats') or [], float) - float(rule.get('beat_lead', 0.05))
            if len(rb):
                j = int(np.argmin(np.abs(rb - t)))
                if abs(rb[j] - t) <= 0.12:
                    t = max(0.0, float(rb[j]))
            if cand and t < cand[-1][0] + float(rule.get('min_item_s', 0.0)):
                t = cand[-1][0] + float(rule.get('min_item_s', 0.0))
        if any(a - 0.05 <= t <= b for a, b in blocked):
            continue
        if rule['pick'] == 'key' and sum(len(_norm(x['text'])) for x in g) < 2:
            continue            # never a lone letter ('I', 'A')
        if rule['pick'] != 'key' and len(idx) > 1:
            continue
        nc = _next_after(cuts, t)
        if rule['where'] == 'body' and nc is not None and nc - t < float(rule.get('min_before_cut', 0.0)):
            continue            # it would straddle the cut: the references let words exit WITH a cut, never enter just before
        cand.append((t, g, idx))
    if rule['where'] == 'intro_black' and rule.get('drop_tail_s'):
        # remake round 6 (critic: the line ended on a lone pickup word held 0.4 s, then black): no word that starts
        # within drop_tail_s of the body cut - the intro ends at the phrase end, the last full word fades to the hero
        tail_popped = []
        while len(cand) > 1 and cand[-1][0] >= drop - float(rule['drop_tail_s']):
            tail_popped.insert(0, cand.pop())
        if rule.get('complete_phrase') and tail_popped:
            # owner 2026-10-18: never drop a sung word of the shown phrase - a late word of the same line joins the last item
            t_l, g_l, idx_l = cand[-1]
            for _, g2, idx2 in tail_popped:
                txt = ' '.join(x['text'] for x in g_l + g2)
                if len(txt) <= int(rule.get('max_chars', 12)) + 6 and g2[0].get('line_id') == g_l[-1].get('line_id'):
                    g_l, idx_l = g_l + g2, list(idx_l) + list(idx2)
                else:
                    break
            cand[-1] = (t_l, g_l, idx_l)
    if rule['where'] == 'intro_black' and rule.get('merge_s'):
        # remake round 6 (critic: two words on screen 0.26 s each, _sX2's shortest is 0.35 s): a word the next one
        # replaces within merge_s shares its item with it (the pair reads as one beat)
        out, k = [], 0
        while k < len(cand):
            t, g, idx = cand[k]
            if k + 1 < len(cand) and cand[k + 1][0] - t < float(rule['merge_s']) and                     len(' '.join(x['text'] for x in g + cand[k + 1][1])) <= int(rule.get('max_chars', 12)):
                out.append((t, g + cand[k + 1][1], list(idx) + list(cand[k + 1][2])))
                k += 2
                continue
            out.append((t, g, idx))
            k += 1
        cand = out
    if rule['where'] == 'intro_black' and rule.get('complete_phrase') and cand:
        cand = _complete_phrase(cand, ws, rule)
    if rule['where'] == 'intro_black' and rule.get('max_hold') and not rule.get('no_repeat') and cand:
        # round 10 (critic, BLOCKING: a sung word stayed on screen ~1.9 s, the hook dragged; _sX2 changes word every
        # 0.4-1.1 s): a word held longer than max_hold is re-triggered on the tracked beats (its smear-in + glow punch
        # again), never a blank black gap
        mh = float(rule['max_hold'])
        rb = np.asarray(plan.get('raw_beats') or plan.get('beats') or [], float) - float(rule.get('beat_lead', 0.05))
        end_all = drop - float(rule.get('end_before_drop', 0.0))
        out = []
        for k, (t, g, idx) in enumerate(cand):
            out.append((t, g, idx))
            t_n = cand[k + 1][0] if k + 1 < len(cand) else end_all
            last_t = t
            while len(rb) and t_n - last_t > mh + 0.25:
                lo_, hi_ = last_t + 0.55 * mh, t_n - 0.35
                c_ = rb[(rb >= lo_) & (rb <= hi_)]
                if not len(c_):
                    break
                # the beat closest to mh after the last trigger
                tr = float(c_[int(np.argmin(np.abs(c_ - (last_t + mh))))])
                out.append((tr, g, ()))
                last_t = tr
        cand = out
    if rule['pick'] == 'key':
        gap = max(float(rule.get('min_gap_beats', 1.0)) * P, 0.25)
        order = sorted(cand, key=lambda c: -_score(c[1], snares))
        n_target = max(1, int(round(float(rule.get('share', 0.5)) * len(ws))))
        chosen, used = [], set()
        for t, g, idx in order:
            if used.isdisjoint(idx) and all(abs(t - u) >= gap for u, _, _ in chosen):
                chosen.append((t, g, idx))
                used.update(idx)
            if len(chosen) >= n_target:
                break
        cand = sorted(chosen, key=lambda c: c[0])
    items = []
    # a 16:9 reference's box (% of ITS height) becomes a fraction of OUR canvas width (1080 / 1920 of it)
    by_width = rule.get('ref_aspect') == '16:9' and H > W
    phrase_id, prev_end, prev_line = -1, -9.0, None
    for k, (t, g, _) in enumerate(cand):
        text = ' '.join(_clean(x['text'], case) for x in g)
        nxt = cand[k + 1][0] if k + 1 < len(cand) else D
        nc = _next_after(cuts, t) if rule['where'] == 'body' else None
        h0, h1 = rule['hold']
        end = min(t + h1, nxt - 0.02, D - 0.05)
        exit_kind = rule['anim_out']
        if nc is not None and nc < end + 1e-6:
            end = nc                                  # carried out by the cut
        elif exit_kind == 'cut':
            exit_kind = 'fade'                        # no cut before the next word: a quick fade (m3lv out 134 ms)
        if rule['where'] == 'intro_black':
            end = min(nxt - 0.02, drop - float(rule.get('end_before_drop', 0.0)))
            if rule.get('abut'):
                # the next word replaces this one on its own frame; the last one fades before the picture cuts in
                last = k + 1 >= len(cand) or nxt >= drop - float(rule.get('end_before_drop', 0.0))
                end = (drop - float(rule.get('end_before_drop', 0.0))) if last else nxt
                exit_kind = 'fade' if last else 'cut'
        if not rule.get('phrases') and end - t < (h0 if rule['where'] == 'body' else 0.15):
            continue
        dur_out = 0.0 if (rule.get('abut') and exit_kind == 'cut') else float(rule['dur_out'])
        it = dict(text=text, sung=round(float(g[0]['t_start']), 4), t0=round(t, 4),
                  x=round(0.5 + rng.uniform(-rule['x_jit'], rule['x_jit']), 4),
                  y=round(rule['y'] + rng.uniform(-rule['y_jit'], rule['y_jit']), 4),
                  box=round(rng.uniform(*rule['box']), 5),
                  anim_in=rule['anim_in'], dur_in=round(rng.uniform(*rule['dur_in']), 3),
                  anim_out=exit_kind, dur_out=dur_out,
                  scale0=round(rng.uniform(*rule['scale0']), 3),
                  rot0=round(rng.choice([-1, 1]) * rng.uniform(0.5, 1.0) * float(rule.get('rot0', 0.0)), 2)
                  if rng.random() < 0.5 else 0.0,
                  lyric=bool(all(x.get('lyric', True) for x in g)))
        if by_width:
            it['box_w'] = round(it.pop('box') * 1080.0 / 1920.0, 5)
        if rule.get('rot1') and rng.random() < rule['rot1'][0]:
            # zawB holds most words tilted 3-9 deg ('OH ISN'T THA', 'KNOW IT'S', 'I GUESS SO')
            it['rot1'] = round(rng.choice([-1, 1]) * rng.uniform(rule['rot1'][1], rule['rot1'][2]), 2)
        if exit_kind == 'cut':
            it['t_out'] = round(end - 0.5 * dur_out, 4)      # the smear peaks on the cut and is gone 1-2 frames after
            it['t1'] = round(min(D, end + 0.5 * dur_out), 4)
        else:
            it['t_out'] = round(end, 4)
            it['t1'] = round(min(D, end + dur_out), 4)
        if rule.get('flip') is not None:
            it['flip'] = rule['flip']
        for kx in ('stretch0', 'squeeze', 'smear0', 'smear_tau', 'smear_hold', 'stretch_blur', 'hot_tau', 'shrink_h',
                   'jitter_px', 'streak_x', 'collapse_s', 'stretch_tau', 'glint', 'hot_amt', 'hot_pow', 'hot_glow',
                   'glow_gain', 'glow_sig', 'grad0', 'grad1', 'glow_rgb', 'collapse_min_hold', 'wave', 'wave_hz', 'jitter_sx', 'jitter_py', 'flicker', 'squeeze_pow', 'hot_hold',
                   'skew_deg', 'glint_t0', 'glint_dur', 'glint_w', 'glint_amt', 'glint_glow', 'hot_edge',
                   'hold_slide', 'hold_tau', 'hold_wobble', 'hold_hz', 'hold_creep', 'dim', 'letter_stagger',
                   'hold_drift', 'hold_bob', 'ss', 'rot_wobble', 'letter_bob', 'hot_rgb', 'streak_white', 'exit_smear', 'exit_smear_s', 'glint_rgb'):
            if kx in rule:
                it[kx] = rule[kx]
        if it.get('hold_slide') and rule.get('hold_flip'):
            # S2rz: each held word slides its own way (left or right), with its own wobble phase
            _sg = rng.choice([-1.0, 1.0])
            it['hold_slide'] = [float(it['hold_slide'][0]) * _sg, float(it['hold_slide'][1])]
            it['hold_phase'] = round(rng.uniform(0.0, 6.28), 2)
            if it.get('hold_drift'):
                it['hold_drift'] = [abs(float(it['hold_drift'][0])) * _sg, float(it['hold_drift'][1])]
        if rule.get('phrases'):
            ln = g[0].get('line_id')
            if t - prev_end > 0.45 or (ln is not None and prev_line is not None and ln != prev_line and t - prev_end > 0.25):
                phrase_id += 1
                it['anim_in'], it['dur_in'] = rule['first_in']
            prev_end, prev_line = float(g[-1]['t_end']), ln
            it['phrase'] = phrase_id
        items.append(it)
    ovs = []
    summary = {'preset': preset, 'track': rule['mode'], 'variant': var, 'refs': rule['refs'], 'words_sung': len(ws),
               'items': len(items)}
    if not items:
        summary['why'] = 'no sung words in the window'
        return [], summary
    if rule.get('phrases'):
        # the phrase holds until its next phrase (or the drop) and fades 0.3 s; words of a phrase share it
        phrases = []
        for pid in sorted(set(i['phrase'] for i in items)):
            its = [i for i in items if i['phrase'] == pid]
            nxt = [i['t0'] for i in items if i['phrase'] == pid + 1]
            end = min((nxt[0] - 0.2) if nxt else drop - float(rule.get('end_before_drop', 0.06)),
                      max(i['t0'] for i in its) + 2.5)
            end = max(end, max(i['t0'] for i in its) + 0.4)
            phrases.append(dict(t0=its[0]['t0'], t1=round(end, 4), recentre=pid > 0))
            for i in its:
                i['t_out'], i['t1'] = round(end - float(rule['dur_out']), 4), round(end, 4)
        summary['phrases'] = len(phrases)
    else:
        phrases = None
    ov = dict(type='lyric_text', t0=round(min(i['t0'] for i in items), 4), t1=round(max(i['t1'] for i in items), 4),
              preset=preset, style=rule['style'], items=items)
    if phrases:
        ov['phrases'] = phrases
    if recipe:
        TFX.apply_recipe(ov, recipe, random.Random('%s|%s|recipe_box' % (plan.get('seed'), preset)))
        summary['text_recipe'] = recipe
    ovs.append(ov)
    summary['first_ink_minus_sung_ms'] = [round((i['t0'] - i['sung']) * 1000.0, 1) for i in items]
    summary['words'] = [i['text'] for i in items]
    return ovs, summary


def _plate_luma(plan, t, cache={}):
    """Mean luma (0-255) of the middle band of the clip frame on screen at edit time t (approximate clip time from
    the shot's speed curve / src_in), or None. Used to keep the pure-white fx hero words off blown-out plates."""
    shots = plan.get('shots') or []
    s = None
    for sh in shots:
        if float(sh['t0']) <= t < float(sh.get('t1', 1e9)):
            s = sh
    if s is None or not s.get('path') or not os.path.exists(s['path']):
        return None
    u = t - float(s['t0'])
    ct = float(s.get('src_in') or 0.0) + u
    cur = s.get('speed_curve')
    if cur:
        us = [c[0] for c in cur]
        ct = float(np.interp(u, us, [c[1] for c in cur]))
    key = (s['path'], round(ct, 2))
    if key in cache:
        return cache[key]
    val = None
    try:
        import cv2
        cap = cv2.VideoCapture(s['path'])
        cap.set(cv2.CAP_PROP_POS_MSEC, ct * 1000.0)
        ok, fr = cap.read()
        cap.release()
        if ok:
            g = cv2.cvtColor(cv2.resize(fr, (160, 90)), cv2.COLOR_BGR2GRAY)
            portrait = int(plan.get('height') or 1920) > int(plan.get('width') or 1080)
            # the part of a 16:9 clip a 9:16 fill shows (calibrated on a render: clip 170 -> screen 204-218,
            # clip 72 -> screen 63; the grade and bloom brighten highlights)
            val = float(g[:, 55:105].mean() if portrait else g[20:75].mean())
    except Exception:  # noqa: BLE001
        val = None
    cache[key] = val
    return val


def _word3d_to_recipe(plan, ovs, recipe, rng):
    """fx_typography with a trialeltron recipe: the words _build_word3d chose (on their syllables, off the joins and
    the built sets) are drawn as a lyric_text track in that recipe instead of the word3d fly-through."""
    import text_fx as TFX
    w3 = next((o for o in (plan.get('overlays') or []) + list(ovs) if o.get('type') == 'word3d'), None)
    if w3 is None or not w3.get('words'):
        return ovs
    D = float(plan['duration'])
    ws = sorted(w3['words'], key=lambda n: float(n['t0']))
    items = []
    for k, n in enumerate(ws):
        t0 = float(n['t0'])
        nxt = float(ws[k + 1]['t0']) if k + 1 < len(ws) else D
        t1 = min(D - 0.05, t0 + 1.4, nxt - 0.03)
        if t1 - t0 < 0.3:
            continue
        items.append(dict(text=n['text'], sung=n.get('sung', t0), t0=round(t0, 4), t_out=round(t1 - 0.2, 4),
                          t1=round(t1, 4), x=round(0.5 + rng.uniform(-0.08, 0.08), 4), y=round(0.5 + rng.uniform(-0.06, 0.06), 4),
                          box=0.08, anim_in='fade', dur_in=0.2, anim_out='fade', dur_out=0.2, flip=0.3, lyric=True))
    if not items:
        return ovs
    w3['words'] = []                  # the fly-through gives way to the recipe's track
    ov = dict(type='lyric_text', t0=items[0]['t0'], t1=items[-1]['t1'], preset='fx_typography', style='hero_fx', items=items)
    TFX.apply_recipe(ov, recipe, random.Random('%s|fx_typography|recipe_box' % plan.get('seed')))
    return [o for o in ovs if o is not w3] + [ov]


def _build_word3d(rule, words, plan, rng):
    """fx_typography: feed the sung words to the word3d fly-through already in the plan (planners.plan_fx_edit)."""
    D = float(plan['duration'])
    ovs = plan.get('overlays') or []
    w3 = next((o for o in ovs if o.get('type') == 'word3d'), None)
    busy = [(float(o['t0']), float(o['t1'])) for o in ovs if o.get('type') in
            ('music_card3d', 'glass_set', 'white_set', 'set_entry', 'set_exit', 'closing_3d')]
    shots = plan.get('shots') or []
    joins = [(float(s['t0']), str(s.get('transition_in') or '')) for s in shots[1:]]
    snares = _snares(plan)
    entry = float((w3 or {}).get('entry_f', rule.get('entry_f', 4))) / 30.0
    cand, skipped_bright = [], []
    for w in words:
        txt = _clean(w['text'], 'lower')
        if not txt or len(txt) > rule['max_chars']:
            continue
        s = float(w['t_start']) + float(rule['lag'])
        if s >= D - 1.6 or any(a - 0.35 <= s < b + 0.15 for a, b in busy):
            continue
        pj = [j for j, tr in joins if j <= s]
        if pj:
            jt = pj[-1]
            jtr = next(tr for j, tr in joins if j == jt)
            if s - jt < (rule['after_join'] if 'whip' in jtr else 0.25):
                continue
        nj = _next_after([j for j, _ in joins], s)
        if nj is not None and nj - s < 0.3:     # never straddle a join
            continue
        lu = _plate_luma(plan, s + 0.15)
        if lu is not None and lu > float(rule.get('max_plate_luma', 150)):
            skipped_bright.append(txt)
            continue                            # legibility: a pure-white word on a blown-out plate cannot be read
        cand.append((s, txt, w))
    n_target = max(1, int(round(rule['share'] * (len(cand) + len(skipped_bright))))) if cand else 0
    order = sorted(cand, key=lambda c: -_score([c[2]], snares))
    chosen = []
    for c in order:
        if all(abs(c[0] - u[0]) >= rule['min_gap_s'] for u in chosen):
            chosen.append(c)
        if len(chosen) >= n_target:
            break
    chosen.sort(key=lambda c: c[0])
    new = [dict(text=txt, t0=round(s + entry, 4), sung=round(float(w['t_start']), 4)) for s, txt, w in chosen]
    summary = {'preset': 'fx_typography', 'track': 'word3d', 'refs': rule['refs'], 'words_sung': len(words),
               'items': len(new), 'words': [n['text'] for n in new],
               'first_ink_minus_sung_ms': [round((n['t0'] - entry - n['sung']) * 1000.0, 1) for n in new],
               'skipped_bright_plate': skipped_bright}
    if not new:
        summary['why'] = 'no sung words on the plates'
        return [], summary
    card = next((o for o in ovs if o.get('type') == 'music_card3d'), None)
    if card is not None:
        # fx-av 3: the intro card TYPES the sung line (14.4 chars/s, running ahead of the voice: each word completes
        # 5-110 ms before it is sung) - the lyric of the card window, not the song title
        iw = [w for w in words if float(w['t_start']) < float(card['t1']) - 0.15 and _clean(w['text'], 'lower')]
        if iw:
            line = ' '.join(_clean(w['text'], 'lower') for w in iw)
            n1 = len(_clean(iw[0]['text'], 'lower'))
            card['text'] = line
            card['cps'] = 14.4
            card['type_at'] = round(max(0.1, float(iw[0]['t_start']) - 0.06 - n1 / 14.4 - float(card['t0'])), 3)
            summary['card_line'] = line
    if w3 is not None:
        w3['words'] = new
        w3['t0'] = round(max(0.0, new[0]['t0'] - 0.3), 3)
        w3['lyric_source'] = 'lyrics.py'
        for o in ovs:                                 # the ground-plane word shows one of the sung words too
            if o.get('type') == 'ground_text':
                inside = [n for n in new if float(o['t0']) - 0.2 <= n['t0'] <= float(o['t1'])]
                if inside:
                    o['text'] = inside[0]['text']
        return [], summary
    return [dict(type='word3d', t0=round(max(0.0, new[0]['t0'] - 0.3), 3), t1=round(D, 3), words=new, style='hero_fx',
                 max_alive=3, vertical=int(plan.get('height') or 1920) > int(plan.get('width') or 1080),
                 lyric_source='lyrics.py')], summary


def attach(plan, opts=None, song=None, artist=None, words=None, preset=None, threads=2, song_audio=None,
           seed=None, offline=False):
    """Add the preset's lyric track to the plan in place. Returns a summary (also stored in
    plan['planner']['lyric_text'])."""
    opts = dict(opts or {})
    preset = preset or preset_of(plan, opts)
    if (plan.get('planner') or {}).get('lyric_text_done'):
        # the planner already placed the sung words itself (hype_i4f.py: the reference's own key-word slots)
        return plan['planner'].get('lyric_text') or {'preset': preset, 'track': 'planner'}
    rule = RULES.get(preset)
    summary = {'preset': preset}
    if not rule:
        summary.update(track='none', why='its references show no lyric text' if preset in NO_TEXT else 'unknown preset')
        plan.setdefault('planner', {})['lyric_text'] = summary
        return summary
    rng = random.Random((seed if seed is not None else (plan.get('seed') or opts.get('seed') or 0)) * 7919 + 17)
    if rule.get('prob', 1.0) < 1.0 and not opts.get('force_lyrics') and rng.random() >= rule['prob']:
        summary.update(track='none', why=f"this preset's references show lyric text in {rule['prob']:.0%} of edits")
        plan.setdefault('planner', {})['lyric_text'] = summary
        return summary
    if words is None:
        song = song or opts.get('song')
        artist = artist or opts.get('artist') or ''
        if not song:
            summary.update(track='none', why='no song name (lyrics need --song / --artist)')
            plan.setdefault('planner', {})['lyric_text'] = summary
            return summary
        st = {}
        words = sung_words(plan, song, artist, song_audio=song_audio, threads=threads, offline=offline, stats=st)
        summary['lyrics'] = st.get('lyrics')
        if st.get('xcheck') is not None:
            summary['xcheck'] = st['xcheck']
    if rule.get('replaces_slams') and words:
        # the planner's song-title slams stand in for lyrics when no word timings exist (catalogue text rule);
        # zawB / m3lv show the SUNG words instead, so the slams over the body give way to the lyric track
        drop = float(plan.get('drop') or 0.0)
        keep = [o for o in plan.get('overlays') or [] if not (o.get('type') == 'text' and o.get('anim') == 'slam'
                                                               and float(o.get('t0', 0)) >= drop - 0.05)]
        summary['removed_title_slams'] = len(plan.get('overlays') or []) - len(keep)
        plan['overlays'] = keep
    if opts.get('text_recipe'):
        plan.setdefault('planner', {})['text_recipe'] = opts['text_recipe']
    ovs, s2 = build(preset, words, plan, rng)
    summary.update(s2)
    if ovs:
        plan.setdefault('overlays', []).extend(ovs)
    if rule['mode'] == 'on_black' and summary.get('items'):
        # the words on black replace the planner's title-on-black (the scene keeps the black and its glow)
        for o in plan.get('overlays') or []:
            if o.get('type') == 'text_on_black':
                o['text'] = ''
                o['words'] = []
                o['lyric_text'] = True
    if rule.get('complete_phrase') and words:
        # owner 2026-10-18: the shown sequence must be the lyric text in order (no word twice in a row, none missing). Only the
        # verdict is kept (no words): judge_slowmo reads plan.planner.lyric_text.phrase_check
        its = [it for o in ovs or [] for it in (o.get('items') or []) if it.get('lyric', True)]
        if its:
            t_a = min(float(it.get('sung', it['t0'])) for it in its) - 0.12
            # 2026-10-19: up to the END of the last item - a tail word of the line joins the last item ('SEE YOU'), its own
            # sung time lies after that item's start (the start-only window flagged a complete phrase as incomplete)
            t_b = max(max(float(it.get('sung', it['t0'])) + 0.05, float(it.get('t1', it['t0'])) - 0.05) for it in its)
            lyr = [w['text'] for w in words if t_a <= float(w['t_start']) <= t_b]
            ok, why = phrase_check([it['text'] for it in its], lyr)
            summary['phrase_check'] = {'ok': bool(ok), 'why': why, 'shown_items': len(its), 'lyric_words': len(lyr)}
    plan.setdefault('planner', {})['lyric_text'] = summary
    return summary


# --------------------------------------------------------------------------------------------- drawing

def _smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _spread_curve(u):
    """Width of the letter spacing over the in-animation: 0.2 -> 1.1 (at 70 %) -> 1.0 (general-av: width
    0.05-0.6 -> 1.0-1.4 x, measured width_rel_min 0.20-0.22, width_rel_max 1.10-1.12)."""
    u = min(1.0, max(0.0, u))
    if u < 0.7:
        e = 1 - (1 - u / 0.7) ** 3
        return 0.2 + 0.9 * e
    return 1.1 - 0.1 * _smooth((u - 0.7) / 0.3)


class _Cache:
    pass


def _prep(ov):
    """Sprites for every item, rendered once at the item's size (letters kept for per-letter animation)."""
    c = ov._cache.get('lt')
    if c is not None:
        return c
    import text as T
    o = ov.ov
    w, h = ov.size
    c = _Cache()
    c.sprites = []
    st = o.get('style', 'lyric_hype')
    look = dict(o.get('look') or {})
    base = T.STYLES.get(st, {})
    for it in o.get('items') or []:
        box_px = float(it['box_w']) * w if 'box_w' in it else float(it.get('box', 0.08)) * h
        size = box_px / _box_ratio(st, it['text'])
        if it.get('ss') and it.get('anim_in') == 'smear_shrink':
            size *= float(it['ss'])       # remake round 8: drawn at 2x, area-downsampled after the warps (crisp edges)
        over = dict(look)
        if base.get('glow_rel') and it.get('anim_in') != 'smear_shrink':    # smear_shrink draws its own glow
            over['glow'] = [(r * size, a, col) for r, a, col in base['glow_rel']]
        if base.get('shadow_rel'):
            dx, dy, bl, a = base['shadow_rel']
            over['shadow'] = (dx * size, dy * size, bl * size, a)
        if base.get('extrude_rel'):
            n, d, col = base['extrude_rel']
            over['extrude'] = (max(2, int(n * size)), d, col)
        mw = int(0.86 * w * (float(it['ss']) if it.get('ss') and it.get('anim_in') == 'smear_shrink' else 1.0))
        sp = T.render(it['text'], st, size, max_w=mw, min_size=10, **over)
        c.sprites.append(sp)
    ov._cache['lt'] = c
    return c


def _box_ratio(style, text):
    """Text box height / font size: caps -> cap height, lower case -> ascender to descender."""
    import text as T
    case = T.STYLES.get(style, {}).get('case')
    fnt = T.STYLES.get(style, {}).get('font', 'poppins_semibold')
    caps = {'bebas': 0.70, 'anton': 0.73, 'lilita': 0.70}.get(fnt, 0.70)
    if case == 'upper' or (case is None and str(text).isupper()):
        return caps
    return 0.98 if fnt.startswith('poppins') else 0.9


def _pos(ov, it, t):
    """Item centre in canvas px; flips to the upper third when the shot's subject sits low (legibility)."""
    w, h = ov.size
    x, y = float(it.get('x', 0.5)), float(it.get('y', 0.5))
    fl = it.get('flip')
    if fl is not None:
        key = ('pos', it['t0'])
        if key not in ov._cache:
            fy = y
            try:
                import fxsets as FXS
                sub = FXS._subject(ov, float(it['t0']))
                if sub is not None and ((y > 0.5 and sub[1] > 0.56) or (y < 0.5 and sub[1] < 0.44)):
                    fy = float(fl)
            except Exception:  # noqa: BLE001
                pass
            ov._cache[key] = fy
        y = ov._cache[key]
    return x * w, y * h


def _letters(sp):
    """[(glyph BGRA, centre offset from the sprite centre in px)] for per-letter drawing."""
    out = []
    cx = sp.size[0] / 2.0
    for k, (ch, x0, x1) in enumerate(sp.letters):
        if not ch.strip():
            continue
        g, gx = sp.letter(k)
        out.append((g, gx + g.shape[1] / 2.0 - cx, k))
    return out


def _backing(ov, k, sp, img, x, y):
    """Legibility (owner rule 6, blocking): a light word over a bright background gets a soft dark backing. The
    picture under the word box is read EVERY frame (the clip moves under a held word: a white brush wipe can slide
    in behind it), and the backing strength follows it smoothly (up fast, down slowly: no flicker). Returns a
    shadow Sprite or None."""
    import cv2
    import text as T
    key = ('bk', k)
    st = ov._cache.get(key)
    if st is None:
        fill = sp.img[..., :3][sp.img[..., 3] > 200]
        text_l = float(fill.mean()) if len(fill) else 230.0
        bh = sp.size[1] - 2 * sp.pad
        sh = None
        if text_l > 150:
            a = sp.img[..., 3].astype(np.float32)
            sh = cv2.GaussianBlur(cv2.dilate(a, np.ones((3, 3), np.uint8)), (0, 0), max(2.0, 0.07 * bh))
        st = ov._cache[key] = {'sh': sh, 's': None}
    if st['sh'] is None:
        return None
    h, w = img.shape[:2]
    bw, bh = sp.size[0] - 2 * sp.pad, sp.size[1] - 2 * sp.pad
    x0, x1 = int(max(0, x - bw / 2)), int(min(w, x + bw / 2))
    y0, y1 = int(max(0, y - bh / 2)), int(min(h, y + bh / 2))
    target = 0.0
    bg = getattr(ov, '_bg_img', None)        # text_fx.py draws the word into a transparent layer: read the picture
    if bg is not None:
        img = bg
    if x1 - x0 > 4 and y1 - y0 > 4:
        # the brightest tenth under the word decides (a white streak behind two letters - 'SAY YOU' on a brush
        # wipe - kills those letters even when the box is mostly dark; on the dark part the backing is invisible)
        luma = float(np.percentile(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), 90))
        if luma > 125:
            target = float(np.clip((luma - 125) / 60.0, 0.3, 1.0))
    prev = st['s'] if st['s'] is not None else target     # the first frame drawn starts at its target
    st['s'] = min(target, prev + 0.25) if target >= prev else max(target, prev - 0.04)
    if st['s'] < 0.02:
        return None
    bgra = np.zeros_like(sp.img)
    bgra[..., 3] = np.clip(st['sh'] * st['s'] * 1.9, 0, 255).astype(np.uint8)
    return T.Sprite(bgra, sp.letters, sp.pad)


def hold_state(ov, it, t):
    """(x, y, hrot, hsc): the item's centre in canvas px with its hold motion (hold_slide / drift / bob / wobble /
    creep) at time t - shared by draw() and text_fx.py (which draws letters itself while they wiggle / scatter)."""
    w, h = ov.size
    x, y = _pos(ov, it, t)
    age = t - float(it['t0'])
    hrot, hsc = 0.0, 1.0
    if it.get('hold_slide'):
        htau = float(it.get('hold_tau', 0.3))
        dd = htau * (1 - math.exp(-max(0.0, age) / htau))
        x += float(it['hold_slide'][0]) * w * dd
        y += float(it['hold_slide'][1]) * w * dd
    if it.get('hold_drift'):
        x += float(it['hold_drift'][0]) * w * max(0.0, age)
        y += float(it['hold_drift'][1]) * w * max(0.0, age)
    if it.get('hold_bob'):
        _ph, _hz, _a = float(it.get('hold_phase', 0.0)), float(it.get('hold_hz', 3.0)), float(it['hold_bob']) * w / 1920.0
        x += _a * math.sin(2 * math.pi * _hz * age + _ph)
        y += 0.7 * _a * math.sin(2 * math.pi * _hz * 0.83 * age + 1.7 * _ph)
    if it.get('hold_wobble'):
        hrot = float(it['hold_wobble']) * math.sin(2 * math.pi * float(it.get('hold_hz', 3.0)) * age + float(it.get('hold_phase', 0.0)))
    if it.get('hold_creep'):
        hsc = 1.0 + float(it['hold_creep']) * max(0.0, age)
    return x, y, hrot, hsc


_TFX_ERR = []


def draw(ov, img, t, only=None, inner=False):
    """engine.py Overlay dispatch for type 'lyric_text'. only: draw item `only` alone; inner: called by text_fx.py to
    draw one item into its transparent premultiplied BGRA layer (no fx, no copy)."""
    import text as T
    o = ov.ov
    items = o.get('items') or []
    live = [(k, it) for k, it in enumerate(items) if float(it['t0']) <= t < float(it['t1']) and (only is None or k == only)]
    if not live:
        return img
    c = _prep(ov)
    if not inner:
        img = img.copy()
    w, h = ov.size
    if o.get('phrases'):
        return _draw_phrases(ov, img, t, c)
    fps = float(getattr(ov.eng, 'fps', 30) or 30)
    for k, it in live:
        if not inner and not _TFX_ERR:
            # 2026-09-29 text upgrade: per-style / per-item wave, wiggle, letter scatter, motion blur, glow and camera
            # ride (core/edit/text_fx.py; FX table per style, item / overlay 'fx' overrides, 'fx': false = off)
            try:
                import text_fx as TFX
                F = TFX.params(o, it)
                if F:
                    img = TFX.draw_item(ov, img, t, k, it, F)
                    continue
            except Exception as e:  # noqa: BLE001
                _TFX_ERR.append(repr(e))
                log(f'text_fx disabled after an error (plain text from here): {e!r}')
        sp = c.sprites[k]
        x, y = _pos(ov, it, t)
        age = t - float(it['t0'])
        # optional hold motion (S2rz remake: held words keep sliding, decelerating, with a small roll wobble and a
        # scale creep): hold_slide [vx, vy] canvas widths / s at first ink, hold_tau s, hold_wobble deg, hold_hz,
        # hold_creep (scale / s). Absent -> the word holds still (every other preset unchanged).
        hrot, hsc = 0.0, 1.0
        if it.get('hold_slide'):
            htau = float(it.get('hold_tau', 0.3))
            dd = htau * (1 - math.exp(-max(0.0, age) / htau))
            x += float(it['hold_slide'][0]) * w * dd
            y += float(it['hold_slide'][1]) * w * dd
        if it.get('hold_drift'):
            # optional constant drift [vx, vy] canvas widths / s for the word's whole life (never a static hold)
            x += float(it['hold_drift'][0]) * w * max(0.0, age)
            y += float(it['hold_drift'][1]) * w * max(0.0, age)
        if it.get('hold_bob'):
            # optional positional wobble: hold_bob px @1920 wide at hold_hz, its own phase (S2rz round 4 critics)
            _ph, _hz, _a = float(it.get('hold_phase', 0.0)), float(it.get('hold_hz', 3.0)), float(it['hold_bob']) * w / 1920.0
            x += _a * math.sin(2 * math.pi * _hz * age + _ph)
            y += 0.7 * _a * math.sin(2 * math.pi * _hz * 0.83 * age + 1.7 * _ph)
        if it.get('hold_wobble'):
            hrot = float(it['hold_wobble']) * math.sin(2 * math.pi * float(it.get('hold_hz', 3.0)) * age + float(it.get('hold_phase', 0.0)))
        if it.get('hold_creep'):
            hsc = 1.0 + float(it['hold_creep']) * max(0.0, age)
        din = max(1e-3, float(it.get('dur_in', 0.25)))
        u = age / din
        alpha, scale, rot, blur, bang, spread = 1.0, 1.0, 0.0, 0.0, 0.0, 1.0
        kind = it.get('anim_in', 'fade')
        # ---- out
        tout = float(it.get('t_out', it['t1']))
        if t >= tout:
            v = (t - tout) / max(1e-3, float(it['t1']) - tout)
            if it.get('anim_out') == 'cut':
                # carried out by the cut: grows and smears along with the picture, gone 1-2 frames after it
                alpha *= max(0.0, 1 - v) ** 1.5
                scale *= 1 + 0.18 * v
                blur = max(blur, 0.10 * w * v)
            elif it.get('anim_out') == 'blur_out':
                # S2rz remake round 3: every held word leaves with a 3-5 frame blur-out (grows a little, smears, fades)
                alpha *= max(0.0, 1 - v) ** 1.2
                scale *= 1 + 0.08 * v
                blur = max(blur, 0.035 * w * v)
            elif it.get('anim_out') == 'zoom_through':
                # S2rz 17.60-17.70 s: the big word zoom-blurs through the cut (scale 1 -> 1.4 over ~3 frames)
                alpha *= max(0.0, 1 - v) ** 0.8
                scale *= 1 + 0.4 * v
                blur = max(blur, 0.06 * w * v)
            else:
                alpha *= max(0.0, 1 - v)
                blur = max(blur, 0.01 * w * v)
        # optional directional blur windows (S2rz remake round 3: during a whip / zoom-through the words take the
        # footage's smear): mblur [[t0, t1, len (canvas widths), angle deg], ...], ramped in / out over 1/3 of the window
        bang = None
        for mb in it.get('mblur') or []:
            m0, m1 = float(mb[0]), float(mb[1])
            if m0 <= t < m1:
                r = min(1.0, (t - m0) / max(1e-3, (m1 - m0) / 3), (m1 - t) / max(1e-3, (m1 - m0) / 3))
                if float(mb[2]) * w * r > blur:
                    blur, bang = float(mb[2]) * w * r, float(mb[3]) if len(mb) > 3 else 0.0
        # ---- in
        if kind == 'smear_shrink':
            _draw_smear_shrink(ov, img, k, it, sp, x, y, t, age, alpha)
            continue
        if kind == 'letter_streak':
            # S2rz 17.30-17.47 s: the letters stream in one by one (1 frame stagger) from `streak_from` canvas widths to
            # the side, each over `streak_frames` frames, decelerating, with a directional blur of the step length
            n_f = float(it.get('streak_frames', 4.5))
            dx0 = float(it.get('streak_from', 0.40)) * w
            stg = float(it.get('stagger', 1.0 / 30.0))
            for g, dx, j in _letters(sp):
                la = age - j * stg
                if la < 0:
                    break
                uu = min(1.0, la / (n_f / 30.0))
                e = 1 - (1 - uu) ** 3
                off = dx0 * (1 - e)
                step = dx0 * 3 * (1 - uu) ** 2 / n_f if uu < 1 else 0.0          # px moved per frame
                T.blit(img, g, x + dx * scale * hsc + off, y, alpha=alpha * min(1.0, 0.25 + uu * 1.5), scale=scale * hsc, rot=hrot,
                       blur_len=max(blur, min(0.03 * w, step)), blur_ang=bang if bang is not None else 0.0, blur_max=401)
            continue
        if kind == 'letter_pop':
            # S2rz remake (critics round 3): each letter pops in on its own, 1 frame after the previous one, from
            # scale pop0 (1.3) to 1 over pop_frames (4) with a small drop (3 %H) and a short vertical smear; the held
            # word keeps the hold wobble / drift / bob above
            n_f = float(it.get('pop_frames', 4.0))
            s0 = float(it.get('pop0', 1.3))
            stg = float(it.get('stagger', 1.0 / 30.0))
            for g, dx, j in _letters(sp):
                la = age - j * stg
                if la < 0:
                    break
                uu = min(1.0, la / (n_f / 30.0))
                e = 1 - (1 - uu) ** 3
                sc = scale * hsc * (s0 + (1 - s0) * e)
                T.blit(img, g, x + dx * scale * hsc, y - 0.03 * h * (1 - e), alpha=alpha * min(1.0, 0.3 + uu * 1.4), scale=sc, rot=hrot,
                       blur_len=max(blur, 0.02 * h * (1 - e) if uu < 1 else 0.0), blur_ang=90.0 if bang is None and uu < 1 else (bang or 0.0),
                       blur_max=201)
            continue
        if kind == 'ring_flare':
            # S2rz 26.97-27.20 s: a glow ring flares out behind the word (radius 0.4 -> 1.2 of the word's half width
            # over ~8 frames, fading), the word itself spreads in as usual
            uu = age / (8 / 30.0)
            if uu < 1.0:
                _ring(img, x, y, (0.4 + 0.8 * uu) * 0.5 * (sp.size[0] - 2 * sp.pad) * scale, 0.9 * (1 - uu) * alpha,
                      tuple(it.get('ring_bgr') or (70, 160, 255)), max(2.0, 0.012 * h))
            kind = 'spread'
        shadow = _backing(ov, k, sp, img, x, y) if it.get('backing', True) else None
        if t >= tout and it.get('anim_out') == 'cut':
            shadow = None       # carried out by a (often white-flash) cut: a dark smear of the backing would read as a
            #                     dark word on the flash; the word dissolves into the light instead
        if kind == 'spread':
            spread = _spread_curve(u)
            alpha *= min(1.0, u / 0.35)
            s0 = float(it.get('scale0', 1.0))
            scale *= s0 + (1 - s0) * (1 - (1 - min(1.0, u)) ** 3)
            rot = float(it.get('rot1', 0.0)) + float(it.get('rot0', 0.0)) * (1 - _smooth(u)) + hrot
            scale *= hsc
            if u < 1:
                dsp = (_spread_curve(u + 1 / (fps * din)) - spread)
                blur = max(blur, abs(dsp) * sp.size[0] * 0.5)
            if shadow is not None:
                # one piece stretched with the spread (per-letter crops of a blurred shadow leave seams)
                T.blit(img, shadow.img, x, y, alpha=alpha * _smooth(u / 0.6), scale=scale, rot=rot, sx=max(0.2, spread), blur_len=blur,
                       blur_ang=-rot, blur_max=201)
            _draw_letters(img, sp, x, y, spread, alpha, scale, rot, blur, bang)
            continue
        if shadow is not None:
            T.blit(img, shadow.img, x, y, alpha=alpha * min(1.0, u / 0.35), scale=scale, blur_len=blur)
        if kind == 'type_smear':
            # letters type on one frame @30 apart, each sliding in from the left with a sideways smear trail
            # that decays over 5 frames (yUui 'EVERY': the streak runs across ~40 % of the width)
            for g, dx, j in _letters(sp):
                la = age - j * (1 / 30.0)
                if la < 0:
                    break
                u5 = max(0.0, 1 - la / (5 / 30.0))
                slide = 0.06 * w * u5 ** 1.5
                T.blit(img, g, x + dx * scale - slide * 0.5, y, alpha=alpha * min(1.0, la / (2 / 30.0) + 0.35), scale=scale,
                       blur_len=max(blur, slide if slide > 2 else 0.0), blur_ang=0.0, blur_max=401)
            continue
        if kind == 'pop3d':
            e = 1 - (1 - min(1.0, u)) ** 3
            s0 = float(it.get('scale0', 1.35))
            T.blit(img, sp.img, x, y, alpha=alpha * min(1.0, u / 0.25 + 0.2), scale=scale * (s0 + (1 - s0) * e),
                   blur_len=blur if blur >= 1.5 else (0.03 * w * (1 - e) if u < 1 else 0.0), blur_ang=90.0)
            continue
        if kind == 'write':
            # light-streak write-on: letters reveal left to right with a soft edge and a bright head
            front = min(1.0, u) * (len(sp.letters) + 1.0)
            n = len(sp.letters)
            for g, dx, j in _letters(sp):
                a = min(1.0, max(0.0, front - j))
                if a <= 0:
                    break
                T.blit(img, g, x + dx, y - 0.004 * h * (1 - a), alpha=alpha * a, blur_len=blur)
            if u < 1 and n:
                j = min(n - 1, int(front))
                hx = x - sp.size[0] / 2.0 + sp.letters[j][2] + sp.pad
                _glint(img, hx, y, 0.012 * h, 0.85 * (1 - u))
            continue
        # plain fade (and the default)
        T.blit(img, sp.img, x, y, alpha=alpha * min(1.0, u), scale=scale, blur_len=blur, **({} if bang is None else {'blur_ang': bang}))
    return img


def _draw_letters(img, sp, x, y, spread, alpha, scale, rot, blur, bang=None):
    import text as T
    if alpha <= 0.003:
        return
    if abs(spread - 1) < 0.01 and abs(rot) < 0.05:
        T.blit(img, sp.img, x, y, alpha=alpha, scale=scale, blur_len=blur, blur_ang=0.0 if bang is None else bang, blur_max=201)
        return
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    for g, dx, _ in _letters(sp):
        px = dx * spread * scale
        T.blit(img, g, x + px * c, y - px * s, alpha=alpha, scale=scale, rot=rot, blur_len=blur, blur_ang=-rot if bang is None else bang,
               blur_max=201)


def _glint(img, x, y, r, a):
    """A small soft light (the head of the light-streak write-on)."""
    if a <= 0.01:
        return
    h, w = img.shape[:2]
    R = int(max(3, r * 3))
    x0, y0, x1, y1 = int(x - R), int(y - R), int(x + R), int(y + R)
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * r * r)) * a
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    g = g[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0, None]
    roi = img[cy0:cy1, cx0:cx1, :3].astype(np.float32)
    col = np.array([214, 238, 255], np.float32)          # warm gold-white (BGR)
    img[cy0:cy1, cx0:cx1, :3] = np.clip(roi + (255 - roi) * g * (col / 255.0), 0, 255).astype(np.uint8)
    if img.shape[2] == 4:                                # text_fx layer: the light is part of the layer
        img[cy0:cy1, cx0:cx1, 3:4] = np.maximum(img[cy0:cy1, cx0:cx1, 3:4], (g * 255).astype(np.uint8))


def _ring(img, x, y, r, a, bgr, thick):
    """A soft glowing ring (screen-added) centred on (x, y) (ring_flare)."""
    import cv2
    if a <= 0.01 or r < 2:
        return
    h, w = img.shape[:2]
    R = int(r + 4 * thick)
    x0, y0, x1, y1 = max(0, int(x - R)), max(0, int(y - R)), min(w, int(x + R)), min(h, int(y + R))
    if x1 <= x0 or y1 <= y0:
        return
    lay = np.zeros((y1 - y0, x1 - x0), np.float32)
    cv2.circle(lay, (int(x - x0), int(y - y0)), int(r), 1.0, max(1, int(thick)), cv2.LINE_AA)
    lay = cv2.GaussianBlur(lay, (0, 0), max(1.0, thick)) * 1.6 + cv2.GaussianBlur(lay, (0, 0), 3 * thick) * 1.2
    lay = np.clip(lay * a, 0, 1)[..., None]
    col = np.array(bgr, np.float32)[None, None, :]
    roi = img[y0:y1, x0:x1, :3].astype(np.float32)
    img[y0:y1, x0:x1, :3] = np.clip(roi + (255 - roi) * lay * (col / 255.0), 0, 255).astype(np.uint8)
    if img.shape[2] == 4:                                # text_fx layer
        img[y0:y1, x0:x1, 3:4] = np.maximum(img[y0:y1, x0:x1, 3:4], (lay * 255).astype(np.uint8))


def ink_boxes(ov_dict, size=(480, 270), fps=30, thr=110, times=None):
    """Per item, per frame: the ink box (x0, y0, x1, y1) in canvas fractions, read from the item drawn ALONE on
    black by draw() itself (hold slide / creep / wobble, spread-in, exits all included). {k: {frame: box}}."""
    out = {}
    items = ov_dict.get('items') or []
    for k, it in enumerate(items):
        one = dict(ov_dict, items=[dict(it)], t0=float(it['t0']), t1=float(it['t1']))
        fo = _FakeOv(one, size)
        fo.eng.fps = fps
        blank = np.zeros((size[1], size[0], 3), np.uint8)
        boxes = {}
        f0, f1 = int(math.floor(float(it['t0']) * fps)), int(math.ceil(float(it['t1']) * fps))
        for f in range(f0, f1 + 1):
            t = f / fps
            if times is not None and f not in times:
                continue
            if not (float(it['t0']) <= t < float(it['t1'])):
                continue
            im = draw(fo, blank, t)
            m = im.max(axis=2) > thr
            if not m.any():
                continue
            ys, xs = np.nonzero(m)
            boxes[f] = (xs.min() / size[0], ys.min() / size[1], (xs.max() + 1) / size[0], (ys.max() + 1) / size[1])
        out[k] = boxes
    return out


def _hit(a, b, pad=0.004):
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def overlap_frames(ov_dict, size=(480, 270), fps=30):
    """[(frame, k_a, k_b)] where two items' ink boxes intersect on the same frame (every frame is tested)."""
    bx = ink_boxes(ov_dict, size, fps)
    bad = []
    ks = sorted(bx)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            for f in sorted(set(bx[a]) & set(bx[b])):
                if _hit(bx[a][f], bx[b][f]):
                    bad.append((f, a, b))
    return bad


def resolve_overlaps(ov_dict, size=(480, 270), fps=30, fade=0.1, out_anim='blur_out'):
    """S2rz remake round 3 (critic: a held word slid into the next word's place, two words read as one for
    13 frames): test the two ink boxes on EVERY frame they share (the held word's slide / creep applied, as drawn),
    and on a hit end the earlier word so its exit is complete at the later word's first ink. Words that never touch
    (the reference's stacked designs) stay together. Returns the number of items shortened; items shorter than
    0.15 s afterwards are dropped."""
    items = sorted(ov_dict.get('items') or [], key=lambda it: float(it['t0']))
    ov_dict['items'] = items
    n = 0
    for _ in range(3):
        bad = overlap_frames(ov_dict, size, fps)
        if not bad:
            break
        done = set()
        for f, a, b in bad:
            if (a, b) in done:
                continue
            done.add((a, b))
            ea, lb = (a, b) if float(items[a]['t0']) <= float(items[b]['t0']) else (b, a)
            A, B = items[ea], items[lb]
            end = float(B['t0'])
            if end <= float(A['t0']) + 0.15:
                A['t1'] = A['t0']
                continue
            A['t1'] = round(end, 3)
            A['t_out'] = round(max(float(A['t0']) + 0.05, end - fade), 3)
            A['anim_out'] = out_anim
            A['dur_out'] = fade
            n += 1
        ov_dict['items'] = items = [it for it in items if float(it['t1']) - float(it['t0']) >= 0.15]
    return n


def _draw_phrases(ov, img, t, c):
    """kpop_text_strobe: the words of a phrase on one centred line (two when it is wider than 0.4 W)."""
    import text as T
    o = ov.ov
    w, h = ov.size
    items = o.get('items') or []
    ph = [p for p in o.get('phrases') or [] if float(p['t0']) <= t < float(p['t1'])]
    if not ph:
        return img
    pid = (o.get('phrases') or []).index(ph[0])
    ks = [k for k, it in enumerate(items) if it.get('phrase') == pid]
    shown = [k for k in ks if float(items[k]['t0']) <= t]
    lay_ks = shown if ph[0].get('recentre') else ks          # phrase 1 in its final place; later ones re-centre
    if not shown:
        return img
    gap = 0.28 * float(items[ks[0]]['box']) * h / 0.98 * 1.0
    rows, cur, cw = [], [], 0.0
    for k in lay_ks:
        sw = c.sprites[k].size[0] - 2 * c.sprites[k].pad
        if cur and cw + gap + sw > 0.40 * w:
            rows.append(cur)
            cur, cw = [], 0.0
        cur.append(k)
        cw += (gap if len(cur) > 1 else 0) + sw
    if cur:
        rows.append(cur)
    lh = float(items[ks[0]]['box']) * h * 1.25
    y0 = 0.5 * h - (len(rows) - 1) * lh / 2.0
    pend = float(ph[0]['t1'])
    out_a = min(1.0, max(0.0, (pend - t) / 0.30))
    for r, row in enumerate(rows):
        widths = [c.sprites[k].size[0] - 2 * c.sprites[k].pad for k in row]
        x = 0.5 * w - (sum(widths) + gap * (len(row) - 1)) / 2.0
        for k, sw in zip(row, widths):
            it = items[k]
            sp = c.sprites[k]
            if float(it['t0']) <= t:
                age = t - float(it['t0'])
                cx = x + sw / 2.0
                blur = 0.004 * w * (1 - out_a) if pid > 0 else 0.0
                if it.get('anim_in') == 'fade':
                    T.blit(img, sp.img, cx, y0 + r * lh, alpha=out_a * min(1.0, age / max(1e-3, float(it['dur_in']))), blur_len=blur)
                else:
                    # left-to-right soft wipe across the word in dur_in, then full
                    u = min(1.0, age / max(1e-3, float(it['dur_in'])))
                    im = sp.img.copy()
                    iw = im.shape[1]
                    edge = u * (iw + 0.3 * iw) - 0.15 * iw
                    ramp = np.clip((edge - np.arange(iw, dtype=np.float32)) / max(1.0, 0.15 * iw) + 0.5, 0, 1)
                    im[..., 3] = (im[..., 3].astype(np.float32) * ramp[None, :]).astype(np.uint8)
                    T.blit(img, im, cx, y0 + r * lh, alpha=out_a, blur_len=blur)
            x += sw + gap
    return img


def _neon_layers(ov, k, sp, it=None):
    """cinematic_slowmo (_sX2) neon word: the glyph mask of the item's sprite, a violet -> magenta fill gradient and a
    two-radius violet glow (measured on the reference in landscape: fill BGR ~(150-160, 10-40, 95-120) around a white-hot
    core, glow +18..+35 luma, halo +28..+48). Cached per item."""
    import cv2
    key = ('neon', k)
    c = ov._cache.get(key)
    if c is not None:
        return c
    a = sp.img[..., 3].astype(np.float32) / 255.0
    core = np.clip(a, 0, 1)                                   # the glyph itself (_prep drew it without the style's glow)
    bh = max(4.0, sp.size[1] - 2 * sp.pad)
    pad = int(0.9 * bh)
    core = cv2.copyMakeBorder(core, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    it = it or {}
    if it.get('letter_stagger'):
        core = _stagger_letters(core, float(it['letter_stagger'][0]), float(it['letter_stagger'][1]) * bh, k)
    gs = it.get('glow_sig') or (0.10, 0.34)
    g1 = cv2.GaussianBlur(core, (0, 0), gs[0] * bh)
    g2 = cv2.GaussianBlur(core, (0, 0), gs[1] * bh)
    u = np.linspace(0.0, 1.0, core.shape[1], dtype=np.float32)[None, :, None]
    # optional per-rule colours (RGB hex tuples): grad0 -> grad1 fill, glow_rgb halo (remake round 2 of _sX2)
    rgb = lambda c, d: np.array(c[::-1] if c else d, np.float32)
    c0 = rgb(it.get('grad0'), [232, 14, 128])                 # BGR violet  (#800ee8)
    c1 = rgb(it.get('grad1'), [246, 40, 182])                 # BGR magenta (#b628f6)
    grad = c0 * (1 - u) + c1 * u
    glow_c = rgb(it.get('glow_rgb'), [210, 8, 124])
    gg = float(it.get('glow_gain', 1.0))
    if gg != 1.0:      # normalised halo: the blurred mask of thin letters peaks well under 1, lift it to the measured ring
        g1 = g1 / max(1e-3, float(g1.max()))
        g2 = g2 / max(1e-3, float(g2.max()))
    glow = np.clip(gg * (0.85 * g1 + 0.55 * g2) if gg != 1.0 else 0.85 * g1 + 0.55 * g2, 0, 1)[..., None]
    c = dict(core=core[..., None], glow=glow, grad=grad, glow_c=glow_c, pad=pad, bh=bh)
    ov._cache[key] = c
    return c


def _stagger_letters(core, deg, dy_px, k):
    """Remake round 6 (critic: _sX2's letters sit at slightly different heights / angles, ours are flat): every glyph
    (a run of inked columns) turned by +-deg and raised / lowered by +-dy_px about its own centre, rigidly - the
    letters keep their shape (no wave warp)."""
    import cv2
    col = core.max(axis=0) > 0.08
    runs, x = [], 0
    n = len(col)
    while x < n:
        if col[x]:
            x0 = x
            while x < n and col[x]:
                x += 1
            runs.append((x0, x))
        x += 1
    if len(runs) < 2:
        return core
    out = np.zeros_like(core)
    hh = core.shape[0]
    for i, (x0, x1) in enumerate(runs):
        one = np.zeros_like(core)
        one[:, x0:x1] = core[:, x0:x1]
        ys = np.where(one.max(axis=1) > 0.08)[0]
        cy = float(ys.mean()) if len(ys) else hh / 2
        M = cv2.getRotationMatrix2D(((x0 + x1) / 2.0, cy), deg * _hash1(i, 7 + k), 1.0)
        M[1, 2] += dy_px * _hash1(i, 11 + k)
        out = np.maximum(out, cv2.warpAffine(one, M, (core.shape[1], hh), flags=cv2.INTER_LINEAR,
                                             borderMode=cv2.BORDER_CONSTANT, borderValue=0))
    return out


def _hash1(i, salt=0):
    """Per-frame pseudo-random value in [-1, 1] (a sin() of the frame index alone is smooth at 25 fps)."""
    v = math.sin(i * 12.9898 + salt * 78.233) * 43758.5453
    return 2.0 * (v - math.floor(v)) - 1.0


def _draw_smear_shrink(ov, img, k, it, sp, x, y, t, age, alpha):
    """cinematic_slowmo words on black (_sX2, measured per frame in landscape): each word arrives as a horizontal
    streak 0.14-0.35 W long with a white-hot right end that collapses with tau ~45 ms (width 2-4x -> 1.0x in 3-4
    frames @25, height 1.9x -> 1x), then holds while it shrinks - slowly, then faster (LIGHT 283 -> 141 px over its
    last 0.4 s, SEE 188 -> 90, x ~0.5 at the next word) and floats up ~1 %H and back; a white glint sweeps across
    the letters once; the next word replaces it on its own frame; the last word fades in 3 frames."""
    import text as T
    L = _neon_layers(ov, k, sp, it)
    w, h = ov.size
    t_next = float(it.get('t_out', it['t1']))
    hold = max(0.1, t_next - float(it['t0']))
    p = min(1.0, max(0.0, age / hold))
    q = float(it.get('shrink', 0.40)) * min(1.0, max(0.0, (hold - 0.4) / 0.6))
    scale = (1.0 - 0.12 * p - q * p ** 4)
    y = y - 0.011 * h * math.sin(math.pi * p)
    tau = float(it.get('smear_tau', 0.045))
    e = math.exp(-max(0.0, age - float(it.get('smear_hold', 0.02))) / tau)   # at 60 fps the full streak shows 1-2 frames
    exit_u = 0.0
    if it.get('exit_smear') and it.get('anim_out') == 'cut':
        # round 10 (critic: the reference's exit is a centred, wide horizontal streak with a white core; ours streaked
        # off to the right and was short): the outgoing word stretches symmetrically about its centre over its last
        # exit_smear_s (2 frames at 25 fps) while the next word's streak arrives
        es_ = float(it.get('exit_smear_s', 0.08))
        exit_u = min(1.0, max(0.0, (age - (hold - es_)) / es_)) ** 1.5
    streak = float(it.get('smear0', 0.24)) * w * e
    # glint: a soft white band crossing the word left -> right between 0.15 s and 0.75 s after the word lands
    gw = L['core'].shape[1]
    gp = (age - float(it.get('glint_t0', 0.15))) / float(it.get('glint_dur', 0.6))
    col = L['grad']
    band = None
    if -0.2 < gp < 1.2 and it.get('glint', True):
        xs = np.arange(gw, dtype=np.float32)[None, :, None]
        gwid = float(it.get('glint_w', 0.10)) * (gw - 2 * L['pad'] if it.get('glint_w') else gw)
        band = float(it.get('glint_amt', 0.8)) * np.exp(-((xs - (L['pad'] + gp * (gw - 2 * L['pad']))) / gwid) ** 2)
        col = col * (1 - band) + (np.array(it['glint_rgb'][::-1], np.float32) if it.get('glint_rgb') else 255.0) * band
    hot = min(1.0, 0.9 * e)                                   # the white head of the streak (right end)
    if it.get('hot_tau'):
        # remake round 2: the white-hot right end outlives the streak (_sX2: still white on the last letter 3-4 frames)
        hot = min(1.0, 0.9 * math.exp(-max(0.0, age) / float(it['hot_tau'])))
    if it.get('hot_amt') is not None:
        # remake round 2 (_sX2 per frame): ONE streak frame, then the first legible frame is white-hot (the fill ~70 %
        # white, strongest on the right end, with a doubled halo) and cools over ~80 ms
        hot = float(it['hot_amt']) * math.exp(-max(0.0, age - 0.04) / float(it.get('hot_tau') or 0.08))
        hot = max(hot, float(it.get('hot_hold', 0.0)))      # _sX2: the right end stays white-hot through the hold
    if hot > 0.02:
        ux = np.linspace(0.0, 1.0, gw, dtype=np.float32)[None, :, None] ** float(it.get('hot_pow', 2.0))
        if it.get('hot_edge'):
            # remake round 5 (_sX2 per column): white on the word's own right ~30 % (smoothstep in glyph coords, the
            # pads excluded), violet-magenta to its left
            e0, e1 = it['hot_edge']
            uw = (np.arange(gw, dtype=np.float32)[None, :, None] - L['pad']) / max(1.0, gw - 2 * L['pad'])
            sm = np.clip((uw - e0) / max(1e-3, e1 - e0), 0, 1)
            ux = sm * sm * (3 - 2 * sm)
        kk = 0.8 if it.get('hot_amt') is None else 1.0
        hc = np.array(it['hot_rgb'][::-1], np.float32)[None, None, :] if it.get('hot_rgb') else 255.0
        col = col * (1 - hot * kk * ux) + hc * hot * kk * ux
    a_core = L['core']
    a_glow = L['glow'] * (1 - a_core)
    if it.get('hot_glow') and hot > 0.02:
        a_glow = np.clip(a_glow * (1 + float(it['hot_glow']) * hot), 0, 1)
    if band is not None and it.get('glint_glow'):
        a_glow = np.clip(a_glow * (1 + float(it['glint_glow']) * band), 0, 1)
    if it.get('flicker'):
        # _sX2 per frame: the neon flickers - glow area +-10 %, letter luma +-5 % frame to frame
        fr = int(round(t * 50))
        fg, fl = it['flicker']
        a_glow = np.clip(a_glow * (1.0 + fg * _hash1(fr, 4 + k)), 0, 1)
        col = np.clip(col * (1.0 + fl * _hash1(fr, 40 + k)), 0, 255)
    if it.get('streak_white'):
        # round 10 (critic: the reference's entry swipe is a centred wide smear with a WHITE core): the streak frames
        # (e = 1 on the first, ~0.3 on the second) and the exit smear (uc, below) wash the whole word toward near-white
        sw_ = max(float(e), exit_u)
        sw_ = float(it['streak_white']) * sw_
        col = col * (1 - sw_) + np.array((255, 240, 255), np.float32)[None, None, :] * sw_
    rgb = col * a_core + L['glow_c'][None, None, :] * a_glow
    a_tot = np.clip(a_core + a_glow, 0, 1)
    bgra = np.dstack([np.clip(rgb / np.maximum(a_tot, 1e-4), 0, 255), a_tot * 255.0]).astype(np.uint8)
    if it.get('wave'):
        # _sX2: the word is warped like a flag (letters ride a gentle wave, ~1 period across the word, ~8 % of the cap
        # height), phase per word
        import cv2
        amp, per = it['wave']
        hh, ww = bgra.shape[:2]
        xs = np.arange(ww, dtype=np.float32)
        ph = 1.7 * (k % 5) + 2 * math.pi * float(it.get('wave_hz', 0.0)) * max(0.0, age)
        dy = (amp * L['bh'] * np.sin(2 * math.pi * xs / max(8.0, per * (ww - 2 * L['pad'])) + ph)).astype(np.float32)
        mx = np.tile(xs[None, :], (hh, 1))
        my = np.arange(hh, dtype=np.float32)[:, None] + dy[None, :]
        bgra = cv2.remap(bgra, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if it.get('skew_deg'):
        # remake round 4: a forward slant (top leans right) of the whole word, glyphs straight
        import cv2
        hh, ww = bgra.shape[:2]
        sk = math.tan(math.radians(float(it['skew_deg'])))
        ex_ = int(math.ceil(abs(sk) * hh / 2)) + 1
        M = np.float32([[1, -sk, sk * hh / 2 + ex_], [0, 1, 0]])
        bgra = cv2.warpAffine(bgra, M, (ww + 2 * ex_, hh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=0)
    ss = float(it.get('ss') or 1.0)
    if it.get('letter_bob') or it.get('rot_wobble') or ss > 1.0:
        bgra = _neon_finish(bgra, L, it, k, t, ss, h)
    blur_y = int(0.35 * L['bh'] / ss * e) if e > 0.2 else 0
    if it.get('stretch0') is not None:
        # remake round 2 (landscape, per frame): the first frame is a BRIGHT stretched word (width 1.4-3.1x, full
        # luma), 1.2-1.5x on the next frame, 1.0x on the third; the hold shrinks the WIDTH only (height stays ~6 %H:
        # 0.109 -> 0.074 W over 0.7 s on the last word) - a squeeze, not a scale
        sq = 1.0 - float(it.get('squeeze', 0.30)) * p ** float(it.get('squeeze_pow', 1.6))
        sc = 1.0 - float(it.get('shrink_h', 0.0)) * p ** float(it.get('squeeze_pow', 1.6))          # round 2 (_sX2 per frame): the height shrinks too
        cs = float(it.get('collapse_s', 0.0))
        if hold < float(it.get('collapse_min_hold', 0.0)):
            cs = 0.0                  # _sX2: short words hold full height into the next word; long ones collapse
        if cs > 0 and it.get('anim_out') == 'cut':
            # _sX2: the outgoing word collapses in its last 2-3 frames (height 0.054 -> 0.044 -> 0.031 of H) into the
            # next word's streak
            uc = min(1.0, max(0.0, (age - (hold - cs)) / cs))
            sc *= 1.0 - 0.45 * uc * uc
        jp = float(it.get('jitter_px', 0.0))
        if jp:
            # +-1 px per-frame tracking jitter (_sX2's words never sit still: no two consecutive frames are identical)
            fr = int(round(t * 50))
            x += jp * _hash1(fr, 1) * (h / 1080.0)
            y += float(it.get('jitter_py', jp)) * _hash1(fr, 2) * (h / 1080.0)
        es = math.exp(-max(0.0, age) / float(it['stretch_tau'])) if it.get('stretch_tau') else e
        if it.get('jitter_sx'):
            # _sX2 per frame: the held word never sits still - width wobbles ~2.5 % frame to frame (dW 6.4 px @1080p)
            sq *= 1.0 + float(it['jitter_sx']) * _hash1(int(round(t * 50)), 3)
        if it.get('dim'):
            # remake round 6 (critic: _sX2's word dims ~25 % over its hold while it shrinks 79 -> 61 px)
            bgra = bgra.copy()
            bgra[..., :3] = (bgra[..., :3].astype(np.float32) * (1.0 - float(it['dim']) * p)).astype(np.uint8)
        sx_exit = 1.0 + (float(it['exit_smear']) - 1.0) * exit_u if it.get('exit_smear') else 1.0
        blur_x = float(it.get('stretch_blur', 0.3)) * streak
        if exit_u > 0:
            blur_x = max(blur_x, 0.12 * w * exit_u)
        T.blit(img, bgra, x + float(it.get('streak_x', 0.12)) * streak, y, alpha=alpha, scale=sc,
               sx=(1.0 + float(it['stretch0']) * es) * sq * sx_exit,
               blur_len=blur_x, blur_ang=0.0, blur_max=801, blur_y=blur_y // 2)
        return img
    T.blit(img, bgra, x + 0.30 * streak, y, alpha=alpha, scale=scale, sx=1.0 + 0.15 * e,
           blur_len=streak, blur_ang=0.0, blur_max=801, blur_y=blur_y)
    return img


def _neon_finish(bgra, L, it, k, t, ss, h):
    """Remake round 8 (critics: _sX2's letters bob at different heights and the word sways +-4-5 deg; ours sat on one
    baseline with no rotation and read ~2 px soft): per-letter vertical bob (letter_bob = (px @1080, Hz), rigid per
    glyph, random phase per letter), a slow whole-word rotation sway (rot_wobble = (deg, Hz), phase per word), then the
    supersampled sprite (ss) is area-downsampled with premultiplied alpha."""
    import cv2
    hh, ww = bgra.shape[:2]
    if it.get('letter_bob'):
        amp, hz = it['letter_bob']
        amp = float(amp) * ss * h / 1080.0
        core = L['core'][..., 0]
        col = core.max(axis=0) > 0.08
        runs, x, n = [], 0, len(col)
        while x < n:
            if col[x]:
                x0 = x
                while x < n and col[x]:
                    x += 1
                runs.append((x0, x))
            x += 1
        if len(runs) >= 2:
            off = (ww - core.shape[1]) / 2.0            # the skew canvas widens the sprite symmetrically
            dy = np.zeros(ww, np.float32)
            mids = [0.0] + [off + (runs[i][1] + runs[i + 1][0]) / 2.0 for i in range(len(runs) - 1)] + [float(ww)]
            for i in range(len(runs)):
                ph = math.pi * (1.0 + _hash1(i, 23 + k))
                v = amp * math.sin(2 * math.pi * float(hz) * t + ph)
                dy[int(max(0, mids[i])):int(min(ww, mids[i + 1]))] = v
            mx = np.tile(np.arange(ww, dtype=np.float32)[None, :], (hh, 1))
            my = np.arange(hh, dtype=np.float32)[:, None] - dy[None, :]
            bgra = cv2.remap(bgra, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if it.get('rot_wobble'):
        deg, hz = it['rot_wobble']
        ang = float(deg) * math.sin(2 * math.pi * float(hz) * t + math.pi * (1.0 + _hash1(k, 31)))
        if abs(ang) > 0.05:
            import text as T
            bgra = T.transform(bgra, 1.0, ang)
    if ss > 1.0:
        hh, ww = bgra.shape[:2]
        a = bgra[..., 3:4].astype(np.float32) / 255.0
        pm = np.dstack([bgra[..., :3].astype(np.float32) * a, a * 255.0])
        nw, nh = max(2, int(round(ww / ss))), max(2, int(round(hh / ss)))
        pm = cv2.resize(pm, (nw, nh), interpolation=cv2.INTER_AREA)
        a2 = np.clip(pm[..., 3:4] / 255.0, 0, 1)
        rgb = np.clip(pm[..., :3] / np.maximum(a2, 1e-4), 0, 255)
        bgra = np.dstack([rgb, a2 * 255.0]).astype(np.uint8)
    return bgra


HANDLERS = {'lyric_text': draw}


# --------------------------------------------------------------------------------------------- measuring

def measure(video, notext, plan, start=0.0, report=None, thr=18):
    """
    First ink and geometry of every lyric item, read from the RENDER (not from the plan): |with - without|
    text difference per frame -> a text mask; an item's first ink is the first frame near its planned time
    where new ink appears; its geometry is the mask box on the settled frame. `start` = plan time of the
    videos' first frame (engine --start).
    """
    import cv2
    pl = json.load(open(plan, encoding='utf-8')) if isinstance(plan, str) else plan
    ca, cb = cv2.VideoCapture(video), cv2.VideoCapture(notext)
    fps = ca.get(cv2.CAP_PROP_FPS) or 60.0
    masks, frames_t = [], []
    i = 0
    while True:
        ok1, fa = ca.read()
        ok2, fb = cb.read()
        if not (ok1 and ok2):
            break
        d = cv2.absdiff(fa, fb).max(axis=2)
        m = (d > thr).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        masks.append(np.packbits(m, axis=None))
        shp = fa.shape
        frames_t.append(start + i / fps)
        i += 1
    H, W = shp[:2] if i else (1920, 1080)
    shape = (H, W)

    def M(j):
        return np.unpackbits(masks[j])[:H * W].reshape(shape)

    items = []
    for o in pl.get('overlays') or []:
        if o.get('type') == 'lyric_text':
            items += [dict(it, _kind='lyric_text') for it in o.get('items') or []]
        elif o.get('type') == 'word3d' and o.get('lyric_source'):
            ent = float(o.get('entry_f', 4)) / 30.0
            items += [dict(text=wd['text'], t0=float(wd['t0']) - ent, sung=float(wd.get('sung', wd['t0'] - ent)),
                           _kind='word3d') for wd in o.get('words') or []]
    out = []
    area = [int(np.unpackbits(m)[:H * W].sum()) for m in masks]
    for it in items:
        tp = float(it['t0'])
        j0 = int(round((tp - 0.25 - start) * fps))
        j1 = int(round((tp + 0.25 - start) * fps))
        if j0 < 1 or j1 >= len(masks):
            continue
        first = None
        for j in range(j0, j1 + 1):
            prev = cv2.dilate(M(j - 1), np.ones((9, 9), np.uint8))
            new = int(((M(j) > 0) & (prev == 0)).sum())
            if new > 0.00015 * H * W:
                # the nearest new-ink frame to the planned time
                if first is None or abs(j - (tp - start) * fps) < abs(first - (tp - start) * fps):
                    first = j
                if j >= (tp - start) * fps:
                    break
        rec = dict(text=it['text'], sung=it.get('sung'), planned=round(tp, 4))
        if first is not None:
            ft = frames_t[first]
            rec['first_ink'] = round(ft, 4)
            if it.get('sung') is not None:
                rec['ink_minus_sung_ms'] = round((ft - float(it['sung'])) * 1000.0, 1)
        if it['_kind'] == 'lyric_text':
            js = int(round((tp + float(it.get('dur_in', 0.25)) + 0.08 - start) * fps))
            je = int(round((float(it.get('t_out', tp + 0.5)) - 0.03 - start) * fps))
            js = min(max(js, 0), len(masks) - 1)
            if je > js:
                js = min(js, je)
            m = M(js)
            ys, xs = np.nonzero(m)
            if len(ys) > 50:
                y0, y1 = np.percentile(ys, [1, 99])
                x0, x1 = np.percentile(xs, [1, 99])
                rec['box_h_pct_H'] = round(100.0 * (y1 - y0) / H, 2)
                rec['box_h_pct_W'] = round(100.0 * (y1 - y0) / W, 2)
                rec['width_pct_W'] = round(100.0 * (x1 - x0) / W, 2)
                rec['centre'] = [round(float((x0 + x1) / 2 / W), 3), round(float((y0 + y1) / 2 / H), 3)]
                # in-animation width curve (the spread) from first ink to settled
                wc = []
                for j in range(first or js, js + 1):
                    xs2 = np.nonzero(M(j).any(axis=0))[0]
                    wc.append(float(xs2[-1] - xs2[0]) if len(xs2) > 3 else 0.0)
                if wc and max(wc) > 0:
                    rec['width_curve_rel'] = [round(v / max(1.0, wc[-1]), 2) for v in wc]
        out.append(rec)
    offs = [r['ink_minus_sung_ms'] for r in out if 'ink_minus_sung_ms' in r]
    summ = dict(video=video, items=len(out), n_timed=len(offs))
    if offs:
        a = np.abs(np.array(offs))
        summ.update(median_abs_ms=round(float(np.median(a)), 1), p90_abs_ms=round(float(np.percentile(a, 90)), 1),
                    median_ms=round(float(np.median(offs)), 1))
    hs = [r['box_h_pct_H'] for r in out if 'box_h_pct_H' in r]
    if hs:
        summ['box_h_pct_H_median'] = round(float(np.median(hs)), 2)
        summ['box_h_pct_W_median'] = round(float(np.median([r['box_h_pct_W'] for r in out if 'box_h_pct_W' in r])), 2)
        summ['centre_median'] = [round(float(np.median([r['centre'][0] for r in out if 'centre' in r])), 3),
                                 round(float(np.median([r['centre'][1] for r in out if 'centre' in r])), 3)]
    res = dict(summary=summ, items=out, text_area_px=area)
    if report:
        with open(report, 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=1)
    return res


# --------------------------------------------------------------------------------------------- self test

def _fake_plan(preset, D=12.0, bpm=100.0):
    P = 60.0 / bpm
    beats = list(np.round(np.arange(0.3, D, P), 4))
    shots = []
    t = 0.0
    k = 0
    while t < D - 0.1:
        t1 = min(D, t + 2 * P)
        shots.append(dict(t0=round(t, 4), t1=round(t1, 4), transition_in='none' if k == 0 else 'blur_bridge'))
        t = t1
        k += 1
    ovs = []
    if preset in ('cinematic_slowmo', 'kpop_text_strobe'):
        ovs.append(dict(type='text_on_black', t0=0.0, t1=4.0, text='title', style='neon'))
    if preset == 'fx_typography':
        ovs.append(dict(type='word3d', t0=0.0, t1=D, words=[dict(text='x', t0=5.0)], style='hero_fx'))
        ovs.append(dict(type='white_set', t0=8.0, t1=10.0))
    return dict(duration=D, beat_period=P, beats=beats, downbeats=beats[::4], drop=4.0, width=1080, height=1920,
                shots=shots, overlays=ovs, seed=3, planner=dict(preset=preset), music_start=0.0)


def _fake_words(D=12.0):
    txt = "i know you want me every night isn't that sweet i guess so say you can't sleep baby i know that's that me espresso".split()
    out, t = [], 0.6
    for i, w in enumerate(txt):
        out.append(dict(text=w, t_start=round(t, 3), t_end=round(t + 0.25, 3), line_id=i // 6, confidence=0.7, lyric=True))
        t += 0.31 if i % 3 else 0.52
        if t > D - 0.5:
            break
    return out


class _FakeEng:
    fps = 60

    def __init__(self, plan):
        self.plan = plan
        self.shots = []


class _FakeOv:
    def __init__(self, ov, size=(1080, 1920)):
        self.ov, self.size, self._cache = ov, size, {}
        self.eng = _FakeEng({})
        self.t0, self.t1 = float(ov['t0']), float(ov['t1'])


def selftest():
    ok = True
    msgs = []
    words = _fake_words()
    for pid in list(RULES) + NO_TEXT[:3]:
        plan = _fake_plan(pid)
        s = attach(plan, words=[dict(w) for w in words], preset=pid, opts={'force_lyrics': True})
        if pid in NO_TEXT:
            if s.get('track') != 'none':
                ok = False
                msgs.append(f'{pid}: expected no text')
            continue
        if not s.get('items'):
            ok = False
            msgs.append(f'{pid}: no items ({s})')
            continue
        offs = s.get('first_ink_minus_sung_ms') or []
        lag = RULES[pid]['lag'] * 1000
        if RULES[pid].get('quantise'):
            bad = [x for x in offs if abs(x) > 0.25 * plan['beat_period'] * 1000 + 1]
        else:
            bad = [x for x in offs if abs(x - lag) > 1.0]
        if bad:
            ok = False
            msgs.append(f'{pid}: first ink off the rule {bad[:4]}')
        if pid == 'fx_typography':
            w3 = next(o for o in plan['overlays'] if o['type'] == 'word3d')
            if any(8.0 - 0.35 <= float(w['t0']) - 4 / 30.0 < 10.15 for w in w3['words']):
                ok = False
                msgs.append('fx: a word over the white set')
            continue
        ov = next(o for o in plan['overlays'] if o['type'] == 'lyric_text')
        # no item may straddle a cut (it exits WITH the cut) and none enters in the 0.28 s before one
        cuts = [s['t0'] for s in plan['shots'][1:]]
        for it in ov['items']:
            inside = [c for c in cuts if it['t0'] + 1e-3 < c < it['t_out'] - 1e-3]
            if RULES[pid]['where'] == 'body' and inside:
                ok = False
                msgs.append(f"{pid}: '{it['text']}' straddles a cut at {inside[0]}")
        # draw every frame of the track on a grey canvas: text must appear at t0 and be gone at t1
        fo = _FakeOv(ov)
        img = np.full((1920, 1080, 3), 90, np.uint8)
        it = ov['items'][0]
        a = draw(fo, img, float(it['t0']) + 0.5 / 60)
        b = draw(fo, img, float(it['t0']) + float(it.get('dur_in', 0.25)) + 0.1)
        if not (np.abs(a.astype(int) - img).sum() > 0 and np.abs(b.astype(int) - img).max() > 60):
            ok = False
            msgs.append(f'{pid}: nothing drawn for the first item')
        z = draw(fo, img, float(ov['items'][-1]['t1']) + 0.01)
        if np.abs(z.astype(int) - img).max() > 0:
            ok = False
            msgs.append(f'{pid}: text left after t1')
        msgs.append(f"{pid}: {s['items']} items {s.get('words', [])[:6]}")
    # remake round 3: two held S2rz words, the first sliding right into the second one's place (the remake's 30.40 /
    # 31.40 s pair): every frame's ink boxes must be disjoint after resolve_overlaps, and new entries / exits draw
    ov2 = dict(type='lyric_text', preset='blur_flow', style='s2rz_word', t0=0.0, t1=3.0, items=[
        dict(text='ABCD', t0=0.40, t_out=1.73, t1=1.83, x=0.40, y=0.30, box=0.12, anim_in='spread', dur_in=0.3,
             anim_out='fade', dur_out=0.1, scale0=1.25, hold_slide=[0.29, 0.0], hold_tau=0.35, hold_wobble=0.5, hold_creep=0.02),
        dict(text='EFGHI', t0=1.40, t_out=1.73, t1=1.83, x=0.77, y=0.30, box=0.12, anim_in='letter_streak', dur_in=0.2,
             anim_out='blur_out', dur_out=0.1),
        dict(text='JK', t0=2.0, t_out=2.6, t1=2.7, x=0.5, y=0.7, box=0.2, anim_in='ring_flare', dur_in=0.3,
             anim_out='zoom_through', dur_out=0.1, mblur=[[2.3, 2.5, 0.05, 0.0]])])
    if not overlap_frames(ov2):
        ok = False
        msgs.append('overlap test: the unresolved pair should touch')
    resolve_overlaps(ov2)
    left = overlap_frames(ov2)
    if left:
        ok = False
        msgs.append(f'overlap test: {len(left)} frames still overlap after resolve_overlaps')
    fo2 = _FakeOv(ov2, (480, 270))
    blank = np.zeros((270, 480, 3), np.uint8)
    for tt in (1.45, 2.05, 2.4, 2.65):
        if draw(fo2, blank, tt).max() < 60:
            ok = False
            msgs.append(f'overlap test: nothing drawn at {tt}')
    msgs.append(f'overlap test: resolved, {len(ov2["items"])} items')
    return ok, msgs


# --------------------------------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description='lyric text per preset (core/edit/lyric_text.py)')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--attach', action='store_true')
    ap.add_argument('--measure', action='store_true')
    ap.add_argument('--plan')
    ap.add_argument('--out')
    ap.add_argument('--song')
    ap.add_argument('--artist', default='')
    ap.add_argument('--preset')
    ap.add_argument('--song-audio')
    ap.add_argument('--threads', type=int, default=2)
    ap.add_argument('--force', action='store_true', help='on for presets whose track is probabilistic (blur_flow)')
    ap.add_argument('--video')
    ap.add_argument('--notext')
    ap.add_argument('--start', type=float, default=0.0)
    ap.add_argument('--report')
    a = ap.parse_args()
    if a.selftest:
        ok, msgs = selftest()
        for m in msgs:
            log(m)
        print(json.dumps({'ok': ok}))
        sys.exit(0 if ok else 1)
    if a.attach:
        plan = json.load(open(a.plan, encoding='utf-8'))
        s = attach(plan, {'force_lyrics': a.force}, song=a.song, artist=a.artist, preset=a.preset, threads=a.threads,
                   song_audio=a.song_audio)
        with open(a.out or a.plan, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=1)
        print(json.dumps(s, ensure_ascii=False))
        return
    if a.measure:
        r = measure(a.video, a.notext, a.plan, a.start, a.report)
        print(json.dumps(r['summary']))
        return
    ap.print_help()


if __name__ == '__main__':
    main()
