#!/usr/bin/env python3
"""
Production entry point for the two vibe-pool edit types (pool format: core/edit/refs/pool-format.md):

    python core/edit/vibe_edit.py --type sound|slowmo --out DIR [--landscape-long] [--vibe V] [--song SOUND_ID]
                                  [--seed N] [--no-mark] [--threads N]

  sound   the sound edit (core/edit/sound_edit.render_vibe): every shot adds one hit from its own anime audio on a song onset
  slowmo  the cinematic slow-mo edit (core/edit/cine_vibe.render_vibe): crisp holds, ~1 s pulses, optical-flow slow motion

It picks a READY vibe of the type (core/edit/vibe_pool.py status), a song not in cooldown, renders the 9:16 Short (30-50 s)
and with --landscape-long the 16:9 long version (1.5-3 min, same vibe + song, more clips; posted a week after the Short),
burns the channel watermark (@zeroyenotaku, env EDIT_WATERMARK) and, unless --no-mark, marks every clip shown and the song
as used in memory/edit-pool-used.json. CPU only by default (EDIT_GPU=0 is set unless the caller set EDIT_GPU).

Writes DIR/<type>-<vibe>-short.mp4, DIR/<type>-<vibe>-long.mp4 and DIR/result.json:
  {"ok", "type", "preset", "vibe", "song", "song_title", "song_artist", "short", "long", "duration_short", "duration_long",
   "clips_short", "clips_long", "clips_used", "sound_used", "render_s": {...}, "licence", "reason"}
The last stdout line is the same JSON. Exit 0 ok, 2 pool too low, 1 other failure.
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('EDIT_GPU', '0')

import vibe_pool as VP  # noqa: E402
import watermark as WM  # noqa: E402

SHORT_LEN = (30.0, 50.0)
LONG_LEN = (90.0, 180.0)


def log(*a):
    print('[vibe_edit]', *a, file=sys.stderr, flush=True)


def pick_vibe(kind, forced, rng):
    st = VP.status()
    cands = [(k.split('/')[1], v) for k, v in st.items() if k.startswith(kind + '/')]
    if forced:
        cands = [c for c in cands if c[0] == forced]
        if not cands:
            raise SystemExit(f'unknown vibe {forced}')
        return cands[0][0], cands[0][1]
    ready = [c for c in cands if c[1]['ready']]
    if not ready:
        return None, {k: v for k, v in cands}
    # weight by runs left so the pools drain evenly
    w = [max(1, c[1]['runs_left']) for c in ready]
    v = rng.choices(ready, weights=w)[0]
    return v[0], v[1]


VERIFIED_P = os.path.join(HERE, 'assets', 'sound-song-verified.json')


def sound_history(content_type='sound-edit'):
    """[{id, at}] of the sound (or slow-mo) edits posted (memory/meme-history.json, oldest first): the music pool's cooldown input."""
    mem = os.environ.get('MEMORY_DIR') or os.path.join(os.path.dirname(os.path.dirname(HERE)), 'memory')
    try:
        with open(os.path.join(mem, 'meme-history.json'), encoding='utf-8') as f:
            ups = json.load(f).get('uploads') or []
    except (OSError, ValueError):
        return []
    return [{'id': u.get('musicPoolId') or u.get('song'), 'at': u.get('postedAt')} for u in ups
            if u.get('contentType') == content_type and not u.get('long')]


def music_pool_song(forced, rng):
    import music_pool as MP
    if forced:
        rec = next((x for x in MP.load_pool()['songs'] if x['id'] == forced), None)
        if not rec:
            return None
        path = MP.fetch_audio(rec)
        sec = rec.get('section') or {}
        s = {'id': rec['id'], 'song': rec['song'], 'artist': rec['artist'], 'audio_path': path,
             'section_start': sec.get('start'), 'section_end': sec.get('end'), 'tempo': rec.get('tempo')}
    else:
        pool_path = None
        if os.environ.get('SOUND_MUSIC_VERIFIED_ONLY', '1') != '0':
            # only songs whose pool section rendered at the approved (sound_11b) quality on CI: core/edit/assets/sound-song-verified.json
            # (written after a judge_sound PASS of a .github/workflows/sound-song-verify.yml render); none verified = the vibe songs
            ver = {k for k, v in (VP.read_json(VERIFIED_P, {}) or {}).get('songs', {}).items() if v.get('pass')}
            pool = MP.load_pool()
            pool['songs'] = [x for x in pool['songs'] if x['id'] in ver]
            if not pool['songs']:
                log('music pool: no verified song yet: the vibe songs')
                return None
            import tempfile
            fd, pool_path = tempfile.mkstemp(suffix='.json')
            os.close(fd)
            with open(pool_path, 'w', encoding='utf-8') as f:
                json.dump(pool, f)
        try:
            s = MP.pick_song(sound_history(), pool_path=pool_path, fetch=True, rng=rng)
        finally:
            if pool_path:
                os.remove(pool_path)
    if not s or not s.get('audio_path') or not os.path.exists(s['audio_path']):
        log('music pool: no audio for', (s or {}).get('id'))
        return None
    log(f"music pool: {s['id']} section {s.get('section_start')}-{s.get('section_end')} s")
    return {'id': s['id'], 'title': s['song'], 'artist': s['artist'], 'file': s['audio_path'], 'music_pool': True,
            'section': [s.get('section_start'), s.get('section_end')], 'tempo': s.get('tempo')}


