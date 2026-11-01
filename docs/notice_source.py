from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

D = "/usr/share/fonts/truetype/dejavu/"
import os as _os
_W = _os.path.join(_os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
for _reg, _bold in ((D + "DejaVuSans.ttf", D + "DejaVuSans-Bold.ttf"),
                    (_os.path.join(_W, "segoeui.ttf"), _os.path.join(_W, "segoeuib.ttf")),
                    (_os.path.join(_W, "arial.ttf"), _os.path.join(_W, "arialbd.ttf"))):
    if _os.path.isfile(_reg) and _os.path.isfile(_bold):
        pdfmetrics.registerFont(TTFont("Sans", _reg))
        pdfmetrics.registerFont(TTFont("Sans-B", _bold))
        break
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-B")

INK = colors.HexColor("#1b1d24")
ACCENT = colors.HexColor("#3f6fd8")
SOFT = colors.HexColor("#eef2fb")
KEY = colors.HexColor("#dfe6f6")
GREY = colors.HexColor("#5d6170")

h1 = ParagraphStyle("h1", fontName="Sans-B", fontSize=24, leading=28, textColor=INK, spaceAfter=2)
sub = ParagraphStyle("sub", fontName="Sans", fontSize=10.5, leading=14, textColor=GREY, spaceAfter=10)
h2 = ParagraphStyle("h2", fontName="Sans-B", fontSize=14, leading=18, textColor=ACCENT, spaceBefore=12, spaceAfter=5)
h3 = ParagraphStyle("h3", fontName="Sans-B", fontSize=11, leading=14, textColor=INK, spaceBefore=8, spaceAfter=4)
body = ParagraphStyle("body", fontName="Sans", fontSize=9.5, leading=13.5, textColor=INK, spaceAfter=4)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=2)
keyst = ParagraphStyle("key", fontName="Sans-B", fontSize=9.5, leading=12.5, textColor=INK)
cell = ParagraphStyle("cell", fontName="Sans", fontSize=9.5, leading=12.5, textColor=INK)
note = ParagraphStyle("note", parent=body, fontSize=9, textColor=GREY)
big = ParagraphStyle("big", fontName="Sans-B", fontSize=18, leading=22, textColor=INK, alignment=TA_CENTER)


def bl(text):
    return Paragraph(text, bullet, bulletText="•")


