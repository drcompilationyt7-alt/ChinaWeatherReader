"""
HIT-FIRST mining of the sound pools (pool growth, 2026-10-12).

Why: library.py picks the best-looking windows of a source; only ~9 % of the sound clips it keeps have a visible cause for their
sound (pool_visible.py stamp: 20 hits in 237 clips), so growing the pool through it alone would need thousands of sources. Here the
order is reversed, on sources that are ALREADY downloaded (the library's cache $EDIT_CACHE/src/*__all.mkv, or any local video):

  scan   per source: the audio's sharp transients (core/edit/sound_edit.py `_sfx_candidates` = the production hit detector, with
         align.sfx_frames) -> scene cuts around each -> sound_visible.measure on the SOURCE (motion onset / contact / spark within
         +-2 frames@24, big enough, inside one 9:16 window) -> SigLIP source tag (gun / blade / punch / glass / mask ...) ->
         sound_visible.gate(m, tags) = the production visible-source gate. Only moments that pass are kept.
  cut    a mini source (<= 3 s, scene-bounded, AAC kept) around every kept moment + a sidecar (source id, offset, tag).
  grow   (scan + cut) -> library.py --local on the mini sources (ALL the library's gates run: subtitles / text, watermark, nudity,
         minors, hentai, dialogue, sharpness, aesthetics, colour) -> real source id / times restored -> vibe_pool.import_shots
         (dedupe vs the live pool, the used ledger, the blocklist) -> pool_visible.stamp (the production gate again, per clip).

    python core/pools/hit_scan.py grow --work DIR [--src GLOB ...] [--max-per-source 10] [--limit-sources N] [--budget-min M] [--gate]
    python core/pools/hit_scan.py scan|cut|import --work DIR ...
--gate: start (and keep running) only while >= 4 GB RAM is free and the CPU is under 60 % (local runs on the owner's PC); IDLE
priority and 2 threads are set by the caller (lowprio.py) or --lowprio.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(ROOT, 'core', 'edit'), os.path.join(ROOT, 'core', 'meme'), HERE):
    sys.path.insert(0, p)

CHUNK_S = 150.0
SUB_RE = re.compile(r'\b(eng(lish)?[ .-]?sub(s|bed|titles?)?|subbed|subtitles?|sub(s)? ?(on|ita|esp|indo)|hardsub|cc)\b|\[sub\]|\(sub\)', re.I)
SKIP_RE = re.compile(r'twixtor|creditless|ncop|nced|op|ed|opening|ending|ost|aesthetic|cute moments|romantic|amv|reaction|tutorial', re.I)
ACTION_RE = re.compile(r'fight|battle|gun|sword|clash|vs|versus|combat|shoot|slash|punch|kick|mask|sakuga|action|reload|duel|katana|sheath|slap|slam|glass|break|shot|sound', re.I)
TAG_VIBE = {'clap': 'crisp', 'object': 'crisp', 'chain': 'crisp'}       # everything else -> impact


def log(*a):
    print('[hit_scan]', *a, file=sys.stderr, flush=True)


def edit_cache():
    import tempfile
    return os.environ.get('EDIT_CACHE') or os.path.join(tempfile.gettempdir(), 'edit-cache')


def read_json(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return d


def write_json(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    os.replace(p + '.tmp', p)


# ------------------------------------------------------------------ resource gate (owner's PC)

def resources_ok(min_gb=4.0, max_cpu=60.0):
    try:
        import psutil
    except ImportError:
        return True
    return psutil.virtual_memory().available >= min_gb * 2 ** 30 and psutil.cpu_percent(interval=2.0) < max_cpu


_STARTED = {'ok': False}


def wait_resources(max_wait_min=240):
    """Start condition (>= 4 GB free RAM, CPU < 60 %) once per process; afterwards only a floor (>= 1 GB free, CPU < 90 %), so a run
    that already holds its memory is not starved by another process that arrived later."""
    t0 = time.time()
    while not (resources_ok(1.0, 90.0) if _STARTED['ok'] else resources_ok()):
        if time.time() - t0 > max_wait_min * 60:
            return False
        log('waiting for >= 4 GB free RAM and CPU < 60 % ...')
        time.sleep(60)
    _STARTED['ok'] = True
    return True


# ------------------------------------------------------------------ sources

def source_files(globs):
    out = []
    pool = os.environ.get('EDIT_POOL_DIR') or os.path.join(ROOT, 'output', 'edit-pools')
    default = [os.path.join(pool, '_src', '*', '*.mp4'), os.path.join(pool, '_src', '*', '*.mkv'),
               os.path.join(edit_cache(), 'sound_src', '*.mkv'), os.path.join(edit_cache(), 'src', '*__all.mkv')]
    seen = set()
    for g in globs or default:
        for p in sorted(glob.glob(g)):
            sid = os.path.basename(p).split('__')[0].rsplit('.', 1)[0]
            if sid not in seen:
                seen.add(sid)
                out.append(p)
    return [p for p in out if os.path.isfile(p) and not p.endswith(('.json', '.part'))]


def source_meta(path):
    sid = os.path.basename(path).split('__')[0].rsplit('.', 1)[0]
    info = read_json(os.path.join(os.path.dirname(path), sid + '.info.json'), None) or read_json(os.path.join(edit_cache(), 'info', sid + '.json'), {})
    return {'source_id': sid, 'title': info.get('title') or sid, 'channel': info.get('channel') or info.get('uploader') or '',
            'views': info.get('view_count'), 'tags': (info.get('tags') or [])[:12]}


def title_ok(meta):
    txt = f"{meta['title']} {' '.join(meta.get('tags') or [])}"
    if SUB_RE.search(meta['title']):
        return False, 'subtitled title'
    if SKIP_RE.search(meta['title']) and not re.search(r'fight|battle|gunfight|duel|clash', meta['title'], re.I):
        return False, 'not a hit source (twixtor / OP / ED / AMV ...)'
    if re.search(r'\b(hentai|nsfw|18\+|uncensored|ecchi compilation)\b', txt, re.I):
        return False, 'explicit title'
    return True, None


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate:format=duration',
                        '-of', 'json', path], capture_output=True, text=True)
    d = json.loads(r.stdout or '{}')
    s = (d.get('streams') or [{}])[0]
    n, k = (s.get('r_frame_rate') or '24/1').split('/')
    return int(s.get('width') or 0), int(s.get('height') or 0), float(n) / max(1.0, float(k)), float((d.get('format') or {}).get('duration') or 0)


def scene_cuts(path, t0, t1):
    """Hard cuts (seconds in the file) inside [t0, t1] (ffmpeg scene score on a 192 px decode)."""
    r = subprocess.run(['ffmpeg', '-v', 'info', '-nostdin', '-ss', f'{max(0.0, t0):.3f}', '-i', path, '-t', f'{t1 - max(0.0, t0):.3f}', '-an',
                        '-vf', "scale=192:-2,select='gt(scene,0.28)',showinfo", '-f', 'null', '-'], capture_output=True, text=True, timeout=120)
    return [max(0.0, t0) + float(m) for m in re.findall(r'pts_time:([0-9.]+)', r.stderr or '')]


# ------------------------------------------------------------------ fetch (new sources through yt-dlp, shared pacing)

DEFAULT_SOURCE_QUERIES = [
    'Lycoris Recoil gunfight 4K', 'Black Lagoon Revy gunfight 4K', 'Trigun Stampede Vash gun fight', 'Cowboy Bebop Spike fight scene 4K',
    'Psycho-Pass Dominator scenes 4K', 'Gangsta anime fight scene', 'Chainsaw Man fight 4K', 'Spy x Family Yor fight scene 4K',
    'Demon Slayer sword fight 4K', 'Jujutsu Kaisen fight scene 4K', 'Bleach Thousand-Year Blood War fight 4K', 'Bleach hollow mask Ichigo fight',
    'Vinland Saga fight scene 4K', 'Samurai Champloo fight scene', 'Tokyo Ghoul Kaneki fight 4K', 'Mob Psycho 100 fight 4K',
    'One Punch Man fight 4K', 'Solo Leveling fight 4K', 'Dandadan fight 4K', 'Hunter x Hunter fight 4K', 'Fullmetal Alchemist Brotherhood fight',
    'Attack on Titan fight 4K', 'Sakamoto Days fight', 'Kaiju No. 8 fight', 'Black Clover fight 4K', 'Baki fight scene 4K', "Hell's Paradise fight",
    'Kengan Ashura fight', 'Record of Ragnarok fight', 'Dorohedoro fight', 'Kabaneri of the Iron Fortress fight', 'Akame ga Kill fight',
    'anime glass shatter scene 4K', 'anime mask break scene', 'anime gun reload sakuga', 'anime sword clash sparks 4K']
BAD_TITLE = re.compile(r'twixtor|amv|\bedit\b|reaction|tutorial|review|explained|ranking|top \d+|\bost\b|opening|ending|creditless|trailer|\bpv\b|'
                       r'full episode|nightcore|shorts?\b|#shorts|1 hour|\d+ hours?', re.I)
BOTBLOCK_RE = re.compile(r"confirm(?:\s+that)?\s+you(?:'re| are)?\s+not\s+a\s+bot|sign in to confirm|http error 429|too many requests|rate.?limit|"
                         r"temporarily blocked|not a bot|use --cookies|requires (?:login|authentication)", re.I)


def _yt(args, timeout=900):
    cmd = [sys.executable, '-m', 'yt_dlp', '--no-warnings', '--socket-timeout', '25', '--sleep-requests', '1', '--sleep-interval', '3', '--max-sleep-interval', '6',
           '--js-runtimes', os.environ.get('YT_JS_RUNTIMES', 'node')] + args   # yt-dlp[default] (EJS solver) + node: YouTube's JS challenge
    if os.environ.get('YT_FORCE_IPV4') == '1':          # GitHub runners (the song top-up's working recipe)
        cmd[3:3] = ['--force-ipv4']
    ck = os.environ.get('YT_COOKIES')
    tmp_ck = None
    if ck and os.path.isfile(ck) and os.path.getsize(ck) > 100:
        if os.environ.get('YT_COOKIE_COPY') == '1':     # yt-dlp rewrites its jar: a private copy per call keeps the secret's jar intact
            import shutil
            import tempfile
            fd, tmp_ck = tempfile.mkstemp(suffix='.txt')
            os.close(fd)
            shutil.copyfile(ck, tmp_ck)
        cmd[3:3] = ['--cookies', tmp_ck or ck, '--sleep-requests', '1', '--sleep-interval', '3', '--max-sleep-interval', '6']   # owner cookies: polite pacing
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    finally:
        if tmp_ck:
            try:
                os.remove(tmp_ck)
            except OSError:
                pass


def source_clean(path, tmp):
    """Burned-in subtitles / static watermark / borders in two 12 s previews of the source (infra's source_probe)."""
    import source_probe as SP
    w, h, fps, dur = probe(path)
    reasons = []
    os.makedirs(tmp, exist_ok=True)
    for q in (0.25, 0.65):
        pv = os.path.join(tmp, f'pv{int(q * 100)}.mp4')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-ss', f'{dur * q:.1f}', '-i', path, '-t', '12', '-an', '-vf', 'scale=640:-2',
                        '-c:v', 'libx264', '-crf', '26', '-preset', 'ultrafast', '-threads', '2', pv], capture_output=True)
        if not os.path.isfile(pv):
            continue
        frames = SP.read_frames(pv, 10)
        res = SP.analyse(frames, SP.text_bands(pv))
        # 2026-10-24: burned-in text is checked PER CLIP (the <= 3 s mini around the hit: sound_refresh.mine before the vision
        # call + hit_import.safety), not per source. Subtitles show during dialogue; the fight hits between the lines are clean,
        # and whole fight sources (Kaiju No. 8, Dandadan, Vinland Saga) were thrown out for a line at 0.06-0.13 bottom.
        # HIT_SCAN_SOURCE_TEXT=1 restores the old per-source reject.
        keep_text = os.environ.get('HIT_SCAN_SOURCE_TEXT') == '1'
        reasons += [r for r in res['reasons'] if not r.startswith('soft') and (keep_text or not r.startswith('burned-in text'))]
        try:
            os.remove(pv)
        except OSError:
            pass
    return not reasons, sorted(set(reasons))


