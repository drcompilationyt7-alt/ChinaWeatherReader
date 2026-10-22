#!/usr/bin/env python3
"""Which pool does tonight's light mining run mine? Deterministic by UTC date: ordinal(date) % len(rotation).
Usage: python scripts/pool-mine-rotation.py [--rotation slowmo,sound,quiz] [--date YYYY-MM-DD] [--days N]
Prints the pool for the date (or N lines 'date pool' with --days). Unknown names are dropped; empty -> the default."""
import argparse, datetime as dt, os

DEFAULT = ['slowmo', 'sound', 'quiz']
KNOWN = set(DEFAULT)


def parse(rot):
    r = [x.strip().lower() for x in (rot or '').split(',') if x.strip().lower() in KNOWN]
    return r or DEFAULT          # repeats weight a pool: "slowmo,slowmo,sound,quiz"


def pool_for(day, rot):
    return rot[day.toordinal() % len(rot)]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--rotation', default=os.environ.get('MINE_ROTATION', ''))
    ap.add_argument('--date', default='')
    ap.add_argument('--days', type=int, default=0)
    a = ap.parse_args()
    rot = parse(a.rotation)
    d0 = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(dt.timezone.utc).date()
    if a.days:
        for i in range(a.days):
            d = d0 + dt.timedelta(days=i)
            print(d, pool_for(d, rot))
    else:
        print(pool_for(d0, rot))
