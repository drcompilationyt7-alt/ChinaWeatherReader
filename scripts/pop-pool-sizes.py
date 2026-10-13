"""Pool sizes of core/quiz/assets/character-extras.json. `pop-pool-sizes.py` prints all; `pop-pool-sizes.py beach-outfit` prints one number."""
import json
import os
import sys

e = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core', 'quiz', 'assets', 'character-extras.json'), encoding='utf-8'))
if len(sys.argv) > 1 and sys.argv[1] == 'beach-outfit':
    print(len(e.get('themes', {}).get('beach', {}).get('outfit', [])))
else:
    print({k: len(v) for k, v in e.items() if isinstance(v, list)})
    print({t: {f: len(v) for f, v in x.items()} for t, x in e.get('themes', {}).items()})