def fetch(work, queries=None, per_query=3, max_sources=20, budget_min=None, gate=False, retry_ids=()):
    """Search -> filter -> download (<= 1080p) -> clean check. Sources land in WORK/src as <id>__all.mkv. State: WORK/fetch.json.
    retry_ids: videos seen before and rejected for burned-in text only (2026-10-24: text is now judged per clip), downloaded
    again directly (they passed the title / length / views filters the first time). Returns the state + 'got' (+ '_blocked')."""
    import library as L
    dest = os.path.join(work, 'src')
    os.makedirs(dest, exist_ok=True)
    st_p = os.path.join(work, 'fetch.json')
    st = read_json(st_p, {'seen': {}, 'queries': {}})
    pacer = L.GlobalPacer({'page': 3.0, 'probe': 6.0, 'download': 20.0}, edit_cache(), name='hit_scan')
    _vs = os.environ.get('HIT_SCAN_VID_SHARD', '')
    _vshard = tuple(int(x) for x in _vs.split('/')) if re.fullmatch(r'\d+/\d+', _vs) else None
    n_search = int(os.environ.get('HIT_SCAN_SEARCH_N', '14'))
    t0 = time.time()
    res = {'got': 0, 'blocked': None}

    def download(vid, title, channel, views, dur, retry=False):
        """-> True when the source was kept; sets res['blocked'] on a bot check"""
        pacer.wait('download')
        out = os.path.join(dest, f'{vid}__all.%(ext)s')
        args = ['--no-playlist', '-f', os.environ.get('HIT_SCAN_FORMAT') or 'bv*[height<=1080][height>=720]+ba/b[height<=1080][height>=720]',
                '--merge-output-format', 'mkv', '--max-filesize', os.environ.get('HIT_SCAN_MAXSIZE', '450M'), '-o', out]
        if retry:
            args += ['--no-simulate', '--print', 'after_move:META\t%(title)s\t%(channel)s\t%(view_count)s\t%(duration)s']
        r = _yt(args + [f'https://www.youtube.com/watch?v={vid}'])
        f = glob.glob(os.path.join(dest, vid + '__all.mkv'))
        if r.returncode != 0 or not f:
            if BOTBLOCK_RE.search(r.stderr or ''):
                pacer.penalise(r.stderr[-150:])
                res['blocked'] = 'download'
                log('bot-block on download: stop fetching')
                return False
            err = [ln for ln in (r.stderr or '').splitlines() if 'ERROR' in ln or 'WARNING' in ln]
            st['seen'][vid] = 'download failed: ' + (err[-1] if err else (r.stderr or '').strip()[-160:])[:200]
            log(f'{vid}: {st["seen"][vid]}')
            return False
        pacer.reward()
        for ln in (r.stdout or '').splitlines():
            if ln.startswith('META\t'):
                parts = (ln.split('\t') + [''] * 5)[1:5]
                title, channel = parts[0] or title, parts[1] or channel
                views = int(parts[2]) if parts[2].isdigit() else views
                dur = float(parts[3]) if parts[3].replace('.', '', 1).isdigit() else dur
        ok, why = source_clean(f[0], os.path.join(work, 'tmp'))
        if not ok:
            os.remove(f[0])
            st['seen'][vid] = 'unclean: ' + '; '.join(why)[:80]
            log(f'{vid} {title[:50]!r}: {st["seen"][vid]}')
            return False
        with open(os.path.join(dest, vid + '.info.json'), 'w', encoding='utf-8') as fh:
            json.dump({'id': vid, 'title': title, 'channel': channel, 'view_count': views, 'tags': []}, fh)
        st['seen'][vid] = 'ok'
        res['got'] += 1
        log(f'{vid} {title[:60]!r} ({dur}s, {views} views) fetched' + (' (text retry)' if retry else ''))
        return True

    def over():
        return res['got'] >= max_sources or res['blocked'] or (budget_min and time.time() - t0 > budget_min * 60)

    for vid in retry_ids or ():
        if over() or (gate and not wait_resources()):
            break
        if glob.glob(os.path.join(edit_cache(), 'src', vid + '__*')) or glob.glob(os.path.join(dest, vid + '__*')):
            continue
        download(vid, vid, '', None, None, retry=True)
        write_json(st_p, st)
    for q in (queries if queries is not None else DEFAULT_SOURCE_QUERIES):
        if over():
            break
        if q in st['queries']:
            continue
        if gate and not wait_resources():
            break
        pacer.wait('page')
        r = _yt(['--flat-playlist', '--dump-single-json', f'ytsearch{n_search}:{q}'], timeout=180)
        if r.returncode != 0 and BOTBLOCK_RE.search(r.stderr or ''):
            pacer.penalise(r.stderr[-150:])
            log('bot-block on search: stop fetching')
            res['blocked'] = 'search'
            break
        try:
            ents = json.loads(r.stdout or '{}').get('entries') or []
        except ValueError:
            ents = []
        st['queries'][q] = len(ents)
        taken_q = 0
        for e in ents:
            if taken_q >= per_query or over():
                break
            vid, title = e.get('id'), e.get('title') or ''
            dur, views = e.get('duration') or 0, e.get('view_count') or 0
            if vid and _vshard and zlib.crc32(vid.encode()) % _vshard[1] != _vshard[0]:
                continue                                  # another shard's video (HIT_SCAN_VID_SHARD=k/n: no double downloads)
            if not vid or vid in st['seen'] or not (90 <= dur <= 1500) or views < 3000 or BAD_TITLE.search(title) or not title_ok({'title': title, 'tags': []})[0]:
                continue
            if glob.glob(os.path.join(edit_cache(), 'src', vid + '__*')) or glob.glob(os.path.join(dest, vid + '__*')):
                st['seen'][vid] = 'have'
                continue
            if download(vid, title, e.get('channel') or e.get('uploader'), views, dur):
                taken_q += 1
        write_json(st_p, st)
    write_json(st_p, st)
    out = dict(st, got=res['got'])
    if res['blocked']:
        out['_blocked'] = res['blocked']
    return out


