"""
Stock B-roll video for the Mainland Unpacked Shorts: Pexels + Pixabay + YouTube Creative Commons.

YouTube: ONLY videos the uploader licensed "Creative Commons Attribution (reuse allowed)". The licence is checked
on every download and anything else is refused (ordinary YouTube footage = Content ID claims / reused-content
rejection on a new channel). Each clip is credited in the description, as CC BY requires.

Both licences allow free commercial use without attribution (no selling the raw clips); we still name the source
in the description. Keys come from .env: PEXELS_API_KEY, PIXABAY_API_KEY (free accounts).

  python core/china/stock.py search "shanghai skyline night" [n]   # candidates + a contact sheet to pick from
  python core/china/stock.py fetch pexels:1234567                 # download into the cache

In a Short's script, a scene takes  "video": "pexels:1234567"  (picked from a contact sheet) or
"video": "q:shanghai skyline night"  (top suitable result), plus an optional "videoStart" (seconds into the clip).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.environ.get('CHINA_STOCK_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'china-stock')
UA = 'MainlandUnpacked/1.0'


def _env():
    p = os.path.join(ROOT, '.env')
    if os.path.exists(p):
        for ln in open(p, encoding='utf-8', errors='ignore'):
            m = re.match(r'\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$', ln)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def _json(url, headers=None):
    """API GET with a 24 h on-disk cache (Pixabay's terms require caching; also keeps Pexels well under its limits)."""
    import time
    d = os.path.join(CACHE, 'api')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, hashlib.sha1(url.encode()).hexdigest()[:20] + '.json')
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        return json.load(open(p, encoding='utf-8'))
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    json.dump(data, open(p, 'w', encoding='utf-8'))
    return data


def _pick_file(files):
    """Best rendition: prefer >= 1080 on the short side, not huge; portrait first."""
    files = [f for f in files if f.get('link') or f.get('url')]
    def score(f):
        w, h = f.get('width') or 0, f.get('height') or 0
        short = min(w, h)
        return (short >= 1080, h > w, -abs(short - 1080))
    return max(files, key=score) if files else None


def search(query, n=12):
    _env()
    out = []
    if os.environ.get('PEXELS_API_KEY'):
        q = urllib.parse.urlencode({'query': query, 'per_page': n, 'size': 'medium'})
        for v in _json(f'https://api.pexels.com/videos/search?{q}', {'Authorization': os.environ['PEXELS_API_KEY']}).get('videos', []):
            f = _pick_file([{'link': x['link'], 'width': x.get('width'), 'height': x.get('height')} for x in v.get('video_files', [])])
            if f:
                out.append({'id': f'pexels:{v["id"]}', 'w': f['width'], 'h': f['height'], 'dur': v.get('duration', 0), 'url': f['link'],
                            'thumb': v.get('image'), 'page': v.get('url'), 'by': (v.get('user') or {}).get('name', '')})
    if os.environ.get('PIXABAY_API_KEY'):
        q = urllib.parse.urlencode({'key': os.environ['PIXABAY_API_KEY'], 'q': query, 'per_page': max(3, n), 'safesearch': 'true'})
        for v in _json(f'https://pixabay.com/api/videos/?{q}').get('hits', []):
            vids = v.get('videos', {})
            f = _pick_file([{'link': x.get('url'), 'width': x.get('width'), 'height': x.get('height')} for x in vids.values()])
            if f:
                thumb = (vids.get('medium') or {}).get('thumbnail') or f'https://i.vimeocdn.com/video/{v.get("picture_id")}_640x360.jpg'
                out.append({'id': f'pixabay:{v["id"]}', 'w': f['width'], 'h': f['height'], 'dur': v.get('duration', 0), 'url': f['link'],
                            'thumb': thumb, 'page': v.get('pageURL'), 'by': v.get('user', '')})
    if os.environ.get('CHINA_STOCK_YT', '1') == '1':
        try:
            out += search_yt_cc(query, n)
        except Exception as e:  # noqa: BLE001  (YouTube can rate-limit; the other sources still work)
            print(f'youtube cc search failed: {str(e)[:80]}', file=sys.stderr)
    return out


