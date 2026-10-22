"""Find / remove links in the video descriptions of the 3 channels (2026-10-22: YouTube removed a quiz and gave a
Community Guidelines warning over a fan-art credit link to a site that also hosts adult content).

Usage: strip_links.py report|apply <chan> <outdir>      chan: quiz | zero | china
  report: list every upload (uploads playlist, incl. private / scheduled) whose description (or a localized
          description) has a URL, a bare domain or an adult-host site name; writes <out>/<chan>-report.json with the
          proposed new descriptions.
  apply:  videos.update for each flagged video: only the description (+ localized descriptions) changes; title,
          tags, categoryId, languages are sent back as they were. Backup of the old snippet in <out>/<chan>-backup.json,
          every change in <out>/<chan>-changes.json.
Quiz: every link goes; a credit block that had links becomes one plain line. zero / china: third-party links go,
links to the channel's own videos / own channel are kept. Creds from env (see comments.py); tokens never printed.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comments import API, http, token, get  # noqa: E402

CREDIT = "All images, clips and songs belong to their original creators and are shown for quiz and identification purposes."
TLDS = ("com|net|org|us|jp|kr|cn|tw|hk|io|co|me|tv|gg|moe|be|ly|app|info|xyz|ai|uk|de|fm|link|site|to|cc|ru|fr|ca|au|"
        "in|dev|page|art|club|top|edu|gov")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\"]+", re.I)
DOM_RE = re.compile(r"(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:" + TLDS + r")\b(?:/[^\s<>()\"]*)?", re.I)
SITE_RE = re.compile(r"\b(danbooru|donmai|gelbooru|safebooru|rule34|yande\.re|konachan|sankaku|pixiv|nhentai)\b", re.I)
# the old quiz credit block (pipeline/pop-quiz-meta.js popCredits before 23be9e7)
QUIZ_CREDIT_RE = re.compile(
    r"(Song clips from|Songs on the reveals|Pictures on the reveals|^Photos\b|Photos(?: \(Wikimedia Commons[^)]*\))?:|"
    r"VTuber portraits|Character art|fan art credited|Clips from the creditless|Voice clips from|Covers via AniList|"
    r"Map data:|Emoji art:|^\s*\d+\.\s*(?:[^:\n]{1,80}:\s*)?Art:|All characters (?:and artwork )?belong|"
    r"All songs belong to)", re.I)
WYR_KEEP = "The picks are my own opinion, not a poll."
YT_ID_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|live/))([\w-]{11})", re.I)
YT_CH_RE = re.compile(r"youtube\.com/(?:@([\w.-]+)|channel/([\w-]+))", re.I)


def links_in(text):
    found = URL_RE.findall(text)
    rest = URL_RE.sub(" ", text)
    return found + [m.group(0) for m in DOM_RE.finditer(rest)]


def is_own(link, own):
    if own is None: return False
    m = YT_ID_RE.search(link)
    if m: return m.group(1) in own["ids"]
    m = YT_CH_RE.search(link)
    if m:
        h, cid = m.group(1), m.group(2)
        return bool((h and h.lower() in own["handles"]) or (cid and cid == own["channel"]))
    return False


def bad_links(text, own):
    return [l for l in links_in(text) if not is_own(l, own)]


def strip_line(line, own):
    def sub(m): return m.group(0) if is_own(m.group(0), own) else " "
    s = URL_RE.sub(sub, line)
    s = DOM_RE.sub(sub, s)
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([:;,])(?:\s*[:;,])+", r"\1", s)
    s = re.sub(r"\s*(?:\||-|·|•|→|->)\s*$", "", s).rstrip()
    return s


def leftover(s):
    """A line that was mostly a link and is now just a label ('Source:', '👉', 'via')."""
    t = s.strip()
    return not re.search(r"\w", t) or bool(re.fullmatch(r"[^\n]{0,40}(?::|\bvia|\bat|\bhere)\s*[^\w\s]*", t, re.I))


def clean(desc, chan, own):
    lines, out = (desc or "").split("\n"), []
    quiz = chan == "quiz"
    credit_has_link = quiz and any(QUIZ_CREDIT_RE.search(l) and (links_in(l) or SITE_RE.search(l)) for l in lines)
    placed = CREDIT in desc
    for l in lines:
        if credit_has_link and QUIZ_CREDIT_RE.search(l):
            if l.strip().startswith(WYR_KEEP): out.append(WYR_KEEP)
            if not placed: out.append(CREDIT); placed = True
            continue
        if bad_links(l, own) or (quiz and SITE_RE.search(l)):
            s = strip_line(l, own)
            if quiz: s = re.sub(r"\s{2,}", " ", SITE_RE.sub("", s)).strip()
            if leftover(s): continue
            out.append(s)
        else:
            out.append(l)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def scan(desc, chan, own):
    d = desc or ""
    return bad_links(d, own), (sorted({m.lower() for m in SITE_RE.findall(d)}) if chan == "quiz" else [])


def uploads(tok):
    ch = get(tok, "channels", part="id,snippet,contentDetails", mine="true")["items"][0]
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, pt = [], None
    while True:
        q = dict(part="contentDetails", playlistId=pl, maxResults=50)
        if pt: q["pageToken"] = pt
        r = get(tok, "playlistItems", **q)
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        pt = r.get("nextPageToken")
        if not pt: break
    vids = []
    for i in range(0, len(ids), 50):
        vids += get(tok, "videos", part="snippet,localizations,status", id=",".join(ids[i:i + 50])).get("items", [])
    handle = (ch["snippet"].get("customUrl") or "").lstrip("@").lower()
    own = {"channel": ch["id"], "ids": set(ids), "handles": {handle} if handle else set()}
    return ch, vids, own


def main(mode, chan, out):
    os.makedirs(out, exist_ok=True)
    tok = token(chan)
    ch, vids, own_all = uploads(tok)
    own = None if chan == "quiz" else own_all   # quiz: no links at all
    print(f"{chan}: channel '{ch['snippet']['title']}', {len(vids)} uploads scanned")
    flagged = []
    for v in vids:
        sn, loc = v["snippet"], v.get("localizations") or {}
        links, sites = scan(sn.get("description"), chan, own)
        lflag = {}
        for lang, L in loc.items():
            ll, ls = scan(L.get("description"), chan, own)
            if ll or ls: lflag[lang] = ll + ls
        if not (links or sites or lflag): continue
        new = clean(sn.get("description", ""), chan, own)
        newloc = {lang: {**L, "description": clean(L.get("description", ""), chan, own)} for lang, L in loc.items()}
        flagged.append({"videoId": v["id"], "title": sn["title"], "privacy": v["status"].get("privacyStatus"),
                        "publishAt": v["status"].get("publishAt"), "links": links, "sites": sites, "localizedLinks": lflag,
                        "old": sn.get("description", ""), "new": new, "_v": v, "_newloc": newloc})
    print(f"{chan}: {len(flagged)} flagged")
    for f in flagged:
        print(f"  {f['videoId']} [{f['privacy']}] {f['title'][:70]!r}: {', '.join((f['links'] + f['sites'])[:6])}"
              + (f" +loc {sorted(f['localizedLinks'])}" if f["localizedLinks"] else ""))
    strip = lambda f: {k: v for k, v in f.items() if not k.startswith("_")}
    json.dump({"channel": ch["snippet"]["title"], "scanned": len(vids), "flagged": [strip(f) for f in flagged]},
              open(f"{out}/{chan}-report.json", "w", encoding="utf8"), indent=1, ensure_ascii=False)
    if mode != "apply": return
    json.dump([{"videoId": f["videoId"], "snippet": f["_v"]["snippet"], "localizations": f["_v"].get("localizations")}
               for f in flagged], open(f"{out}/{chan}-backup.json", "w", encoding="utf8"), indent=1, ensure_ascii=False)
    changes, hdr = [], {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
    for f in flagged:
        v, sn = f["_v"], f["_v"]["snippet"]
        body = {"id": v["id"], "snippet": {k: sn[k] for k in ("title", "tags", "categoryId", "defaultLanguage",
                                                              "defaultAudioLanguage") if k in sn}}
        body["snippet"]["description"] = f["new"]
        parts = ["snippet"]
        if v.get("localizations"):
            body["localizations"] = f["_newloc"]; parts.append("localizations")
        rec = {"videoId": v["id"], "title": sn["title"], "old": f["old"], "new": f["new"]}
        try:
            r = http(API + "videos?part=" + ",".join(parts), json.dumps(body).encode(), hdr, "PUT")
            ok = r.get("snippet", {}).get("title") == sn["title"] and r.get("snippet", {}).get("description") == f["new"]
            rec["status"] = "updated" if ok else "updated-mismatch"
        except SystemExit as e:
            rec["status"] = f"error: {e}"
        changes.append(rec)
        print(f"  {rec['status']}: {v['id']} {sn['title'][:60]!r}")
        if "quota" in rec["status"].lower(): print("quota exhausted - stopping; re-run later"); break
        time.sleep(1.5)
    json.dump(changes, open(f"{out}/{chan}-changes.json", "w", encoding="utf8"), indent=1, ensure_ascii=False)
    print(f"{chan}: {sum(c['status'] == 'updated' for c in changes)}/{len(flagged)} updated")


if __name__ == "__main__":
    main(*sys.argv[1:4])
