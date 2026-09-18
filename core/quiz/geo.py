"""
Country silhouettes from the Natural Earth topojson (world-atlas@2, 50m).

- decode_topojson(): arcs -> lon/lat rings
- country_parts(): the recognisable part of a country (drops far-off
  overseas territories such as French Guiana or Hawaii)
- project(): Lambert azimuthal equal-area around the country, in km, so two
  countries drawn at the same km/px scale compare at true size
- shape_mask(): anti-aliased L-mode mask of a projected country
"""
import json
import math
import os

import numpy as np
from PIL import Image, ImageDraw

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
EARTH_R = 6371.0

_TOPO = None


def _topo():
    global _TOPO
    if _TOPO is None:
        with open(os.path.join(ASSETS, 'countries-50m.json'), encoding='utf-8') as f:
            _TOPO = json.load(f)
        sx, sy = _TOPO['transform']['scale']
        tx, ty = _TOPO['transform']['translate']
        arcs = []
        for arc in _TOPO['arcs']:
            a = np.cumsum(np.array(arc, dtype=np.float64), axis=0)
            a[:, 0] = a[:, 0] * sx + tx
            a[:, 1] = a[:, 1] * sy + ty
            arcs.append(a)
        _TOPO['_arcs'] = arcs
        _TOPO['_by_id'] = {g.get('id'): g for g in _TOPO['objects']['countries']['geometries']}
    return _TOPO


def _ring(arc_ids):
    arcs = _topo()['_arcs']
    pts = []
    for i in arc_ids:
        a = arcs[i] if i >= 0 else arcs[~i][::-1]
        pts.append(a if not pts else a[1:])
    return np.concatenate(pts) if pts else np.zeros((0, 2))


def polygons(numeric_id):
    """List of polygons; each polygon is [exterior, hole, ...] as (N,2) lon/lat arrays."""
    g = _topo()['_by_id'].get(numeric_id)
    if not g:
        return []
    if g['type'] == 'Polygon':
        return [[_ring(r) for r in g['arcs']]]
    if g['type'] == 'MultiPolygon':
        return [[_ring(r) for r in p] for p in g['arcs']]
    return []


def land_polygons():
    t = _topo()
    out = []
    for g in t['objects']['land']['geometries']:
        polys = [g['arcs']] if g['type'] == 'Polygon' else g['arcs']
        for p in polys:
            out.append([_ring(r) for r in p])
    return out


def _approx_area_km2(ring):
    if len(ring) < 3:
        return 0.0
    lat0 = math.radians(float(ring[:, 1].mean()))
    x = np.radians(ring[:, 0]) * math.cos(lat0) * EARTH_R
    y = np.radians(ring[:, 1]) * EARTH_R
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2


