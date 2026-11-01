"""Recommandations « dans le meme genre », 100 % local.

Profil de gout construit a partir des ♥, des notes, du temps regarde et des lancements :
chaque video apporte des points a son theme, a ses personnes et a ses tags.
Une video candidate est notee selon ces points ; les jamais vues sont favorisees.
"""
import math
import random
from collections import defaultdict

from .common import log


def _links(conn, table, col):
    out = defaultdict(set)
    try:
        for r in conn.execute("SELECT video_id, %s FROM %s WHERE manual>=0" % (col, table)):
            out[r[0]].add(r[1])
    except Exception:
        pass
    return out


def features(conn):
    people = _links(conn, "video_people", "person_id")
    tags = _links(conn, "video_tags", "tag_id")
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM videos WHERE missing=0 AND kind='video'")}

    def f(vid):
        out = {("c", cats.get(vid))}
        out |= {("p", p) for p in people.get(vid, ())}
        out |= {("t", t) for t in tags.get(vid, ())}
        return out
    return f, cats


def video_weights(conn):
    """Interet de l'utilisateur pour chaque video deja connue."""
    w = defaultdict(float)
    for r in conn.execute("SELECT id, liked, rating, play_count FROM videos WHERE missing=0 AND kind='video' "
                          "AND (liked=1 OR rating>0 OR play_count>0)"):
        w[r["id"]] += 3.0 * (r["liked"] or 0)
        rating = r["rating"] or 0
        if rating:
            w[r["id"]] += (rating - 2.5) * 1.2           # 1-2 etoiles : interet negatif
        w[r["id"]] += 0.4 * math.log1p(r["play_count"] or 0)
    try:
        for r in conn.execute("SELECT video_id, SUM(seconds) AS s FROM watch GROUP BY video_id"):
            w[r["video_id"]] += 0.6 * math.log1p((r["s"] or 0) / 60.0)
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT video_id, COUNT(*) AS n FROM moments GROUP BY video_id"):
            w[r["video_id"]] += 1.0 * r["n"]
    except Exception:
        pass
    return w


def profile(conn):
    f, cats = features(conn)
    prof = defaultdict(float)
    for vid, weight in video_weights(conn).items():
        if vid not in cats:
            continue
        for feat in f(vid):
            prof[feat] += weight
    return prof, f, cats


def score(prof, feats):
    s = 0.0
    for feat in feats:
        v = prof.get(feat, 0.0)
        s += v * (0.5 if feat[0] == "c" else 1.0)        # personnes et tags comptent plus que le dossier
    return s


def recommended(conn, limit=40):
    """Videos recommandees (lignes SQL), les mieux notees d'abord, un peu de hasard pour varier."""
    try:
        prof, f, cats = profile(conn)
        if not prof:
            return []
        rows = conn.execute("SELECT * FROM videos WHERE missing=0 AND kind='video' AND liked=0 "
                            "AND rating IN (0, 3)").fetchall()
        scored = []
        for r in rows:
            s = score(prof, f(r["id"]))
            if s <= 0:
                continue
            s *= 1.0 if not r["play_count"] else 0.35         # favorise les jamais vues
            s *= random.uniform(0.85, 1.15)
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        # pas plus de 6 videos du meme theme pour varier
        out, per_cat = [], defaultdict(int)
        for s, r in scored:
            if per_cat[r["category"]] >= 6:
                continue
            per_cat[r["category"]] += 1
            out.append(r)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        log("recommandations : %r" % e)
        return []


def similar(conn, vid, limit=60):
    """Videos qui ressemblent a une video donnee (personnes, tags, theme), puis selon les gouts."""
    f, cats = features(conn)
    base = f(vid)
    prof = profile(conn)[0]
    out = []
    for r in conn.execute("SELECT * FROM videos WHERE missing=0 AND kind='video' AND id<>?", (vid,)):
        common = base & f(r["id"])
        if not common:
            continue
        s = sum(3.0 if k[0] == "p" else 2.0 if k[0] == "t" else 1.0 for k in common)
        s += 0.05 * score(prof, f(r["id"]))
        out.append((s + random.uniform(0, 0.3), r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:limit]]


def home_rows(main):
    rows = recommended(main.conn)
    return [{"title": "✨ Recommandé pour toi", "rows": rows}] if rows else []


def show_similar(main, vid):
    rows = similar(main.conn, vid)
    t = main.conn.execute("SELECT title FROM videos WHERE id=?", (vid,)).fetchone()
    label = "Vidéos similaires" if main.discreet or not t else "Dans le même genre que « %s »" % t["title"][:50]
    main.show_grid("%s  (%d)" % (label, len(rows)), rows)


def card_menu(main, menu, vids):
    vids = [v for v in vids if isinstance(v, int) and v > 0]
    if len(vids) != 1:
        return {}
    a = menu.addAction("✨  Vidéos dans le même genre")
    return {a: (lambda v=vids[0]: show_similar(main, v))}


def install(main):
    pass
