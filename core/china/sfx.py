"""
Sound-effect library for the Mainland Unpacked renders, downloaded on demand into remotion/public/sfx (gitignored).

- Kenney "Interface Sounds" and "Impact Sounds": CC0 (public domain).
- Mixkit whooshes / transitions: Mixkit Sound Effects Free License (free in monetised videos, no attribution;
  the raw files must not be redistributed, which is why they are fetched here and never committed).

  python core/china/sfx.py      # fetch everything, print the name -> file map
"""
import io
import json
import os
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, 'remotion', 'public', 'sfx')
UA = {'User-Agent': 'Mozilla/5.0'}

KENNEY = {
    'interface': 'https://kenney.nl/media/pages/assets/interface-sounds/fa43c1dd4d-1677589452/kenney_interface-sounds.zip',
    'impact': 'https://kenney.nl/media/pages/assets/impact-sounds/87b4ddecda-1677589768/kenney_impact-sounds.zip',
}
MIXKIT = {   # name: (id, title on mixkit.co)
    'whoosh': (1492, 'Cinematic whoosh fast transition'),
    'whoosh_air': (1489, 'Air woosh'),
    'whoosh_soft': (1490, 'Fast whoosh transition'),
    'pop_whoosh': (3005, 'Explainer video pops whoosh light pop'),
    'hit': (2918, 'Epic movie trailer whoosh impact'),
}
# named roles -> file (relative to DIR); the renderer only uses these names
ROLES = {
    'whoosh': 'mixkit/whoosh.mp3',
    'whoosh_air': 'mixkit/whoosh_air.mp3',
    'whoosh_soft': 'mixkit/whoosh_soft.mp3',
    'pop_whoosh': 'mixkit/pop_whoosh.mp3',
    'hit': 'mixkit/hit.mp3',
    'pop': 'kenney/interface/Audio/drop_002.ogg',
    'click': 'kenney/interface/Audio/click_002.ogg',
    'tick': 'kenney/interface/Audio/tick_002.ogg',
    'select': 'kenney/interface/Audio/select_005.ogg',
    'confirm': 'kenney/interface/Audio/confirmation_002.ogg',
    'thud': 'kenney/impact/Audio/impactPunch_heavy_002.ogg',
    'glitch': 'kenney/interface/Audio/glitch_002.ogg',
}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def ensure():
    os.makedirs(DIR, exist_ok=True)
    for name, url in KENNEY.items():
        dest = os.path.join(DIR, 'kenney', name)
        if not os.path.isdir(dest):
            zipfile.ZipFile(io.BytesIO(_get(url))).extractall(dest)
    os.makedirs(os.path.join(DIR, 'mixkit'), exist_ok=True)
    for name, (i, _) in MIXKIT.items():
        p = os.path.join(DIR, 'mixkit', f'{name}.mp3')
        if not os.path.exists(p):
            open(p, 'wb').write(_get(f'https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3'))
    missing = [r for r, f in ROLES.items() if not os.path.exists(os.path.join(DIR, f))]
    if missing:
        raise FileNotFoundError(f'sfx missing: {missing}')
    # Remotion's @remotion/media decodes WAV / MP3 reliably; the Kenney packs ship OGG, so convert those once
    out = {}
    for r, f in ROLES.items():
        if f.endswith('.ogg'):
            wav = f[:-4] + '.wav'
            if not os.path.exists(os.path.join(DIR, wav)):
                import subprocess
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', os.path.join(DIR, f), '-ar', '44100', os.path.join(DIR, wav)], check=True)
            f = wav
        out[r] = 'sfx/' + f
    return out


if __name__ == '__main__':
    print(json.dumps(ensure(), indent=1))