def slowmo_pool_song(forced, rng):
    """the slow-mo song from core/edit/slowmo_music.py (emotional / chill famous songs, slowed; section = the analysed hook):
    never the last song, 30-day cooldown, Thompson sampling; audio fetched now (yt-dlp, cached)"""
    import music_pool as MP
    import slowmo_music as SM
    if forced:
        # --song <pool id> (its slowed version) or <pool id>@v<speed> (e.g. jvke_golden_hour@v1 = the regular version)
        base, sp = SM.split_id(forced)
        rec = next((x for x in SM.load_pool()['songs'] if x['id'] == base), None)
        if not rec:
            return None
        sp = sp if sp is not None else SM.versions(rec)[-1]
        s = {'id': rec['id'], 'version_id': SM.version_id(rec['id'], sp), 'song': rec['song'], 'artist': rec['artist'],
             'audio_path': MP.fetch_audio(rec), 'section_start': rec['section']['start'], 'section_end': rec['section']['end'],
             'section_hero': rec['section'].get('hero'), 'speed': sp}
    else:
        s = SM.pick(sound_history('slowmo-edit'), fetch=True, rng=rng)
    if not s or not s.get('audio_path') or not os.path.exists(s['audio_path']):
        log('slow-mo music pool: no audio for', (s or {}).get('id'))
        return None
    log(f"slow-mo music pool: {s.get('version_id') or s['id']} x{s['speed']} section {s.get('section_start')}-{s.get('section_end')} hero {s.get('section_hero')}")
    # id = song + version ('id@v0.85' / 'id@v1'): the history / cooldown unit (owner 2026-10-18: regular AND slowed versions)
    return {'id': s.get('version_id') or s['id'], 'title': s['song'], 'artist': s['artist'], 'file': s['audio_path'], 'music_pool': True,
            'section': [s.get('section_start'), s.get('section_end')], 'hero': s.get('section_hero'),
            'treatment': {'speed': s['speed']}}


def pick_song(kind, vibe, forced, rng):
    # MUSIC POOL (2026-10-17, core/edit/music_pool.py + core/edit/assets/sound-music-pool.json): with SOUND_MUSIC_POOL=1 the
    # sound edit's song AND its section come from the pool (Thompson sampling, never the last song, 30-day cooldown); the audio
    # is fetched at render time (yt-dlp, cached in output/music-pool/audio). --song <pool id> forces a pool song; a vibe song id
    # (s_...) or any music-pool failure falls back to the vibe's own songs below (the planner then picks the section).
    if kind == 'sound' and os.environ.get('SOUND_MUSIC_POOL') == '1' and not (forced and str(forced).startswith('s_')):
        try:
            s = music_pool_song(forced, rng)
            if s:
                return s
        except Exception as e:  # noqa: BLE001
            log('music pool failed, using the vibe songs:', repr(e)[:200])
    if kind == 'slowmo' and os.environ.get('SLOWMO_MUSIC_POOL') == '1' and not (forced and str(forced).startswith('s_')):
        try:
            s = slowmo_pool_song(forced, rng)
            if s:
                return s
        except Exception as e:  # noqa: BLE001
            log('slow-mo music pool failed, using the vibe songs:', repr(e)[:200])
    songs = VP.song_ok(kind, vibe)
    if forced:
        songs = [s for s in songs if s['id'] == forced] or [s for s in VP.load_manifest(kind, vibe, 'sounds')['items']
                                                            if s['id'] == forced]
    if not songs:
        return None
    s = dict(rng.choice(songs))
    entry = VP.load_vibes()['sounds'].get(s['id'], s)
    path = VP.resolve_sound(s['id'], entry, VP.vibe_dir(kind, vibe))
    if not path:
        return None
    s['file'] = path
    return s