YT_CC_FILTER = 'EgIwAQ%3D%3D'   # YouTube search filter: Creative Commons


def search_yt_cc(query, n=12):
    import yt_dlp
    url = f'https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp={YT_CC_FILTER}'
    with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True, 'playlistend': n, 'skip_download': True}) as y:
        info = y.extract_info(url, download=False)
    out = []
    for e in (info.get('entries') or [])[:n]:
        if not e.get('id') or (e.get('duration') or 0) < 5:
            continue
        out.append({'id': f'yt:{e["id"]}', 'w': 1920, 'h': 1080, 'dur': int(e.get('duration') or 0),
                    'url': None, 'thumb': f'https://i.ytimg.com/vi/{e["id"]}/hqdefault.jpg',
                    'page': f'https://www.youtube.com/watch?v={e["id"]}', 'by': e.get('channel') or e.get('uploader') or '',
                    'title': e.get('title', '')})
    return out


def _fetch_yt(vid, path, start=0.0, seconds=None):
    """Download a Creative Commons YouTube video (or just the needed section), refusing any other licence."""
    import yt_dlp
    url = f'https://www.youtube.com/watch?v={vid[3:]}'
    with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as y:
        meta = y.extract_info(url, download=False)
    lic = meta.get('license') or ''
    if 'creative commons' not in lic.lower():
        raise PermissionError(f'{vid}: licence is "{lic or "Standard YouTube License"}", not Creative Commons; not used')
    opts = {'quiet': True, 'outtmpl': path, 'overwrites': True, 'merge_output_format': 'mp4',
            'format': 'bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]'}
    if seconds:
        from yt_dlp.utils import download_range_func
        opts['download_ranges'] = download_range_func(None, [(max(0, start - 1), start + seconds + 1)])
        opts['force_keyframes_at_cuts'] = True
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    return {'id': vid, 'url': url, 'page': url, 'by': meta.get('channel') or meta.get('uploader') or '',
            'title': meta.get('title', ''), 'licence': lic, 'dur': meta.get('duration', 0), 'sectionStart': max(0, start - 1) if seconds else 0}


def _lookup(vid):
    _env()
    src, i = vid.split(':', 1)
    if src == 'pexels':
        v = _json(f'https://api.pexels.com/videos/videos/{i}', {'Authorization': os.environ['PEXELS_API_KEY']})
        f = _pick_file([{'link': x['link'], 'width': x.get('width'), 'height': x.get('height')} for x in v.get('video_files', [])])
        return {'id': vid, 'url': f['link'], 'page': v.get('url'), 'by': (v.get('user') or {}).get('name', ''), 'dur': v.get('duration', 0)}
    if src == 'pixabay':
        v = _json(f'https://pixabay.com/api/videos/?key={os.environ["PIXABAY_API_KEY"]}&id={i}')['hits'][0]
        f = _pick_file([{'link': x.get('url'), 'width': x.get('width'), 'height': x.get('height')} for x in v['videos'].values()])
        return {'id': vid, 'url': f['link'], 'page': v.get('pageURL'), 'by': v.get('user', ''), 'dur': v.get('duration', 0)}
    raise ValueError(vid)


def _valid(p):
    """A real video file (empty / truncated downloads happen with YouTube sections)."""
    if not os.path.exists(p) or os.path.getsize(p) < 50_000:
        return False
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width', '-of', 'csv=p=0', p],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() != ''