def _haversine(a, b):
    """Pairwise great-circle km between point sets a (N,2) and b (M,2) lon/lat."""
    la1, lo1 = np.radians(a[:, 1])[:, None], np.radians(a[:, 0])[:, None]
    la2, lo2 = np.radians(b[:, 1])[None, :], np.radians(b[:, 0])[None, :]
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def _thin(ring, n=160):
    step = max(1, len(ring) // n)
    return ring[::step]


# Per-country lon/lat keep-boxes where the generic rule picks the wrong parts.
KEEP_BOX = {
    '840': (-130, 20, -60, 50),   # United States: contiguous 48 only
}


def country_parts(numeric_id, link_km=380, min_frac=0.004):
    """The polygons that make up a country's recognisable silhouette."""
    polys = polygons(numeric_id)
    if not polys:
        return []
    if numeric_id in KEEP_BOX:
        x0, y0, x1, y1 = KEEP_BOX[numeric_id]
        polys = [p for p in polys if x0 <= p[0][:, 0].mean() <= x1 and y0 <= p[0][:, 1].mean() <= y1]
    main = max(range(len(polys)), key=lambda i: _approx_area_km2(polys[i][0]))
    lon0 = float(polys[main][0][:, 0].mean())
    # unwrap longitudes around the main part (Russia, Fiji cross the antimeridian)
    fixed = []
    for p in polys:
        rings = []
        for r in p:
            r = r.copy()
            r[:, 0] = np.where(r[:, 0] - lon0 > 180, r[:, 0] - 360, r[:, 0])
            r[:, 0] = np.where(r[:, 0] - lon0 < -180, r[:, 0] + 360, r[:, 0])
            rings.append(r)
        fixed.append(rings)
    polys = fixed
    areas = [_approx_area_km2(p[0]) for p in polys]
    big = areas[main]
    cand = [i for i in range(len(polys)) if areas[i] >= big * min_frac]
    kept = {main}
    frontier = [main]
    thin = {i: _thin(polys[i][0]) for i in cand}
    while frontier:
        i = frontier.pop()
        for j in cand:
            if j in kept:
                continue
            # large pieces (Malaysia's two halves) may sit further apart
            limit = link_km * (2.2 if areas[j] >= 0.25 * big else 1.0)
            if float(_haversine(thin[i], thin[j]).min()) <= limit:
                kept.add(j)
                frontier.append(j)
    return [polys[i] for i in sorted(kept)]


def project(parts):
    """Lambert azimuthal equal-area around the area-weighted centre -> km coords."""
    if not parts:
        return [], (0, 0, 0, 0)
    w = np.array([_approx_area_km2(p[0]) for p in parts])
    cx = np.array([p[0][:, 0].mean() for p in parts])
    cy = np.array([p[0][:, 1].mean() for p in parts])
    lon0, lat0 = math.radians(float((cx * w).sum() / w.sum())), math.radians(float((cy * w).sum() / w.sum()))
    out = []
    for p in parts:
        rings = []
        for r in p:
            lon, lat = np.radians(r[:, 0]), np.radians(r[:, 1])
            k = np.sqrt(2 / (1 + math.sin(lat0) * np.sin(lat) + math.cos(lat0) * np.cos(lat) * np.cos(lon - lon0)))
            x = EARTH_R * k * np.cos(lat) * np.sin(lon - lon0)
            y = EARTH_R * k * (math.cos(lat0) * np.sin(lat) - math.sin(lat0) * np.cos(lat) * np.cos(lon - lon0))
            rings.append(np.stack([x, -y], axis=1))  # image y grows downwards
        out.append(rings)
    allpts = np.concatenate([r for p in out for r in p])
    bbox = (float(allpts[:, 0].min()), float(allpts[:, 1].min()), float(allpts[:, 0].max()), float(allpts[:, 1].max()))
    return out, bbox


def shape_mask(projected, bbox, km_per_px, pad=4, ss=3):
    """Anti-aliased mask (PIL 'L') of a projected country at km_per_px scale."""
    x0, y0, x1, y1 = bbox
    w = max(2, int(math.ceil((x1 - x0) / km_per_px)) + 2 * pad)
    h = max(2, int(math.ceil((y1 - y0) / km_per_px)) + 2 * pad)
    big = Image.new('L', (w * ss, h * ss), 0)
    d = ImageDraw.Draw(big)
    for p in projected:
        for idx, r in enumerate(p):
            pts = ((r - [x0, y0]) / km_per_px + pad) * ss
            if len(pts) < 3:
                continue
            d.polygon([tuple(q) for q in pts.tolist()], fill=255 if idx == 0 else 0)
    return big.resize((w, h), Image.LANCZOS)


def fit_mask(numeric_id, box_w, box_h):
    """Country silhouette scaled to fit a box. Returns (mask, km_per_px)."""
    proj, bbox = project(country_parts(numeric_id))
    if not proj:
        return None, None
    kmpp = max((bbox[2] - bbox[0]) / (box_w - 8), (bbox[3] - bbox[1]) / (box_h - 8))
    return shape_mask(proj, bbox, kmpp), kmpp


def fit_mask_points(numeric_id, box_w, box_h, points, pad=4):
    """Like fit_mask, plus the pixel position of each (lon, lat) point on the mask."""
    parts = country_parts(numeric_id)
    proj, bbox = project(parts)
    if not proj:
        return None, []
    kmpp = max((bbox[2] - bbox[0]) / (box_w - 8), (bbox[3] - bbox[1]) / (box_h - 8))
    mask = shape_mask(proj, bbox, kmpp, pad=pad)
    # same centre as project()
    w = np.array([_approx_area_km2(p[0]) for p in parts])
    cx = np.array([p[0][:, 0].mean() for p in parts])
    cy = np.array([p[0][:, 1].mean() for p in parts])
    lon0, lat0 = math.radians(float((cx * w).sum() / w.sum())), math.radians(float((cy * w).sum() / w.sum()))
    out = []
    for lon, lat in points:
        lo, la = math.radians(lon), math.radians(lat)
        k = math.sqrt(2 / (1 + math.sin(lat0) * math.sin(la) + math.cos(lat0) * math.cos(la) * math.cos(lo - lon0)))
        x = EARTH_R * k * math.cos(la) * math.sin(lo - lon0)
        y = -EARTH_R * k * (math.cos(lat0) * math.sin(la) - math.sin(lat0) * math.cos(la) * math.cos(lo - lon0))
        out.append(((x - bbox[0]) / kmpp + pad, (y - bbox[1]) / kmpp + pad))
    return mask, out


def mask_at_scale(numeric_id, km_per_px):
    proj, bbox = project(country_parts(numeric_id))
    if not proj:
        return None
    return shape_mask(proj, bbox, km_per_px)


def extent_km(numeric_id):
    proj, bbox = project(country_parts(numeric_id))
    return (bbox[2] - bbox[0], bbox[3] - bbox[1]) if proj else (0, 0)


def centroid_lat(numeric_id):
    parts = country_parts(numeric_id)
    if not parts:
        return 0.0
    w = np.array([_approx_area_km2(p[0]) for p in parts])
    cy = np.array([p[0][:, 1].mean() for p in parts])
    return float((cy * w).sum() / w.sum())


def dot_world(width, height, spacing=18, radius=3, lat_top=78, lat_bottom=-58):
    """Equirectangular dotted world map (L mask), used as a subtle background."""
    ss = 1
    land = Image.new('L', (width, height), 0)
    d = ImageDraw.Draw(land)
    sx = width / 360.0
    sy = height / float(lat_top - lat_bottom)
    for p in land_polygons():
        for idx, r in enumerate(p):
            pts = np.stack([(r[:, 0] + 180) * sx, (lat_top - r[:, 1]) * sy], axis=1)
            if len(pts) >= 3:
                d.polygon([tuple(q) for q in pts.tolist()], fill=255 if idx == 0 else 0)
    arr = np.array(land)
    dots = Image.new('L', (width, height), 0)
    dd = ImageDraw.Draw(dots)
    for y in range(spacing // 2, height, spacing):
        for x in range(spacing // 2, width, spacing):
            if arr[y, x] > 127:
                dd.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)
    return dots