def text_retry_ids(seen):
    """seen videos rejected for burned-in text ONLY (no watermark / borders / softness reason): retried since text is per clip"""
    return [k for k, v in (seen or {}).items() if isinstance(v, str) and v.startswith('unclean: burned-in text') and ';' not in v]


# ------------------------------------------------------------------ scan

def audio_candidates(path, dur, per_min=8):
    import numpy as np
    import align
    import sound_edit as SE
    out = []
    t = 0.0
    while t < dur - 2.0:
        y = SE.load_audio(path, SE.SR, 1, start=t, dur=CHUNK_S + 2.0)
        if y is not None and len(y) > SE.SR * 2 and float(np.abs(y).max()) > 1e-3:
            fr = align.sfx_frames(y)
            span = len(y) / SE.SR
            for tt, st in SE._sfx_candidates(fr, max_hits=max(4, int(span / 60 * per_min * 2))):
                if tt < CHUNK_S or t + CHUNK_S >= dur - 2.0:      # the 2 s overlap belongs to the next chunk
                    out.append((round(t + tt, 3), st))
        t += CHUNK_S
    return sorted(set(out))


def taken(vp_mod, sid, t0, t1, led, items, bl):
    return vp_mod.moment_taken(sid, t0, t1, led, items) or vp_mod.blocked(sid, t0, t1, bl)