def keys(rows, width=170 * mm, kcol=48 * mm):
    data = [[Paragraph(k, keyst), Paragraph(v, cell)] for k, v in rows]
    t = Table(data, colWidths=[kcol, width - kcol])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), KEY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#c9d1e4")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#b7c2dc")),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def callout(text):
    t = Table([[Paragraph(text, body)]], colWidths=[170 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t


PARTOUT = [
    ("0", "Revenir à l'accueil"),
    ("1 · 2 · 3", "Aller au lecteur de l'écran 1, 2 ou 3 (il devient le lecteur actif)"),
    ("Maj + Espace", "Pause / lecture de <b>tous</b> les lecteurs"),
    ("Maj + ←  /  →", "Tous les lecteurs reculent / avancent de 10 s"),
    ("Maj + ↑  /  ↓", "Volume de <b>tous</b> les lecteurs"),
    ("Maj + M", "Couper / remettre le son de tous les lecteurs"),
    ("Maj + Échap", "Fermer tous les lecteurs (positions mémorisées)"),
    ("Maj + C", "Changer le format d'image de tous les lecteurs"),
    ("Maj + B", "Fond flou sur les bords : tous les lecteurs"),
    ("Maj + R  /  Maj + E", "Décaler les vidéos d'un écran (1 → 2 → 3 → 1 / sens inverse)"),
    ("Ctrl + D", "<b>Mode discret</b> : cacher / montrer miniatures, titres et bannière"),
    ("F12", "<b>Touche panique</b> : tout en pause (vidéos et musique) et tout masqué. "
            "F12 à nouveau pour revenir"),
]
LECTEUR = [
    ("Espace", "Pause / lecture"),
    ("←  /  →", "Reculer / avancer de 10 secondes"),
    ("Ctrl + ←  /  →", "Reculer / avancer d'1 minute"),
    ("↑  /  ↓", "Volume de ce lecteur"),
    ("M", "Couper / remettre le son de ce lecteur"),
    ("F", "Plein écran / fenêtre"),
    ("L", "♥ J'aime / je n'aime plus cette vidéo"),
    ("B", "Fond flou sur les bords (quand l'image ne remplit pas l'écran)"),
    ("C", "Format d'image : bandes noires → remplir l'écran → étirer → 4:3 → 16:9 → …"),
    ("I", "Infos : titre, temps écoulé, durée, volume"),
    ("Échap", "Fermer ce lecteur (la position est mémorisée)"),
    ("N  /  P", "Playlist : vidéo suivante / précédente"),
]
MUSIQUE = [
    ("Ctrl + Espace", "Lecture / pause de la musique"),
    ("Ctrl + N", "Morceau suivant"),
    ("Ctrl + B", "Morceau précédent"),
    ("Ctrl + ↑  /  ↓", "Volume de la musique"),
    ("Ctrl + R", "Montage au rythme : marche / arrêt"),
]
ACCUEIL = [
    ("Entrée / double-clic", "Lancer la vidéo sélectionnée"),
    ("Flèches", "Se déplacer dans une rangée ou une grille"),
    ("Molette", "Faire défiler la page (même au-dessus d'une rangée)"),
    ("Maj + molette, ‹ ›", "Faire défiler une rangée de côté"),
    ("Souris sur une vignette", "Aperçu animé de la vidéo"),
    ("Clic sur un titre de rangée", "Voir toutes les vidéos du thème"),
    ("Clic droit sur un titre", "Définir / retirer le thème favori"),
    ("Page suiv. / Page préc.", "Bannière : diapositive suivante / précédente"),
    ("Ctrl + L", "Bannière : lire la vidéo en vedette"),
    ("Ctrl + E", "Bannière : lancer le thème sur tous les écrans"),
]


def footer(c, doc):
    c.saveState()
    c.setFont("Sans", 8)
    c.setFillColor(GREY)
    c.drawString(20 * mm, 12 * mm, "Phoque Al — notice d'utilisation")
    c.drawRightString(190 * mm, 12 * mm, "page %d" % doc.page)
    c.restoreState()


s = []
s.append(Paragraph("Phoque Al", h1))
s.append(Paragraph("Notice d'utilisation — vidéothèque locale, lecture sur plusieurs écrans", sub))
s.append(callout("<b>L'essentiel :</b> Phoque Al se pilote presque entièrement <b>au clavier</b>. "
                 "Le <b>lecteur actif</b> (entouré d'un cadre bleu) reçoit les touches simples. "
                 "Ajouter <b>Maj</b> applique la même commande à <b>tous</b> les lecteurs. "
                 "Ajouter <b>Ctrl</b> pilote la <b>musique</b>. "
                 "Un aide-mémoire à imprimer se trouve en dernière page."))

s.append(Paragraph("1. Démarrer", h2))
s.append(bl("Branche le disque externe qui contient les vidéos."))
s.append(bl("Lance Phoque Al : double-clic sur <b>Phoque Al.exe</b> (ou <b>lancer_phoqueal.bat</b>) dans le dossier Phoque Al."))
s.append(bl("Première fois seulement : <b>Paramètres → Bibliothèque → « Ajouter un dossier source… »</b>, "
            "puis choisis le dossier des vidéos. "
            "Phoque Al analyse le disque et prépare les miniatures en arrière-plan (l'avancement s'affiche en bas)."))
s.append(bl("Après avoir ajouté ou supprimé des vidéos : bouton <b>« Actualiser »</b>."))
s.append(Paragraph("La lettre du disque (E:, F:…) peut changer : Phoque Al le reconnaît quand même. "
                   "Disque débranché : la bibliothèque reste visible, la lecture revient quand le disque est rebranché.", note))

s.append(Paragraph("2. L'accueil", h2))
s.append(bl("<b>Bannière</b> en haut : jusqu'à 6 diapositives qui défilent (à reprendre, thème à découvrir, "
            "thème favori, jamais vue, à revoir, nouveauté), avec la vidéo qui joue en fond. "
            "Le défilement se met en pause quand la souris est dessus."))
s.append(bl("Boutons de la bannière : <b>Lire / Reprendre</b>, <b>Sur les 3 écrans</b> (lance 3 vidéos du thème, "
            "une par écran), <b>Voir le thème</b>. Les petites vignettes « Dans ce thème » se lancent d'un clic."))
s.append(bl("<b>Rangées</b> : une par studio ou thème (les dossiers du disque). Clic sur le titre = toutes les vidéos."))
s.append(bl("<b>Recherche</b> : tape quelques mots (titre, studio, thème) ; les accents ne comptent pas."))
s.append(bl("<b>Aperçus</b> : laisse la souris sur une vignette pour la voir s'animer. "
            "Le bouton « Aperçus » choisit le mode : extraits vidéo (si les données sont sur un SSD) ou images."))

s.append(Paragraph("Personnes & tags", h3))
s.append(bl("Bouton <b>« Personnes & tags »</b> : Phoque Al analyse les noms de fichiers et <b>propose</b> des noms de personnes "
            "et des tags, triés par nombre de fichiers. Coche, corrige si besoin, puis <b>Valider</b> (ou Rejeter, ou "
            "« C'est plutôt un tag »). Survoler une proposition montre des exemples de fichiers."))
s.append(bl("Les noms validés sont <b>reconnus automatiquement</b> dans tous les fichiers, même collés (« skylarsnow »). "
            "« Autres noms… » ajoute des orthographes ; « Fusionner » regroupe deux noms ; « Mots-clés… » règle un tag."))
s.append(bl("Fichier sans nom exploitable : clic droit sur la vignette → <b>« Personnes et tags… »</b> (plusieurs vidéos à la fois possible)."))
s.append(bl("La recherche trouve aussi les personnes et tags (boutons au-dessus des résultats). Chaque personne ou tag a sa page "
            "(Tout lire, Aléatoire, Sur les 3 écrans, En boucle, Copier en playlist) et peut servir de source de mosaïque. "
            "Rangées « Personnes » et « Tags » sur l'accueil (masquées en mode discret)."))

s.append(Paragraph("3. Lancer une vidéo sur un écran", h2))
s.append(bl("Menu <b>« Écran »</b> en haut : <b>auto</b> (premier écran libre) ou un écran précis."))
s.append(bl("Bouton <b>« Identifier »</b> : affiche le numéro de chaque écran (numérotés de gauche à droite)."))
s.append(bl("<b>Double-clic</b> ou <b>Entrée</b> sur une vignette : la vidéo s'ouvre en plein écran. "
            "Elle reprend là où tu l'avais laissée."))
s.append(bl("En « auto », si <b>tous les écrans sont occupés</b>, Phoque Al affiche les numéros sur les écrans et "
            "demande où lancer la vidéo (touches 1, 2, 3 ou clic ; option dans Paramètres → Affichage)."))
s.append(bl("Bouton <b>#</b> de la télécommande (ou du téléphone) : affiche le numéro de chaque écran et ce qu'il lit."))
s.append(bl("Chaque lecteur a <b>son propre son</b>, réglable ou coupé indépendamment."))

s.append(PageBreak())
s.append(Paragraph("4. Les touches", h2))
s.append(Paragraph("Ce sont les <b>touches par défaut</b> : chacune peut être changée dans "
                   "<b>Paramètres → Clavier</b> (le bouton « Raccourcis » affiche toujours les touches actuelles).", note))
s.append(Paragraph("À retenir : <b>touche simple</b> = lecteur actif · <b>Maj</b> = tous les lecteurs · "
                   "<b>Ctrl</b> = musique · <b>0 à 3</b> = changer d'écran · <b>F12</b> = panique.", body))
s.append(KeepTogether([Paragraph("Partout (accueil comme lecteurs)", h3), keys(PARTOUT)]))
s.append(KeepTogether([Paragraph("Dans le lecteur actif (cadre bleu)", h3), keys(LECTEUR)]))
s.append(KeepTogether([Paragraph("Musique de fond (partout)", h3), keys(MUSIQUE)]))
s.append(KeepTogether([Paragraph("Sur l'accueil", h3), keys(ACCUEIL)]))
s.append(Spacer(1, 4))
s.append(Paragraph("Quand on écrit dans la barre de recherche, les chiffres et lettres s'y tapent normalement : "
                   "appuie sur 1, 2 ou 3 après avoir cliqué ailleurs.", note))

s.append(Paragraph("Photos", h3))
s.append(bl("Phoque Al lit aussi les <b>photos</b> (JPG, PNG, WEBP, GIF, BMP, TIFF) et les <b>archives ZIP / RAR / CBZ / CBR</b> "
            "contenant des photos. Chaque archive devient un <b>album</b> ; les photos en vrac sont regroupées par dossier."))
s.append(bl("Rangée <b>« Albums photo »</b> sur l'accueil. Double-clic sur une photo : diaporama de l'album sur l'écran choisi "
            "(N / P pour avancer ou reculer, durée d'affichage réglable)."))
s.append(bl("<b>Paramètres → Photos</b> : inclure ou non les photos, <b>mélanger photos et vidéos</b> (thèmes, recherche, "
            "mosaïques, montage au rythme), durée d'affichage d'une photo. Les photos peuvent aussi aller dans les playlists."))
s.append(Paragraph("Les archives RAR demandent 7-Zip, WinRAR ou UnRAR.exe dans Phoque Al\\tools (l'onglet Photos indique ce qui est trouvé).", note))

s.append(Paragraph("Télécommande (mode souris)", h3))
s.append(bl("Bouton <b>« Télécommande »</b> (ou Ctrl + T) : petit panneau toujours au premier plan, à <b>déplacer où tu veux</b> "
            "sur n'importe quel écran en le glissant par sa barre du haut (sa position est mémorisée). "
            "Elle <b>disparaît après 5 s sans bouger la souris</b> et <b>réapparaît dès que la souris bouge</b> ; "
            "l'épingle 📌 la garde affichée en permanence."))
s.append(bl("Une ligne <b>« Tous les écrans »</b>, une ligne <b>par écran</b> et une ligne <b>musique</b>. Sur chaque ligne : "
            "précédent, pause / lecture, suivant, −10 s / +10 s, <b>boucle</b>, <b>aléatoire</b>, son, volume, arrêt. "
            "Sous chaque écran, une <b>ligne de temps</b> : clic ou glisser pour se déplacer dans la vidéo "
            "(en mosaïque : la case sélectionnée). Clic sur le numéro d'un écran pour y aller ; bouton ⌂ pour l'accueil, bouton rouge = panique, ▴ = version compacte."))
s.append(bl("Suivant / précédent sur une vidéo seule : Phoque Al enchaîne dans son thème. Boucle sur une vidéo seule : elle est rejouée. "
            "En mosaïque : suivant change toutes les cases, boucle = rejouer, aléatoire = ordre aléatoire."))

s.append(Paragraph("Télécommande sur téléphone", h3))
s.append(bl("Bouton <b>« Téléphone »</b> : coche « Activer », puis <b>scanne le QR code</b> avec l'appareil photo du téléphone "
            "(ou tape l'adresse affichée et le <b>code à 6 chiffres</b>). Téléphone et PC doivent être sur le <b>même Wi-Fi</b> ; "
            "sans box, active le partage de connexion du téléphone et connecte le PC à ce Wi-Fi."))
s.append(bl("Dans le navigateur du téléphone : menu ⋮ → <b>« Ajouter à l'écran d'accueil »</b> pour l'ouvrir comme une appli."))
s.append(bl("Onglet <b>Écrans</b> : les mêmes commandes que la télécommande (par écran et « Tous »), ligne de temps, ♥, "
            "rotation des écrans, musique, mode discret, panique. Onglet <b>Bibliothèque</b> : recherche, playlists et thèmes "
            "à lancer sur l'écran choisi, en aléatoire ou répartis sur tous les écrans."))
s.append(Paragraph("Si le téléphone ne se connecte pas : « Autoriser dans le pare-feu » dans la fenêtre Téléphone. "
                   "« Nouveau code » déconnecte tous les téléphones déjà appairés.", note))

s.append(Paragraph("Tablette", h3))
s.append(bl("<b>Comme télécommande</b> : comme le téléphone (bouton « Téléphone », QR code). À l'horizontale, la tablette "
            "affiche les écrans à gauche et la bibliothèque à droite ; un téléphone à l'horizontale passe en mode compact."))
s.append(bl("<b>Comme écran</b> : avec l'application gratuite spacedesk (pilote sur le PC + appli sur la tablette, USB "
            "conseillé), puis Paramètres Windows → Affichage → « Étendre ». Phoque Al détecte le nouvel écran tout seul, même "
            "s'il est branché pendant qu'il tourne (les numéros suivent la position gauche → droite)."))
s.append(Paragraph("Ordinateur satellite (ex. un portable relié par câble)", h3))
s.append(bl("Le satellite garde <b>sa propre bibliothèque</b> sur son disque. Sur lui : ajoute ses dossiers, clique sur "
            "<b>« Téléphone »</b> (note le code) et sur « Autoriser dans le pare-feu »."))
s.append(bl("Sur le PC principal : <b>Paramètres → Bibliothèque → « Ordinateurs satellites… »</b> → « Rechercher sur le réseau » "
            "(ou « Ajouter par adresse IP »), puis le code. Autorise aussi le pare-feu sur le PC principal."))
s.append(bl("Ses écrans deviennent les <b>écrans 4, 5…</b> (menu Écran, télécommande, téléphone) et sa bibliothèque apparaît "
            "sur l'accueil (rangées « 💻 »). Une vidéo du satellite est lue sur <b>son</b> disque ; une vidéo présente sur les "
            "deux machines (même nom, même taille) est lue sur place ; sinon elle passe par le câble."))
s.append(Paragraph("Pas encore pour les vidéos du satellite : J'aime, playlists, personnes/tags, aperçus animés, mosaïque "
                   "et montage sur ses écrans.", note))

s.append(Paragraph("Nouveautés : étoiles, moments, scènes, transitions, soirées, manette", h3))
s.append(bl("<b>Étoiles</b> : Alt + 1 à 5 dans le lecteur (Alt + 0 retire), ou clic droit → Noter. Rangée « Les mieux notées »."))
s.append(bl("<b>Moments favoris</b> : K marque l'instant, J saute au moment suivant de la vidéo. Rangée « Mes moments » "
            "(clic sur son titre = la <b>bobine</b> : tous les moments enchaînés). Plus → Mes moments : durée, montage."))
s.append(bl("<b>Scènes</b> : Page suiv. / Page préc. dans le lecteur. Le montage au rythme démarre sur de vrais débuts de scène."))
s.append(bl("<b>Transitions</b> entre vidéos (fondu, noir, flash, glissement, zoom, flou, au hasard) : Plus → Transitions, ou T."))
s.append(bl("<b>Soirées</b> : bouton « Soirées » → Enregistrer la soirée en cours ; rangée « Mes soirées » pour tout relancer d'un clic."))
s.append(bl("<b>Recommandé pour toi</b> (rangée de l'accueil) et clic droit → « Vidéos dans le même genre »."))
s.append(bl("<b>Statistiques</b> et <b>doublons visuels</b> (même vidéo en plusieurs qualités) : bouton « Plus »."))
s.append(bl("<b>Manette Xbox</b> : A pause, B son, X ♥, Y suivante, LB/RB écran, LT/RT ±10 s, croix ←→ scènes, ↑↓ volume, "
            "stick gauche avance rapide, stick droit musique, L3 moment, R3 format, Start numéros, Back panique."))

s.append(Paragraph("Nouveautés 2 : télécommande une main, numéros d'écran, accueil, mur d'écrans", h3))
s.append(bl("<b>Téléphone tenu droit</b> : tout tient sous le pouce, sans faire défiler. En bas : Écrans, "
            "Bibliothèque, Musique et le bouton rouge <b>!</b> (panique). Au-dessus : 1 · 2 · 3 · Tous pour choisir "
            "l'écran piloté (on peut aussi glisser à gauche / à droite sur le titre). Gros bouton pause au centre, "
            "−10 / +10 s, précédente / suivante, volume. « ⋯ » ouvre : boucle, aléatoire, format, fond flou, "
            "moment favori, scène suivante, décaler les écrans, arrêter, et la note en étoiles."))
s.append(bl("<b>Bibliothèque sur le téléphone</b> : choisir l'écran cible (Auto, Écran 1…), puis Résultats, Playlists, "
            "Thèmes ou Soirées ; la recherche est en bas, sous le pouce."))
s.append(bl("<b>Mes écrans</b> (⋯ → Mes écrans) : choisir quel écran porte le numéro 1, 2, 3… Les numéros "
            "s'affichent sur les écrans pendant le réglage. « Ordre automatique » revient à gauche → droite."))
s.append(bl("<b>Nouvelle barre</b> : les boutons moins utilisés (Paramètres, Actualiser, Personnes & tags, Téléphone, "
            "Aperçus, Identifier, Raccourcis, Statistiques…) sont dans le bouton <b>⋯</b>. "
            "⋯ → Accueil : barre complète, bandeau simplifié, ordre des rangées, diapositives."))
s.append(bl("<b>Jusqu'à 9 écrans</b> : avec 5 écrans ou plus, le téléphone montre une <b>mini-carte</b> des écrans "
            "placés comme sur le bureau Windows (ex. 3 en haut, 2 en bas). Toucher = piloter cet écran ; "
            "<b>appui long</b> = l'ajouter à un groupe (pause, volume, suivante… sur tout le groupe). Téléphone couché : "
            "même vue, sur deux colonnes. Tablette : cartes en grille. Sur le PC, quand tous les écrans sont occupés, "
            "le choix se fait sur la même mini-carte (clic ou touches 1 à 9)."))
s.append(bl("<b>Frise néon (PMV)</b> : sur l'écran principal (le plus grand, au centre — réglable), une frise "
            "lumineuse montre les temps de la musique des vidéos taguées « pmv » (ou avec PMV dans le titre). "
            "Y affiche / masque, Maj+Y change de style (Convergence, Défilement, Autoroute synthwave, "
            "Compteur), Alt+← et Alt+→ décalent de 20 ms si la frise est en avance ou en retard. "
            "Effets sur les temps : bordure néon, glitch, vignette, flash (désactivé par défaut). ⋯ → Rythme pour tout régler ; "
            "téléphone : ⋯ → Frise néon."))
s.append(bl("<b>Créer un PMV</b> (⋯ → Créer un PMV, ou clic droit sur des vidéos) : choisir les vidéos et une "
            "musique ; Phoque Al coupe sur les temps (plus serré quand la musique monte), ajoute zoom, glitch, secousse, "
            "flash et couleurs néon, puis enregistre « PMV Phoque Al - … .mp4 ». La frise s'y affiche toute seule. Styles : classique, stutter (extraits hachés), écran partagé, grille 2×2, miroir / kaléidoscope, pulse (zoom à chaque temps), ralenti / turbo, ou mix (le style suit la musique)."))
s.append(bl("<b>Mur d'écrans</b> : ⋯ → Mur d'écrans pour dessiner ses écrans à leur taille et place réelles "
            "(glisser-déposer ; « Afficher la mire » pour vérifier que les lignes restent droites). Puis clic droit sur "
            "une vidéo → <b>Lire en mur d'écrans</b>. Dans le mur : Espace pause, ← → 10 s, ↑ ↓ volume, M son, "
            "Échap fermer, touche panique."))

s.append(bl("<b>Tags proposés</b> (⋯ → Tags proposés) : Phoque Al regarde quelques images de chaque vidéo (petite IA "
            "téléchargée une fois, ≈ 155 Mo, rien ne sort du PC) et propose des tags : lieu, nombre de personnes, cheveux, "
            "style, tenue… Il apprend aussi des tags que tu as déjà posés (à partir de 6 vidéos) et repère les pmv "
            "(musique continue + coupes rapides). Valide ou refuse par lot ; au-dessus du seuil choisi, les tags "
            "s'appliquent seuls (bouton pour tout annuler). La liste des tags se modifie dans la même fenêtre."))
s.append(bl("<b>Séance</b> (⋯ → Séance) : vidéos d'une sélection montées sur ta musique, en phases (calme, montée, "
            "intense, redescente…) qui règlent la vitesse des coupes et le volume ; minuteur discret, pauses en option. "
            "Ctrl+Maj+S démarrer / arrêter, Ctrl+Maj+P pause, Ctrl+Maj+N phase suivante ; téléphone : onglet Musique."))
s.append(bl("<b>Edging guidé</b> : ⋯ → Séance → Déroulé « Edging guidé ». Cycles MONTE (coupes de plus en plus rapides) → "
            "RALENTIS → STOP (vidéos en pause, compte à rebours), chaque cycle un peu plus long, puis FINAL. "
            "La consigne s'affiche en grand sur l'écran principal."))
s.append(bl("<b>Compilation de mes moments</b> : ⋯ → Compilation de mes moments favoris : un PMV fait de tes moments "
            "marqués (K), calé sur la musique choisie, avec tous les styles."))
s.append(bl("<b>Passages forts</b> : Phoque Al repère les moments les plus intenses (mouvement + son) des vidéos lues "
            "(ou de toute la bibliothèque : ⋯ → Passages forts). H / Maj+H dans le lecteur : passage fort suivant / "
            "précédent. Le montage, les séances et les PMV partent de préférence de ces passages."))
s.append(bl("<b>PMV pendant ma musique</b> : quand le lecteur de musique de Phoque Al joue, les PMV (tag pmv ou PMV dans le "
            "titre) ne font plus entendre leur propre musique. Les PMV créés par Phoque Al ont 3 pistes son (musique + sons, "
            "musique seule, sons des vidéos seuls) : Phoque Al passe sur les sons des vidéos (voix, cris…). Les autres PMV sont "
            "coupés (ou baissés, au choix : ⋯ → PMV pendant ma musique). Les autres vidéos gardent leur son. "
            "U dans le lecteur : retirer / rendre la musique de la vidéo en cours."))
s.append(Paragraph("5. Playlists", h2))
s.append(bl("<b>Chaque thème est aussi une playlist</b> : une vidéo lancée depuis un thème est suivie des autres vidéos du "
            "thème. La page d'un thème a les boutons Tout lire, Lecture aléatoire, Répartir sur les écrans, En boucle "
            "et « Copier en playlist… ». Option désactivable dans Paramètres → Bibliothèque."))
s.append(bl("<b>♥ J'aime</b> : touche L dans le lecteur, bouton ♥ de la télécommande (à côté du titre), ou clic droit "
            "sur une vignette. Les vidéos aimées portent un petit cœur et vont dans la playlist automatique <b>« J'aime »</b> "
            "(en tête de « Mes playlists ») ; elles ont aussi leur diapositive « Vos coups de cœur » dans la bannière."))
s.append(bl("<b>Créer / remplir</b> : clic droit sur une vignette → « Ajouter à la playlist » (existante ou nouvelle). "
            "Ctrl + clic pour sélectionner plusieurs vidéos d'un coup."))
s.append(bl("<b>Retrouver</b> : rangée « Mes playlists » sur l'accueil. Une playlist s'ouvre sur sa propre page."))
s.append(bl("<b>Organiser</b> : glisser-déposer pour l'ordre, Suppr pour retirer, Renommer, Supprimer."))
s.append(bl("<b>Lire</b> : « Lire », « Lecture aléatoire », « En boucle », ou <b>« Répartir sur les 3 écrans »</b> "
            "(chaque écran enchaîne sa part de la playlist). Dans le lecteur : <b>N</b> suivante, <b>P</b> précédente."))

s.append(Paragraph("6. Mosaïques (plusieurs vidéos sur un écran)", h2))
s.append(bl("Bouton <b>« Mosaïque »</b> : choisis l'écran, le <b>nombre de vidéos</b> (2 à 16), la source "
            "(toute la bibliothèque, un thème ou une playlist), l'ordre, le départ (début ou moment au hasard), "
            "ce qui se passe à la fin d'une vidéo (<b>suivante</b> ou <b>rejouer</b>) et le son."))
s.append(KeepTogether([Paragraph("À la souris : une seule vidéo (celle sous le curseur)", h3), keys([
    ("Clic gauche", "Sélectionner la vidéo (cadre bleu)"),
    ("Double-clic", "Agrandir cette vidéo sur tout l'écran / revenir à la mosaïque"),
    ("Molette", "Volume de cette vidéo"),
    ("Ctrl + molette", "Avancer / reculer de 10 s"),
    ("Clic milieu (molette)", "Couper / remettre le son de cette vidéo"),
    ("Clic droit", "Menu : pause, son seul ici, suivante, rejouer, choisir une vidéo, "
                   "fin de vidéo propre à la tuile, ouvrir seule en plein écran, enregistrer, fermer"),
])]))
s.append(Spacer(1, 4))
s.append(bl("<b>Au clavier : toutes les vidéos de la mosaïque à la fois</b>, avec les touches du lecteur "
            "(Espace, flèches, M, N pour tout changer, I, F, Échap…). Les touches Maj + … agissent sur tous les écrans."))
s.append(bl("<b>Enregistrer</b> : Ctrl + S (ou clic droit → Enregistrer). Tout est gardé : réglages, vidéos de chaque "
            "tuile, position, volume, son, fin de vidéo. Pour la rouvrir : bouton « Mosaïque » → Mosaïques enregistrées → Charger."))
s.append(Paragraph("Conseil : toutes les vidéos sont lues depuis le disque externe ; au-delà d'une quinzaine de "
                   "vidéos simultanées (tous écrans confondus), un disque dur classique peut saccader.", note))

s.append(Paragraph("7. Musique de fond", h2))
s.append(bl("Barre en bas de l'accueil : <b>« ♪ Dossier musique… »</b> pour choisir le dossier (une seule fois)."))
s.append(bl("Boutons <b>précédent / lecture / suivant</b>, <b>Aléatoire</b>, et un curseur de volume propre à la musique. MP3, FLAC, M4A, OGG, WAV…"))
s.append(bl("Le titre du morceau s'affiche quelques secondes sur les vidéos à chaque changement."))
s.append(bl("Pour n'avoir que la musique : coupe le son des vidéos avec <b>Maj + M</b>."))
s.append(bl("<b>Montage au rythme</b> (bouton dans la barre de musique, ou Ctrl + R) : pendant la musique, les vidéos "
            "changent <b>sur les temps du morceau</b>, vers une autre vidéo prise à un moment au hasard (dans les 80 derniers %). "
            "La petite flèche du bouton règle : durée minimale d'un extrait, couper sur chaque temps / un sur deux / un sur quatre, "
            "piocher dans le thème ou la playlist en cours ou dans toute la bibliothèque, couper ou non le son des vidéos "
            "(gardé par défaut). "
            "Avec plusieurs écrans, c'est l'écran qui montre son extrait depuis le plus longtemps qui change. "
            "En <b>mosaïque</b> (menu du bouton → Mosaïques) : une case change à chaque temps, ou toutes les cases ensemble ; "
            "les vidéos sont piochées dans la source de la mosaïque (thème, playlist ou bibliothèque)."))
s.append(Paragraph("Chaque morceau est analysé une seule fois (quelques secondes) ; le tempo détecté s'affiche dans le menu du bouton.", note))

s.append(Paragraph("8. Paramètres", h2))
s.append(bl("<b>Bibliothèque</b> : ajouter ou retirer un dossier source, et <b>cocher / décocher les sous-dossiers</b> "
            "(studios, thèmes) à inclure. Retirer ou décocher ne supprime aucun fichier du disque."))
s.append(bl("<b>Rechercher les doublons…</b> (onglet Bibliothèque) : liste les fichiers identiques et les doublons "
            "probables (même durée). Les copies cochées sont <b>déplacées</b> dans le dossier <b>_Corbeille_Focal</b> "
            "du disque (rien n'est effacé tant qu'on ne vide pas la corbeille). Il reste toujours au moins une copie. "
            "Double-clic sur une ligne pour la regarder ; « Vider la corbeille Phoque Al » supprime définitivement."))
s.append(bl("<b>Affichage</b> : format d'image par défaut. <b>« Remplir l'écran »</b> rogne chaque vidéo au format exact "
            "de son écran (pratique avec des écrans de formats différents) ; on peut aussi rogner en 4:3, 16:9, 21:9…, "
            "étirer, ou garder les bandes noires. Modifiable pendant la lecture : C, Maj + C, bouton ▭ de la télécommande."))
s.append(bl("<b>Départ des vidéos</b> (onglet Affichage) : à un moment au hasard dans les 80 derniers % (par défaut) "
            "ou au début / à la reprise. La diapositive « Reprendre » reprend toujours là où tu t'étais arrêté."))
s.append(bl("<b>Télécommande</b> (onglet Affichage) : l'afficher ou non quand une vidéo démarre, et la faire "
            "réapparaître ou non quand la souris bouge (Ctrl + T l'affiche toujours)."))
s.append(bl("<b>Clavier</b> : clique dans une case et appuie sur la nouvelle touche. ↺ remet la touche par défaut, "
            "✕ la désactive, « Tout remettre par défaut » restaure tout. Phoque Al signale les touches en double."))

s.append(Paragraph("9. Bon à savoir", h2))
s.append(bl("<b>Mode discret</b> : case « Mode discret » en haut de l'accueil (ou Ctrl + D). Miniatures remplacées par "
            "des cases neutres, titres, noms de thèmes et de playlists masqués, bannière et aperçus désactivés. "
            "Le choix est mémorisé : si tu quittes Phoque Al en mode discret, il redémarre en mode discret."))
s.append(bl("Toutes les données de Phoque Al (base, miniatures, aperçus) sont dans le dossier <b>Phoque Al\\data</b>, "
            "rien ailleurs sur le PC. Pour repartir de zéro, il suffit de supprimer ce dossier."))
s.append(bl("Fin d'une vidéo : le lecteur passe à la suivante de la playlist, ou se ferme s'il n'y en a pas."))
s.append(bl("Après F12, si tout est masqué : clique sur Phoque Al dans la barre des tâches, puis appuie sur F12."))

# aide-memoire
s.append(PageBreak())
s.append(Paragraph("Aide-mémoire clavier", big))
s.append(Paragraph("touches par défaut — à imprimer et garder près du clavier", ParagraphStyle("c", parent=note, alignment=TA_CENTER)))
s.append(Spacer(1, 8))
legend = Table([[Paragraph("<b>Touche simple</b><br/>lecteur actif", cell), Paragraph("<b>Maj +</b><br/>tous les lecteurs", cell),
                 Paragraph("<b>Ctrl +</b><br/>musique", cell), Paragraph("<b>F12</b><br/>panique", cell)]],
               colWidths=[42.5 * mm] * 4)
legend.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.6, ACCENT),
                            ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.white),
                            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
