"""Mainland Unpacked branding v2:  python scripts/make-china-branding.py branding/china-channel

Writes avatar.png (800x800, option B: open box + Pearl Tower), banner.png (2560x1440, essentials inside
the 1235x338 box every device shows) and watermark.png (150x150, transparent).

Rules from the research (2026-10-12):
- The avatar fills the whole circle with a saturated colour (near-black vanishes in dark mode, white in light mode).
- One bold mark that still reads at 40 px, centred for the circle crop; no small text.
- The banner carries one clear message plus the schedule; everything that matters sits in the central band; the colours match the avatar.
- The imagery is recognisable (the Pudong skyline) instead of abstract texture.
"""
import random, sys, os, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

OUT = sys.argv[1] if len(sys.argv) > 1 else "branding/china-channel"

os.makedirs(OUT, exist_ok=True)
F = "C:/Windows/Fonts/"
RED, RED_D, RED_DD = (226, 36, 36), (150, 14, 22), (58, 6, 12)
GOLD, GOLD_L = (247, 184, 52), (255, 220, 120)
INK, WHITE = (16, 10, 12), (250, 247, 240)


def font(name, size):
    return ImageFont.truetype(F + name, int(size))


def vgrad(w, h, stops):
    """Vertical gradient through [(pos 0..1, rgb)]."""
    im = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                im.putpixel((0, y), tuple(int(a + (b - a) * k) for a, b in zip(c0, c1)))
                break
    return im.resize((w, h))


def radial(w, h, cx, cy, r, rgb, alpha):
    g = Image.new("L", (w, h), 0)
    ImageDraw.Draw(g).ellipse([cx - r, cy - r, cx + r, cy + r], fill=alpha)
    g = g.filter(ImageFilter.GaussianBlur(r * 0.45))
    return Image.new("RGB", (w, h), rgb), g


# ---------- landmark silhouettes (drawn into an L mask, 255 = building) ----------
def pearl_tower(d, cx, base, h, fill=255, hole=0):
    """Oriental Pearl Tower: inclined legs, three columns, two big spheres, a small one, spire."""
    leg = h * 0.2
    for dx in (-1, 1):
        d.polygon([(cx + dx * h * 0.11, base), (cx + dx * h * 0.075, base),
                   (cx + dx * h * 0.012, base - leg), (cx + dx * h * 0.035, base - leg)], fill=fill)
    for dx in (-0.032, 0, 0.032):
        w = h * 0.011
        d.rectangle([cx + dx * h - w, base - h * 0.66, cx + dx * h + w, base], fill=fill)
    r1, y1 = h * 0.088, base - h * 0.29
    d.ellipse([cx - r1, y1 - r1, cx + r1, y1 + r1], fill=fill)
    for yy in (0.43, 0.49, 0.55):                       # the small spheres between the big ones
        r = h * 0.02
        d.ellipse([cx - r, base - h * yy - r, cx + r, base - h * yy + r], fill=fill)
    r2, y2 = h * 0.058, base - h * 0.66
    d.ellipse([cx - r2, y2 - r2, cx + r2, y2 + r2], fill=fill)
    d.rectangle([cx - h * 0.012, base - h * 0.80, cx + h * 0.012, y2], fill=fill)
    r3, y3 = h * 0.024, base - h * 0.80
    d.ellipse([cx - r3, y3 - r3, cx + r3, y3 + r3], fill=fill)
    d.polygon([(cx - h * 0.007, y3), (cx + h * 0.007, y3), (cx, base - h)], fill=fill)


def shanghai_tower(d, cx, base, h, fill=255):
    """Twisting taper with the sloped crown."""
    pts = []
    for i in range(41):
        t = i / 40
        w = h * (0.085 - 0.05 * t) * (1 + 0.06 * math.sin(t * math.pi))
        pts.append((cx - w + h * 0.012 * math.sin(t * 2.2), base - h * t * 0.97))
    right = []
    for i in range(41):
        t = i / 40
        w = h * (0.085 - 0.046 * t) * (1 + 0.06 * math.sin(t * math.pi))
        right.append((cx + w + h * 0.012 * math.sin(t * 2.2), base - h * t * (0.97 if i < 40 else 1.0)))
    d.polygon(pts + right[::-1], fill=fill)


def swfc(d, cx, base, h, fill=255, hole=0):
    """Shanghai World Financial Center: the 'bottle opener', with the trapezoid hole at the top."""
    d.polygon([(cx - h * 0.075, base), (cx + h * 0.075, base), (cx + h * 0.032, base - h), (cx - h * 0.032, base - h)], fill=fill)
    d.polygon([(cx - h * 0.026, base - h * 0.95), (cx + h * 0.026, base - h * 0.95),
               (cx + h * 0.02, base - h * 0.88), (cx - h * 0.02, base - h * 0.88)], fill=hole)


