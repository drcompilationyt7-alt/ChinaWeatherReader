#!/usr/bin/env python3
"""
text_sound.py - sounds on TEXT events (typed letters, word entries / landings / exits, title cards), per text style.

MEASURED FIRST (core/edit/refs/text-sound.md / .json, 2026-10-01): in none of the studied references do typed letters, word
entries, pops, glitches or card lands carry a sound of their own (event-triggered averages of the song-subtracted
residual at 149 typed S2rz letters, 38 S2rz body word entries, 9 WGvt hero words, 22 yUui words, 5 mFHp card events: flat
vs random times; S2rz intro captions are SUBTITLES of spoken dialogue and are typed ~90 ms ahead of the voice).  The only
added sound that sits on a title is the S2rz card -> body landing (sub 64 -> 43 Hz + hiss), already scheduled by
song_fx TRANSITION_SOUND['s2rz']['entry'].  Therefore:

    plan["text_sound"]   absent | "none" | "off"   nothing (DEFAULT: every existing plan renders exactly as before)
                         "measured"                only the MEASURED entries of TEXT_SOUND (none today: the table says so)
                         "design"                  measured + the DESIGN tier the owner asked for ("the words coming in
                                                   also have sound effects"): small band-limited sounds per text style,
                                                   TYPE-SPECIFIC (a style only gets what its family of text does)
    plan["text_sound_gain_db"]   trim for all of them (default 0)
    plan["text_sound_skip"]      list of style / recipe keys to silence

Each text style (the 18 text_fx.RECIPES + the engine's text cards) declares what it plays on ENTRY / LANDING / EXIT /
per LETTER: TEXT_SOUND[key] = dict(measured=[...], design=[...], note=...), spec = dict(on, sfx, gain_db, dt, ...):
    on        letter  one per typed / written letter (typed anims; every=N thins)
              enter   at the first ink of the item (t0)
              land    when the entry animation completes (t0 + dur_in): the frame the word is full size
              exit    at t_out (start of the exit animation)
    sfx       a sfx.GENERATORS name;  gain_db re the song's +-0.3 s RMS at that time (sfx.render_cues);  dt  seconds added
    ceiling   'song' (default): never brighter than the song (its codec limit); hp_hz / lp_hz fixed corners

    text_events(plan)            -> [{kind, t, key, text, ...}]  every letter / word event of the plan's text overlays
    text_cues(plan, mode=None)   -> sfx cues (render_cues form) for engine._mix_sfx_design
    python text_sound.py --selftest | --cues plan.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TYPED_ANIMS = ('type', 'type_smear', 'write')          # letters appear one after another


def _s(on, sfx, gain_db, **kw):
    d = dict(on=on, sfx=sfx, gain_db=float(gain_db))
    d.update(kw)
    return d


# families of text behaviour -> the design sounds (levels are re the music's RMS; a whoosh in the 'full' profile is -6)
_SPREAD = [_s('land', 'word_swish', -17, dur=0.22, brightness=0.55, why='word spreads in'),
           _s('land', 'word_pop', -23, dt=0.03, why='word reaches full size')]
_SPREAD_EXIT = [_s('exit', 'word_swish', -27, dur=0.16, brightness=0.4, why='word scatters out')]
_FLY = [_s('land', 'word_swish', -14, dur=0.34, brightness=0.6, why='word flies in')]
_POP = [_s('land', 'word_pop', -18, why='word lands')]
_TYPE = [_s('letter', 'type_click', -28, why='typed letter')]

TEXT_SOUND = {
    # ---------------------------------------------------------------- the 18 recipes of text_fx.RECIPES
    's2rz_zoomdrift': dict(measured=[], design=_SPREAD + _SPREAD_EXIT, family='blur_flow',
                           note='S2rz words: 38 body entries, residual flat vs random at -100..+300 ms (text-sound.md)'),
    'la_wide_scatter': dict(measured=[], design=_SPREAD + _SPREAD_EXIT, family='blur_flow',
                            note='Love Again project: no SFX at all (projects-study.md)'),
    'la_script_scatter': dict(measured=[], design=_SPREAD + _SPREAD_EXIT, family='blur_flow'),
    'la_word_camera': dict(measured=[], design=_FLY, family='blur_flow'),
    'sx2_neon_sway': dict(measured=[], design=[_s('land', 'word_pop', -22, brightness=0.35, why='neon word lands')],
                          family='cinematic_slowmo', note='_sX2: song only (transition-sound.md), words sit on the half-bar'),
    'i4f_persp_type': dict(measured=[], design=[_s('letter', 'type_click', -27, when_anim=TYPED_ANIMS, why='typed letter'),
                                                _s('land', 'word_pop', -19, unless_anim=TYPED_ANIMS)],
                           family='hype_typography', note='i4f: 42 text entries, residual flat (text-sound.md)'),
    'zawb_halo_spread': dict(measured=[], design=_POP, family='hype_typography'),
    'npsom_page_flip': dict(measured=[], design=[_s('enter', 'paper_slide', -16, dur=0.30, brightness=0.5,
                                                    why='page turn (the project has the Turning Book Pages sound)')],
                            family='hype_typography', note='book.aep page flip; NPSOM has no text, the sound is the book shot\'s'),
    'yuui_ride_smear': dict(measured=[], design=[_s('letter', 'type_click', -28, when_anim=TYPED_ANIMS, why='typed letter'),
                                                 _s('land', 'word_pop', -20, unless_anim=TYPED_ANIMS)],
                            family='char_quick_velocity', note='yUui: 22 text entries, residual flat'),
    'yh_hook3d_pop': dict(measured=[], design=[_s('land', 'word_pop', -14, brightness=0.6, why='3D hook word pops')],
                          family='char_quick_velocity'),
    'beach_title3d': dict(measured=[], design=[_s('land', 'word_pop', -16, brightness=0.4, why='title lands')],
                          family='char_quick_velocity', note='The Beach project: sounds sit on cuts, none on text'),
    'rfsj_soft_write': dict(measured=[], design=[_s('enter', 'sparkle_chime', -27, dur=0.5, brightness=0.6,
                                                    why='light-streak write-on')], family='char_soft_romance'),
    'pb7p_static_wipe': dict(measured=[], design=[], family='kpop_text_strobe',
                             note='pB7P: the strobes / wipes are silent (kpop-av.md) - this type stays silent in every mode'),
    'wgvt_word3d': dict(measured=[], design=[_s('land', 'word_swish', -16, dur=0.30, brightness=0.6,
                                                why='word passes the lens')], family='fx_typography',
                        note='WGvt: 9 hero words + a 14.4 cps typewriter card, residual flat'),
    'tri_line_fade': dict(measured=[], design=[], family='fx_typography', note='fade-in lines: silent'),
    'tri_fly3d': dict(measured=[], design=_FLY, family='fx_typography'),
    'tri_twitch': dict(measured=[], design=[_s('enter', 'glitch_tick', -23, why='word twitch')], family='fx_typography'),
    'tri_camera_push': dict(measured=[], design=_FLY, family='fx_typography'),
    # ---------------------------------------------------------------- engine text cards (overlay type text / title_card)
    'card:type': dict(measured=[], design=_TYPE, family='cards',
                      note='S2rz typed captions (149 letters measured): NO key click in the reference; the typing runs ~90 ms ahead of '
                           'the spoken line.  design = a very soft tick'),
    'card:letters_fly': dict(measured=[], design=[_s('enter', 'word_swish', -15, dur=0.30, brightness=0.6, why='letters fly in')],
                             family='cards'),
    'card:letters_3d': dict(measured=[], design=[_s('enter', 'word_swish', -15, dur=0.40, brightness=0.55)], family='cards'),
    'card:slam': dict(measured=[], design=[_s('letter', 'word_pop', -21, why='letter slams')], family='cards'),
    'card:pop': dict(measured=[], design=_POP, family='cards'),
    'card:stretch': dict(measured=[], design=[_s('enter', 'word_swish', -15, dur=0.18, brightness=0.6)], family='cards'),
    'card:shrink_blur': dict(measured=[], design=[_s('enter', 'word_swish', -18, dur=0.2, brightness=0.45)], family='cards'),
    'card:slide': dict(measured=[], design=[_s('enter', 'word_swish', -20, dur=0.25, brightness=0.4)], family='cards'),
    'card:fade': dict(measured=[], design=[], family='cards'),
    'card:none': dict(measured=[], design=[], family='cards'),
    # the S2rz name card that zooms into the first body shot: the MEASURED landing (sub + hiss) is song_fx's 'entry' op
    'title:s2rz': dict(measured=[], design=[], family='cards', measured_elsewhere='song_fx.TRANSITION_SOUND s2rz entry',
                       note='the card-into-body landing is measured (lows +10.4 dB, peak +120 ms after the switch) and scheduled by song_fx'),
}
# lyric styles without a recipe (plans built before the library): by style name -> recipe key above
STYLE_ALIAS = {'s2rz_word': 's2rz_zoomdrift', 'lyric_pastel': 's2rz_zoomdrift', 'lyric_neon': 'sx2_neon_sway',
               'lyric_hype': 'zawb_halo_spread', 'lyric_hype_wide': 'i4f_persp_type', 'lyric_thin': 'yuui_ride_smear',
               'lyric_hook3d': 'yh_hook3d_pop', 'lyric_soft': 'rfsj_soft_write', 'lyric_round': 'pb7p_static_wipe'}
CARD_STYLE_ALIAS = {'s2rz_caption': 'card:type', 's2rz_title': 'card:letters_fly'}


def _letters(text):
    return [i for i, ch in enumerate(text or '') if not ch.isspace()]


def text_events(plan):
    """Every text event of the plan's overlays as dicts: kind enter | land | exit | letter, t (s), key (TEXT_SOUND key),
    text_len, i (letter index), anim."""
    ev = []
    for ov in plan.get('overlays') or []:
        typ = ov.get('type')
        if typ == 'lyric_text':
            key = ov.get('recipe') or STYLE_ALIAS.get(ov.get('style'))
            if key is None:
                continue
            for it in ov.get('items') or []:
                t0 = float(it.get('t0', 0.0))
                di = float(it.get('dur_in') or 0.0)
                anim = it.get('anim_in') or 'fade'
                text = it.get('text') or ''
                base = dict(key=key, text_len=len(_letters(text)), anim=anim)
                ev.append(dict(base, kind='enter', t=t0))
                ev.append(dict(base, kind='land', t=t0 + max(di, 0.0)))
                if anim in TYPED_ANIMS:
                    L = _letters(text)
                    for j, _ in enumerate(L):
                        ev.append(dict(base, kind='letter', t=t0 + (j * di / max(1, len(L))), i=j))
                if it.get('t_out') is not None:
                    ev.append(dict(base, kind='exit', t=float(it['t_out'])))
        elif typ in ('text', 'title_card'):
            anim = ov.get('anim', 'pop' if typ == 'title_card' else 'fade')
            key = ov.get('sound_key') or CARD_STYLE_ALIAS.get(ov.get('style')) or 'card:' + str(anim)
            if key not in TEXT_SOUND:
                continue
            t0 = float(ov.get('t0', 0.0))
            L = _letters(ov.get('text') or '')
            base = dict(key=key, text_len=len(L), anim=anim)
            ev.append(dict(base, kind='enter', t=t0))
            ev.append(dict(base, kind='land', t=t0 + (0.3 if anim in ('pop', 'letters_fly') else 0.1)))
            if anim == 'type':
                cps = float(ov.get('cps', 24))
                for j in range(len(L)):
                    ev.append(dict(base, kind='letter', t=t0 + j / cps, i=j))
            elif anim in ('slam', 'letters_3d', 'letters_fly'):
                stag = {'slam': 1 / 30.0, 'letters_fly': float(ov.get('stagger', 3 / 30.0)), 'letters_3d': 3 / 60.0}[anim]
                for j in range(len(L)):
                    ev.append(dict(base, kind='letter', t=t0 + j * stag, i=j))
            if ov.get('t1') is not None:
                ev.append(dict(base, kind='exit', t=float(ov['t1']) - 0.25))
    ev.sort(key=lambda e: (e['t'], e['kind']))
    return ev


def _gap(sfx):
    return {'type_click': 0.045, 'word_pop': 0.09, 'word_swish': 0.18, 'glitch_tick': 0.12, 'sparkle_chime': 0.4,
            'paper_slide': 0.4, 'title_impact': 1.0}.get(sfx, 0.15)


def text_cues(plan, mode=None, duration=None):
    """sfx cues (dicts for sfx.render_cues) for the plan's text events under `mode` (default: plan['text_sound'])."""
    mode = mode if mode is not None else plan.get('text_sound')
    if mode in (None, False, '', 'none', 'off'):
        return []
    use_design = mode in ('design', True, 'on', 'all')
    skip = set(plan.get('text_sound_skip') or [])
    trim = float(plan.get('text_sound_gain_db', 0.0) or 0.0)
    cues = []
    for e in text_events(plan):
        key = e['key']
        if key in skip:
            continue
        row = TEXT_SOUND.get(key)
        if not row:
            continue
        specs = list(row.get('measured') or []) + (list(row.get('design') or []) if use_design else [])
        for sp in specs:
            if sp['on'] != e['kind']:
                continue
            if sp.get('when_anim') and e.get('anim') not in sp['when_anim']:
                continue
            if sp.get('unless_anim') and e.get('anim') in sp['unless_anim']:
                continue
            if sp.get('every', 1) > 1 and (e.get('i', 0) % int(sp['every'])):
                continue
            at = e['t'] + float(sp.get('dt', 0.0))
            if at < 0.0 or (duration is not None and at > duration - 0.05):
                continue
            c = dict(sfx=sp['sfx'], at=round(at, 4), gain_db=float(sp['gain_db']) + trim, ceiling=sp.get('ceiling', 'song'), floor_db=-70.0,
                     seed=int(round(at * 1000)) % 100003 + 17, family='text', key=key, kind=e['kind'],
                     why='text %s %s' % (key, sp.get('why', e['kind'])))
            for k in ('dur', 'brightness', 'intensity', 'hp_hz', 'lp_hz', 'direction'):
                if k in sp:
                    c[k] = sp[k]
            cues.append(c)
    # thin: the same sound never closer than its gap (a typed line at 15 cps is 66 ms apart: allowed for clicks)
    cues.sort(key=lambda c: c['at'])
    last, out = {}, []
    for c in cues:
        g = _gap(c['sfx'])
        if c['sfx'] in last and c['at'] - last[c['sfx']] < g:
            continue
        last[c['sfx']] = c['at']
        out.append(c)
    return out