LOOSE = os.environ.get('HIT_SCAN_LOOSE', '0') == '1'
LOOSE_OK = ('source_ambiguous', 'source_unclear', 'punch_unclear', 'source_off_crop', 'source_too_wide_for_9x16', 'event_off_sync', 'wide_only')


def scan_source(path, work, max_per_source=10, tags=True, existing=None):
    """Kept hit moments of one source: [{t, scene, event_t, vis{...}, tag}]."""
    import sound_visible as SV
    meta = source_meta(path)
    ok, why = title_ok(meta)
    if not ok:
        return meta, [], {'skip': why}
    w, h, fps, dur = probe(path)
    if min(w, h) < 700 or dur < 20:
        return meta, [], {'skip': f'lowres/short ({w}x{h}, {dur:.0f}s)'}
    cands = audio_candidates(path, dur, per_min=int(os.environ.get('HIT_SCAN_PER_MIN', '8')))
    cap = int(min(60, max(max_per_source, round(dur / 30.0))))          # a long compilation holds many distinct hits
    # 2026-10-17 beat remake (shots = short build-up + hit + quick cut): env knobs for a denser mining of fight scenes
    spacing = float(os.environ.get('HIT_SCAN_SPACING', '6.0'))
    pre_min, post_min = float(os.environ.get('HIT_SCAN_PRE_MIN', '0.25')), float(os.environ.get('HIT_SCAN_POST_MIN', '0.45'))
    cap = int(os.environ.get('HIT_SCAN_CAP') or cap)
    stats = {'cands': len(cands)}
    led, bl, items = existing
    import vibe_pool as VP
    kept = []
    for t, st in sorted(cands, key=lambda x: -x[1]):
        if len(kept) >= cap * 2:
            break
        if any(abs(t - k['t']) < spacing for k in kept):           # distinct moments
            continue
        if taken(VP, meta['source_id'], t - 1.0, t + 1.5, led, items, bl):
            stats['moment_used'] = stats.get('moment_used', 0) + 1
            continue
        cuts = scene_cuts(path, t - 2.0, t + 2.0)
        before = [c for c in cuts if c <= t + 0.02]
        after = [c for c in cuts if c > t + 0.02]
        t_min, t_max = (before[-1] if before else t - 2.0), (after[0] if after else t + 2.0)
        if t - t_min < pre_min or t_max - t < post_min:
            stats['scene_room'] = stats.get('scene_room', 0) + 1
            continue
        m = SV.measure(path, t, t_min=t_min, t_max=t_max)
        ok0, why0 = SV.gate(m, None)             # motion-only first (cheap); a clear source tag may still rescue a low onset ratio
        if not ok0 and not (why0 == 'nothing_visible' and float(m.get('prom') or 0) >= float(getattr(SV, 'PROM_MIN_STRONG', 1.0))):
            stats[why0] = stats.get(why0, 0) + 1
            continue
        tg = None
        if tags and m.get('event_t') is not None:
            tg = SV.tags(path, [max(t_min + 0.01, m['event_t'] - 1 / fps), m['event_t']], region=m.get('bbox'))
        ok, why = SV.gate(m, tg)
        if not ok and LOOSE and ok0 and why in LOOSE_OK:
            # 2026-10-14: the by-eye review of the hit strips is the bar (sound-hit-allowlist.json); keep motion-clear
            # moments the tag gate is only unsure about, as review candidates (they need an eye approval to be a hit)
            stats['loose_' + why] = stats.get('loose_' + why, 0) + 1
            ok = True
        if not ok:
            stats[why] = stats.get(why, 0) + 1
            continue
        src, non, margin = SV.tag_summary(tg) if tg else (None, None, None)
        kept.append({'t': t, 'strength': st, 'scene': [round(t_min, 3), round(t_max, 3)], 'event_t': m.get('event_t'),
                     'windup_t': m.get('windup_t'), 'score': SV.score(m, tg), 'tag': src, 'margin': margin,
                     'prom': m.get('prom'), 'area': m.get('area'), 'fit': m.get('fit')})
    kept.sort(key=lambda k: -k['score'])
    stats['kept'] = len(kept[:cap])
    return meta, kept[:cap], stats