def jin_mao(d, cx, base, h, fill=255):
    """Jin Mao: pagoda-like stepped tiers and a spire."""
    y, w = base, h * 0.07
    tiers = [0.34, 0.16, 0.12, 0.09, 0.07, 0.05, 0.04]
    for k, th in enumerate(tiers):
        top = y - h * th
        d.polygon([(cx - w, y), (cx + w, y), (cx + w * 0.93, top), (cx - w * 0.93, top)], fill=fill)
        y, w = top, w * 0.84
    d.polygon([(cx - h * 0.012, y), (cx + h * 0.012, y), (cx, base - h)], fill=fill)


def skyline_mask(w, h, horizon, scale, rnd):
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    # back filler blocks
    x = 0
    while x < w:
        bw = rnd.randint(int(scale * .05), int(scale * .14)); bh = rnd.randint(int(scale * .04), int(scale * .14))
        d.rectangle([x, horizon - bh, x + bw, horizon], fill=255)
        if rnd.random() < .3: d.rectangle([x + bw * .4, horizon - bh - scale * .05, x + bw * .5, horizon - bh], fill=255)
        x += bw + rnd.randint(0, int(scale * .02))
    return m, d


def windows(img, mask, rnd, n, size, cols, ymax=None):
    d = ImageDraw.Draw(img)
    px = mask.load()
    W, H = mask.size
    for _ in range(n):
        x, y = rnd.randrange(W), rnd.randrange(ymax or H)
        if px[x, y] > 200 and px[min(W - 1, x + size), min(H - 1, y + size)] > 200:
            d.rectangle([x, y, x + size, y + size * 1.4], fill=rnd.choice(cols))


# ---------- icons (gold line art, drawn in a box of side s at x, y) ----------
def icon(d, kind, x, y, s, col, lw):
    if kind == "ev":
        d.rounded_rectangle([x + s * .05, y + s * .45, x + s * .95, y + s * .72], radius=s * .1, outline=col, width=lw)
        d.polygon([(x + s * .22, y + s * .45), (x + s * .35, y + s * .26), (x + s * .68, y + s * .26), (x + s * .82, y + s * .45)], outline=col, width=lw)
        for cxw in (.28, .72):
            r = s * .11; d.ellipse([x + s * cxw - r, y + s * .72 - r, x + s * cxw + r, y + s * .72 + r], fill=INK, outline=col, width=lw)
        d.polygon([(x + s * .52, y + s * .29), (x + s * .44, y + s * .43), (x + s * .53, y + s * .43), (x + s * .47, y + s * .56), (x + s * .6, y + s * .38), (x + s * .51, y + s * .38)], fill=col)
    elif kind == "chip":
        d.rounded_rectangle([x + s * .25, y + s * .25, x + s * .75, y + s * .75], radius=s * .05, outline=col, width=lw)
        d.rectangle([x + s * .38, y + s * .38, x + s * .62, y + s * .62], fill=col)
        for k in (.33, .5, .67):
            for a, b, c, e in ((k, .1, k, .25), (k, .75, k, .9), (.1, k, .25, k), (.75, k, .9, k)):
                d.line([x + s * a, y + s * b, x + s * c, y + s * e], fill=col, width=lw)
    elif kind == "city":
        for bx, bt, bw in ((.12, .45, .2), (.36, .12, .22), (.62, .3, .24)):
            d.rectangle([x + s * bx, y + s * bt, x + s * (bx + bw), y + s * .88], outline=col, width=lw)
        d.line([x + s * .47, y + s * .12, x + s * .47, y + s * .02], fill=col, width=lw)
        d.line([x + s * .05, y + s * .88, x + s * .95, y + s * .88], fill=col, width=lw)
    elif kind == "clock":
        d.ellipse([x + s * .1, y + s * .1, x + s * .9, y + s * .9], outline=col, width=lw)
        d.line([x + s * .5, y + s * .5, x + s * .5, y + s * .24], fill=col, width=lw)
        d.line([x + s * .5, y + s * .5, x + s * .7, y + s * .62], fill=col, width=lw)
        d.ellipse([x + s * .46, y + s * .46, x + s * .54, y + s * .54], fill=col)
    elif kind == "cap":
        d.polygon([(x + s * .5, y + s * .18), (x + s * .95, y + s * .38), (x + s * .5, y + s * .58), (x + s * .05, y + s * .38)], outline=col, width=lw)
        d.arc([x + s * .24, y + s * .3, x + s * .76, y + s * .8], 20, 160, fill=col, width=lw)
        d.line([x + s * .86, y + s * .42, x + s * .86, y + s * .72], fill=col, width=lw)
    elif kind == "tv":
        d.rounded_rectangle([x + s * .08, y + s * .28, x + s * .92, y + s * .86], radius=s * .08, outline=col, width=lw)
        d.line([x + s * .5, y + s * .28, x + s * .32, y + s * .08], fill=col, width=lw)
        d.line([x + s * .5, y + s * .28, x + s * .68, y + s * .08], fill=col, width=lw)
        d.polygon([(x + s * .42, y + s * .42), (x + s * .42, y + s * .72), (x + s * .66, y + s * .57)], fill=col)


