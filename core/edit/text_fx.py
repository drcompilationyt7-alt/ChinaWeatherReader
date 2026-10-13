#!/usr/bin/env python3
"""
Text animation layer (2026-09-29 upgrade, owner: "the text ... is not wavy enough"; write-up refs/text-animation.md).

Where our words used to land and stop dead, real editors' After Effects projects (refs/projects-study.md T9 / T4 /
T10) keep every word alive: a per-letter animator with a Wiggly Selector, a whole-layer wiggle(), a Warp TWIST whose
bend swings -8 / +12 / -9 / +16 / -4 on eased keys 6-15 frames apart, letters that scatter in / out, motion blur on the
text, bevel + glow + a hard drop shadow, and words parented to the camera. This module adds those as PER-STYLE /
PER-ITEM parameters on top of the existing lyric_text.py animations (entries / exits keep working as before; every
parameter defaults off, FX below turns them on per style, an item's own 'fx' dict or the overlay's 'fx' dict
overrides, 'fx': false switches the layer off).

How it draws: the item is rendered (by lyric_text.draw itself, or letter by letter here while letters wiggle /
scatter) into a transparent premultiplied BGRA layer, the layer is warped as a whole with ONE cv2.remap (rigid move:
wiggle / tilt / zoom / camera ride; non-rigid: twist, arc bend, perspective keystone), motion-blurred by averaging
sub-frame renders when it moves fast, lit (bevel alpha, hard drop shadow, tight bloom, very wide soft glow) and
composited onto the picture.

Parameters (px values are px at a 1080-px SHORT side and scale with the canvas; 'hz' = cycles per second; keyed
motion = AE-style keys two per cycle with easy ease (in-out power 1.75) between them, seeded per word):
  wig        [px, hz, y_ratio]          whole-word positional wiggle (smooth keyed random walk)
  tilt       [deg, hz]                  rotation sway (alternating-sign keys: a sway, not a jitter)
  twist      [deg, hz]                  Warp TWIST: the word's middle turns by up to deg, its ends stay (an S-wave
                                        through the word); capped so no letter moves more than 0.45 cap height
  bend       [frac, hz]                 Warp ARC: the baseline bows by frac x cap height (alternating)
  persp      [frac, hz]                 perspective keystone: one end taller by frac (a 3D swing of the word)
  zin        [[s0, ...], frames, pow]   scale from s0 (one picked per word) to 1 over frames @30 (ease-out pow)
  zrate      [per_s, flip]              continuing zoom (scale x e^(rate t)); flip: true = random sign per word,
                                        'follow' = the zin direction (a shrink-in word keeps shrinking)
  drift      [W_frac_per_s, tau_s, ang] own slide decelerating with tau; ang = max deg off horizontal
  lw         [frac, deg, hz, corr, delay_s]  per-letter wiggle (Wiggly Selector: amount x cap height and deg,
                                        correlation 0..1 between letters, per-letter delay ramp)
  scatter_in [dx_W, dy_H, frames, pow, stagger_f, rot_deg]  letters start scattered by up to (dx W, dy H) and
                                        converge over frames @30 (ease-out pow), stagger frames per letter
  scatter_out [dx_W, dy_H, frames, pow] letters leave scattering to (dx W, dy H) (ease-in pow) with a fade
  scatter_on / scatter_out_on           the item anim_in / anim_out kinds these replace (default: all / blur_out)
  mblur      [shutter, nmax, step_px]   sample-accumulated motion blur (shutter fraction of a frame)
  bevel      [px, strength]             Bevel Alpha: lit top-left edge, shaded bottom-right
  shadow     [dx_px, dy_px, alpha]      hard drop shadow (0 blur)
  bloom      [sigma_capfrac, gain]      tight bloom (screen)
  glow       [sigma_W, gain, aspect, tint_bgr|None]  very wide soft glow (Deep Glow r 500-2000), x-stretched
  ride       [pos, scale, rot]          fraction of the picture's own frame-to-frame camera move the word follows
  keep_in    frac                       soft clamp of the moving word inside [frac, 1 - frac] of the frame
  seed       int
"""
import math

import numpy as np

try:
    import cv2
except Exception:  # noqa: BLE001
    cv2 = None

# ---------------------------------------------------------------------------------------------- per-style defaults
# Tuned against each style's reference (refs/text-animation.md has the per-reference measurements):
#   s2rz_word / lyric_pastel (blur_flow, S2rz): words enter at 0.55-2x scale and zoom 8-15 %/frame toward 1 then keep
#       breathing, drift 5-25 px/frame @1920, roll +-2-7 deg, a ~10 deg perspective swing, letters converge from a
#       one-letter-width scatter with motion echoes over 6-7 frames; the camera carries them. Technique from Love Again
#       (the S2rz character's editor): twist +-10-16 at ~2 Hz, wiggle 0.6-2 Hz, bevel, Deep Glow, RSMB.
#   lyric_neon (cinematic_slowmo, _sX2 intro words on black): the whole word sways its tilt +-5-7 deg at ~0.8 Hz with
#       +-9 px vertical sway and a +-5 deg top / bottom perspective; straight glyphs (no warp: the critics rejected an
#       arched WordArt baseline in round 4).
#   lyric_hype_wide / _ink / lyric_hype (hype_typography, i4f): words slide 20-40 px/frame @1920, turn 2-6 deg, land
#       from 1.2x in 2 frames, sit in perspective (the right end taller), letters type on with small offsets.
#   lyric_thin / lyric_hook3d (char_quick_velocity, yUui): the word rides the camera ~1:1 (text 44-58 px/frame vs the
#       picture's 50-57) and turns +-3-6 deg with it.
#   lyric_soft (char_soft_romance, rfsj): a gentle float only.
#   lyric_round (kpop_text_strobe, pB7P): measured STATIC (0.1 px/frame, tilt drift < 1 deg): no fx (type-specific).
FX = {
    's2rz_word': dict(
        wig=[14.0, 1.2, 0.7], tilt=[6.0, 1.1], twist=[9.0, 2.0], persp=[0.10, 0.9],
        zin=[[1.9, 0.55, 1.6, 0.7], 7, 2.5], zrate=[0.9, 'follow'], drift=[0.22, 0.40, 35.0],
        lw=[0.045, 3.0, 2.0, 0.5, 0.03],
        scatter_in=[0.045, 0.07, 7, 3.0, 1.0, 14.0], scatter_on=['spread', 'letter_pop', 'fade'],
        scatter_out=[0.10, 0.22, 5, 2.5], scatter_out_on=['blur_out'],
        mblur=[0.5, 6, 5.0], bevel=[3.0, 0.45], bloom=[0.10, 0.30], glow=[0.045, 0.16, 1.5, (255, 175, 110)],
        ride=[1.0, 0.6, 0.6], keep_in=0.03),
    'lyric_pastel': dict(
        wig=[14.0, 1.2, 0.7], tilt=[4.0, 1.1], twist=[9.0, 2.0], persp=[0.08, 0.9], zin=[[1.6, 0.7], 6, 3.0],
        zrate=[0.18, True], lw=[0.04, 3.0, 2.0, 0.5, 0.03],
        scatter_in=[0.04, 0.06, 7, 3.0, 1.0, 12.0], scatter_on=['spread', 'fade'],
        mblur=[0.5, 5, 5.0], bevel=[3.0, 0.4], glow=[0.04, 0.12, 1.5, (255, 190, 200)], ride=[1.0, 0.6, 0.6],
        keep_in=0.03),
    'lyric_neon': dict(
        wig=[5.0, 0.9, 2.2], tilt=[6.8, 0.85], persp=[0.10, 0.7], mblur=[0.5, 3, 5.0],
        bloom=[0.10, 0.20], glow=[0.03, 0.12, 1.3, (230, 70, 255)], keep_in=0.05),
    'lyric_hype_wide': dict(
        drift=[0.36, 0.38, 25.0], tilt=[3.5, 0.9], twist=[6.0, 1.6], persp=[0.14, 0.8], zin=[[1.2], 3, 3.0],
        lw=[0.05, 2.5, 2.0, 0.5, 0.035], scatter_in=[0.012, 0.03, 5, 3.0, 1.5, 6.0], scatter_on=['spread', 'fade'],
        mblur=[0.5, 5, 5.0], bevel=[2.0, 0.3], ride=[0.6, 0.4, 0.4], keep_in=0.03),
    'lyric_hype_wide_ink': dict(
        drift=[0.36, 0.38, 25.0], tilt=[3.5, 0.9], twist=[6.0, 1.6], persp=[0.14, 0.8], zin=[[1.2], 3, 3.0],
        lw=[0.05, 2.5, 2.0, 0.5, 0.035], scatter_in=[0.012, 0.03, 5, 3.0, 1.5, 6.0], scatter_on=['spread', 'fade'],
        mblur=[0.5, 5, 5.0], ride=[0.6, 0.4, 0.4], keep_in=0.03),
    'lyric_hype': dict(
        drift=[0.32, 0.40, 25.0], tilt=[3.5, 0.9], twist=[6.0, 1.6], persp=[0.12, 0.8], zin=[[1.2], 3, 3.0],
        lw=[0.05, 2.5, 2.0, 0.5, 0.035], mblur=[0.5, 5, 5.0], bevel=[2.0, 0.3], ride=[0.6, 0.4, 0.4], keep_in=0.03),
    'lyric_hype_ink': dict(
        drift=[0.32, 0.40, 25.0], tilt=[3.5, 0.9], twist=[6.0, 1.6], persp=[0.12, 0.8], zin=[[1.2], 3, 3.0],
        lw=[0.05, 2.5, 2.0, 0.5, 0.035], mblur=[0.5, 5, 5.0], ride=[0.6, 0.4, 0.4], keep_in=0.03),
    'lyric_thin': dict(
        wig=[10.0, 1.0, 0.7], tilt=[4.5, 0.8], twist=[5.0, 1.5], persp=[0.07, 0.7], lw=[0.035, 2.0, 1.8, 0.6, 0.03],
        mblur=[0.5, 5, 5.0], ride=[1.0, 0.8, 0.8], keep_in=0.03),
    'lyric_hook3d': dict(
        wig=[10.0, 1.0, 0.7], tilt=[4.5, 0.8], persp=[0.09, 0.7], mblur=[0.5, 4, 5.0], ride=[1.0, 0.8, 0.8], keep_in=0.03),
    'lyric_soft': dict(wig=[7.0, 0.6, 0.8], tilt=[3.0, 0.5], persp=[0.05, 0.5], ride=[0.8, 0.5, 0.5], keep_in=0.04),
}