def existing_state():
    import vibe_pool as VP
    items = []
    for p, pd in VP.load_vibes()['presets'].items():
        for vn in pd['vibes']:
            items += VP.load_manifest(p, vn)['items']
    return VP.load_ledger(), VP.load_blocklist(), items


def scan(work, globs=None, max_per_source=10, limit=None, budget_min=None, gate=False, tags=True):
    done_p = os.path.join(work, 'scan.json')
    state = read_json(done_p, {'sources': {}})
    t0 = time.time()
    ex = existing_state()
    n = 0
    paths = source_files(globs)
    paths.sort(key=lambda p: not ACTION_RE.search(source_meta(p)['title']))        # action-titled sources first
    for path in paths:
        sid = source_meta(path)['source_id']
        if sid in state['sources']:
            continue
        if limit and n >= limit:
            break
        if budget_min and (time.time() - t0) > budget_min * 60:
            log('scan budget used up')
            break
        if gate and not wait_resources():
            log('no resources: stop')
            break
        t1 = time.time()
        try:
            meta, kept, stats = scan_source(path, work, max_per_source, tags, ex)
        except Exception as e:  # noqa: BLE001
            log(f'{sid}: failed {str(e)[:120]}')
            state['sources'][sid] = {'path': path, 'error': str(e)[:200]}
            write_json(done_p, state)
            continue
        state['sources'][sid] = {'path': path, 'meta': meta, 'kept': kept, 'stats': stats, 'secs': round(time.time() - t1)}
        write_json(done_p, state)
        n += 1
        log(f'{sid} {meta["title"][:40]!r}: {stats} ({time.time() - t1:.0f}s)')
    return state