def is_letterbox(plan_path):
    try:
        with open(plan_path, encoding='utf-8') as f:
            pl = json.load(f)
    except Exception:  # noqa: BLE001
        return False
    dv = pl.get('deliver') or {}
    return str(pl.get('frame') or '').startswith('letterbox') or (isinstance(dv, dict) and 'letterbox' in str(dv.get('mode')))


def is_rot90(plan_path):
    try:
        with open(plan_path, encoding='utf-8') as f:
            pl = json.load(f)
    except Exception:  # noqa: BLE001
        return False
    dv = pl.get('deliver') or {}
    return str(pl.get('frame') or '').startswith('rot90') or (isinstance(dv, dict) and str(dv.get('mode')).startswith('rot90'))


def render(kind, song, shots, out, aspect, length, seed, workdir, vibe_def):
    os.environ.setdefault('EDIT_NO_SHAKE', '1')          # owner 2026-10-13: no shake / wiggle / jitter in the sound and slow-mo edits (default)
    ms = (song.get('section') or [None])[0] if kind == 'sound' else None     # the music pool's section start (song seconds)
    if kind == 'sound':
        import sound_edit as M
        if aspect != '9:16':
            return M.render_vibe(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                                 theme=vibe_def.get('theme'), title=song.get('title'))
        # owner 2026-10-12: a REAL vertical Short. The edit is planned and rendered as its 16:9 master (sync, hits and
        # mix untouched), then core/edit/reframe.py crops every shot to 9:16 around its subject (face / action centre,
        # one eased move per shot). The references deliver rot90 / letterbox; the owner overruled both.
        if os.environ.get('SOUND_VERTICAL_NATIVE', '1') == 'rot90':
            # owner 2026-10-13: the reference's own layout is fine: the full 16:9 frame turned 90 deg into 9:16 (whole action, no crop upscale);
            # the watermark is burned upright in the master's orientation by watermark.burn(rot90=True)
            os.environ['SOUND_DELIVER'] = 'rot90'
            return M.render_vibe(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                                 theme=vibe_def.get('theme'), title=song.get('title'), music_start=ms)
        if os.environ.get('SOUND_VERTICAL_NATIVE', '1') != '0':
            # judge 2026-10-13: crop the 9:16 window from the SOURCE-resolution clips, compose the vertical natively at 1080x1920
            os.environ['SOUND_DELIVER'] = 'vertical'
            return M.render_vibe(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                                 theme=vibe_def.get('theme'), title=song.get('title'))
        os.environ['SOUND_DELIVER'] = 'master'
        master = os.path.splitext(out)[0] + '_master.mp4'
        r = M.render_vibe(song['file'], shots, master, aspect=aspect, length=length, seed=seed, workdir=workdir,
                          theme=vibe_def.get('theme'), title=song.get('title'))
        if not r.get('ok'):
            return r
        import reframe as RF
        t0 = time.time()
        RF.reframe(r['video'], r['plan'], out, threads=int(os.environ.get('EDIT_FFMPEG_THREADS') or 2))
        r.update(video=out, master=master, reframe_s=round(time.time() - t0, 1))
        return r
    import cine_vibe as M
    sec = None
    if song.get('music_pool') and song.get('hero') is not None:
        sec = {'start': (song.get('section') or [None])[0], 'hero': song['hero'], 'end': (song.get('section') or [None, None])[1]}
    return M.render_vibe_isolated(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                         song_speed=float((song.get('treatment') or {}).get('speed') or 0.95), title=song.get('title'), artist=song.get('artist'),
                         section=sec)