# ---------------------------------------------------------------------------------------------- the style library
# Owner 2026-09-29: "there are lots of fonts and text animations, look at the project files, we should have one for
# each". Every distinct text treatment of the four decoded AE projects (refs/projects-study.md) and of every reference
# edit's text (refs/*-av.md, remake logs) as a named RECIPE = a text.STYLES look (free font) + its animation (lyric_text
# anim_in / anim_out + the fx parameters above) + where it sits. Each recipe belongs to ONE edit type (presets are
# type-specific); lyric_text.RULES[preset]['recipes'] weights which ones a planner picks per edit (variety), the first
# being the type's reference look. A recipe's fx replaces the style's FX defaults key by key (None switches one off).
# Contact sheets / previews: review-shorts/edits/effects-av/text/styles/ (text_fx.py --catalogue).
RECIPES = {
    # ---- blur_flow (S2rz measured; Love Again = the S2rz character's edit, same type)
    's2rz_zoomdrift': dict(
        types=['blur_flow'], style='s2rz_word', source='S2rz 17.4-35.5 s (measured: refs/text-animation.md)',
        what='rounded caps, lilac core + blue halo; letters converge from a one-letter scatter with motion echoes, the '
             'word keeps zooming the way it came in (0.55-1.9x), drifts 5-25 px/frame, rolls +-4 deg, twists, rides '
             'the camera; blur-out / letter scatter-out',
        anim_in=None, anim_out=None, fx={}),
    'la_wide_scatter': dict(
        types=['blur_flow'], style='wide_heavy', source='Love Again (AE project): Akira Expanded words',
        what='wide heavy caps (Unbounded 800 for Akira Expanded), per-letter Wiggly-selector scatter-in from (10-18 %W, '
             '27-48 %H) over 16-21 f, layer wiggle 60 px, Warp twist +-12 on eased keys, bevel alpha, Deep Glow, 22 px '
             'hard drop shadow, RSMB; letters scatter out (22 %W, 72-98 %H) in 9-11 f',
        anim_in='fade', dur_in=0.6, anim_out='blur_out', dur_out=0.34, box=(0.10, 0.12),
        fx=dict(wig=[60.0, 1.2, 0.6], tilt=[3.0, 1.0], twist=[12.0, 2.0], persp=None, zin=None, zrate=None, drift=None,
                lw=[0.05, 3.0, 2.0, 0.5, 0.04], scatter_in=[0.13, 0.35, 18, 3.0, 0.0, 25.0],
                scatter_on=['fade', 'spread', 'letter_pop', 'letter_streak'],
                scatter_out=[0.22, 0.85, 10, 2.5], scatter_out_on=['blur_out', 'fade', 'cut'],
                mblur=[0.5, 5, 5.0], bevel=[4.0, 0.5], shadow=[22.0, 22.0, 0.55], bloom=[0.08, 0.25],
                glow=[0.07, 0.18, 1.5, None], ride=[1.0, 0.6, 0.6], keep_in=0.04)),
    'la_script_scatter': dict(
        types=['blur_flow'], style='script_brush', source='Love Again (AE project): ChristmasMerryland words',
        what='brush script (Kaushan Script for ChristmasMerryland), the same wiggly per-letter animator, softer twist, '
             'glow and shadow',
        anim_in='fade', dur_in=0.6, anim_out='blur_out', dur_out=0.34, box=(0.09, 0.11),
        fx=dict(wig=[45.0, 1.0, 0.6], tilt=[3.0, 0.9], twist=[8.0, 1.8], persp=None, zin=None, zrate=None, drift=None,
                lw=[0.05, 4.0, 2.0, 0.5, 0.04], scatter_in=[0.10, 0.30, 17, 3.0, 0.0, 30.0],
                scatter_on=['fade', 'spread', 'letter_pop', 'letter_streak'],
                scatter_out=[0.20, 0.80, 10, 2.5], scatter_out_on=['blur_out', 'fade', 'cut'],
                mblur=[0.5, 5, 5.0], bevel=[2.0, 0.35], shadow=[14.0, 14.0, 0.5], bloom=[0.10, 0.25],
                glow=[0.06, 0.16, 1.5, None], ride=[1.0, 0.6, 0.6], keep_in=0.04)),
    'la_word_camera': dict(
        types=['blur_flow'], style='wide_heavy', source='Love Again (AE project, Pre-comp 1): word-by-word 3D camera',
        what='each word fades in over 4 f while the camera flies onto it over 12-26 f (ease-out p3.25), some words '
             'rolled 90 / 180 deg on the way, z push; the camera moves on for the next word',
        anim_in='fade', dur_in=0.133, anim_out='fade', dur_out=0.2, box=(0.09, 0.12),
        fx=dict(wig=[20.0, 0.8, 0.6], tilt=None, twist=None, persp=[0.06, 0.6], zin=None, zrate=None, drift=None,
                lw=None, scatter_in=None, scatter_out=None,
                fly_in=[0.35, 0.10, [0.0, 0.0, 90.0, 180.0], 18, 3.25, 1.6], fly_out=[0.35, 0.05, 8, 4.0, 0.7],
                mblur=[0.5, 12, 4.0], bevel=[3.0, 0.4], glow=[0.05, 0.14, 1.5, None], ride=[0.6, 0.4, 0.4],
                keep_in=0.04)),
    # ---- cinematic_slowmo
    'sx2_neon_sway': dict(
        types=['cinematic_slowmo'], style='lyric_neon', source='_sX2 intro words on black (measured)',
        what='violet-magenta neon caps streak in, the whole word sways its tilt +-5-7 deg at ~0.8 Hz with +-9 px '
             'vertical sway and a +-5 deg perspective; straight glyphs',
        anim_in=None, anim_out=None, fx={}),
    # ---- hype_typography
    'i4f_persp_type': dict(
        types=['hype_typography'], style='lyric_hype_wide', source='i4f (measured)',
        what='thin rounded wide caps, letters type on with small offsets, the word slides 20-40 px/frame, turns 2-6 '
             'deg, lands from 1.2x in 2-3 f, sits in perspective (one end taller) with a light twist',
        anim_in=None, anim_out=None, fx={}),
    'zawb_halo_spread': dict(
        types=['hype_typography'], style='lyric_hype', source='zawB / m3lv (general-av 3.2)',
        what='tall condensed cream caps with the strongest halo, letter spread-in 200-300 ms, held tilted 3-9 deg, '
             'drifting and swaying; carried out by the cut',
        anim_in='spread', anim_out=None, fx={}),
    'npsom_page_flip': dict(
        types=['hype_typography'], style='lyric_hype', source='New Person, Same Old Mistakes: book.aep page turn',
        what='the word turns in like a book page (edge-on -> flat about its vertical axis over 8 f @24, the near edge '
             'taller), then a small sway; hard shadow',
        anim_in='fade', dur_in=0.08, anim_out=None, fx=dict(flip_in=[10, 2.0], scatter_in=None, lw=None, zin=None,
                                                          shadow=[8.0, 8.0, 0.45], twist=[4.0, 1.2])),
    # ---- char_quick_velocity
    'yuui_ride_smear': dict(
        types=['char_quick_velocity'], style='lyric_thin', source='yUui / TG / KSjp (measured: rides the camera 1:1)',
        what='thin warm caps typed on with a sideways smear, riding the camera 1:1, turning +-3-6 deg with it',
        anim_in=None, anim_out=None, fx={}),
    'yh_hook3d_pop': dict(
        types=['char_quick_velocity'], style='lyric_hook3d', source='YH hook words',
        what='chunky cyan 3D hook words popping in from 1.35x, riding the camera',
        anim_in='pop3d', anim_out=None, fx={}),
    'beach_title3d': dict(
        types=['char_quick_velocity'], style='beach_3d', source='The Beach (AE project): Element 3D title + transforms',
        what='wide extruded heavy caps with a lit bevel, entering at 150 % (70 % of the way in 7 f, ease-out p4), an '
             'inertial bounce after landing (y 5.9 sin(2 pi 2.65 t) e^-4t, rot 0.6 deg), a slow 3D swing',
        anim_in='fade', dur_in=0.1, anim_out=None, box=(0.10, 0.13),
        fx=dict(wig=[10.0, 0.7, 0.6], tilt=[2.0, 0.6], twist=None, persp=[0.16, 0.5], lw=None,
                zin=[[1.5], 10, 4.0], bounce=[14.0, 2.65, 4.0, 1.2, 1.82, 0.25], bevel=[3.0, 0.5],
                bloom=[0.08, 0.25], mblur=[0.5, 5, 5.0], ride=[1.0, 0.8, 0.8], keep_in=0.04)),
    # ---- char_soft_romance
    'rfsj_soft_write': dict(
        types=['char_soft_romance'], style='lyric_soft', source='rfsj', what='small light lowercase, light-streak '
        'write-on, a gentle float', anim_in=None, anim_out=None, fx={}),
    # ---- kpop_text_strobe
    'pb7p_static_wipe': dict(
        types=['kpop_text_strobe'], style='lyric_round', source='pB7P (measured static: 0.1 px/frame)',
        what='rounded lowercase phrase on black, words wipe in L->R, no motion (the reference holds still)',
        anim_in=None, anim_out=None, fx={}),
    # ---- fx_typography (WGvt's word3d fly-through is fxsets.word3d; trialeltron adds the rest)
    'wgvt_word3d': dict(
        types=['fx_typography'], style='hero_fx', source='WGvt (fx-av 3; fxsets.word3d)',
        what='lowercase white words flying past the lens (entry smear, 2-8 f hold, exit past the lens)',
        anim_in=None, anim_out=None, fx={}),
    'tri_line_fade': dict(
        types=['fx_typography'], style='hero_fx', source='trialeltron (AE project): Pre-comp 11 line track',
        what='the sung word fades in over 13 f and out over 10 f (linear) while the camera drifts 100-900 px over '
             '30-50 f (easy ease)',
        anim_in='fade', dur_in=0.433, anim_out='fade', dur_out=0.333, box=(0.07, 0.09),
        fx=dict(drift=[0.10, 1.2, 30.0], wig=[10.0, 0.6, 0.7], tilt=[1.5, 0.5], persp=[0.05, 0.4], glow=[0.03, 0.10, 1.3, None],
                mblur=[0.5, 3, 5.0], keep_in=0.05)),
    'tri_fly3d': dict(
        types=['fx_typography'], style='hero_fx', source='trialeltron (AE project): 3D words, position animator',
        what='letters fly in from a (500, 500) px offset in 3D over 17-43 f (ease-out p4.25-12), staggered, motion '
             'blurred; then a slow drift',
        anim_in='fade', dur_in=0.1, anim_out='fade', dur_out=0.2, box=(0.07, 0.10),
        fx=dict(scatter_in=[0.26, 0.46, 24, 6.0, 2.0, 0.0, True], scatter_on=['fade', 'spread'],
                drift=[0.06, 1.0, 20.0], tilt=[1.5, 0.5], persp=[0.08, 0.5], mblur=[0.5, 8, 4.0],
                glow=[0.03, 0.10, 1.3, None], keep_in=0.05)),
    'tri_twitch': dict(
        types=['fx_typography'], style='hero_fx', source='trialeltron (AE project): Twitch on the word markers',
        what='the word lands with a 3-frame Twitch strobe (slide / blur / light 59 -> 0) on its marker, then holds '
             'with a slow drift',
        anim_in='fade', dur_in=0.05, anim_out='fade', dur_out=0.15, box=(0.07, 0.10),
        fx=dict(twitch=[0.06, 3, 0.8], drift=[0.05, 1.0, 20.0], tilt=[1.5, 0.5], mblur=[0.5, 5, 5.0],
                glow=[0.03, 0.12, 1.3, None], keep_in=0.05)),
    'tri_camera_push': dict(
        types=['fx_typography'], style='hero_fx', source='trialeltron (AE project): camera z 2250-4000 -> 0',
        what='the camera flies onto the word from depth over 23-49 f (ease-out p4.25-12: scale 0.25 -> 1), then '
             'drifts slowly',
        anim_in='fade', dur_in=0.15, anim_out='fade', dur_out=0.2, box=(0.07, 0.10),
        fx=dict(fly_in=[0.05, 0.03, [0.0, 15.0], 30, 6.0, 0.25], drift=[0.05, 1.0, 20.0], tilt=[1.5, 0.5],
                mblur=[0.5, 8, 4.0], glow=[0.03, 0.12, 1.3, None], keep_in=0.05)),
}


