"""Personnes (actrices / acteurs) et tags, 100 % local.

1. Extraction : analyse des noms de fichiers pour proposer des noms de personnes et des tags.
   Les mots trop frequents dans la bibliotheque sont ecartes automatiquement des noms.
2. Validation : l'utilisateur accepte / rejette / fusionne les propositions (atelier).
3. Reconnaissance : les noms et tags valides sont retrouves dans tous les fichiers, y compris
   colles (« skylarsnow », « SofieMarie »). Les attributions manuelles priment toujours.
"""
import re
from collections import Counter, defaultdict
from pathlib import PurePath

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, aliases TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS video_people(video_id INTEGER NOT NULL, person_id INTEGER NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(video_id, person_id));
CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, keywords TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS video_tags(video_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(video_id, tag_id));
CREATE TABLE IF NOT EXISTS rejected(kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind, value));
CREATE INDEX IF NOT EXISTS idx_vp_person ON video_people(person_id);
CREATE INDEX IF NOT EXISTS idx_vt_tag ON video_tags(tag_id);
"""
# manual : 1 = ajoute a la main, -1 = retire a la main (la reconnaissance ne le remet pas), 0 = automatique

STOP = set("""
a an and or of in on with without for to by from at as is are was were be been it its this that these those
her his she he him they them their my your our you me we i not no yes all any some more most very too so
new hot big huge little small best full real true first last next only just get got gets getting make makes
watch video videos clip clips movie film films porn porno xxx sex free online hd hq sd uhd fhd high quality
part parts vol volume episode ep scene scenes cut cuts edit edited version upload test re reup trailer
one two three four five six seven eight nine ten man men girl girls guy guys boy boys woman women babe babes
teen teens milf mom step sister brother wife husband friend friends lady ladies chick chicks
le la les un une des du de et ou en au aux avec pour sur dans par regardez vid video
com net org www site official original full length hot sexy nasty dirty naughty wild crazy
up down out over into off back again day night time love loves loving takes take gives give fucking
ft feat featuring vs versus goes try hour hours min mins series complet complete channel channels
""".split())

# vocabulaire « thematique » : ces mots ne font pas partie d'un nom de personne, mais d'un tag
VOCAB = set("""
anal ass assfucked atm bang gang gangbang double penetration dp dap tp gape gaping gaped deep throat deepthroat
throatfuck throatfucking gag gagging gagged gaggers facial facials cum cumshot cumshots creampie creampies
swallow bukkake bukakke blowjob blowjobs bj handjob footjob cowgirl reverse missionary missonary doggy doggystyle
interracial bbc pov solo lesbian threesome foursome orgy group squirt squirting rough hardcore softcore amateur
compilation compilations pmv hmv cumpilation latina asian ebony blonde brunette redhead milf teen mature
street meat bull drill team chronicles factor raw toy toys fisting gloryhole bareback shemale trans ts gay
spit sloppy messy piss pissing feet bondage bdsm spanking pegging strapon stockings lingerie
""".split())
SHORT_TAGS = {"dp", "tp", "ts", "bj", "bbc", "pov", "atm", "dap", "hd"}

NOISE = re.compile(r"^(\d+p|\d+fps|h26[45]|x26[45]|hevc|avc|aac|mp4|4k|8k|\d+|s\d+|e\d+|[a-z]?\d+[a-z]+\d*|"
                   r"[a-z]+\d+[a-z]*\d*|hqcollect|upscaled|enhanced|ai|remastered|uncensored|censored)$")
SITE = re.compile(r"(?i)(spankbang\.com|spankbang|eroprofile|sexyporn|xhamster|pornhub|xvideos|youporn|"
                  r"^watch\s+|^regardez\s+|\b\w+\.(com|net|org|xxx)\b)")


def _ensure(conn):
    conn.executescript(SCHEMA)


def tokens(filename):
    """[(mot_original, mot_minuscule)] d'un nom de fichier nettoye."""
    stem = PurePath(filename).stem
    stem = SITE.sub(" ", stem)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)          # CamelCase -> Camel Case
    stem = re.sub(r"[_+.\-()\[\]{}#,;:!?&'\"@|/\\]+", " ", stem)
    out = []
    for w in stem.split():
        lw = w.lower()
        if NOISE.match(lw):
            continue
        out.append((w, lw))
    return out


def squash(text):
    return re.sub(r"[^a-z]", "", text.lower())


