#!/usr/bin/env python3
"""Which pool does tonight's light mining run mine? Deterministic by UTC date: ordinal(date) % len(rotation).
2026-10-25: MINE_WEEK="mon:sound,tue:slowmo,wed:ranking,thu:sound,fri:quiz,sat:slowmo,sun:quiz" (UTC weekday) overrides all of this.
Usage: python scripts/pool-mine-rotation.py [--rotation slowmo,sound,quiz] [--date YYYY-MM-DD] [--days N]
Prints the pool for the date (or N lines 'date pool' with --days). Unknown names are dropped; empty -> the default.
2026-10-25: the RANKING compilation pool mines ONE night a week (it replaces that night's pool): weekday RANKING_MINE_WEEKDAY
(Monday = 0 ... Sunday = 6; default 2 = Wednesday); RANKING_MINE=off removes it. A "ranking" name inside MINE_ROTATION also works."""
import argparse, datetime as dt, os

DEFAULT = ['slowmo', 'sound', 'quiz']
KNOWN = set(DEFAULT) | {'ranking'}


def parse(rot):
    r = [x.strip().lower() for x in (rot or '').split(',') if x.strip().lower() in KNOWN]
    return r or DEFAULT          # repeats weight a pool: "slowmo,slowmo,sound,quiz"


DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def parse_week(week):
    """MINE_WEEK="mon:sound,tue:slowmo,..." -> {weekday: pool}; None when unset / unusable (fall back to the rotation)."""
    out = {}
    for part in (week or '').split(','):
        d, _, p = part.strip().lower().partition(':')
        if d[:3] in DAYS and p.strip() in KNOWN:
            out[DAYS.index(d[:3])] = p.strip()
    return out or None


def pool_for(day, rot, week=None):
    if week is None:
        week = parse_week(os.environ.get('MINE_WEEK', ''))
    if week and day.weekday() in week:       # fixed 7-day week (2026-10-25); RANKING_MINE=off swaps ranking for the rotation
        if not (week[day.weekday()] == 'ranking' and os.environ.get('RANKING_MINE', 'on') == 'off'):
            return week[day.weekday()]
        return rot[day.toordinal() % len(rot)]
    if week:
        return rot[day.toordinal() % len(rot)]
    if os.environ.get('RANKING_MINE', 'on') != 'off' and day.weekday() == int(os.environ.get('RANKING_MINE_WEEKDAY') or 2):
        return 'ranking'
    return rot[day.toordinal() % len(rot)]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--rotation', default=os.environ.get('MINE_ROTATION', ''))
    ap.add_argument('--date', default='')
    ap.add_argument('--days', type=int, default=0)
    a = ap.parse_args()
    rot = parse(a.rotation)
    # the mining NIGHT's date: the 19:30 UTC schedule starts 5-6 h late in this repo (2026-10-26), i.e. after midnight UTC;
    # a run that starts before 12:00 UTC still belongs to the previous evening's slot (else Wednesday's ranking run would
    # mine Thursday's pool and Tuesday's late run would mine ranking)
    d0 = dt.date.fromisoformat(a.date) if a.date else (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)).date()
    if a.days:
        for i in range(a.days):
            d = d0 + dt.timedelta(days=i)
            print(d, pool_for(d, rot))
    else:
        print(pool_for(d0, rot))
