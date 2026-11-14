"""Finds duplicates in a Plex-named movie library and prepares the cases for the comparator.
(FR) Cherche les doublons d'une bibliothèque de films nommée à la manière de Plex (« Titre (Année).ext ») et prépare la
liste des cas pour le comparateur.

Deux fichiers (ou dossiers de film) sont des doublons s'ils portent le même titre et la même année, sans tenir compte
des accents, de la casse, de la ponctuation ni de l'extension. Les éditions ({edition-...}) et les parties (- cd1, - pt2)
sont laissées de côté : ce sont des versions différentes, pas des doublons.

Usage : python -I trouver_doublons.py "D:\\Films" [--sortie D:\\Films\\_choix_a_faire.json]
"""
import argparse, json, os, re, unicodedata
from collections import defaultdict

VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.divx', '.wmv', '.mpg', '.ts')
NOM = re.compile(r'^(.+?) \((\d{4})\)(.*)$')

ap = argparse.ArgumentParser()
ap.add_argument('bibliotheque')
ap.add_argument('--sortie', default='')
a = ap.parse_args()
R = os.path.abspath(a.bibliotheque)
sortie = a.sortie or os.path.join(R, '_choix_a_faire.json')


def cle(titre):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFD', titre).encode('ascii', 'ignore').decode().lower())


groupes = defaultdict(list)
for e in os.scandir(R):
    if e.name.startswith(('_', '.')):
        continue
    base, ext = os.path.splitext(e.name) if e.is_file() else (e.name, '')
    if e.is_file() and ext.lower() not in VIDEO:
        continue
    if e.is_dir() and not any(f.lower().endswith(VIDEO) for f in os.listdir(e.path)):
        continue
    m = NOM.match(base)
    if not m or '{edition-' in m.group(3) or re.search(r' - (cd|pt|disc|part)\d', m.group(3), re.I):
        continue
    groupes[(cle(m.group(1)), m.group(2))].append(e.path)

cas = []
for chemins in groupes.values():
    if len(chemins) < 2:
        continue
    chemins.sort(key=lambda p: (os.path.isdir(p), p.lower()))
    for b in chemins[1:]:                       # 3 copies ou plus : A comparée à chacune des autres
        cas.append({'id': str(len(cas)), 'titre': os.path.splitext(os.path.basename(chemins[0]))[0],
                    'a': chemins[0], 'b': b, 'type': 'doublon', 'note': ''})

json.dump(cas, open(sortie, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'{len(cas)} doublon(s) / duplicate(s) -> {sortie}')