# ---------------------------------------------------------------------------------
# Propositions
# ---------------------------------------------------------------------------------
def propose(conn, min_files=2):
    """Retourne (personnes, tags) proposes : listes de (nom, nb_fichiers, exemples)."""
    _ensure(conn)
    rows = conn.execute("SELECT id, rel_path, category FROM videos WHERE missing=0 AND kind='video'").fetchall()
    n = max(1, len(rows))
    cats = {r["category"].lower() for r in rows}
    known_people = {squash(r["name"]) for r in conn.execute("SELECT name FROM people")}
    for r in conn.execute("SELECT aliases FROM people"):
        known_people |= {squash(a) for a in r["aliases"].split("|") if a.strip()}
    known_tags = {r["name"].lower() for r in conn.execute("SELECT name FROM tags")}
    rejected = {(r["kind"], r["value"]) for r in conn.execute("SELECT kind, value FROM rejected")}

    per_file = []
    word_df = Counter()
    for r in rows:
        toks = tokens(PurePath(r["rel_path"]).name)
        per_file.append((r, toks))
        word_df.update({lw for _, lw in toks})
    generic = {w for w, c in word_df.items() if c > n * 0.06 and c > 10}

    def name_like(w):
        return (w.isalpha() and 2 <= len(w) <= 14 and w not in STOP and w not in VOCAB
                and w not in generic and w not in cats)

    pairs = Counter()
    strong = Counter()
    examples = defaultdict(list)
    display = {}
    for r, toks in per_file:
        seen = set()
        for i in range(len(toks) - 1):
            (o1, w1), (o2, w2) = toks[i], toks[i + 1]
            if not (name_like(w1) and name_like(w2)):
                continue
            key = w1 + " " + w2
            if key in seen:
                continue
            seen.add(key)
            pairs[key] += 1
            if o1[:1].isupper() and o2[:1].isupper() and not o1.isupper():
                strong[key] += 1
                display.setdefault(key, o1.capitalize() + " " + o2.capitalize())
            if len(examples[key]) < 3:
                examples[key].append(PurePath(r["rel_path"]).name)
    # « jonctions » entre deux noms qui se suivent (« Reyes Billy » dans « Rico Reyes Billy Boston ») :
    # ecartees si le debut termine un nom plus frequent ET la fin commence un nom plus frequent
    ends = defaultdict(int)
    starts = defaultdict(int)
    for key, c in pairs.items():
        a, b = key.split()
        ends[b] = max(ends[b], c)
        starts[a] = max(starts[a], c)
    people = []
    for key, c in pairs.items():
        if c < min_files and strong[key] == 0:
            continue
        a, b = key.split()
        if ends.get(a, 0) > c and starts.get(b, 0) > c:
            continue
        if squash(key) in known_people or ("person", key) in rejected:
            continue
        name = display.get(key) or " ".join(w.capitalize() for w in key.split())
        score = c + strong[key]
        people.append((name, c, examples[key], score))
    people.sort(key=lambda t: (-t[3], t[0]))

    in_names = {w for p in people for w in p[0].lower().split()}
    tags = []
    for w, c in word_df.most_common():
        if c < 5:
            break
        if (not w.isalpha() or (len(w) < 3 and w not in SHORT_TAGS) or w in STOP or w in known_tags
                or ("tag", w) in rejected or w in in_names or w.startswith("http")):
            continue
        tags.append((w, c, [], c + (1000 if w in VOCAB else 0)))
    # expressions de deux mots thematiques (« double penetration », « reverse cowgirl »)
    bigrams = Counter()
    for r, toks in per_file:
        seen = set()
        for i in range(len(toks) - 1):
            w1, w2 = toks[i][1], toks[i + 1][1]
            if w1 in VOCAB and w2 in VOCAB and w1 != w2:
                key = w1 + " " + w2
                if key not in seen:
                    seen.add(key)
                    bigrams[key] += 1
    for key, c in bigrams.items():
        if c >= 5 and key not in known_tags and ("tag", key) not in rejected:
            tags.append((key, c, [], c + 1000))
    tags.sort(key=lambda t: -t[3])
    return [p[:3] for p in people[:400]], [t[:3] for t in tags[:300]]


def reject(conn, kind, value):
    conn.execute("INSERT OR IGNORE INTO rejected(kind, value) VALUES (?,?)", (kind, value.lower()))
    conn.commit()


# ---------------------------------------------------------------------------------
# Personnes et tags valides
# ---------------------------------------------------------------------------------
def add_person(conn, name, aliases=()):
    _ensure(conn)
    conn.execute("INSERT OR IGNORE INTO people(name, aliases) VALUES (?,?)", (name.strip(), "|".join(aliases)))
    conn.commit()
    return conn.execute("SELECT id FROM people WHERE name=?", (name.strip(),)).fetchone()["id"]