# ------------------------------------------------------------------ cut mini sources

def cut(work, threads=2):
    state = read_json(os.path.join(work, 'scan.json'), {'sources': {}})
    mini_dir = os.path.join(work, 'mini')
    os.makedirs(mini_dir, exist_ok=True)
    side_p = os.path.join(work, 'mini.json')
    side = read_json(side_p, {})
    n = 0
    for sid, s in state['sources'].items():
        for k in s.get('kept') or []:
            t0, t1 = k['scene']
            a, b = max(t0 + 0.03, k['t'] - 1.4), min(t1 - 0.03, k['t'] + 1.6)
            if b - a < 1.0:
                continue
            name = f"{sid}_{int(round(a * 1000))}"
            out = os.path.join(mini_dir, name + '.mp4')
            if name not in side or not os.path.isfile(out):
                r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-nostdin', '-ss', f'{a:.3f}', '-i', s['path'], '-t', f'{b - a:.3f}',
                                    '-vf', "scale=-2:'min(%s,ih)'" % os.environ.get('HIT_SCAN_CUT_H', '2160'), '-c:v', 'libx264', '-crf', '16', '-preset', 'veryfast', '-threads', str(threads),
                                    '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', out], capture_output=True, text=True)
                if r.returncode != 0 or not os.path.isfile(out):
                    log('cut failed', name, (r.stderr or '')[-120:])
                    continue
            side[name] = {'source_id': sid, 'offset': round(a, 3), 'hit_t': round(k['t'] - a, 3), 'tag': k.get('tag'), 'score': k.get('score'),
                          'title': s['meta']['title'], 'channel': s['meta'].get('channel'), 'views': s['meta'].get('views')}
            n += 1
    write_json(side_p, side)
    return n