def recipes_for(preset):
    """[(name, recipe)] of an edit type, reference look first."""
    return [(n, r) for n, r in RECIPES.items() if preset in r['types']]


def apply_recipe(ov, name, rng=None):
    """Give a lyric_text overlay (dict) a recipe: its style, fx and the items' entry / exit (and box when the recipe
    sets one). Returns the overlay."""
    r = RECIPES[name]
    if r.get('style'):
        ov['style'] = r['style']
    if r.get('fx'):
        ov['fx'] = dict(r['fx'])
    ov['recipe'] = name
    for it in ov.get('items') or []:
        if r.get('anim_in'):
            it['anim_in'] = r['anim_in']
            if r.get('dur_in') is not None:
                it['dur_in'] = r['dur_in']
        if r.get('anim_out') and it.get('anim_out') != 'cut':
            it['anim_out'] = r['anim_out']
            if r.get('dur_out') is not None:
                d = float(r['dur_out'])
                it['t_out'] = round(max(float(it['t0']) + 0.2, float(it['t1']) - d), 4)
                it['dur_out'] = d
        if r.get('box') and rng is not None:
            b = round(rng.uniform(*r['box']), 5)
            if 'box_w' in it:
                it['box_w'] = round(b * 1080.0 / 1920.0, 5)
            else:
                it['box'] = b
    return ov