def add_tag(conn, name, keywords=()):
    _ensure(conn)
    kw = "|".join(keywords) if keywords else name.strip().lower()
    conn.execute("INSERT OR IGNORE INTO tags(name, keywords) VALUES (?,?)", (name.strip(), kw))
    conn.commit()
    return conn.execute("SELECT id FROM tags WHERE name=?", (name.strip(),)).fetchone()["id"]


def list_people(conn):
    _ensure(conn)
    return conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM video_people vp JOIN videos v ON v.id = vp.video_id "
        "  WHERE vp.person_id = p.id AND vp.manual >= 0 AND v.missing = 0) AS n "
        "FROM people p ORDER BY n DESC, p.name COLLATE NOCASE").fetchall()


def list_tags(conn):
    _ensure(conn)
    return conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM video_tags vt JOIN videos v ON v.id = vt.video_id "
        "  WHERE vt.tag_id = t.id AND vt.manual >= 0 AND v.missing = 0) AS n "
        "FROM tags t ORDER BY n DESC, t.name COLLATE NOCASE").fetchall()


def rename(conn, table, item_id, name):
    conn.execute("UPDATE %s SET name=? WHERE id=?" % table, (name.strip(), item_id))
    conn.commit()


def set_aliases(conn, person_id, aliases):
    conn.execute("UPDATE people SET aliases=? WHERE id=?", ("|".join(a.strip() for a in aliases if a.strip()), person_id))
    conn.commit()


def set_keywords(conn, tag_id, keywords):
    conn.execute("UPDATE tags SET keywords=? WHERE id=?", ("|".join(k.strip().lower() for k in keywords if k.strip()), tag_id))
    conn.commit()


def merge_people(conn, keep_id, other_id):
    """Fusionne other dans keep : son nom devient un alias."""
    other = conn.execute("SELECT * FROM people WHERE id=?", (other_id,)).fetchone()
    keep = conn.execute("SELECT * FROM people WHERE id=?", (keep_id,)).fetchone()
    if not other or not keep or keep_id == other_id:
        return
    aliases = [a for a in keep["aliases"].split("|") if a] + [other["name"]] + [a for a in other["aliases"].split("|") if a]
    set_aliases(conn, keep_id, aliases)
    conn.execute("INSERT OR IGNORE INTO video_people(video_id, person_id, manual) "
                 "SELECT video_id, ?, manual FROM video_people WHERE person_id=?", (keep_id, other_id))
    delete(conn, "people", other_id)


def delete(conn, table, item_id):
    link = "video_people" if table == "people" else "video_tags"
    col = "person_id" if table == "people" else "tag_id"
    conn.execute("DELETE FROM %s WHERE %s=?" % (link, col), (item_id,))
    conn.execute("DELETE FROM %s WHERE id=?" % table, (item_id,))
    conn.commit()


# ---------------------------------------------------------------------------------
# Attribution manuelle
# ---------------------------------------------------------------------------------
def video_links(conn, video_id):
    _ensure(conn)
    p = {r["person_id"] for r in conn.execute(
        "SELECT person_id FROM video_people WHERE video_id=? AND manual>=0", (video_id,))}
    t = {r["tag_id"] for r in conn.execute(
        "SELECT tag_id FROM video_tags WHERE video_id=? AND manual>=0", (video_id,))}
    return p, t


def set_link(conn, kind, video_id, item_id, on):
    table, col = ("video_people", "person_id") if kind == "person" else ("video_tags", "tag_id")
    conn.execute("INSERT OR REPLACE INTO %s(video_id, %s, manual) VALUES (?,?,?)" % (table, col),
                 (video_id, item_id, 1 if on else -1))
    conn.commit()


