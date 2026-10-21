"""
Cinematic slow-mo vibe edit: one call from library-format shots + a song to a finished video (no watermark).

    render_vibe(song, shots, out, *, aspect='9:16', length=(30.0, 50.0), seed=0, workdir=None, song_speed=0.95,
                title=None) -> {'ok', 'video', 'duration', 'used', 'plan', 'render_s', 'reason'?}

Called by core/edit/vibe_edit.py (the production entry point). What it does:
  1. the song SLOWED by `song_speed` (resampled, pitch follows: song_fx.make_version, as the _sX2 remake), analysed
     once (music.py, cached next to the slowed file);
  2. planners.plan_cinematic_slowmo with the body length set from `length` (cine_body_s): words on black (the title,
     never lyrics), phrases of [hero hold] + ~1 s pulses on the song's accents, an emotional lift on the loudest
     section, the last shot into a fade with the song; 16:9 + length >= 90 s = the landscape LONG version (looser
     2-pulse shots in the quiet sections, more clips);
  3. engine.py renders it (CPU only works: EDIT_GPU=0 -> libx264; the slow motion is DIS optical-flow interpolation
     in footage.py, never frame repeats); music only (AUDIO_AV: no sfx, no clip audio); mastered by the plan's
     loudness / limiter keys.
Lyrics (owner 2026-10-12): the sung words of the song's intro line, fetched and aligned at render time (lyrics.py with
LYRICS_NO_STORE=1: nothing stored; the plan on disk is redacted); a song without lyrics shows no text.
CLI (test): python core/edit/cine_vibe.py --song S --shots shots.json [--shots ...] --out O.mp4 [--aspect 16:9]
            [--length 90 180] [--speed 0.95] [--title "summer glow"] [--seed 0]
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

INTRO_MAX = 5.5        # _sX2: words on black for 5.5 s before the hero
FPS = 30


def log(*a):
    print('[cine_vibe]', *a, file=sys.stderr, flush=True)


def _slowed(song, speed, wd):
    import song_fx as SONGFX
    base = os.path.splitext(os.path.basename(song))[0]
    vp = os.path.join(wd, f'{base}@v{speed:g}.m4a')
    if not os.path.exists(vp) or os.path.getmtime(vp) < os.path.getmtime(song):
        if abs(speed - 1.0) < 1e-4:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', song, '-vn', '-c:a', 'aac', '-b:a', '256k', vp], check=True)
        else:
            SONGFX.make_version(song, vp, rate=speed)
    return vp


def _analysis(path):
    import music as MU
    cache = path + '.music.json'
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
        A = MU.load(cache)
    else:
        A = MU.analyze(path)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(A, f)
    A['path'] = os.path.abspath(path)
    return A


def _title_words(title):
    import text as T
    toks = [w for w in T.latin_only(str(title or '')).replace('-', ' ').split() if w.strip(' ,.;:!?"()[]')]
    return toks[:4]


def _intro_words(plan, toks):
    """Edit-time word items for lyric_text (the title words, one per pulse ending ~0.25 s before the hero)."""
    drop = float(plan.get('drop') or 0.0)
    beats = [b for b in plan.get('beats') or [] if 0.0 <= b < drop - 0.3]
    n = len(toks)
    if not n or drop < 1.0:
        return []
    # one word per beat-group, the last word ending before the hero; evenly over the intro when beats are sparse
    span = drop - 0.25
    ts = [span * i / n for i in range(n)]
    if len(beats) >= n:
        step = max(1, len(beats) // n)
        cand = beats[::step][:n]
        if len(cand) == n:
            ts = [0.0] + [float(b) for b in cand[1:]]
    out = []
    for i, w in enumerate(toks):
        a = ts[i]
        b = ts[i + 1] - 0.02 if i + 1 < n else span
        out.append(dict(text=w, t_start=round(a, 4), t_end=round(max(a + 0.3, b), 4), line_id=0, confidence=1.0,
                        lyric=False))
    return out


def _lengths(aspect, length, n_clean, pulse, song_s, intro, rng):
    """(body_s, loose, max_shots) for the requested video length, fitted to the CLEAN clips (no hard reject) and the
    song. A 9:16 short cuts on (almost) every ~1 s pulse; when clean clips are short, pairs of pulses become 2-pulse
    shots (cine_body_layout max_shots) before the video gets shorter."""
    lo, hi = float(length[0]), float(length[1])
    long_v = aspect == '16:9' and hi >= 80.0
    want = rng.uniform(lo + 0.35 * (hi - lo), lo + 0.75 * (hi - lo))
    avg_ok = 2.0 if long_v else 1.45             # mean pulses per shot we accept before shortening the video
    L = min(want, intro + 0.3 + n_clean * avg_ok * pulse, song_s - 8.0)
    L = max(L, min(lo, song_s - 8.0))
    body = max(8.0, L - intro + 0.7)               # + a pulse of margin: the pulse grid rounds the body down
    return body, long_v, int(n_clean)


def _loudness(path):
    import re
    e = subprocess.run(['ffmpeg', '-nostats', '-i', path, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True, errors='replace').stderr
    e = e[e.rfind('Summary'):]
    i = re.search(r'I:\s+(-?[\d.]+) LUFS', e)
    tp = re.search(r'Peak:\s+(-?[\d.]+) dBFS', e)
    return (float(i.group(1)) if i else None), (float(tp.group(1)) if tp else None)


def _tp_guard(out, limit=-1.0):
    """The AAC encode of a hard-limited master can overshoot the wav's ceiling by 2-3 dB (a 91 s test long: wav
    -1.8 dBFS, AAC +1.5 dBTP). Measure the delivered true peak and re-encode the audio only (video copied) with the
    gain that brings it under `limit`. -> delivered (lufs, true peak)"""
    lu, tp = _loudness(out)
    for _ in range(2):
        if tp is None or tp <= limit:
            break
        red = tp - limit + 0.3
        tmp = out + '.tp.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', out, '-map', '0', '-c:v', 'copy', '-af', f'volume=-{red:.2f}dB',
                        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', tmp], check=True)
        os.replace(tmp, out)
        lu, tp = _loudness(out)
        log(f'true peak guard: -{red:.2f} dB -> {lu} LUFS / {tp} dBTP')
    return dict(lufs=lu, dbtp=tp)


def _start_guard(out, d=0.08):
    """the mastered mix starts at full level on frame 0 (the engine's 30 ms fade-in sits BEFORE its limiter / make-up gain: a
    first-10-ms peak of 0.9 measured on slowmo_3 m1): re-encode the audio only with an 80 ms fade-in (video copied)"""
    try:
        r = subprocess.run(['ffmpeg', '-v', 'error', '-i', out, '-t', '0.02', '-ac', '1', '-ar', '22050', '-f', 'f32le', '-'],
                           capture_output=True)
        import numpy as np
        y = np.frombuffer(r.stdout, np.float32)
        if not len(y) or float(np.abs(y[:220]).max()) <= 0.1:
            return
        tmp = out + '.fi.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', out, '-map', '0', '-c:v', 'copy', '-af', f'afade=t=in:st=0:d={d}',
                        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', tmp], check=True)
        os.replace(tmp, out)
        log(f'start guard: {d * 1000:.0f} ms fade-in on the delivered mix')
    except Exception as e:  # noqa: BLE001
        log(f'start guard failed: {e}')


def _dedupe(shots):
    """One entry per pool id / moment, paths that exist."""
    seen, out = set(), []
    for s in shots:
        p = s.get('path')
        if not p or not os.path.exists(p):
            continue
        ks = [s.get('pool_id') or p]
        if s.get('source_id') and s.get('start') is not None:
            # the same source moment imported twice (two library runs / two vibes): one entry
            ks.append('%s@%.1f' % (s['source_id'], float(s['start'])))
        if any(k in seen for k in ks):
            continue
        seen.update(ks)
        out.append(s)
    return out


SIDECARS = ('.flow30.json', '.portrait.json', '.cinemood.json', '.mouth.json', '.interp.json', '.people.json')


def precompute(shots, log_every=10):
    """Compute and write every per-clip cache plan_cinematic_slowmo reads, as sidecar files next to each clip
    (<clip>.flow30.json  native motion curve (planners.clip_flow),
     <clip>.portrait.json  graded portrait QA: face size, sharpness, luma / sat, SigLIP tags (portrait.qa),
     <clip>.cinemood.json  gore / fierce mood (planners._cine_mood),
     <clip>.mouth.json  mouth open / close cycles (portrait.mouth_cycles),
     <clip>.interp.json  drawing rate / flow consistency / roll (interp_qa.risk),
     <clip>.people.json  two-shot / gag / blink (portrait.people)).
    Call it at pool build time so a CI render only reads them (cold ~25-40 s per clip on CPU, warm ~0). The red-wash
    and panel checks are cheap and are not cached on disk. -> number of clips whose caches are all present."""
    import planners as PL
    import portrait as PQ
    ok = 0
    for i, s in enumerate(shots):
        p = s.get('path') if isinstance(s, dict) else str(s)
        if not p or not os.path.exists(p):
            continue
        try:
            PL.clip_flow(p)
            PQ.qa(p)
            PL._cine_mood(p)
            PL._cine_mouth(p)
            PL._cine_interp(p)
            PL._cine_people(p)
            PL._cine_red(p)
            PL._cine_panels(p)
        except Exception as e:  # noqa: BLE001
            log(f'precompute {os.path.basename(p)} failed: {e}')
            continue
        if all(os.path.exists(p + x) for x in SIDECARS):
            ok += 1
        if log_every and (i + 1) % log_every == 0:
            log(f'precompute {i + 1}/{len(shots)}')
    return ok


def gate(shots, song=None, song_speed=0.95, workdir=None):
    """The planner's HARD rejects per clip (planners.CINE_HARD + grey / villain / gloomy / face_small / oversat / frozen
    / too_fast), pool-independent: a clip with a non-empty list is never shown by this preset, so the pool build can
    drop it. Reads the precompute() sidecars (computes them when missing). -> {pool_id or path: [reasons]}"""
    import planners as PL
    shots = _dedupe(list(shots or []))
    if not shots:
        return {}
    real = list(shots)
    while len(shots) < 3:
        # the planner needs >= 3 shots; a pool build that adds 1-2 clips gates them padded with copies (verdicts per path)
        shots.append(dict(real[len(shots) % len(real)], pool_id=f'_pad{len(shots)}'))
    if song:
        A = _analysis(_slowed(song, float(song_speed or 1.0), os.path.abspath(workdir or tempfile.mkdtemp(prefix='cine-gate-'))))
    else:
        A = PL.synthetic_analysis(dur=200.0, bpm=62.0)
    pr = PL.plan_cinematic_slowmo(A, shots, dict(seed=0, aspect='9:16', fps=FPS, cine_body_s=30.0, cine_intro_s=3.0,
                                                 offline=True, cine_probe=True, type='song'))['_probe']
    by = {os.path.normcase(os.path.abspath(s['path'])): s for s in real}
    out = {}
    for p, r in pr['rejects'].items():
        s = by.get(os.path.normcase(os.path.abspath(p))) or {}
        out[s.get('pool_id') or p] = list(r)
    return out


def _lyrics_available(title, artist):
    """Render-time check that the song has lyrics (LRCLIB via lyrics.fetch, nothing stored: LYRICS_NO_STORE)."""
    if not title or os.environ.get('CINE_LYRICS', '1') == '0':
        return False
    try:
        import lyrics as LY
        return bool(LY.fetch(title, artist or '').get('lines'))
    except Exception as e:  # noqa: BLE001
        log(f'lyrics fetch failed ({type(e).__name__}): no text')
        return False


def _redact(plan):
    """The plan as written to disk after the render: no lyric text (owner: lyrics never stored)."""
    p = json.loads(json.dumps(plan))
    for o in p.get('overlays') or []:
        if 'text' in o:
            o['text'] = '' if not o['text'] else '<redacted>'
        if isinstance(o.get('words'), list):
            o['words'] = [dict(w, text='<redacted>') if isinstance(w, dict) else '<redacted>' for w in o['words']]
        for k in ('items', 'lines'):
            if isinstance(o.get(k), list):
                o[k] = [dict(w, text='<redacted>') if isinstance(w, dict) and 'text' in w else w for w in o[k]]
    lt = (p.get('planner') or {}).get('lyric_text')
    if isinstance(lt, dict):
        for k in ('words', 'xcheck', 'card_line'):
            lt.pop(k, None)
    return p


def _free_models():
    """Drop the models the planning step loaded (Demucs / wav2vec / faster-whisper for the lyrics, SigLIP / face
    nets for the portrait QA) before the engine subprocess starts: the parent kept ~1.5 GB resident and the pair went
    over the 3 GB budget (owner resource rule)."""
    import gc
    for name, keys in (('lyrics', ('_DEMUCS', '_W2V', '_FW')), ('portrait', ('_FACE', '_SIG', '_SIG2'))):
        m = sys.modules.get(name)
        if m is None:
            continue
        for k in keys:
            v = getattr(m, k, None)
            if isinstance(v, dict):
                v.clear()
            elif v is not None:
                setattr(m, k, None)
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache() if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001
        pass


def render_vibe(song, shots, out, *, aspect='9:16', length=(30.0, 50.0), seed=0, workdir=None, song_speed=0.95,
                title=None, artist=None, song_original=None, section=None):
    t_start = time.time()
    res = dict(ok=False, video=out, duration=0.0, used=[], plan=None, render_s=0.0)
    try:
        import planners as PL
        rng = random.Random(seed)
        wd = os.path.abspath(workdir or tempfile.mkdtemp(prefix='cine-vibe-'))
        out = os.path.abspath(out)
        res['video'] = out
        os.makedirs(wd, exist_ok=True)
        shots = _dedupe(list(shots or []))
        if len(shots) < 6:
            res['reason'] = f'only {len(shots)} usable shots'
            return res
        sp = _slowed(song, float(song_speed or 1.0), wd)
        A = _analysis(sp)
        # owner 2026-10-12: the on-screen words are the SONG'S LYRICS (sung words in time with the vocal, the preset's
        # words-on-black intro as _sX2), never the vibe / title name; no lyrics found = no text at all
        os.environ.setdefault('LYRICS_NO_STORE', '1')
        import re as _re
        title = _re.sub(r'\s*[\(\[][^)\]]*(slowed|reverb|sped|remix|ver)[^)\]]*[\)\]]', '', str(title or ''), flags=_re.I).strip() or None
        lyr = _lyrics_available(title, artist)
        log('lyrics: ' + ('found (render-time, not stored)' if lyr else 'none -> no text'))
        toks = []
        intro = 5.5 if lyr else 0.0
        # loudness: _sX2 (the 'blue' reference) masters at -6.9 LUFS integrated; -7.5 with a -1.8 dB ceiling keeps the
        # AAC true peak under -1 dBTP (the limiter, not the bus compressor, catches the hits)
        base_opts = dict(seed=seed, aspect=aspect, fps=FPS, cine_intro_s=intro or 0.05, work_dir=wd, offline=not lyr,
                         song=title if lyr else None, artist=artist or '',
                         type='song', cine_lufs=-6.8, cine_ceiling=-2.3)
        clean_audio = os.environ.get('SLOWMO_CLEAN_AUDIO', '1') != '0'
        if clean_audio:
            # owner 2026-10-18/19: slow-mo is clean and smooth - NO built gap / gate / riser / glitch anywhere (3b's 0.45 s gate
            # + reversed riser before the first clip read as a stutter); effects belong to the other edit types
            base_opts['cine_breath'] = 'none'
        if os.environ.get('SLOWMO_MOMENT_CUTS', '1') != '0':
            base_opts['cine_moments'] = True     # cuts on the song's musical moments (planners._cine_moment_cuts)
        if os.environ.get('SLOWMO_HOLD_CUTS', '1') != '0':
            # 2026-10-19 round 2 (owner: never cut while the singer holds a note / word): hold-aware cuts
            # (planners._cine_hold_cuts; falls back to the moment cuts when the analysis fails)
            base_opts['cine_hold_cuts'] = True
        if os.environ.get('SLOWMO_WAVE_CUTS', '1') != '0':
            # 2026-10-19 round 3 (owner, "momentum"): one clip per wave of the sung line, the switch at the next wave's
            # start, the shot count follows the waves (planners._cine_wave_cuts; falls back to the hold cuts)
            base_opts['cine_wave_cuts'] = True
            base_opts['cine_song_speed'] = float(song_speed or 1.0)   # waves at the calibration tempo (music_moments.tempo_scale)
        if os.environ.get('CINE_PIN_PLAN'):
            # re-time an approved edit: its clips / order / entry points, its song window and intro, new cuts only
            with open(os.environ['CINE_PIN_PLAN'], encoding='utf-8') as f:
                pj = json.load(f)
            base_opts['cine_pin'] = dict(music_start=pj['music_start'], duration=pj['duration'],
                                         shots=[dict(path=x['path'], t0=x['t0'], t1=x['t1'], src_in=x['src_in'],
                                                     speed_curve=x['speed_curve'], role=x.get('role')) for x in pj['shots']])
            log(f"pinned clips from {os.environ['CINE_PIN_PLAN']}: {len(pj['shots']) - 1} shots, new cut timing only")
        if section and section.get('hero') is not None:
            # the music pool's analysed hook start (ORIGINAL song seconds) -> slowed-song seconds: the hero hint
            base_opts['cine_hero'] = float(section['hero']) / float(song_speed or 1.0)
            log(f"music pool section hint: hero {section['hero']} s -> {base_opts['cine_hero']:.2f} s slowed")
        # pass 1: the per-clip QA (cached next to each clip) -> how many clips may be shown at all
        pr = PL.plan_cinematic_slowmo(A, shots, dict(base_opts, cine_body_s=30.0, cine_probe=True))['_probe']
        res['clean'] = pr['clean']
        log(f"{pr['clean']} of {pr['n']} clips pass the hard gates (pulse {pr['pulse']} s)")
        long_req = aspect == '16:9' and float(length[1]) >= 80
        # enough clean clips for the minimum length with 2-pulse (short) / 2.6-pulse (long) mean holds
        min_clean = max(10, int(math.ceil((float(length[0]) - intro) / (float(pr['pulse']) * (2.4 if long_req else 1.9)))))
        if pr['clean'] < min_clean:
            res['reason'] = f"pool_low: only {pr['clean']} clips pass the hard gates (need {min_clean})"
            return res
        clean_set = {os.path.normcase(os.path.abspath(p)) for p in pr['paths']}
        shots = [s_ for s_ in shots if os.path.normcase(os.path.abspath(s_['path'])) in clean_set]
        body, long_v, max_shots = _lengths(aspect, length, pr['clean'], float(pr['pulse']), float(A['duration']), intro, rng)
        lo_s, hi_s = float(length[0]), float(length[1])
        for _try in range(3):
            opts = dict(base_opts, cine_body_s=body, cine_loose=long_v, cine_max_shots=max_shots)
            plan = PL.plan_cinematic_slowmo(A, shots, opts)
            D = float(plan['duration'])
            if lo_s - 0.02 <= D <= hi_s + 0.02:
                break
            body += (lo_s + 0.4 - D) if D < lo_s else (hi_s - 0.4 - D)
            log(f'duration {D:.2f} s outside {lo_s}-{hi_s}: body -> {body:.2f} s')
        plan.setdefault('planner', {})['preset'] = 'cinematic_slowmo'
        plan['preset'] = 'cinematic_slowmo'
        plan['watermark'] = None                                 # vibe_edit adds the channel watermark in a final pass
        (plan.get('style_overrides') or {}).pop('watermark', None)
        plan['sfx'] = 'none'                                     # AUDIO_AV: music only
        plan.setdefault('audio', {})['fade_in'] = 0.03           # no click where the song window starts
        sfx_ops = plan.setdefault('song_fx', [])
        lyr_items = 0
        if lyr:
            import lyric_text as LT
            try:
                sm = LT.attach(plan, dict(seed=seed, preset='cinematic_slowmo'), song=title, artist=artist or '',
                               preset='cinematic_slowmo', song_audio=song_original or song, seed=seed)
                lyr_items = len(sm.get('words') or [])
            except Exception as e:  # noqa: BLE001
                log(f'lyric track failed ({type(e).__name__}): no text')
            log(f'lyric words on screen: {lyr_items}')
        if lyr and not lyr_items:
            # lyrics exist but none are sung in this window: re-plan without the words-on-black intro (no empty black)
            log('no sung words in the chosen window: re-planning with no text')
            return render_vibe(song, shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                               song_speed=song_speed, title=None, artist=None, song_original=song_original, section=section)
        if not lyr_items:
            plan['overlays'] = [o for o in plan.get('overlays') or [] if o.get('type') != 'text_on_black']
        if lyr_items and clean_audio and len(plan['shots']) > 1:
            # the words-on-black intro sits a gentle 6 dB under the body and rises smoothly over the last 0.6 s into the
            # first clip: a level change of the song itself, no gate / riser / effect (the judge's no-SFX residual check)
            t_hero = float(plan['shots'][1]['t0'])
            sfx_ops.insert(0, dict(op='fade', t0=0.0, t1=round(max(0.05, t_hero - 0.6), 4), db0=-6.0, db1=-6.0))
            sfx_ops.insert(1, dict(op='fade', t0=round(max(0.05, t_hero - 0.6), 4), t1=round(t_hero + 0.04, 4), db0=-6.0, db1=0.0))
        elif lyr_items and not any(o.get('op') in ('gap_drop', 'breath_riser') for o in sfx_ops) and len(plan['shots']) > 1:
            # _sX2 holds the song ~13 dB down under the words on black and the hero lands on the hit several dB louder.
            # With no lyric timing we never gate the song (a sung word could sit there): the intro plays 7 dB under, a
            # shallow 4 dB breath for 0.12 s, then +3 dB on the hit settling over 0.9 s (song_fx.op_gap_drop)
            t_hero = float(plan['shots'][1]['t0'])
            sfx_ops.insert(0, dict(op='gap_drop', cut=round(t_hero + 0.06, 4), gap_s=0.12, gate_db=-4.0, fade_s=0.03,
                                   return_s=0.02, step_db=3.0, step_tau=0.9, riser=False, pre_db=-7.0))
        plan['clip_audio'] = 'none'
        if float(plan['duration']) < lo_s - 0.5:
            res['reason'] = (f"pool_low: {pr['clean']} clean clips fill only {float(plan['duration']):.1f} s "
                             f"(minimum {lo_s:.0f} s)")
            return res
        errs, warns = PL.check_plan(plan)
        for w in warns[:8]:
            log('plan warning:', w)
        if errs:
            res['reason'] = 'bad plan: ' + '; '.join(errs[:4])
            return res
        base = os.path.splitext(os.path.basename(out))[0]
        pp = os.path.join(wd, base + '.plan.json')
        with open(pp, 'w', encoding='utf-8') as f:
            json.dump(plan, f, indent=1)
        res['plan'] = pp
        by_path = {os.path.normcase(os.path.abspath(s['path'])): s for s in shots}
        used = []
        for sh in plan.get('shots') or []:
            src = by_path.get(os.path.normcase(os.path.abspath(sh.get('path') or ''))) or {}
            pid = src.get('pool_id') or src.get('id') or sh.get('path')
            if pid and pid not in used:
                used.append(pid)
        res['used'] = used
        res['lyric_words'] = lyr_items
        if os.environ.get('CINE_PLAN_ONLY'):
            res.update(ok=True, duration=float(plan['duration']), plan_ready=True, workdir=wd)
            return res
        return run_engine(res, plan, pp, out, wd)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        res['reason'] = f'{type(e).__name__}: {e}'[:600]
        return res
    finally:
        res['render_s'] = round(time.time() - t_start, 1)


def _run_watch(cmd, log_path):
    """run a subprocess, polling the resident memory of its whole process tree -> (returncode, stderr tail, peak GB)"""
    peak = 0.0
    with open(log_path, 'a', encoding='utf-8') as lf:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
        try:
            import psutil
            pp_ = psutil.Process(p.pid)
            while p.poll() is None:
                try:
                    rss = pp_.memory_info().rss + sum(c.memory_info().rss for c in pp_.children(recursive=True))
                    peak = max(peak, rss / 2 ** 30)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.5)
        except ImportError:
            p.wait()
    with open(log_path, encoding='utf-8', errors='replace') as f:
        tail = f.read()[-1500:]
    return p.returncode, tail, round(peak, 2)


def _segments(plan, seg_s):
    """[(a, b)] render ranges of about seg_s seconds, split at shot starts (hard cuts of the plan)"""
    D = float(plan['duration'])
    cuts = sorted({float(sh['t0']) for sh in plan.get('shots') or [] if 0.5 < float(sh.get('t0') or 0) < D - 0.5})
    out, a = [], 0.0
    while D - a > seg_s * 1.4:
        cand = [c for c in cuts if c >= a + 0.6 * seg_s]
        if not cand:
            break
        b = min(cand, key=lambda c: abs(c - (a + seg_s)))
        if D - b < 0.4 * seg_s:
            break
        out.append((a, b))
        a = b
    out.append((a, D))
    return out


def run_engine(res, plan, pp, out, wd):
    """engine.py on a written plan (a subprocess), then the redaction (no lyric text on disk) and the TP guard.
    CINE_SEGMENT_S=N (> 0) renders the video in ~N s parts split at the plan's shot starts, one engine process each (the
    engine's resident memory grows with the takes it has decoded: ~3.4 GB peak for a whole 9:16 short at 1080p work frames),
    then joins the parts (stream copy) and muxes the whole song mix (the engine's audio.wav) once. The peak resident memory
    of the engine process tree is measured either way (res['engine_peak_gb'])."""
    t_r = time.time()
    base = os.path.splitext(os.path.basename(out))[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or '.', exist_ok=True)
    ew = os.path.join(wd, base + '_work')
    os.makedirs(ew, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, 'engine.py'), '--plan', pp, '--out', out, '--work', ew]
    cpus = os.environ.get('CINE_CPUS') or os.environ.get('EDIT_CPUS')
    if cpus:
        cmd += ['--cpus', str(int(cpus))]
    seg_s = float(os.environ.get('CINE_SEGMENT_S') or 0)
    _free_models()
    elog = os.path.join(wd, base + '.engine.log')
    open(elog, 'w').close()
    peak, rc, tail = 0.0, 0, ''
    try:
        segs = _segments(plan, seg_s) if seg_s > 0 else [(0.0, float(plan['duration']))]
        if len(segs) == 1:
            rc, tail, peak = _run_watch(cmd, elog)
        else:
            parts = []
            for i, (a, b) in enumerate(segs):
                po = os.path.join(ew, f'part{i:02d}.mp4')
                c = [x if x != out else po for x in cmd] + ['--start', f'{a:.4f}', '--end', f'{b:.4f}']
                rc, tail, pk = _run_watch(c, elog)
                peak = max(peak, pk)
                log(f'engine part {i + 1}/{len(segs)} {a:.2f}-{b:.2f} s: rc {rc}, peak {pk} GB')
                if rc != 0 or not os.path.exists(po):
                    rc = rc or 1
                    break
                parts.append(po)
            if rc == 0:
                lst = os.path.join(ew, 'parts.txt')
                with open(lst, 'w', encoding='utf-8') as f:
                    f.writelines("file '%s'\n" % os.path.basename(x) for x in parts)
                vid = os.path.join(ew, 'video_only.mp4')
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', 'parts.txt', '-map', '0:v', '-c', 'copy',
                                'video_only.mp4'], check=True, cwd=ew)
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', vid, '-i', os.path.join(ew, 'audio.wav'), '-map', '0:v', '-map', '1:a',
                                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', out], check=True)
                for x in parts + [vid]:
                    try:
                        os.remove(x)
                    except OSError:
                        pass
    finally:
        with open(pp, 'w', encoding='utf-8') as f:
            json.dump(_redact(plan), f, indent=1)            # no lyric text left on disk
    res['engine_peak_gb'] = peak
    if rc != 0 or not os.path.exists(out):
        res['reason'] = 'engine failed: ' + tail[-600:]
        res['ok'] = False
        return res
    _start_guard(out)
    res['true_peak'] = _tp_guard(out)
    d = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out],
                             capture_output=True, text=True).stdout.strip() or 0.0)
    res.update(ok=True, duration=round(d, 3), engine_s=round(time.time() - t_r, 1))
    log(f'engine done in {res["engine_s"]} s, peak resident {peak} GB')
    return res


def render_vibe_isolated(song, shots, out, **kw):
    """render_vibe with the planning (lyrics alignment, SigLIP / face QA: ~2.9 GB resident) in a child process
    that exits before the engine starts, so planning and rendering never hold memory together (owner: < 3 GB)."""
    t0 = time.time()
    wd = os.path.abspath(kw.get('workdir') or tempfile.mkdtemp(prefix='cine-vibe-'))
    os.makedirs(wd, exist_ok=True)
    job = os.path.join(wd, 'plan_job.json')
    with open(job, 'w', encoding='utf-8') as f:
        json.dump({'song': song, 'shots': shots, 'out': out, 'kw': dict(kw, workdir=wd)}, f)
    code = ('import json, sys; sys.path.insert(0, %r); import cine_vibe as C; j = json.load(open(%r, encoding="utf-8")); '
            'r = C.render_vibe(j["song"], j["shots"], j["out"], **j["kw"]); '
            'json.dump(r, open(%r, "w", encoding="utf-8"))') % (HERE, job, job + '.res')
    env = dict(os.environ, CINE_PLAN_ONLY='1')
    p = subprocess.run([sys.executable, '-c', code], env=env)
    try:
        with open(job + '.res', encoding='utf-8') as f:
            res = json.load(f)
    except (OSError, ValueError):
        return {'ok': False, 'video': out, 'reason': f'planning process failed (exit {p.returncode})', 'used': []}
    if not res.get('ok') or not res.get('plan_ready'):
        return res
    with open(res['plan'], encoding='utf-8') as f:
        plan = json.load(f)
    res = run_engine(res, plan, res['plan'], os.path.abspath(out), res.get('workdir') or wd)
    res['render_s'] = round(time.time() - t0, 1)
    return res


def _load_shots(paths):
    out = []
    for p in paths:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        ss = d.get('shots') if isinstance(d, dict) else d
        for s in ss or []:
            s = dict(s)
            s.setdefault('pool_id', os.path.splitext(os.path.basename(str(s.get('path'))))[0] + '@' +
                         os.path.basename(os.path.dirname(str(s.get('path')))))
            out.append(s)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--song', required=True)
    ap.add_argument('--shots', action='append', default=[])
    ap.add_argument('--pool', action='append', default=[], help='preset/vibe of a vibe pool (vibe_pool.fresh_clips)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--aspect', default='9:16')
    ap.add_argument('--length', type=float, nargs=2, default=None)
    ap.add_argument('--speed', type=float, default=0.95)
    ap.add_argument('--title')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--work')
    a = ap.parse_args()
    length = tuple(a.length) if a.length else ((90.0, 180.0) if a.aspect == '16:9' else (30.0, 50.0))
    shots = _load_shots(a.shots)
    for pv in a.pool:
        import vibe_pool as VP
        shots += VP.fresh_clips(*pv.split('/'))
    r = render_vibe(a.song, shots, a.out, aspect=a.aspect, length=length, seed=a.seed, workdir=a.work,
                    song_speed=a.speed, title=a.title)
    print(json.dumps(r, indent=1))
    sys.exit(0 if r['ok'] else 1)


if __name__ == '__main__':
    main()