# ------------------------------------------------------------------ library run on the mini sources + import

def run_library(work, budget_s=3000, threads=2, count=400):
    mini = os.path.join(work, 'mini')
    out = os.path.join(work, 'lib')
    os.makedirs(out, exist_ok=True)
    py = os.environ.get('MEME_PYTHON') or sys.executable
    cmd = [py, os.path.join(ROOT, 'core', 'edit', 'library.py'), '--mode', 'mood', '--query', 'anime impact hit', '--kind', 'anime',
           '--count', str(count), '--min-dur', '0.8', '--max-dur', '4', '--with-audio', '--need-sfx', '--preset', 'sound_hit_sync',
           '--local', os.path.join(mini, '*.mp4'), '--local-only', '--budget', str(int(budget_s)), '--workers', '1',
           '--out-dir', out, '--reject-sheet', os.path.join(out, 'rej.jpg')]
    env = dict(os.environ, PYTHONIOENCODING='utf-8', EDIT_THREADS=str(threads), OMP_NUM_THREADS=str(threads))
    log('library on the mini sources:', ' '.join(cmd[-12:]))
    subprocess.run(cmd, env=env, timeout=budget_s + 600)
    return os.path.join(out, 'shots.json')


def import_library(work, shots_json, root=None):
    """Restore the real source id / times in the library's shots and import them (impact / crisp by the sound's source tag)."""
    import vibe_pool as VP
    side = read_json(os.path.join(work, 'mini.json'), {})
    d = read_json(shots_json, {})
    shots = d.get('shots') if isinstance(d, dict) else d
    by_vibe = {'impact': [], 'crisp': []}
    for s in shots or []:
        stem = os.path.splitext(os.path.basename(str(s.get('source_title') or '')))[0]
        m = side.get(stem)
        if not m:
            continue
        s = dict(s, source_id=m['source_id'], source_title=m['title'], source_channel=m.get('channel') or s.get('source_channel'),
                 views=m.get('views') or s.get('views'), start=float(s.get('start') or 0) + m['offset'],
                 end=float(s.get('end') or 0) + m['offset'], hit_scan={'tag': m.get('tag'), 'score': m.get('score')})
        by_vibe[TAG_VIBE.get(m.get('tag'), 'impact')].append(s)
    res = {}
    for vibe, lst in by_vibe.items():
        if not lst:
            continue
        p = os.path.join(work, f'shots.{vibe}.json')
        write_json(p, {'shots': lst})
        n, sk = VP.import_shots('sound', vibe, p, root=root, query='hit_scan')
        res[vibe] = {'added': n, 'skip': sk}
    VP.write_index(root)
    return res