def _hash(i, salt=0):
    v = math.sin(i * 12.9898 + salt * 78.233) * 43758.5453
    return 2.0 * (v - math.floor(v)) - 1.0


def _inout(f, p=1.75):
    f = min(1.0, max(0.0, f))
    return 0.5 * (2 * f) ** p if f < 0.5 else 1.0 - 0.5 * (2 - 2 * f) ** p


def keyed(t, hz, seed, alt=False, lo=0.35, p=1.75):
    """AE hand-keyed motion in [-1, 1]: keys two per cycle (1 / (2 hz) apart), easy ease between them. alt: the keys
    alternate sign with magnitudes lo..1 (-8 / +12 / -9 / +16 / -4: a sway); else uniform random keys (a wiggle)."""
    if not hz or hz <= 0:
        return 0.0
    u = t * 2.0 * hz + (0.5 + 0.5 * _hash(seed, 3))
    i = int(math.floor(u))
    f = u - i
    s0 = 1.0 if _hash(seed, 9) >= 0 else -1.0

    def val(n):
        r = _hash(n, seed + 0.37)
        if alt:
            return (lo + (1 - lo) * abs(r)) * (1.0 if n % 2 == 0 else -1.0) * s0
        return r
    a, b = val(i), val(i + 1)
    return a + (b - a) * _inout(f, p)


def _f(v, i, d=0.0):
    try:
        return float(v[i])
    except Exception:  # noqa: BLE001
        return d


def params(ov_dict, it):
    """Merged fx parameters of an item (style defaults < overlay 'fx' < item 'fx'), or None when off."""
    if it.get('fx') is False or ov_dict.get('fx') is False:
        return None
    F = dict(FX.get(ov_dict.get('style', ''), {}))
    for src in (ov_dict.get('fx'), it.get('fx')):
        if isinstance(src, dict):
            F.update(src)
    F = {k: v for k, v in F.items() if v is not None and v is not False}
    return F or None


def _unit(size):
    return min(size) / 1080.0


def _seed(F, k, it):
    return int(F.get('seed', 0)) * 7919 + k * 101 + int(round(float(it.get('t0', 0)) * 1000)) % 9973


def _item_scale0(F, sd):
    zin = F.get('zin')
    if not zin:
        return None
    ch = zin[0] if isinstance(zin[0], (list, tuple)) else [zin[0]]
    return float(ch[int(abs(_hash(sd, 17)) * len(ch)) % len(ch)])


def word_motion(F, it, k, age, size, fps=30.0, cap_h=40.0, half_w=100.0):
    """Whole-word motion at age (s): dict(tx, ty (px), rot (deg, + = counter-clockwise), sc, twist (rad), bend (px),
    persp (frac))."""
    W, H = size
    un = _unit(size)
    sd = _seed(F, k, it)
    a = max(0.0, age)
    tx = ty = rot = 0.0
    sc = 1.0
    if F.get('wig'):
        amp, hz = _f(F['wig'], 0) * un, _f(F['wig'], 1, 1.0)
        tx += amp * keyed(a, hz, sd + 1)
        ty += amp * _f(F['wig'], 2, 0.7) * keyed(a, hz * 0.87, sd + 2)
    if F.get('drift'):
        v, tau, ang = _f(F['drift'], 0) * W, max(1e-3, _f(F['drift'], 1, 0.7)), _f(F['drift'], 2, 20.0)
        th = math.radians(ang * _hash(sd, 23))
        d = (1.0 if _hash(sd, 29) >= 0 else -1.0)
        s = v * tau * (1 - math.exp(-a / tau))
        tx += d * s * math.cos(th)
        ty += s * math.sin(th)
    if F.get('tilt'):
        rot += _f(F['tilt'], 0) * keyed(a, _f(F['tilt'], 1, 1.0), sd + 3, alt=True)
    s0 = _item_scale0(F, sd)
    if s0 is not None:
        nf, p = max(1.0, _f(F['zin'], 1, 6)), _f(F['zin'], 2, 3.0)
        u = min(1.0, a * 30.0 / nf)
        sc *= s0 + (1 - s0) * (1 - (1 - u) ** p)
    if F.get('zrate'):
        r = _f(F['zrate'], 0)
        mode = F['zrate'][1] if len(F['zrate']) > 1 else None
        if mode == 'follow' and s0 is not None and s0 != 1.0:
            r *= (-1.0 if s0 > 1.0 else 1.0)          # keeps zooming the way it came in (S2rz: shrink-in words keep shrinking)
        elif mode:
            r *= (1.0 if _hash(sd, 31) >= 0 else -1.0)
        sc *= math.exp(r * a)
    tw = 0.0
    if F.get('twist'):
        tw = math.radians(_f(F['twist'], 0)) * keyed(a, _f(F['twist'], 1, 2.0), sd + 4, alt=True)
        # legibility cap: the S-wave's peak shift (0.286 x half width x angle) stays under 0.45 cap height
        cap = 0.45 * cap_h / max(1.0, 0.286 * half_w)
        tw = max(-cap, min(cap, tw))
    bend = 0.0
    if F.get('bend'):
        bend = _f(F['bend'], 0) * cap_h * keyed(a, _f(F['bend'], 1, 1.0), sd + 5, alt=True)
    persp = 0.0
    if F.get('persp'):
        persp = _f(F['persp'], 0) * keyed(a, _f(F['persp'], 1, 0.8), sd + 6, alt=True)
    sx, light = 1.0, 0.0
    un = _unit(size)
    t_out = float(it.get('t_out', it.get('t1', 1e9))) - float(it.get('t0', 0.0))
    if F.get('fly_in'):
        # camera flight onto the word (Love Again's word camera / trialeltron's null-chain camera): the word arrives
        # from (dx W, dy H) off its place, rolled by one of the roll choices and at depth scale z0, easing out
        dxw, dyh, nf, p, z0 = (_f(F['fly_in'], 0), _f(F['fly_in'], 1), max(1.0, _f(F['fly_in'], 3, 18)),
                               _f(F['fly_in'], 4, 3.25), _f(F['fly_in'], 5, 1.0))
        rolls = F['fly_in'][2] if len(F['fly_in']) > 2 and isinstance(F['fly_in'][2], (list, tuple)) else [0.0]
        u = min(1.0, a * 30.0 / nf)
        e = 1 - (1 - u) ** p
        sgx = 1.0 if _hash(sd, 81) >= 0 else -1.0
        sgy = 1.0 if _hash(sd, 83) >= 0 else -1.0
        roll = float(rolls[int(abs(_hash(sd, 85)) * len(rolls)) % len(rolls)]) * (1.0 if _hash(sd, 87) >= 0 else -1.0)
        tx += sgx * dxw * W * (1 - e)
        ty += sgy * dyh * H * (1 - e)
        rot += roll * (1 - e)
        sc *= z0 + (1 - z0) * e
    if F.get('fly_out') and a >= t_out:
        # the camera moves on: the word leaves the way the next one comes from (ease-in)
        dxw, dyh, nf, p, z1 = (_f(F['fly_out'], 0), _f(F['fly_out'], 1), max(1.0, _f(F['fly_out'], 2, 8)),
                               _f(F['fly_out'], 3, 4.0), _f(F['fly_out'], 4, 1.0))
        v = min(1.0, (a - t_out) * 30.0 / nf)
        e = v ** p
        sgx = 1.0 if _hash(sd, 81) >= 0 else -1.0
        tx -= sgx * dxw * W * e
        ty -= dyh * H * e
        sc *= 1 + (z1 - 1) * e
    if F.get('bounce'):
        # The Beach's inertial-bounce expression after the landing: y = A sin(2 pi f t) e^(-d t), rot the same way
        land = _f(F['bounce'], 5, 0.2)
        tb = a - land
        if tb > 0:
            dec = math.exp(-_f(F['bounce'], 2, 4.0) * tb)
            ty += _f(F['bounce'], 0) * un * math.sin(2 * math.pi * _f(F['bounce'], 1, 2.65) * tb) * dec
            rot += -_f(F['bounce'], 3) * math.sin(2 * math.pi * _f(F['bounce'], 4, 1.82) * tb) * dec
    if F.get('twitch'):
        # Video Copilot Twitch on the word's marker: slide / blur / light jitter, amount decaying to 0 over frames
        amt, nf, lt = _f(F['twitch'], 0) * W, max(1.0, _f(F['twitch'], 1, 3)), _f(F['twitch'], 2, 0.5)
        env = max(0.0, 1 - a * 30.0 / nf)
        if env > 0:
            tx += amt * env * keyed(a, 14.0, sd + 91)
            ty += 0.35 * amt * env * keyed(a, 13.0, sd + 93)
            light = lt * env
    if F.get('flip_in'):
        # book.aep page turn: the card swings in about its vertical axis (edge-on -> flat) over frames, the near edge
        # taller while it turns
        nf, p = max(1.0, _f(F['flip_in'], 0, 8)), _f(F['flip_in'], 1, 2.0)
        u = min(1.0, a * 30.0 / nf)
        th = (math.pi / 2) * (1 - (1 - (1 - u) ** p)) if u < 1 else 0.0
        sx = max(0.03, math.cos(th))
        persp += 0.45 * math.sin(th) * (1.0 if _hash(sd, 95) >= 0 else -1.0)
    return dict(tx=tx, ty=ty, rot=rot, sc=sc, twist=tw, bend=bend, persp=persp, sx=sx, light=light)