s.append(legend)
s.append(Spacer(1, 10))
CHEAT = [
    ("0  ·  1 · 2 · 3", "Accueil  ·  aller à l'écran 1, 2 ou 3"),
    ("Espace", "Pause / lecture"),
    ("←  →", "-10 s / +10 s   (Ctrl : ±1 min)"),
    ("↑  ↓", "Volume"),
    ("M", "Son coupé / rétabli"),
    ("F", "Plein écran / fenêtre"),
    ("C  /  Maj + C", "Format d'image (lecteur / tous)"),
    ("B  /  Maj + B", "Fond flou sur les bords (lecteur / tous)"),
    ("L", "♥ J'aime"),
    ("I", "Infos"),
    ("Échap", "Fermer le lecteur"),
    ("N  /  P", "Playlist : suivante / précédente"),
    ("Maj + Espace / ← → / ↑ ↓ / M", "Même chose sur tous les lecteurs"),
    ("Maj + Échap", "Fermer tous les lecteurs"),
    ("Maj + R  /  Maj + E", "Décaler les vidéos d'écran"),
    ("Ctrl + Espace", "Musique : lecture / pause"),
    ("Ctrl + N  /  Ctrl + B", "Musique : suivant / précédent"),
    ("Ctrl + ↑  ↓", "Musique : volume"),
    ("Ctrl + R", "Montage au rythme : marche / arrêt"),
    ("Ctrl + S", "Mosaïque : enregistrer"),
    ("Ctrl + D", "Mode discret"),
    ("Ctrl + T", "Télécommande (mode souris)"),
    ("F12", "Panique (F12 à nouveau pour revenir)"),
]
s.append(keys(CHEAT, kcol=70 * mm))

doc = SimpleDocTemplate("../NOTICE_Phoque_Al.pdf", pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=18 * mm, bottomMargin=20 * mm, title="Phoque Al — Notice d'utilisation",
                        author="Phoque Al")
doc.build(s, onFirstPage=footer, onLaterPages=footer)
