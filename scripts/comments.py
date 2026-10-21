"""Fetch unreplied top-level comments / post approved replies for the 3 channels.
Creds come from env (CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN per channel). Tokens are never printed.
Usage: comments.py fetch <chan> <outdir> | post <chan> <replies.json> <outdir>
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error

PFX = {"zero": "YT_ZERO", "quiz": "YT_QUIZ", "china": "YT_CHINA"}
API = "https://www.googleapis.com/youtube/v3/"


def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf8", "replace")[:300]
            if e.code >= 500 and i < 3:
                time.sleep(2 ** i); continue
            raise SystemExit(f"HTTP {e.code} {url.split('?')[0]}: {body}")


def token(chan):
    p = PFX[chan]
    d = urllib.parse.urlencode({
        "client_id": os.environ[p + "_ID"], "client_secret": os.environ[p + "_SECRET"],
        "refresh_token": os.environ[p + "_REFRESH"], "grant_type": "refresh_token"}).encode()
    return http("https://oauth2.googleapis.com/token", d, method="POST")["access_token"]


def get(tok, path, **q):
    return http(API + path + "?" + urllib.parse.urlencode(q), headers={"Authorization": "Bearer " + tok})


def own_channel(tok):
    it = get(tok, "channels", part="id,snippet", mine="true")["items"][0]
    return it["id"], it["snippet"]["title"]


def owner_replied(tok, thread, me):
    sn = thread["snippet"]
    if not sn.get("totalReplyCount"):
        return False
    reps = thread.get("replies", {}).get("comments", [])
    if len(reps) < sn["totalReplyCount"]:
        reps, pt = [], None
        while True:
            q = dict(part="snippet", parentId=thread["id"], maxResults=100)
            if pt: q["pageToken"] = pt
            r = get(tok, "comments", **q)
            reps += r.get("items", []); pt = r.get("nextPageToken")
            if not pt: break
    return any(c["snippet"].get("authorChannelId", {}).get("value") == me for c in reps)


def fetch(chan, out):
    tok = token(chan); me, name = own_channel(tok)
    rows, pt, total, titles = [], None, 0, {}
    while True:
        q = dict(part="snippet,replies", allThreadsRelatedToChannelId=me, maxResults=100, textFormat="plainText")
        if pt: q["pageToken"] = pt
        r = get(tok, "commentThreads", **q)
        for t in r.get("items", []):
            total += 1
            top = t["snippet"]["topLevelComment"]; s = top["snippet"]
            if s.get("authorChannelId", {}).get("value") == me: continue
            if owner_replied(tok, t, me): continue
            rows.append({"videoId": s.get("videoId"), "commentId": top["id"], "author": s.get("authorDisplayName"),
                         "text": s.get("textDisplay"), "publishedAt": s.get("publishedAt"), "likeCount": s.get("likeCount", 0)})
        pt = r.get("nextPageToken")
        if not pt: break
    ids = sorted({x["videoId"] for x in rows if x["videoId"]})
    for i in range(0, len(ids), 50):
        for v in get(tok, "videos", part="snippet", id=",".join(ids[i:i + 50])).get("items", []):
            titles[v["id"]] = v["snippet"]["title"]
    for x in rows: x["videoTitle"] = titles.get(x["videoId"], "")
    os.makedirs(out, exist_ok=True)
    json.dump({"channel": chan, "channelName": name, "threadsScanned": total, "unreplied": rows},
              open(f"{out}/comments-{chan}.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(f"{chan} ({name}): {total} threads, {len(rows)} unreplied")


def post(chan, replies_path, out):
    reps = json.load(open(replies_path, encoding="utf8")).get(chan, {})
    tok = token(chan); me, _ = own_channel(tok); done = []
    for cid, text in reps.items():
        text = (text or "").strip()
        if not text: continue
        r = get(tok, "commentThreads", part="snippet,replies", id=cid)
        if not r.get("items"):
            print("missing/removed", cid); continue
        if owner_replied(tok, r["items"][0], me):
            print("already replied, skip", cid); continue
        body = json.dumps({"snippet": {"parentId": cid, "textOriginal": text}}).encode()
        res = http(API + "comments?part=snippet", body, {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}, "POST")
        done.append({"parent": cid, "reply": res.get("id")}); print("posted", cid, "->", res.get("id"))
        time.sleep(8)
    os.makedirs(out, exist_ok=True)
    json.dump(done, open(f"{out}/posted-{chan}.json", "w"), indent=1)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "fetch": fetch(a[1], a[2])
    else: post(a[1], a[2], a[3])