# ---------------------------------------------------------------------------------
# Reconnaissance automatique + texte de recherche
# ---------------------------------------------------------------------------------
def rebuild(conn):
    """Recalcule les liens automatiques et le texte de recherche (dossiers + personnes + tags)."""
    _ensure(conn)
    people = []
    for r in conn.execute("SELECT id, name, aliases FROM people"):
        forms = [r["name"]] + [a for a in r["aliases"].split("|") if a.strip()]
        people.append((r["id"], [(squash(f), [w.lower() for w in f.split()]) for f in forms if squash(f)]))
    tags = []
    for r in conn.execute("SELECT id, name, keywords FROM tags"):
        kws = [k for k in (r["keywords"] or r["name"].lower()).split("|") if k.strip()]
        tags.append((r["id"], [k.lower().split() for k in kws]))
    conn.execute("DELETE FROM video_people WHERE manual=0")
    conn.execute("DELETE FROM video_tags WHERE manual=0")
    blocked_p = {(r[0], r[1]) for r in conn.execute("SELECT video_id, person_id FROM video_people")}
    blocked_t = {(r[0], r[1]) for r in conn.execute("SELECT video_id, tag_id FROM video_tags")}
    new_p, new_t = [], []
    for v in conn.execute("SELECT id, rel_path FROM videos WHERE missing=0").fetchall():
        name = PurePath(v["rel_path"]).name
        toks = [lw for _, lw in tokens(name)]
        text = " " + " ".join(toks) + " "
        flat = squash(PurePath(name).stem)
        for pid, forms in people:
            for sq, words in forms:
                if (len(sq) >= 7 and sq in flat) or (" " + " ".join(words) + " ") in text:
                    if (v["id"], pid) not in blocked_p:
                        new_p.append((v["id"], pid))
                    break
        for tid, kws in tags:
            for words in kws:
                if (" " + " ".join(words) + " ") in text:
                    if (v["id"], tid) not in blocked_t:
                        new_t.append((v["id"], tid))
                    break
    conn.executemany("INSERT OR IGNORE INTO video_people(video_id, person_id, manual) VALUES (?,?,0)", new_p)
    conn.executemany("INSERT OR IGNORE INTO video_tags(video_id, tag_id, manual) VALUES (?,?,0)", new_t)
    # texte de recherche : dossiers + noms + tags
    names = defaultdict(list)
    for r in conn.execute("SELECT vp.video_id, p.name, p.aliases FROM video_people vp JOIN people p "
                          "ON p.id = vp.person_id WHERE vp.manual >= 0"):
        names[r[0]].append(r[1] + " " + r[2].replace("|", " "))
    for r in conn.execute("SELECT vt.video_id, t.name FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
                          "WHERE vt.manual >= 0"):
        names[r[0]].append(r[1])
    updates = []
    for v in conn.execute("SELECT id, rel_path, tags FROM videos").fetchall():
        parts = PurePath(v["rel_path"].split("::")[0]).parts[:-1]
        base = " ".join(parts) + (" photo" if "::" in v["rel_path"] or v["tags"].endswith("photo") else "")
        new = (base + " " + " ".join(names.get(v["id"], []))).strip()
        if new != v["tags"]:
            updates.append((new, v["id"]))
    conn.executemany("UPDATE videos SET tags=? WHERE id=?", updates)
    conn.commit()
    return len(new_p), len(new_t)


# ---------------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------------
def person_video_ids(conn, person_id, kind_filter=""):
    return [r["id"] for r in conn.execute(
        "SELECT v.id FROM video_people vp JOIN videos v ON v.id = vp.video_id WHERE vp.person_id=? "
        "AND vp.manual>=0 AND v.missing=0%s ORDER BY v.category COLLATE NOCASE, v.title COLLATE NOCASE"
        % kind_filter, (person_id,))]


def tag_video_ids(conn, tag_id, kind_filter=""):
    return [r["id"] for r in conn.execute(
        "SELECT v.id FROM video_tags vt JOIN videos v ON v.id = vt.video_id WHERE vt.tag_id=? "
        "AND vt.manual>=0 AND v.missing=0%s ORDER BY v.category COLLATE NOCASE, v.title COLLATE NOCASE"
        % kind_filter, (tag_id,))]


def rows_for(conn, ids):
    if not ids:
        return []
    order = {vid: i for i, vid in enumerate(ids)}
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += conn.execute("SELECT * FROM videos WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk).fetchall()
    return sorted(rows, key=lambda r: order[r["id"]])


def find(conn, text):
    """Personnes et tags dont le nom (ou un alias / mot-cle) contient le texte."""
    _ensure(conn)
    q = "%" + text.strip() + "%"
    if len(text.strip()) < 2:
        return [], []
    ppl = conn.execute("SELECT * FROM people WHERE name LIKE ? OR aliases LIKE ? ORDER BY name LIMIT 12", (q, q)).fetchall()
    tg = conn.execute("SELECT * FROM tags WHERE name LIKE ? OR keywords LIKE ? ORDER BY name LIMIT 12", (q, q)).fetchall()
    return ppl, tg