def grow(work, globs=None, max_per_source=10, limit=None, budget_min=None, gate=False, root=None, tags=True, library_budget_min=None):
    """scan -> cut -> library --local -> import -> stamp. budget_min = the scan's time box, library_budget_min the library run's."""
    os.makedirs(work, exist_ok=True)
    if root:                     # grow into a STAGING copy of the live manifests (dedupe sees the live pool); promote afterwards
        import pool_refresh as PR
        PR.init_staging(root)
    scan(work, globs, max_per_source, limit, budget_min, gate, tags)
    n = cut(work)
    log(f'{n} mini sources')
    if not n:
        return {'mini': 0}
    if gate and not wait_resources():
        return {'mini': n, 'stopped': 'resources'}
    sj = run_library(work, budget_s=int((library_budget_min or 180) * 60))
    res = import_library(work, sj, root) if os.path.isfile(sj) else {'error': 'no shots.json'}
    import pool_visible as PV
    res['stamp'] = PV.stamp(root)
    res['counts'] = PV.counts(root)
    return res


def prune_sources(work, only_fetched=True):
    """Owner storage rule 2026-10-13: once a source's hit clips are cut and gated, delete the (large, hi-res) source file at once and
    keep its URL in the pool metadata (work/sources-pruned.json + scan.json 'url') so it can be re-fetched. Only files fetched
    into WORK/src are removed (never the shared edit-cache)."""
    state = read_json(os.path.join(work, 'scan.json'), {'sources': {}})
    rec_p = os.path.join(work, 'sources-pruned.json')
    rec = read_json(rec_p, {})
    src_dir = os.path.abspath(os.path.join(work, 'src'))
    freed = 0
    for sid, s in state['sources'].items():
        p = s.get('path')
        if not p or not os.path.isfile(p) or (only_fetched and not os.path.abspath(p).startswith(src_dir)):
            continue
        freed += os.path.getsize(p)
        rec[sid] = {'url': f'https://www.youtube.com/watch?v={sid}', 'title': (s.get('meta') or {}).get('title'), 'bytes': os.path.getsize(p)}
        s['url'], s['pruned'] = rec[sid]['url'], True
        os.remove(p)
    write_json(os.path.join(work, 'scan.json'), state)
    write_json(rec_p, rec)
    log(f'pruned {len(rec)} source files, {freed / 1048576:.0f} MB freed (URLs in {rec_p})')
    return freed


def queries_for_shard(k, n):
    return DEFAULT_SOURCE_QUERIES[k::n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['fetch', 'scan', 'cut', 'import', 'grow', 'prune'])
    ap.add_argument('--work', required=True)
    ap.add_argument('--src', nargs='*')
    ap.add_argument('--max-per-source', type=int, default=10)
    ap.add_argument('--limit-sources', type=int)
    ap.add_argument('--max-sources', type=int, default=20)
    ap.add_argument('--budget-min', type=float)
    ap.add_argument('--library-budget-min', type=float)
    ap.add_argument('--gate', action='store_true')
    ap.add_argument('--no-tags', action='store_true')
    ap.add_argument('--root')
    ap.add_argument('--shots')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    if a.cmd == 'fetch':
        print(json.dumps({k: (len(v) if isinstance(v, (dict, list)) else v) for k, v in fetch(a.work, None, 3, a.max_sources, a.budget_min, a.gate).items()}))
    elif a.cmd == 'scan':
        st = scan(a.work, a.src, a.max_per_source, a.limit_sources, a.budget_min, a.gate, not a.no_tags)
        print(json.dumps({'sources': len(st['sources']), 'kept': sum(len(s.get('kept') or []) for s in st['sources'].values())}))
    elif a.cmd == 'prune':
        print(json.dumps({'freed_mb': round(prune_sources(a.work) / 1048576)}))
    elif a.cmd == 'cut':
        print(json.dumps({'mini': cut(a.work)}))
    elif a.cmd == 'import':
        print(json.dumps(import_library(a.work, a.shots or os.path.join(a.work, 'lib', 'shots.json'), a.root)))
    else:
        print(json.dumps(grow(a.work, a.src, a.max_per_source, a.limit_sources, a.budget_min, a.gate, a.root, not a.no_tags, a.library_budget_min)))


if __name__ == '__main__':
    main()