# ------------------------------------------------------------------------------------------------ selftest / cli

def selftest():
    import numpy as np
    import sfx as SFX
    ok = True
    for k, row in TEXT_SOUND.items():
        for sp in list(row.get('measured') or []) + list(row.get('design') or []):
            ok &= sp['sfx'] in SFX.GENERATORS and sp['on'] in ('letter', 'enter', 'land', 'exit')
    try:
        import text_fx
        missing = [r for r in text_fx.RECIPES if r not in TEXT_SOUND]
        print('recipes without a sound row:', missing)
        ok &= not missing
    except Exception as e:  # noqa: BLE001
        print('text_fx import:', e)
    plan = dict(overlays=[
        dict(type='text', t0=3.0, t1=6.0, text='Earlier, you asked', anim='type', cps=15.0, style='s2rz_caption'),
        dict(type='lyric_text', recipe='s2rz_zoomdrift', items=[dict(text='WORD', t0=8.0, dur_in=0.33, t_out=9.0, anim_in='spread')])])
    assert text_cues(plan) == [] and text_cues(plan, 'measured') == []
    cs = text_cues(plan, 'design')
    n_click = sum(1 for c in cs if c['sfx'] == 'type_click')
    good = n_click == 16 and any(c['sfx'] == 'word_swish' and abs(c['at'] - 8.33) < 1e-6 for c in cs)
    print('cues', len(cs), 'clicks', n_click, 'ok' if good else 'FAIL')
    ok &= good
    mus = np.random.default_rng(0).standard_normal((2, SFX.SR * 11)) * 0.1
    lay, rep = SFX.render_cues(cs, 11.0, SFX.SR, mus)
    ok &= len(rep) == len(cs) and np.isfinite(lay).all()
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    return ok


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    if '--cues' in sys.argv:
        p = json.load(open(sys.argv[sys.argv.index('--cues') + 1], encoding='utf-8'))
        print(json.dumps(text_cues(p, p.get('text_sound') or 'design'), indent=1))
