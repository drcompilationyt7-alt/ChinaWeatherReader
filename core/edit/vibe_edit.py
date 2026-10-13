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


def pick_song(kind, vibe, forced, rng):
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
    if kind == 'sound':
        import sound_edit as M
        if aspect != '9:16':
            return M.render_vibe(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                                 theme=vibe_def.get('theme'), title=song.get('title'))
        # owner 2026-10-12: a REAL vertical Short. The edit is planned and rendered as its 16:9 master (sync, hits and
        # mix untouched), then core/edit/reframe.py crops every shot to 9:16 around its subject (face / action centre,
        # one eased move per shot). The references deliver rot90 / letterbox; the owner overruled both.
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
    return M.render_vibe_isolated(song['file'], shots, out, aspect=aspect, length=length, seed=seed, workdir=workdir,
                         song_speed=float((song.get('treatment') or {}).get('speed') or 0.95), title=song.get('title'), artist=song.get('artist'))


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
               sound_used=song['id'], licence=VP.load_vibes().get('licence_note'))
    shots = VP.fresh_clips(kind, vibe)
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
            res['render_s']['short'] = round(time.time() - t0, 1)
            if not r.get('ok'):
                res.update(reason=r.get('reason') or 'short_failed', short_report=r)
                done(2 if r.get('reason') == 'pool_low' else 1)
            final = os.path.join(a.out, f'{kind}-{vibe}-short.mp4')
            t0 = time.time()
            WM.burn(r['video'], final, threads=a.threads, rot90=is_rot90(r.get('plan')),
                    letterbox=is_letterbox(r.get('plan')))
            res['render_s']['watermark_short'] = round(time.time() - t0, 1)
            used_short = list(r.get('used') or [])
            res.update(short=final, duration_short=r.get('duration'), clips_short=used_short, plan_short=r.get('plan'))
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