# ---------- avatars ----------
def avatar(kind, A=800, SS=3):
    S = A * SS
    im = vgrad(S, S, [(0, (240, 52, 44)), (1, RED_D)])
    col, al = radial(S, S, S * .5, S * .38, S * .45, (255, 120, 70), 110)
    im.paste(col, (0, 0), al)
    d = ImageDraw.Draw(im)
    if kind == "a":                               # bold MU monogram inside a gold seal ring
        d.ellipse([S * .1, S * .1, S * .9, S * .9], outline=GOLD, width=int(S * .035))
        f = font("impact.ttf", S * .42)
        bb = d.textbbox((0, 0), "MU", font=f)
        x, y = (S - (bb[2] - bb[0])) / 2 - bb[0], (S - (bb[3] - bb[1])) / 2 - bb[1]
        d.text((x + S * .012, y + S * .016), "MU", font=f, fill=RED_DD)
        d.text((x, y), "MU", font=f, fill=WHITE)
    else:
        m = Image.new("L", (S, S), 0)
        md = ImageDraw.Draw(m)
        if kind == "b":                           # open box with the Pearl Tower rising out ("unpacked")
            bx0, bx1, by0, by1 = S * .27, S * .73, S * .58, S * .84
            pearl_tower(md, S * .5, by0 + S * .02, S * .56)
            box = Image.new("L", (S, S), 0); bd = ImageDraw.Draw(box)
            bd.rectangle([bx0, by0, bx1, by1], fill=255)
            bd.polygon([(bx0, by0), (bx0 - S * .1, by0 - S * .1), (bx0 + S * .1, by0 - S * .1), (bx0 + S * .17, by0)], fill=255)   # left flap
            bd.polygon([(bx1, by0), (bx1 + S * .1, by0 - S * .1), (bx1 - S * .1, by0 - S * .1), (bx1 - S * .17, by0)], fill=255)   # right flap
            shadow = box.filter(ImageFilter.GaussianBlur(S * .02))
            im.paste(Image.new("RGB", (S, S), RED_DD), (int(S * .01), int(S * .02)), shadow)
            tshadow = m.filter(ImageFilter.GaussianBlur(S * .015))
            im.paste(Image.new("RGB", (S, S), RED_DD), (int(S * .01), int(S * .015)), tshadow)
            im.paste(Image.new("RGB", (S, S), GOLD), (0, 0), m)
            im.paste(Image.new("RGB", (S, S), WHITE), (0, 0), box)
            d.line([bx0 + S * .02, by0 + S * .06, bx1 - S * .02, by0 + S * .06], fill=(225, 215, 200), width=int(S * .012))   # tape
        else:                                     # "c": the Pearl Tower alone, big, in white on red
            pearl_tower(md, S * .5, S * .9, S * .82)
            sh = m.filter(ImageFilter.GaussianBlur(S * .015))
            im.paste(Image.new("RGB", (S, S), RED_DD), (int(S * .012), int(S * .015)), sh)
            im.paste(Image.new("RGB", (S, S), WHITE), (0, 0), m)
            d.ellipse([S * .06, S * .06, S * .94, S * .94], outline=GOLD, width=int(S * .025))
    return im.resize((A, A), Image.LANCZOS)


def circle(im, size, bg):
    sm = im.resize((size, size), Image.LANCZOS)
    out = Image.new("RGB", (size, size), bg)
    mk = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mk).ellipse([0, 0, size * 4, size * 4], fill=255)
    out.paste(sm, (0, 0), mk.resize((size, size), Image.LANCZOS))
    return out