def letter_offsets(F, n, age, age_out, size, sd, cap_h, fps=30.0, ramp=1.0):
    """Per letter (ox, oy, drot, alpha, scale) at age (s); age_out = s since the exit started (< 0 before)."""
    W, H = size
    out = []
    lw = F.get('lw')
    si = F.get('scatter_in')
    so = F.get('scatter_out')
    for j in range(n):
        ox = oy = dr = 0.0
        al, ls = 1.0, 1.0
        if lw and ramp > 0:
            amp, deg, hz, corr, dl = (_f(lw, 0) * cap_h, _f(lw, 1), _f(lw, 2, 2.0), _f(lw, 3, 0.5), _f(lw, 4))
            tj = max(0.0, age - dl * j)
            nrm = 1.0 / max(0.5, math.hypot(corr, 1 - corr))

            def mix(salt):
                return (corr * keyed(tj, hz, sd + salt) + (1 - corr) * keyed(tj, hz, sd + salt + 1000 + 37 * j)) * nrm
            ox += ramp * amp * 0.6 * mix(41)
            oy += ramp * amp * mix(43)
            dr += ramp * deg * mix(47)
        if si:
            dx, dy, nf, p, stg, rdeg = (_f(si, 0) * W, _f(si, 1) * H, max(1.0, _f(si, 2, 7)), _f(si, 3, 3.0),
                                        _f(si, 4, 1.0), _f(si, 5))
            fr = age * 30.0 - stg * j
            u = min(1.0, max(0.0, fr / nf))
            e = 1 - (1 - u) ** p
            rx, ry, rr = _hash(j, sd + 51), _hash(j, sd + 53), _hash(j, sd + 57)
            mx = (0.35 + 0.65 * abs(rx)) * (1 if rx >= 0 else -1)      # Wiggly Selector: every letter its own amount
            my = (0.35 + 0.65 * abs(ry)) * (1 if ry >= 0 else -1)
            if len(si) > 6 and si[6]:
                # a plain position animator: every letter from the same offset (+-15 %), staggered by the ramp
                sg = 1.0 if _hash(sd, 59) >= 0 else -1.0
                mx, my = sg * (1 + 0.15 * rx), 1 + 0.15 * ry
            ox += dx * mx * (1 - e)
            oy += dy * my * (1 - e)
            dr += rdeg * rr * (1 - e)
            al *= 0.0 if fr < 0 else min(1.0, 0.35 + 1.6 * u)     # ink on the syllable's first frame
        if so and age_out >= 0:
            dx, dy, nf, p = _f(so, 0) * W, _f(so, 1) * H, max(1.0, _f(so, 2, 5)), _f(so, 3, 2.5)
            v = min(1.0, age_out * 30.0 / nf)
            e = v ** p
            rx, ry = _hash(j, sd + 61), _hash(j, sd + 63)
            ox += dx * rx * e
            oy += dy * (0.4 + 0.6 * abs(ry)) * (1 if ry >= 0 else -1) * e
            dr += 20.0 * _hash(j, sd + 67) * e
            al *= max(0.0, 1 - v ** 1.5)
        out.append((ox, oy, dr, al, ls))
    return out


def vary(ov, preset, seed, forced=None, rng=None):
    """Variety for planner-made lyric_text tracks (flow_s2rz / hype_i4f new-song edits; remakes keep the reference
    recipe): pick one recipe of this edit type (lyric_text.RECIPE_WEIGHTS, seeded by `seed`, or `forced`) and apply it
    to the overlay dict (style, fx, entry / exit kinds). Ink variants (style ending _ink) keep their ink twin of the
    recipe's style. Returns the recipe name (or None when nothing changed)."""
    import random
    import lyric_text as LT
    import text as T
    name = forced or LT.pick_recipe(preset, dict(seed=seed))
    if not name or name not in RECIPES or preset not in RECIPES[name]['types']:
        return None
    ink = str(ov.get('style', '')).endswith('_ink')
    apply_recipe(ov, name, None)
    st = RECIPES[name].get('style')
    if ink and st and (st + '_ink') in T.STYLES:
        ov['style'] = st + '_ink'
    return name


def card_wave(o, since, size):
    """Optional alive-ness of an engine title / text card (engine.py: overlay key 'wave': [wig_px, hz, tilt_deg, tilt_hz]
    at a 1080 short side): a keyed whole-card wiggle + roll (AE wiggle() on the layer), seeded by the card's text.
    Returns (dx_px, dy_px, rot_deg); zeros while off."""
    wv = o.get('wave')
    if not wv:
        return 0.0, 0.0, 0.0
    un = _unit(size)
    sd = sum(ord(c) for c in str(o.get('text', ''))) % 997
    amp, hz = _f(wv, 0, 8.0) * un, _f(wv, 1, 0.9)
    th, thz = _f(wv, 2, 1.2), _f(wv, 3, 0.8)
    a = max(0.0, since)
    return (amp * keyed(a, hz, sd + 1), 0.7 * amp * keyed(a, hz * 0.87, sd + 2),
            th * keyed(a, thz, sd + 3, alt=True))


# ---------------------------------------------------------------------------------------------- camera ride

