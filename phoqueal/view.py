"""Format d'image : bandes noires, remplir l'ecran (rognage auto), etirer, ou rognage a un format fixe."""
from math import gcd

MODES = [
    ("original", "Format d'origine (bandes noires)"),
    ("fill", "Remplir l'écran (rognage automatique)"),
    ("stretch", "Étirer (déformer pour remplir)"),
    ("4:3", "Rogner en 4:3"),
    ("16:9", "Rogner en 16:9"),
    ("16:10", "Rogner en 16:10"),
    ("21:9", "Rogner en 21:9"),
    ("5:4", "Rogner en 5:4"),
    ("1:1", "Rogner en carré 1:1"),
    ("9:16", "Rogner en vertical 9:16"),
]
LABELS = dict(MODES)
ORDER = [m for m, _ in MODES]


def ratio(w, h):
    w, h = max(1, int(w)), max(1, int(h))
    g = gcd(w, h)
    return "%d:%d" % (w // g, h // g)


def next_mode(mode):
    i = ORDER.index(mode) if mode in ORDER else 0
    return ORDER[(i + 1) % len(ORDER)]


def apply(player, mode, width, height):
    """Applique le format a un lecteur VLC dont la surface fait width x height pixels."""
    try:
        if mode == "fill":
            player.video_set_aspect_ratio(None)
            player.video_set_crop_geometry(ratio(width, height))
        elif mode == "stretch":
            player.video_set_crop_geometry(None)
            player.video_set_aspect_ratio(ratio(width, height))
        elif mode in ORDER and mode != "original":
            player.video_set_aspect_ratio(None)
            player.video_set_crop_geometry(mode)
        else:
            player.video_set_crop_geometry(None)
            player.video_set_aspect_ratio(None)
    except Exception:
        pass