# ---------- banner ----------
def banner(av):
    SS = 2
    W, H = 2560 * SS, 1440 * SS
    cx0, cy0, sw, sh = (W - 1546 * SS) // 2, (H - 423 * SS) // 2, 1546 * SS, 423 * SS
    horizon = cy0 + sh                                  # skyline stands on the bottom of the phone band
    im = vgrad(W, H, [(0, (20, 6, 10)), (0.40, (70, 8, 16)), (0.62, (190, 30, 30)), (0.66, (120, 14, 20)), (1, (14, 5, 8))])
    col, al = radial(W, H, W * .70, horizon - sh * .1, sh * 1.1, (255, 150, 60), 120)
    im.paste(col, (0, 0), al)
    rnd = random.Random(11)
    # skyline: filler blocks across the width, the landmarks on the right of the safe band
    m, md = skyline_mask(W, H, horizon, sh * 1.0, rnd)
    lx = cx0 + sw * .745                                 # the Pudong skyline, right of the text block, all inside the phone band
    jin_mao(md, lx + sh * .05, horizon, sh * .74)
    swfc(md, lx + sh * .25, horizon, sh * .94)
    shanghai_tower(md, lx + sh * .45, horizon, sh * 1.06)
    pearl_tower(md, lx + sh * .72, horizon, sh * .98)
    md.rectangle([0, horizon, W, H], fill=255)
    glow = m.filter(ImageFilter.GaussianBlur(SS * 14))
    im.paste(Image.new("RGB", (W, H), (255, 120, 50)), (0, 0), glow.point(lambda v: v * .35))
    im.paste(Image.new("RGB", (W, H), INK), (0, 0), m)
    windows(im, m, rnd, 7000, SS * 2, [(90, 50, 25), (150, 95, 40), (220, 160, 70)], ymax=horizon - SS * 12)
    # river reflection band under the horizon
    d = ImageDraw.Draw(im)
    for i in range(60):
        y = horizon + SS * 10 + i * SS * 9
        x = rnd.randint(cx0, cx0 + sw)
        d.line([x, y, x + rnd.randint(SS * 30, SS * 160), y], fill=(110, 40, 20), width=SS * 2)
    # left-hand text block (keep inside 1235x338 core)
    tx, ty = (W - 1235 * SS) // 2 + SS * 4, (H - 338 * SS) // 2 - SS * 6
    a = av.resize((SS * 132, SS * 132), Image.LANCZOS)
    mk = Image.new("L", a.size, 0); ImageDraw.Draw(mk).ellipse([0, 0, a.size[0] - 1, a.size[1] - 1], fill=255)
    ring = SS * 6
    d.ellipse([tx - ring, ty - ring, tx + a.size[0] + ring, ty + a.size[1] + ring], fill=GOLD)
    im.paste(a, (tx, ty), mk)
    t1 = font("impact.ttf", SS * 92)
    x1 = tx + a.size[0] + SS * 34
    for dx, dy, c in ((SS * 5, SS * 6, RED_DD), (0, 0, WHITE)):
        d.text((x1 + dx, ty - SS * 8 + dy), "MAINLAND", font=t1, fill=c)
    wM = d.textlength("MAINLAND ", font=t1)
    for dx, dy, c in ((SS * 5, SS * 6, RED_DD), (0, 0, GOLD)):
        d.text((x1 + wM + dx, ty - SS * 8 + dy), "UNPACKED", font=t1, fill=c)
    t2 = font("segoeuib.ttf", SS * 30)
    d.text((x1 + SS * 4, ty + SS * 104), "China's tech, cities & pressure, explained fast", font=t2, fill=WHITE)
    # schedule pill
    t3 = font("segoeuib.ttf", SS * 23)
    msg = "NEW SHORT DAILY  •  FULL REALITY-SHOW RECAP EVERY SATURDAY"
    pw = d.textlength(msg, font=t3) + SS * 44
    py = ty + SS * 160
    d.rounded_rectangle([tx, py, tx + pw, py + SS * 50], radius=SS * 25, fill=GOLD)
    d.text((tx + SS * 22, py + SS * 8), msg, font=t3, fill=INK)
    # topic icons row
    topics = [("ev", "EVs"), ("chip", "CHIPS"), ("city", "CITIES"), ("clock", "996"), ("cap", "GAOKAO"), ("tv", "REALITY TV")]
    t4 = font("segoeuib.ttf", SS * 21)
    x, y, s = tx, py + SS * 72, SS * 34
    for k, lab in topics:
        lw_ = d.textlength(lab, font=t4)
        bw = s + SS * 22 + lw_ + SS * 18
        d.rounded_rectangle([x, y, x + bw, y + s + SS * 14], radius=SS * 12, fill=(30, 10, 14), outline=GOLD, width=SS * 2)
        icon(d, k, x + SS * 10, y + SS * 7, s, GOLD, SS * 3)
        d.text((x + s + SS * 22, y + SS * 11), lab, font=t4, fill=WHITE)
        x += bw + SS * 12
    return im.resize((2560, 1440), Image.LANCZOS)


# Owner picked option B (2026-10-12): the open box with the Pearl Tower rising out of it.
av = avatar("b")
av.save(f"{OUT}/avatar.png")
banner(av).save(f"{OUT}/banner.png")
# Studio video watermark: a 150x150 circle on a transparent background
wm = av.resize((150, 150), Image.LANCZOS).convert("RGBA")
mk = Image.new("L", (600, 600), 0)
ImageDraw.Draw(mk).ellipse([0, 0, 599, 599], fill=255)
wm.putalpha(mk.resize((150, 150), Image.LANCZOS))
wm.save(f"{OUT}/watermark.png")
print("ok")