def _cam_matrix(eng, j, t):
    s = eng.shots[j]
    lay = s.get('layout', 'fill')
    import camera3d as C3  # noqa: F401  (engine module; 3D cameras are not followed)
    c = eng.shot_cam(j, t)
    if C3.is3d(c):
        return None
    M = eng._matrix(s, eng.foot(s), eng.canvas, c, t, None, lay if lay in ('fit', 'letterbox', 'fit_black') else 'fill')
    return np.vstack([np.asarray(M, np.float64), [0.0, 0.0, 1.0]])


def ride_state(ov, it, k, t, F, anchor):
    """(dx, dy, scale, rot_deg) the word has followed the picture's camera since its first frame: per frame the anchor
    point is mapped by M(f) M(f-1)^-1 of the shot on screen (no step across a cut), times the ride ratios."""
    rd = F.get('ride')
    eng = getattr(ov, 'eng', None)
    if not rd or eng is None or not hasattr(eng, 'shot_cam'):
        return 0.0, 0.0, 1.0, 0.0
    rp, rs, rr = _f(rd, 0, 1.0), _f(rd, 1, 0.5), _f(rd, 2, 0.5)
    fps = float(getattr(eng, 'fps', 30) or 30)
    cache = ov._cache.setdefault(('tfx_ride', k), {})
    f0 = int(math.floor(float(it['t0']) * fps))
    ft = t * fps
    fi = int(math.floor(ft))
    if f0 not in cache:
        cache[f0] = (0.0, 0.0, 1.0, 0.0)
    last = max(f for f in cache if f <= max(f0, fi)) if cache else f0
    W, H = ov.size
    lim = 0.35 * W
    while last < fi + 1:
        f = last + 1
        dx, dy, s, r = cache[last]
        try:
            ta, tb = (f - 1) / fps, f / fps
            ja, jb = eng.shot_at(ta), eng.shot_at(tb)
            if ja == jb:
                Ma, Mb = _cam_matrix(eng, ja, ta), _cam_matrix(eng, jb, tb)
                if Ma is not None and Mb is not None:
                    A = Mb @ np.linalg.inv(Ma)
                    p = np.array([anchor[0] + dx, anchor[1] + dy, 1.0])
                    q = A @ p
                    det = abs(np.linalg.det(A[:2, :2]))
                    ang = math.degrees(math.atan2(-A[1, 0], A[0, 0]))
                    dx = max(-lim, min(lim, dx + rp * (q[0] - p[0])))
                    dy = max(-lim, min(lim, dy + rp * (q[1] - p[1])))
                    s = max(0.5, min(2.0, s * (1 + rs * (math.sqrt(det) - 1))))
                    r = max(-25.0, min(25.0, r + rr * ang))
        except Exception:  # noqa: BLE001
            pass
        cache[f] = (dx, dy, s, r)
        last = f
    a = cache.get(fi, cache[max(f for f in cache if f <= fi)] if any(f <= fi for f in cache) else (0.0, 0.0, 1.0, 0.0))
    b = cache.get(fi + 1, a)
    u = ft - fi
    return tuple(a[i] + (b[i] - a[i]) * u for i in range(4))


# ---------------------------------------------------------------------------------------------- warp + look

def _fwd(pts, c, m):
    """Forward rigid map of word-local points (N, 2) around centre c."""
    rr = math.radians(m['rot'])
    co, si = math.cos(rr), math.sin(rr)
    x, y = (pts[:, 0] - c[0]) * m.get('sx', 1.0), pts[:, 1] - c[1]
    s = m['sc']
    return np.stack([c[0] + m['tx'] + s * (co * x + si * y), c[1] + m['ty'] + s * (-si * x + co * y)], 1)


