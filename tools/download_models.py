"""Vérifie (et télécharge si besoin) les modèles d'IA avant de fabriquer l'installateur personnel,
pour que le PC hors ligne les reçoive directement. Lancé par fabriquer_installateurs.bat."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import onnxruntime  # noqa: F401  (doit etre installe : il part dans Phoque Al.exe)

from phoqueal.extras import ameliorer, autotags, separation

ok = True
clip = all((autotags.MODEL_DIR / n).is_file() for _s, n, _mb in autotags.MODEL_FILES)
if not clip:
    print("Modele des tags (CLIP) absent : telechargement...")
    try:
        autotags.download()
        clip = True
    except Exception as e:
        print("  echec : %r" % e)
        ok = False
print("Modele des tags (CLIP) :", "OK" if clip else "MANQUANT")

if not separation.model_ready():
    print("Modele de separation (Kim Vocal 2) absent : telechargement...")
    try:
        separation.download()
    except Exception as e:
        print("  echec : %r" % e)
        ok = False
print("Modele de separation :", "OK" if separation.model_ready() else "MANQUANT")
if not ameliorer.tool_ready():
    print("Real-ESRGAN (amelioration d'image) absent : telechargement...")
    try:
        ameliorer.download()
    except Exception as e:
        print("  echec : %r" % e)
        ok = False
print("Real-ESRGAN :", "OK" if ameliorer.tool_ready() else "MANQUANT")
sys.exit(0 if ok else 1)