def main():
    ap = argparse.ArgumentParser(description='sound / slow-mo vibe edits: Short + optional landscape long')
    ap.add_argument('--type', required=True, choices=['sound', 'slowmo'])
    ap.add_argument('--out', required=True)
    ap.add_argument('--landscape-long', action='store_true')
    ap.add_argument('--long-only', action='store_true', help='only the landscape long (tests)')
    ap.add_argument('--vibe')
    ap.add_argument('--song', help='sound id from core/edit/vibes.json (default: random, not in cooldown)')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--no-mark', action='store_true', help='do not mark clips / song used (tests, owner samples)')
    ap.add_argument('--threads', type=int, default=0, help='ffmpeg threads for the watermark pass (0 = auto)')
    a = ap.parse_args()
    kind = a.type
    seed = a.seed if a.seed is not None else int(time.time()) % 100000
    rng = random.Random(seed)
    os.environ.setdefault('CLIP_ROTATION_SEED', str(seed))     # the same candidates on every get_shots() of this run
    os.makedirs(a.out, exist_ok=True)
    res = {'ok': False, 'type': kind, 'preset': VP.load_vibes()['presets'][kind]['preset_id'], 'seed': seed,
           'short': None, 'long': None, 'render_s': {}}
    res_path = os.path.join(a.out, 'result.json')

    def done(code):
        VP.write_json(res_path, res)
        print(json.dumps(res))
        sys.exit(code)

    vibe, st = pick_vibe(kind, a.vibe, rng)
    if not vibe:
        res.update(reason='pool_low', pools=st)
        log('no vibe is ready:', json.dumps(st))
        done(2)
    vdef = VP.load_vibes()['presets'][kind]['vibes'][vibe]
    song = pick_song(kind, vibe, a.song, rng)
    if not song:
        res.update(vibe=vibe, reason='no_song')
        done(2)
    res.update(vibe=vibe, song=song['id'], song_title=song.get('title'), song_artist=song.get('artist'),
               sound_used=song['id'], licence=VP.load_vibes().get('licence_note'),
               music_pool=bool(song.get('music_pool')), song_section=song.get('section'))
    def get_shots():
        sh = VP.fresh_clips(kind, vibe)
        for xv in [v for v in os.environ.get('SOUND_EXTRA_VIBES', '').split(',') if v and v != vibe and kind == 'sound']:
            sh += VP.fresh_clips(kind, xv)          # 2026-10-15: the eye-approved crisp hits (snap / drop) join an impact short
        return sh

    # Clip cooldown (sound clips are reused; the last SOUND_COOLDOWN_VIDEOS videos' clips are skipped). With the
    # eye-approved hit pool (SOUND_ALLOW_ONLY=1, ~60 pins, ~40 per short) a hard cooldown would starve every second
    # short, so the cooldown is relaxed step by step (3 -> 2 -> 1 -> 0) until enough pinned clips are free
    # (SOUND_RELAX_MIN_PINS, default 45) and again when the render reports pool_low. SOUND_COOLDOWN_RELAX=0 = hard cooldown.
    cooldowns = [VP.sound_cooldown(kind)]
    if (kind == 'sound' and os.environ.get('SOUND_COOLDOWN_RELAX', '1') != '0') or kind == 'slowmo':
        cooldowns += list(range(cooldowns[0] - 1, -1, -1))     # slow-mo: pool_low -> the last video's clips come back

    def pins(sh):
        try:
            import sound_edit as SE
            SE.hit_allow('x', 0)
            return sum(1 for s in sh if s.get('pool_id') in (SE._ALLOW or {}))
        except Exception:  # noqa: BLE001
            return len(sh)

    need_pins = int(os.environ.get('SOUND_RELAX_MIN_PINS', '45'))
    if kind == 'sound' and len(cooldowns) > 1:
        os.environ['SOUND_COOLDOWN_VIDEOS'] = '0'
        need_pins = min(need_pins, pins(get_shots()))
        while len(cooldowns) > 1:
            os.environ['SOUND_COOLDOWN_VIDEOS'] = str(cooldowns[0])
            n = pins(get_shots())
            if n >= need_pins:
                break
            log(f'cooldown {cooldowns[0]}: {n} pinned clips free (< {need_pins}): relaxing')
            cooldowns.pop(0)
        os.environ['SOUND_COOLDOWN_VIDEOS'] = str(cooldowns[0])
    shots = get_shots()
    res['cooldown_videos'] = cooldowns[0]
    if kind in VP.REUSE_PRESETS:
        import clip_rotation as CR
        rc = CR.config(kind)
        rot = {k: v for k, v in VP.LAST_ROTATION.items() if k.startswith(kind + '/')}
        res['clip_rotation'] = {'choice': rot}
        if rc.get('hard') and rot and all(v.get('mode') == 'hard_cap' for v in rot.values()):
            # owner 2026-10-22 (sound): variety AND good matches; too few fresh clips for <= cap reuse -> no sound video today
            fresh = sum(int(v.get('fresh') or 0) for v in rot.values())
            if fresh < int(rc.get('min_fresh') or 0):
                res.update(reason=f'pool_too_small: {fresh} fresh clips (< {int(rc["min_fresh"])}) for <= {float(rc["cap"]):.0%} reuse '
                                  f'of the last {int(rc["window"])} {kind} videos')
                log(res['reason'])
                done(2)
    log(f'{kind}/{vibe}: {len(shots)} fresh clips, song {song["id"]} ({song["file"]}), seed {seed}')
    work = os.path.join(a.out, f'work-{kind}-{vibe}')
    os.makedirs(work, exist_ok=True)
    used_short, used_long = [], []
    t_all = time.time()
    try:
        if not a.long_only:
            raw = os.path.join(work, 'short_raw.mp4')
            t0 = time.time()
            r = render(kind, song, shots, raw, '9:16', SHORT_LEN, seed, os.path.join(work, 'short'), vdef)
            while not r.get('ok') and (r.get('reason') == 'pool_low' or (kind == 'slowmo' and str(r.get('reason') or '').startswith('pool_low'))) and len(cooldowns) > 1:
                cooldowns.pop(0)
                os.environ[VP.COOLDOWN_ENV[kind][0]] = str(cooldowns[0])
                # clip rotation (core/edit/clip_rotation.py): a pool_low retry hands the planner more candidates
                os.environ['CLIP_ROTATION_LEVEL'] = str(int(os.environ.get('CLIP_ROTATION_LEVEL') or 0) + 1)
                shots = get_shots()
                res['cooldown_videos'] = cooldowns[0]
                log(f'pool_low: retry with clip cooldown {cooldowns[0]} ({len(shots)} clips)')
                r = render(kind, song, shots, raw, '9:16', SHORT_LEN, seed, os.path.join(work, 'short'), vdef)
            res['render_s']['short'] = round(time.time() - t0, 1)
            if not r.get('ok'):
                res.update(reason=r.get('reason') or 'short_failed', short_report=r)
                done(2 if str(r.get('reason') or '').startswith(('pool_low', 'pool_too_small')) else 1)
            final = os.path.join(a.out, f'{kind}-{vibe}-short.mp4')
            t0 = time.time()
            WM.burn(r['video'], final, threads=a.threads, rot90=is_rot90(r.get('plan')),
                    letterbox=is_letterbox(r.get('plan')))
            res['render_s']['watermark_short'] = round(time.time() - t0, 1)
            used_short = list(r.get('used') or [])
            res.update(short=final, duration_short=r.get('duration'), clips_short=used_short, plan_short=r.get('plan'))
            if kind in VP.REUSE_PRESETS:
                # owner 2026-10-20: reuse only here and there -> the reuse share of every video is logged (result.json)
                import clip_rotation as CR
                rep = CR.report(used_short, CR.history(VP.load_ledger(), kind), int(CR.config(kind)['window']))
                res['clip_rotation'] = dict(rep, choice={k: v for k, v in VP.LAST_ROTATION.items()})
                log(f"clip rotation: {rep['reused']} of {rep['shots']} shots reused from the last {rep['window']} {kind} videos "
                    f"({rep['reuse_share']:.0%}; from the previous video {rep['from_last_video']:.0%})")
        if a.landscape_long or a.long_only:
            raw = os.path.join(work, 'long_raw.mp4')
            t0 = time.time()
            r = render(kind, song, shots, raw, '16:9', LONG_LEN, seed + 1, os.path.join(work, 'long'), vdef)
            res['render_s']['long'] = round(time.time() - t0, 1)
            if r.get('ok'):
                final = os.path.join(a.out, f'{kind}-{vibe}-long.mp4')
                t0 = time.time()
                WM.burn(r['video'], final, threads=a.threads)
                res['render_s']['watermark_long'] = round(time.time() - t0, 1)
                used_long = list(r.get('used') or [])
                res.update(long=final, duration_long=r.get('duration'), clips_long=used_long, plan_long=r.get('plan'))
            else:
                res.update(long_error=r.get('reason') or 'long_failed')
                log('long version failed:', r.get('reason'))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        res.update(reason=f'{type(e).__name__}: {e}'[:500])
        done(1)
    res['render_s']['total'] = round(time.time() - t_all, 1)
    res['clips_used'] = sorted(set(used_short) | set(used_long))
    res['ok'] = bool(res.get('short') or res.get('long'))
    if res['ok'] and not a.no_mark:
        if used_short:
            VP.mark_used(kind, vibe, used_short, sound_id=song['id'], video=res.get('short') or '', role='short')
        extra = [c for c in used_long if c not in set(used_short)]
        if extra:
            VP.mark_used(kind, vibe, extra, sound_id=song['id'], video=res.get('long') or '', role='long')
        VP.write_index()
    done(0 if res['ok'] else 1)


if __name__ == '__main__':
    main()