def _remap(layer, box, c, m, half_w, cap_h):
    """Warp a full-canvas premultiplied BGRA layer into the dest box (x0, y0, x1, y1): rigid (tx, ty, rot, sc) about the
    word centre c, then (inverse order) perspective keystone, arc bend, twist. Returns float32 crop."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    ds = 2 if bw * bh > 160000 else 1
    xs = np.arange(x0, x1, ds, dtype=np.float32)
    ys = np.arange(y0, y1, ds, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)
    X -= c[0] + m['tx']
    Y -= c[1] + m['ty']
    rr = math.radians(m['rot'])
    co, si = math.cos(rr), math.sin(rr)
    s = max(1e-3, m['sc'])
    x = (co * X - si * Y) / (s * max(0.02, m.get('sx', 1.0)))
    y = (si * X + co * Y) / s
    hw = max(4.0, half_w)
    if m.get('persp'):
        den = 1.0 + m['persp'] * np.clip(x / hw, -1.5, 1.5)
        y = y / np.maximum(0.3, den)
    if m.get('bend'):
        u = np.clip(x / hw, -1.0, 1.0)
        y = y - m['bend'] * (1.0 - u * u)
    if m.get('twist'):
        r2 = (x * x + y * y) / (hw * 1.05) ** 2
        th = -m['twist'] * np.clip(1.0 - r2, 0.0, 1.0) ** 2
        ct, st = np.cos(th), np.sin(th)
        x, y = ct * x - st * y, st * x + ct * y
    mx = (x + c[0]).astype(np.float32)
    my = (y + c[1]).astype(np.float32)
    if ds > 1:
        mx = cv2.resize(mx, (bw, bh), interpolation=cv2.INTER_LINEAR)
        my = cv2.resize(my, (bw, bh), interpolation=cv2.INTER_LINEAR)
    return cv2.remap(layer, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float32)


def _bbox(layer):
    a = layer[::4, ::4, 3]
    rows = np.nonzero(a.max(axis=1) > 2)[0]
    if not len(rows):
        return None
    cols = np.nonzero(a.max(axis=0) > 2)[0]
    return (max(0, cols[0] * 4 - 4), max(0, rows[0] * 4 - 4), min(layer.shape[1], cols[-1] * 4 + 8),
            min(layer.shape[0], rows[-1] * 4 + 8))


def _look(acc, F, un, cap_h):
    """Bevel + hard shadow on a premultiplied float crop; returns (crop, light) where light is the tight bloom
    (float BGR, same size) or None."""
    A = acc[..., 3] / 255.0
    if F.get('bevel') and A.max() > 0.05:
        px, stg = max(1.0, _f(F['bevel'], 0, 3.0) * un), _f(F['bevel'], 1, 0.4)
        Ab = cv2.GaussianBlur(A, (0, 0), px * 0.6)
        # one pass of (Sobel x + Sobel y): light from the top-left, + on the lit rim, - on the far rim
        ker = np.array([[-2, -2, 0], [-2, 0, 2], [0, 2, 2]], np.float32) * (px * 0.9)
        sh = cv2.filter2D(Ab, cv2.CV_32F, ker)
        hi = cv2.multiply(np.minimum(np.maximum(sh, 0), 1), A, scale=255.0 * stg)
        lo = 1.0 - np.minimum(np.maximum(-sh, 0), 1) * stg
        c3 = acc[..., :3]
        c3 *= lo[..., None]
        c3 += hi[..., None]
    if F.get('shadow'):
        # hard drop shadow (0 blur): black, under the text (premultiplied: colour unchanged, alpha grows)
        dx, dy, sa = _f(F['shadow'], 0) * un, _f(F['shadow'], 1) * un, _f(F['shadow'], 2, 0.6)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        As = cv2.warpAffine(A, M, (A.shape[1], A.shape[0]), borderValue=0) * sa
        acc[..., 3] = 255.0 * (A + (1 - A) * As)
    light = None
    if F.get('bloom'):
        sg, gn = max(1.0, _f(F['bloom'], 0, 0.1) * cap_h), _f(F['bloom'], 1, 0.3)
        h, w = acc.shape[:2]
        f = 2 if sg > 4 else 1
        sm = cv2.resize(acc[..., :3], (max(2, w // f), max(2, h // f)), interpolation=cv2.INTER_AREA) if f > 1 else acc[..., :3]
        light = cv2.GaussianBlur(sm, (0, 0), sg / f) * gn
        if f > 1:
            light = cv2.resize(light, (w, h), interpolation=cv2.INTER_LINEAR)
    return acc, light


def _screen_u8(dst, light_u8):
    """dst += light (additive light, saturating uint8; in place on a view) - Deep Glow / bloom add light."""
    dst[...] = cv2.add(dst, light_u8)


def _wide_glow(img, box, acc_rgb, F, W, H):
    """Deep Glow style very wide soft halo (x-stretched, optionally tinted), screened onto img around box; computed
    at 1/8 scale and upsampled only over its own region."""
    sgw, gn, asp = _f(F['glow'], 0, 0.05) * W, _f(F['glow'], 1, 0.15), _f(F['glow'], 2, 1.5)
    tint = F['glow'][3] if len(F['glow']) > 3 and F['glow'][3] is not None else None
    x0, y0, x1, y1 = box
    f = 8
    mx, my = int(2.5 * sgw * asp), int(2.5 * sgw)
    rx0, ry0, rx1, ry1 = max(0, x0 - mx), max(0, y0 - my), min(W, x1 + mx), min(H, y1 + my)
    sw, sh = max(2, (rx1 - rx0) // f), max(2, (ry1 - ry0) // f)
    small = np.zeros((sh, sw, 3), np.float32)
    cw, chh = max(1, (x1 - x0) // f), max(1, (y1 - y0) // f)
    ox, oy = min(sw - cw, (x0 - rx0) // f), min(sh - chh, (y0 - ry0) // f)
    if ox < 0 or oy < 0:
        return
    small[oy:oy + chh, ox:ox + cw] = cv2.resize(acc_rgb, (cw, chh), interpolation=cv2.INTER_AREA)
    if tint is not None:
        small = small.mean(axis=2, keepdims=True) * (np.array(tint, np.float32)[None, None, :] / 255.0)
    g = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, sgw * asp / f), sigmaY=max(0.5, sgw / f)) * (gn * 3.0)
    g = cv2.resize(np.clip(g, 0, 255).astype(np.uint8), (rx1 - rx0, ry1 - ry0), interpolation=cv2.INTER_LINEAR)
    _screen_u8(img[ry0:ry1, rx0:rx1], g)


# ---------------------------------------------------------------------------------------------- the item

def active_letter_path(F, it, age, t, tout, n_letters):
    """'in' / 'hold' / 'out' when the letters are drawn here (scatter / per-letter wiggle), else None (the item's own
    lyric_text animation draws it)."""
    kind = it.get('anim_in', 'fade')
    if kind == 'smear_shrink':
        return None
    if F.get('scatter_out') and t >= tout and it.get('anim_out') in (F.get('scatter_out_on') or ['blur_out']):
        return 'out'
    if t >= tout:
        return None
    if F.get('scatter_in') and kind in (F.get('scatter_on') or [kind]):
        return 'in'
    if F.get('lw') and age >= entry_len(it, n_letters):
        return 'hold'
    return None


def entry_len(it, n):
    kind = it.get('anim_in', 'fade')
    din = float(it.get('dur_in', 0.25))
    if kind == 'letter_streak':
        return (n - 1) * float(it.get('stagger', 1 / 30.0)) + float(it.get('streak_frames', 4.5)) / 30.0 + 0.02
    if kind == 'letter_pop':
        return (n - 1) * float(it.get('stagger', 1 / 30.0)) + float(it.get('pop_frames', 4.0)) / 30.0 + 0.02
    if kind == 'type_smear':
        return (n + 5) / 30.0
    return din + 0.02


def _letter_sprites(ov, k, it, LT):
    """Each letter of item k rendered ALONE in the item's style and final size (cut-outs of the word sprite carry
    slivers of the neighbouring glyphs and their glow, which show as seams once letters move): [(bgra, dx from the
    word centre, index)], or None (then lyric_text._letters)."""
    key = ('tfx_lsp', k)
    if key in ov._cache:
        return ov._cache[key]
    import text as T
    res = None
    try:
        o = ov.ov
        w, h = ov.size
        st = o.get('style', 'lyric_hype')
        base = T.STYLES.get(st, {})
        box_px = float(it['box_w']) * w if 'box_w' in it else float(it.get('box', 0.08)) * h
        size = box_px / LT._box_ratio(st, it['text'])
        over = dict(o.get('look') or {})
        if base.get('glow_rel'):
            over['glow'] = [(r * size, a, col) for r, a, col in base['glow_rel']]
        if base.get('shadow_rel'):
            dx, dy, bl, a = base['shadow_rel']
            over['shadow'] = (dx * size, dy * size, bl * size, a)
        if base.get('extrude_rel'):
            n, d, col = base['extrude_rel']
            over['extrude'] = (max(2, int(n * size)), d, col)
        sd = dict(T.STYLES.get(st, T.STYLES['title']))
        sd.update({kk: v for kk, v in over.items() if v is not None})
        text = T._case(T.latin_only(it['text']), sd.get('case')) or ' '
        size, mw = int(size), int(0.86 * w)
        while True:                                        # the same fit loop as text.render
            spr = T._render(text, sd, size)
            if spr.size[0] - 2 * spr.pad <= mw or size <= 10:
                break
            size = max(10, int(size * max(0.6, mw / max(1, spr.size[0] - 2 * spr.pad)) * 0.98))
        cx = spr.size[0] / 2.0
        res = []
        for j, (ch, x0, x1) in enumerate(spr.letters):
            if not ch.strip():
                continue
            g = T._render(ch, sd, size)
            gc = g.pad + (g.letters[0][1] + g.letters[0][2]) / 2.0 - g.size[0] / 2.0
            res.append((g.img, spr.pad + (x0 + x1) / 2.0 - cx - gc, j))
    except Exception:  # noqa: BLE001
        res = None
    ov._cache[key] = res
    return res


def _render_layer(ov, img, ts, k, it, F, LT, size, fps=30.0):
    """The item alone at time ts on a transparent premultiplied BGRA canvas (uint8) + its centre and cap height."""
    import text as T
    W, H = size
    lay = np.zeros((H, W, 4), np.uint8)
    c = LT._prep(ov)
    sp = c.sprites[k]
    x, y, hrot, hsc = LT.hold_state(ov, it, ts)
    cap_h = max(4.0, (sp.size[1] - 2 * sp.pad) * 0.72) * hsc
    half_w = max(4.0, (sp.size[0] - 2 * sp.pad) / 2.0) * hsc
    age = ts - float(it['t0'])
    if not (float(it['t0']) <= ts < float(it['t1'])):
        return lay, (x, y), cap_h, half_w, None
    tout = float(it.get('t_out', it['t1']))
    letters = LT._letters(sp)
    mode = active_letter_path(F, it, age, ts, tout, len(letters))
    if mode is not None:
        letters = _letter_sprites(ov, k, it, LT) or letters
    if mode is None:
        ov._bg_img = img
        try:
            lay = LT.draw(ov, lay, ts, only=k, inner=True)
        finally:
            ov._bg_img = None
        return lay, (x, y), cap_h, half_w, None
    sd = _seed(F, k, it)
    ramp = 1.0
    if mode == 'hold' and not F.get('scatter_in'):
        ramp = min(1.0, max(0.0, (age - entry_len(it, len(letters))) / 0.15))
    offs = letter_offsets(F, len(letters), age, (ts - tout) if mode == 'out' else -1.0, size, sd, cap_h, ramp=ramp)
    vel = None
    if F.get('mblur') and mode in ('in', 'out'):
        # scattering letters: each letter smeared along its own path over the shutter (per-letter RSMB)
        dt = _f(F['mblur'], 0, 0.5) / fps
        o2 = letter_offsets(F, len(letters), age + dt, (ts + dt - tout) if mode == 'out' else -1.0, size, sd, cap_h,
                            ramp=ramp)
        vel = [(b[0] - a_[0], b[1] - a_[1]) for a_, b in zip(offs, o2)]
    rot = float(it.get('rot1', 0.0)) + hrot
    alpha = 1.0
    if mode == 'in' and it.get('anim_in') == 'fade':
        alpha = min(1.0, 0.3 + age / max(1e-3, float(it.get('dur_in', 0.2))))
    ov._bg_img = img
    try:
        shadow = LT._backing(ov, k, sp, img, x, y) if it.get('backing', True) else None
    finally:
        ov._bg_img = None
    if shadow is not None and mode != 'out':
        T.blit(lay, shadow.img, x, y, alpha=alpha * (min(1.0, age / 0.2) if mode == 'in' else 1.0), scale=hsc, rot=rot)
    cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    for q, ((g, dx, j), (ox, oy, dr, la, ls)) in enumerate(zip(letters, offs)):
        if la * alpha <= 0.004:
            continue
        px = dx * hsc
        bl, ba = 0.0, 0.0
        if vel is not None:
            # along the dominant axis only (separable, cheap; the sub-frame warp adds the rest)
            vx_, vy_ = vel[q]
            bl, ba = (abs(vx_), 0.0) if abs(vx_) >= abs(vy_) else (abs(vy_), 90.0)
        T.blit(lay, g, x + px * cr + ox, y - px * sr + oy, alpha=la * alpha, scale=hsc * ls, rot=rot + dr,
               blur_len=bl if bl >= 2.0 else 0.0, blur_ang=ba, blur_max=161)
    return lay, (x, y), cap_h, half_w, mode


def _motion_at(ov, it, k, F, ts, size, anchor, cap_h, half_w, W):
    m = word_motion(F, it, k, ts - float(it['t0']), size, cap_h=cap_h, half_w=half_w)
    rx, ry, rs, rr = ride_state(ov, it, k, ts, F, anchor)
    m['tx'] += rx
    m['ty'] += ry
    m['sc'] *= rs
    m['rot'] += rr
    return m


def draw_item(ov, img, t, k, it, F):
    """Draw item k of a lyric_text overlay with the fx layer onto img (BGR, modified and returned)."""
    import lyric_text as LT
    W, H = ov.size
    size = (W, H)
    un = _unit(size)
    fps = float(getattr(getattr(ov, 'eng', None), 'fps', 30) or 30)
    lay, anchor, cap_h, half_w, mode = _render_layer(ov, img, t, k, it, F, LT, size, fps)
    bb = _bbox(lay)
    if bb is None:
        return img
    # motion blur (RSMB on the text): the layer is warped at n sub-frame times across the shutter and averaged; how
    # many from how far the word's corners travel in one shutter (scattering letters carry their own smear)
    ms = [_motion_at(ov, it, k, F, t, size, anchor, cap_h, half_w, W)]
    mb = F.get('mblur')
    if mb:
        sh, nmax, step = _f(mb, 0, 0.5), int(_f(mb, 1, 5)), max(1.0, _f(mb, 2, 5.0) * un)
        dt = sh / fps
        ma = _motion_at(ov, it, k, F, t - dt / 2, size, anchor, cap_h, half_w, W)
        mbb = _motion_at(ov, it, k, F, t + dt / 2, size, anchor, cap_h, half_w, W)
        pts = np.array([[bb[0], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]], [bb[2], bb[1]]], np.float64)
        d = float(np.abs(_fwd(pts, anchor, ma) - _fwd(pts, anchor, mbb)).max())
        d += abs(ma['twist'] - mbb['twist']) * half_w * 0.3 + abs(ma['bend'] - mbb['bend'])
        n = int(min(nmax, max(1, math.ceil(d / step))))
        if n > 1:
            ms = [_motion_at(ov, it, k, F, t + dt * (i / (n - 1) - 0.5), size, anchor, cap_h, half_w, W) for i in range(n)]
    corners = np.array([[bb[0], bb[1]], [bb[2], bb[1]], [bb[0], bb[3]], [bb[2], bb[3]]], np.float64)
    boxes = []
    for m in ms:
        q = _fwd(corners, anchor, m)
        mg = abs(m['bend']) + abs(m['twist']) * half_w * 0.35 + abs(m['persp']) * cap_h * 1.5 + 6
        boxes.append((q[:, 0].min() - mg, q[:, 1].min() - mg, q[:, 0].max() + mg, q[:, 1].max() + mg))
    bx0, by0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    bx1, by1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    # keep the moving word inside the frame (a soft clamp of the added motion, never of a designed exit)
    kin = float(F.get('keep_in', 0.0) or 0.0)
    sx = sy = 0.0
    if kin and mode != 'out':
        lo_x, hi_x, lo_y, hi_y = kin * W, (1 - kin) * W, kin * H, (1 - kin) * H
        if bx1 - bx0 < hi_x - lo_x:
            sx = max(0.0, lo_x - bx0) - max(0.0, bx1 - hi_x)
        if by1 - by0 < hi_y - lo_y:
            sy = max(0.0, lo_y - by0) - max(0.0, by1 - hi_y)
        bx0, bx1, by0, by1 = bx0 + sx, bx1 + sx, by0 + sy, by1 + sy
    pad = 0
    if F.get('shadow'):
        pad = max(pad, int(abs(_f(F['shadow'], 0)) * un + abs(_f(F['shadow'], 1)) * un) + 2)
    if F.get('bloom'):
        pad = max(pad, int(3 * _f(F['bloom'], 0, 0.1) * cap_h) + 2)
    box = (int(max(0, math.floor(bx0 - pad))), int(max(0, math.floor(by0 - pad))),
           int(min(W, math.ceil(bx1 + pad))), int(min(H, math.ceil(by1 + pad))))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return img
    acc = None
    for m in ms:
        m = dict(m, tx=m['tx'] + sx, ty=m['ty'] + sy)
        cr = _remap(lay, box, anchor, m, half_w, cap_h)
        acc = cr if acc is None else acc + cr
    if len(ms) > 1:
        acc /= float(len(ms))
    # tighten the working box to the ink (+ the look's margins): the bevel / bloom / composite cost follows its area
    al = acc[::2, ::2, 3]
    rows = np.nonzero(al.max(axis=1) > 1.0)[0]
    if not len(rows):
        return img
    cols = np.nonzero(al.max(axis=0) > 1.0)[0]
    tx0, ty0 = max(0, cols[0] * 2 - 2 - pad), max(0, rows[0] * 2 - 2 - pad)
    tx1, ty1 = min(acc.shape[1], cols[-1] * 2 + 4 + pad), min(acc.shape[0], rows[-1] * 2 + 4 + pad)
    acc = acc[ty0:ty1, tx0:tx1]
    box = (box[0] + tx0, box[1] + ty0, box[0] + tx1, box[1] + ty1)
    acc, light = _look(acc, F, un, cap_h)
    fl = max(m_.get('light', 0.0) for m_ in ms)
    if fl > 0.01:
        acc[..., :3] = np.minimum(acc[..., :3] * (1 + fl), acc[..., 3:4])
    x0, y0, x1, y1 = box
    roi = img[y0:y1, x0:x1].astype(np.float32)
    A = acc[..., 3:4] * (1.0 / 255.0)
    img[y0:y1, x0:x1] = np.clip(roi * (1 - A) + acc[..., :3], 0, 255).astype(np.uint8)
    if light is not None:
        _screen_u8(img[y0:y1, x0:x1], np.clip(light, 0, 255).astype(np.uint8))
    if F.get('glow'):
        _wide_glow(img, box, acc[..., :3], F, W, H)
    return img