def fetch(vid, start=0.0, seconds=None):
    """'pexels:ID' / 'pixabay:ID' / 'yt:ID' (Creative Commons only) / 'q:query' -> (local mp4 path, info with a credit line)."""
    if vid.startswith('q:'):
        res = search(vid[2:], 10)
        res.sort(key=lambda r: (r['h'] > r['w'], min(r['w'], r['h']) >= 1080, r['dur'] >= 6), reverse=True)
        if not res:
            raise LookupError(f'no stock video for {vid}')
        vid = res[0]['id']
    os.makedirs(CACHE, exist_ok=True)
    meta_p = os.path.join(CACHE, vid.replace(':', '-') + '.json')
    path = os.path.join(CACHE, vid.replace(':', '-') + '.mp4')
    if vid.startswith('yt:'):
        # one full download per video (cut locally, reused by every scene); sections only for very long videos.
        # Section downloads sometimes come back empty, and every file is validated before use.
        full = os.path.join(CACHE, f'{vid.replace(":", "-")}_0-0.mp4')
        if _valid(full) and os.path.exists(full[:-4] + '.json'):
            path, meta_p = full, full[:-4] + '.json'
        else:
            tag = f'{int(start)}-{int(seconds or 0)}'
            path = os.path.join(CACHE, f'{vid.replace(":", "-")}_{tag}.mp4')
            meta_p = path[:-4] + '.json'
    if os.path.exists(path) and os.path.exists(meta_p) and _valid(path):
        info = json.load(open(meta_p, encoding='utf-8'))
    elif vid.startswith('yt:'):
        info = None
        for sec in ((None,) if not seconds else (seconds, None)):
            p = full if sec is None else path
            try:
                info = _fetch_yt(vid, p, start, sec)
            except PermissionError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f'{vid}: download failed ({str(e)[:60]})', file=sys.stderr)
            if info and _valid(p):
                path, meta_p = p, p[:-4] + '.json'
                break
            if os.path.exists(p):
                os.remove(p)
            info = None
        if not info:
            raise RuntimeError(f'{vid}: could not download a valid clip')
        json.dump(info, open(meta_p, 'w', encoding='utf-8'))
    else:
        info = _lookup(vid)
        req = urllib.request.Request(info['url'], headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=180) as r, open(path, 'wb') as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        json.dump(info, open(meta_p, 'w', encoding='utf-8'))
    if vid.startswith('yt:'):
        info['credit'] = f'"{info.get("title", "")}" by {info["by"]} on YouTube, licensed CC BY ({info["page"]})'
    else:
        site = 'Pexels' if vid.startswith('pexels') else 'Pixabay'
        info['credit'] = f'Video by {info["by"]} on {site} ({info["page"]})'
    return path, info


def prepare(src, dst, start, seconds):
    """Cut [start, start + seconds], crop to cover 1080x1920, h264, no audio: small, fast to decode in Remotion."""
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.2f}', '-i', src, '-t', f'{seconds:.2f}', '-an',
                    '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-movflags', '+faststart', dst], check=True)
    return dst


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'search':
        from io import BytesIO
        from PIL import Image, ImageDraw
        q, n = sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 12
        res = search(q, n)
        cols, tw, th = 6, 300, 360
        sheet = Image.new('RGB', (cols * tw, ((len(res) + cols - 1) // cols) * th or th), (25, 25, 25))
        for k, r in enumerate(res):
            print(f'[{k}] {r["id"]:18} {r["w"]}x{r["h"]} {r["dur"]}s  {r.get("title", "")[:50]}  {r["page"]}')
            try:
                with urllib.request.urlopen(urllib.request.Request(r['thumb'], headers={'User-Agent': UA}), timeout=20) as resp:
                    im = Image.open(BytesIO(resp.read())).convert('RGB')
                im.thumbnail((tw - 6, th - 40))
                sheet.paste(im, ((k % cols) * tw + 3, (k // cols) * th + 3))
            except Exception:
                pass
            ImageDraw.Draw(sheet).text(((k % cols) * tw + 6, (k // cols) * th + th - 34), f'[{k}] {r["id"]} {r["dur"]}s {"P" if r["h"] > r["w"] else "L"}', fill=(255, 255, 255))
        out = os.path.join(os.environ.get('SHEET_DIR', '.'), 'stock-' + re.sub(r'\W+', '_', q)[:40] + '.jpg')
        sheet.save(out, quality=85)
        print('sheet:', out)
    elif cmd == 'fetch':
        p, info = fetch(sys.argv[2])
        print(p, info['credit'])
