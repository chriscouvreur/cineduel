"""CinéDuel — page locale pour trancher, au même endroit, tous les doublons et films à identifier d'une bibliothèque.

Pour chaque cas : fiches techniques (résolution, codec, taille, durée, pistes audio), boutons « VLC » pour chaque copie,
« Côte à côte » (une seule fenêtre ffplay, les deux films synchronisés au même instant, son de A) et captures pleine résolution aux mêmes
instants pour A et B (agrandissables, bascule A/B). Les choix sont enregistrés dans --choix ; le bouton « Appliquer mes choix » les applique
(perdants -> _Doublons, jamais de suppression ; film identifié « Titre (Année) » -> renommé ; journal de la bibliothèque).

Usage : python -I cineduel.py --cas cas.json --choix choix.json [--port 8770] [--racine <bibliothèque>] [--appliquer]
cas.json : [{"id": "...", "titre": "...", "a": "chemin", "b": "chemin" (absent = film à identifier), "note": "..."}]
"""
import argparse, csv, hashlib, json, os, re, subprocess, sys, threading, time, unicodedata, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolution import resolution  # noqa: E402
from langues import langues  # noqa: E402
from duree import duree  # noqa: E402
from codec import codec  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--cas', required=True)
ap.add_argument('--choix', required=True)
ap.add_argument('--port', type=int, default=8770)
ap.add_argument('--racine', default='', help='bibliothèque (défaut : dossier de --cas)')
ap.add_argument('--appliquer', action='store_true', help='appliquer les choix enregistrés puis quitter')
a = ap.parse_args()
VLC = next((p for p in (r'C:\Program Files\VideoLAN\VLC\vlc.exe', r'C:\Program Files (x86)\VideoLAN\VLC\vlc.exe')
            if os.path.exists(p)), 'vlc')
CAS = json.load(open(a.cas, encoding='utf-8'))
R = a.racine or os.path.dirname(os.path.abspath(a.cas))
DBL = os.path.join(R, '_Doublons')
INFOS = {}
VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.divx', '.wmv', '.mpg', '.ts')


def video(p):
    """Un dossier de film -> sa plus grosse vidéo."""
    if os.path.isdir(p):
        v = [os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(VIDEO)]
        return max(v, key=os.path.getsize) if v else p
    return p


LANGUE = {'fre': 'fr', 'fra': 'fr', 'eng': 'en', 'ger': 'de', 'deu': 'de', 'ita': 'it', 'spa': 'es', 'jpn': 'ja',
          'por': 'pt', 'dut': 'nl', 'nld': 'nl', 'rus': 'ru', 'kor': 'ko', 'chi': 'zh', 'zho': 'zh', 'swe': 'sv'}
CODECS = {'h264': 'avc', 'hevc': 'hevc', 'av1': 'av1', 'vp9': 'vp9', 'mpeg4': 'mpeg4', 'msmpeg4v3': 'mpeg4', 'mpeg2video': 'mpeg2'}


def sonde(v):
    """Résolution, codec, durée et langues audio via ffprobe (None si ffprobe est absent ou échoue)."""
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'format=duration:stream=codec_type,codec_name,width,height:stream_tags=language',
                            '-of', 'json', v], capture_output=True, timeout=60)
        d = json.loads(r.stdout or b'{}')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    vs = [x for x in d.get('streams', []) if x.get('codec_type') == 'video' and x.get('width')]
    if not vs:
        return None
    au = [(x.get('tags') or {}).get('language', 'und') for x in d.get('streams', []) if x.get('codec_type') == 'audio']
    return {'w': vs[0]['width'], 'h': vs[0]['height'], 'codec': CODECS.get(vs[0].get('codec_name'), vs[0].get('codec_name', '?')),
            'min': float(d.get('format', {}).get('duration') or 0) / 60,
            'audio': [LANGUE.get(x, x) for x in au if x != 'und']}


def taille(o):
    return f'{o / 2**30:.2f} Go' if o >= 2**30 else f'{o / 2**20:.0f} Mo'


def info(p):
    if p not in INFOS:
        v = video(p)
        try:
            s = sonde(v)
            if s is None:                                   # sans ffprobe : lecture directe des en-têtes
                w, h = resolution(v)
                s = {'w': w, 'h': h, 'codec': codec(v), 'min': duree(v), 'audio': [x for x in langues(v) if x != 'und']}
            o = os.path.getsize(v)
            INFOS[p] = {'nom': os.path.basename(p), 'dossier': os.path.basename(os.path.dirname(p)),
                        'res': f"{s['w']}x{s['h']}" if s['h'] else '?', 'h': s['h'] or 0, 'codec': s['codec'],
                        'go': round(o / 2**30, 3), 'taille': taille(o), 'min': round(s['min']),
                        'audio': ', '.join(dict.fromkeys(s['audio'])) or '?', 'ext': os.path.splitext(v)[1]}
        except OSError as e:
            INFOS[p] = {'nom': os.path.basename(p), 'erreur': str(e)}
    return INFOS[p]


def precharger():
    for c in CAS:
        for k in ('a', 'b'):
            if c.get(k):
                info(c[k])


def esc_filtre(p):
    """Chemin -> valeur d'option dans un graphe de filtres ffmpeg (deux niveaux d'échappement)."""
    p = p.replace('\\', '/')
    for ch in "\\:'":
        p = p.replace(ch, '\\' + ch)
    out = ''
    for ch in p:
        out += '\\' + ch if ch in "\\'[],;" else ch
    return out


def cote(pa, pb, t):
    va, vb = esc_filtre(video(pa)), esc_filtre(video(pb))
    g = (f"movie={va}:seek_point={t},scale=-2:540,setsar=1,format=yuv420p[a];"
         f"movie={vb}:seek_point={t},scale=-2:540,setsar=1,format=yuv420p[b];"
         f"[a][b]hstack=inputs=2[out0];amovie={va}:seek_point={t}[out1]")
    subprocess.Popen(['ffplay', '-hide_banner', '-loglevel', 'error', '-window_title',
                      f'A = {os.path.basename(pa)}   |   B = {os.path.basename(pb)}', '-x', '1900', '-f', 'lavfi', g])


CAPTURES = os.path.join(os.path.expanduser('~'), '.ranger-videos', 'captures')
MOMENTS = (.15, .35, .55, .75)
VERROU = threading.Lock()


def instants(c):
    """Mêmes instants pour A et B : fractions de la plus courte des deux durées."""
    d = [info(c[k]).get('min') or 0 for k in ('a', 'b') if c.get(k)]
    d = min([x for x in d if x] or [90]) * 60
    return [int(d * x) for x in MOMENTS]


def capture(c, k, n):
    """Capture pleine résolution de la copie k du cas c au n-ième instant (mise en cache sur le disque)."""
    p, t = c[k], instants(c)[n]
    nom = hashlib.md5(f'{p}|{os.path.getmtime(video(p))}|{t}'.encode()).hexdigest() + '.jpg'
    f = os.path.join(CAPTURES, nom)
    if not os.path.exists(f):
        os.makedirs(CAPTURES, exist_ok=True)
        with VERROU:                                       # une extraction à la fois (lecture sur le NAS)
            if not os.path.exists(f):
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(t), '-i', video(p),
                                '-frames:v', '1', '-q:v', '2', '-y', f + '.tmp.jpg'], capture_output=True, timeout=300)
                if os.path.exists(f + '.tmp.jpg'):
                    os.replace(f + '.tmp.jpg', f)
    return f if os.path.exists(f) else None


def precapturer():
    for c in CAS:
        for n in range(len(MOMENTS)):
            for k in ('a', 'b'):
                if c.get(k):
                    try:
                        capture(c, k, n)
                    except Exception:
                        pass


def lire_choix():
    try:
        return json.load(open(a.choix, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


# ---------- application des choix (bouton « Appliquer mes choix », ou --appliquer) ----------
JOLI = set("éèêëàâäîïôöûüùçÉÈÊÀÂÎÔÛÇ'’-:,!?&")
ANNEE = re.compile(r'^(.+?) \((\d{4})\)(.*)$')


def sans_accents(s):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode().lower())


def base_ext(p):
    if os.path.isdir(p):
        return os.path.basename(p), ''
    return os.path.splitext(os.path.basename(p))


def dans_doublons(p):
    return os.path.normcase(os.path.dirname(p)) == os.path.normcase(DBL)


def libre(t):
    if not os.path.exists(t):
        return t
    b, e = os.path.splitext(t); k = 2
    while os.path.exists(f'{b} [{k}]{e}'):
        k += 1
    return f'{b} [{k}]{e}'


def bouger(s, t, journal):
    if os.path.normcase(s) == os.path.normcase(t):
        if s != t:                                        # seule la casse change
            os.rename(s, t + '.tmp'); os.rename(t + '.tmp', t); journal.append((s, t))
        return t
    if os.path.exists(t):
        raise FileExistsError(f'« {os.path.basename(t)} » existe déjà')
    os.makedirs(os.path.dirname(t), exist_ok=True)
    os.rename(s, t); journal.append((s, t))
    return t


def aplatir(dossier, journal):
    """Dossier de film sans sous-titres -> sa vidéo remonte à la racine (règle : dossier seulement s'il y a des .srt)."""
    fichiers = os.listdir(dossier)
    if any(f.lower().endswith(('.srt', '.ass', '.sub', '.idx')) for f in fichiers):
        return dossier
    v = video(dossier)
    if v == dossier:
        return dossier
    t = bouger(v, os.path.join(R, os.path.basename(dossier) + os.path.splitext(v)[1]), journal)
    if not os.listdir(dossier):
        os.rmdir(dossier)
    return t


def appliquer_cas(c, ch, journal):
    choix, note = ch.get('choix', ''), (ch.get('note') or '').strip()
    if c.get('b'):                                        # paire
        if choix not in ('A', 'B'):
            return {'code': 'deux'} if choix == 'les deux' else None
        w, l = (c['a'], c['b']) if choix == 'A' else (c['b'], c['a'])
        if not os.path.exists(w) or not os.path.exists(l):
            return {'code': 'introuvable'}
        bw, ew = base_ext(w); bl, _ = base_ext(l)
        # nom du gagnant : celui de la bibliothèque s'il revient de _Doublons, sinon le plus « joli » des deux
        if dans_doublons(w) and not dans_doublons(l):
            cible = bl
        elif sans_accents(bl) == sans_accents(bw) and sum(ch_ in JOLI for ch_ in bl) > sum(ch_ in JOLI for ch_ in bw):
            cible = bl
        else:
            cible = bw
        if not dans_doublons(l):
            bouger(l, libre(os.path.join(DBL, os.path.basename(l))), journal)
        if os.path.isdir(w):
            w = aplatir(w, journal)
            bw, ew = base_ext(w)
        t = bouger(w, os.path.join(R, cible + ew), journal)
        return {'code': 'garde', 'nom': os.path.basename(t)}
    # film seul
    p = c['a']
    if not os.path.exists(p):
        return {'code': 'introuvable'}
    if choix == 'doublons':
        bouger(p, libre(os.path.join(DBL, os.path.basename(p))), journal)
        return {'code': 'doublons'}
    if note:
        m = ANNEE.match(note)
        if not m:
            return {'code': 'annee', 'nom': note}
        base, ext = base_ext(p)
        suite = ANNEE.match(base)
        nom = f'{m.group(1).strip()} ({m.group(2)})' + (suite.group(3) if suite else '')
        deja = [os.path.join(R, f) for f in os.listdir(R) if os.path.splitext(f)[0].lower() == nom.lower()
                and os.path.normcase(os.path.join(R, f)) != os.path.normcase(p)]
        if deja:                                          # film déjà présent -> nouvelle paire à comparer
            if not any(x.get('b') and {x['a'], x['b']} == {deja[0], p} for x in CAS):
                CAS.append({'id': str(max(int(x['id']) for x in CAS) + 1), 'titre': nom, 'a': deja[0], 'b': p,
                            'type': 'deja_present', 'nom': nom, 'note': ''})
                json.dump(CAS, open(a.cas, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            return {'code': 'deja', 'nom': os.path.basename(deja[0])}
        t = bouger(p, os.path.join(R, nom + ext), journal)
        return {'code': 'renomme', 'nom': os.path.basename(t)}
    return None


def appliquer():
    choix, journal, res = lire_choix(), [], {}
    for c in CAS:
        ch = choix.get(c['id'], {})
        if not ch or ch.get('applique'):
            continue
        try:
            r = appliquer_cas(c, ch, journal)
        except OSError as e:
            r = {'code': 'erreur', 'nom': str(e)}
        if r:
            res[c['id']] = r
            if r['code'] in ('garde', 'deux', 'doublons', 'renomme'):
                ch['applique'] = r
    with open(os.path.join(R, '_journal_renommage_plex.csv'), 'a', newline='', encoding='utf-8') as h:
        csv.writer(h).writerows((s, t, time.strftime('%Y-%m-%d')) for s, t in journal)
    json.dump(choix, open(a.choix, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return res


if a.appliquer:
    for k, v in appliquer().items():
        print(k, next((c['titre'] for c in CAS if c['id'] == k), ''), '->', v['code'], v.get('nom', ''))
    sys.exit(0)

PAGE = r'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CinéDuel</title><style>
:root{color-scheme:dark;--bg:#0f1012;--c:#17181c;--c2:#1e2025;--c3:#262830;--t:#ececf1;--m:#9b9ca8;--l:#2c2e36;--a:#e5a00d;--a2:#f4b62a;--ok:#4cc38a;--okf:rgba(76,195,138,.12);--warn:#ffb35c;--warnf:rgba(255,179,92,.12);--r:14px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--t);-webkit-font-smoothing:antialiased}
button,input{font:inherit;color:inherit}
header{position:sticky;top:0;z-index:5;background:rgba(15,16,18,.85);backdrop-filter:blur(12px);border-bottom:1px solid var(--l)}
.hd{max-width:1320px;margin:0 auto;padding:12px 28px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.logo{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px;letter-spacing:-.01em;white-space:nowrap}
.logo small{font-weight:500;font-size:12px;color:var(--m)}.logo i{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:var(--a);color:#1a1300;font-style:normal;font-size:15px}
.prog{display:flex;align-items:center;gap:10px;color:var(--m);font-size:13px;font-variant-numeric:tabular-nums;min-width:170px}
.barre{flex:1;height:6px;border-radius:9px;background:var(--c3);overflow:hidden;min-width:90px}.barre b{display:block;height:100%;width:0;background:var(--ok);border-radius:9px;transition:width .4s cubic-bezier(.2,.8,.2,1)}
.sp{flex:1}
.seg{display:inline-flex;background:var(--c2);border:1px solid var(--l);border-radius:10px;padding:3px;gap:2px}
.seg button{border:0;background:none;padding:5px 11px;border-radius:7px;font-size:13px;color:var(--m);cursor:pointer;transition:background .15s,color .15s;white-space:nowrap}
.seg button:hover{color:var(--t)}.seg button.on{background:var(--c3);color:var(--t);font-weight:600}
.seg button .n{opacity:.6;margin-left:4px;font-variant-numeric:tabular-nums}
.btn{border:1px solid var(--l);background:var(--c2);border-radius:9px;padding:7px 13px;font-size:13px;cursor:pointer;transition:background .15s,transform .1s,opacity .15s;white-space:nowrap}
.btn:hover{background:var(--c3)}.btn:active{transform:scale(.97)}.btn:disabled{opacity:.4;cursor:default;transform:none}
.btn.p{background:var(--a);border-color:var(--a);color:#1a1300;font-weight:650}.btn.p:hover{background:var(--a2)}
main{max-width:1320px;margin:0 auto;padding:22px 28px 90px}
.cas{background:var(--c);border:1px solid var(--l);border-radius:var(--r);padding:18px 20px 16px;margin-bottom:16px;transition:border-color .2s,opacity .2s}
.cas.choisi{border-color:rgba(76,195,138,.4)}.cas.fini{opacity:.6}
.tete{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}.num{color:var(--m);font-variant-numeric:tabular-nums;font-size:13px}
.tete h2{font-size:17px;margin:0;font-weight:650;letter-spacing:-.01em}
.tag{font-size:11px;font-weight:650;letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--c3);color:var(--m)}
.tag.dup{color:#9db7ff;background:rgba(91,140,255,.13)}.tag.id{color:var(--a2);background:rgba(229,160,13,.13)}.tag.fait{color:var(--ok);background:var(--okf)}
.note{color:var(--m);font-size:13px;margin:4px 0 14px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}.cols.un{grid-template-columns:minmax(0,1fr)}
.copie{position:relative;background:var(--c2);border:1px solid var(--l);border-radius:12px;padding:13px 14px;display:flex;flex-direction:column;gap:10px;transition:border-color .2s,box-shadow .2s}
.copie.garde{border-color:var(--ok);box-shadow:0 0 0 1px var(--ok) inset}
.ctete{display:flex;gap:10px;align-items:flex-start}
.lettre{flex:none;display:grid;place-items:center;width:26px;height:26px;border-radius:7px;background:var(--c3);font-weight:700;font-size:13px}
.copie.garde .lettre{background:var(--ok);color:#06210f}
.nom{font-weight:560;word-break:break-word;line-height:1.35;flex:1}
.reco{flex:none;font-size:11px;font-weight:650;color:var(--ok);background:var(--okf);padding:2px 8px;border-radius:99px;cursor:help;white-space:nowrap}
.puces{display:flex;flex-wrap:wrap;gap:5px}
.puces span{font:12px/1 ui-monospace,Consolas,monospace;padding:5px 7px;border-radius:6px;background:var(--c3);color:#cfd0d8}
.puces span.mieux{color:var(--ok);background:var(--okf);font-weight:650}
.ou{font-size:12px;color:var(--m)}
.caps{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.caps figure{margin:0;position:relative;aspect-ratio:16/9;background:#0a0a0c;border-radius:6px;overflow:hidden;cursor:zoom-in}
.caps img{width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .3s,transform .25s}.caps img.ok{opacity:1}
.caps figure:hover img{transform:scale(1.05)}
.caps figcaption{position:absolute;left:5px;bottom:3px;font-size:10.5px;color:#fff;text-shadow:0 1px 3px #000;font-variant-numeric:tabular-nums}
.caps figure.att::before{content:attr(data-att);position:absolute;inset:0;display:grid;place-items:center;font-size:11px;color:var(--m)}
.cbas{display:flex;gap:6px}
.outils{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px;color:var(--m);font-size:13px}
.outils input{font-size:13px;padding:6px 8px;border:1px solid var(--l);border-radius:8px;width:66px;background:var(--c2);text-align:center;font-variant-numeric:tabular-nums}
.decision{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px;padding-top:14px;border-top:1px solid var(--l)}
.decision .seg button{padding:7px 14px;font-size:13.5px}
.decision .seg button.on{background:var(--okf);color:var(--ok)}
.decision input.txt{flex:1;min-width:240px;font-size:13.5px;padding:8px 12px;border:1px solid var(--l);border-radius:10px;background:var(--c2)}
.decision input.txt:focus,.outils input:focus{outline:2px solid var(--a);outline-offset:-1px}
.res{margin-top:10px;font-size:13px;padding:8px 12px;border-radius:9px;background:var(--okf);color:var(--ok)}.res.ko{background:var(--warnf);color:var(--warn)}
.vide{text-align:center;color:var(--m);padding:80px 20px;font-size:16px}.vide b{display:block;font-size:40px;margin-bottom:8px}
#lb{position:fixed;inset:0;z-index:10;background:#000;display:none;flex-direction:column}#lb.on{display:flex}
#lb .vue{flex:1;display:flex;gap:4px;min-height:0}
#lb .vue div{flex:1;display:flex;flex-direction:column;min-width:0}#lb .vue img{flex:1;min-height:0;width:100%;object-fit:contain}
#lb .lt{font-size:13px;color:#ddd;padding:7px 12px;background:#111;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#lb .bas{display:flex;gap:10px;align-items:center;padding:10px 16px;background:#111;color:#bbb;font-size:13px;flex-wrap:wrap}
#lb kbd{border:1px solid #444;border-radius:4px;padding:0 5px;font-size:12px;color:#ddd}
@media (max-width:900px){.cols{grid-template-columns:1fr}.hd{padding:10px 16px}main{padding:16px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto}}
</style></head><body>
<header><div class="hd"><div class="logo"><i>⚔</i><span data-t="titre"></span><small data-t="sous"></small></div>
<div class="prog"><span id="compte"></span><div class="barre"><b id="barre"></b></div></div>
<div class="seg" id="filtres"></div><span class="sp"></span>
<div class="seg" id="langues"><button data-l="fr">FR</button><button data-l="en">EN</button></div>
<button class="btn" onclick="sauver(true)" data-t="enregistrer"></button><button class="btn p" id="appl" onclick="appliquer()" data-t="appliquer"></button></div></header>
<main id="liste"></main>
<div id="lb"><div class="vue" id="vue"></div><div class="bas"><span id="lbt"></span><span class="sp"></span><span id="lbaide"></span>
<button class="btn" onclick="bascule()">A ⇄ B</button><button class="btn" onclick="mode()" data-t="lbcote"></button><button class="btn" onclick="fermer()" data-t="fermer"></button></div></div>
<script>
const T={
fr:{titre:'CinéDuel',sous:'pour Plex et Jellyfin',enregistrer:'Enregistrer',enregistre:'enregistré ✓',appliquer:'Appliquer mes choix',atrancher:'À trancher',appliques:'Appliqués',tous:'Tous',
 compte:'{n} / {t} tranchés',dup:'Doublon',ident:'À identifier',fait:'Appliqué',reco:'Recommandé',lecture:'Lecture à',vlc:'▶ VLC',cote:'⇆ Côte à côte (vidéo)',
 garderA:'Garder A',garderB:'Garder B',garderDeux:'Garder les deux',jsp:'Je ne sais pas',versDbl:'Mettre dans _Doublons',remarque:'Remarque (facultatif)',quel:'Quel film est-ce ? Titre (Année)',
 dans:'dans',extraction:'extraction…',lbaide:'<kbd>←</kbd><kbd>→</kbd> instant · <kbd>Espace</kbd> A/B · <kbd>S</kbd> côte à côte · <kbd>Échap</kbd> fermer',lbcote:'Côte à côte',fermer:'Fermer',
 confirmer:'Appliquer {n} choix ?\n\nLes copies écartées iront dans _Doublons (rien n\'est supprimé) ; les films identifiés seront renommés.',rien:'Aucun choix à appliquer.',encours:'application en cours…',traites:'{n} cas traité(s) ✓',
 vide:'Tout est tranché',videTous:'Aucun cas',noteDup:'Deux copies du même film : compare-les et garde la meilleure.',noteId:'Film non identifié : regarde un passage et écris son titre si tu le reconnais.',noteDeja:'Tu as identifié B comme « {nom} », déjà présent dans la bibliothèque (A) : c\'est un doublon.',
 r_garde:'Gardé : {nom} · l\'autre → _Doublons',r_deux:'Les deux copies sont gardées',r_introuvable:'Fichier introuvable (déjà déplacé ?)',r_doublons:'Mis dans _Doublons',r_annee:'« {nom} » : il manque l\'année, écris « Titre (Année) »',r_deja:'Déjà présent ({nom}) : ajouté comme doublon à comparer',r_renomme:'Renommé : {nom}',r_erreur:'Non appliqué : {nom}',
 w_h:'meilleure résolution',w_eq:'plus de détail (taille corrigée selon le codec)',w_fmt:'format plus récent que .avi',w_fr:'piste audio française',w_mkv:'conteneur .mkv',min:'min',audio:'audio'},
en:{titre:'CinéDuel',sous:'for Plex & Jellyfin',enregistrer:'Save',enregistre:'saved ✓',appliquer:'Apply my choices',atrancher:'To decide',appliques:'Applied',tous:'All',
 compte:'{n} / {t} decided',dup:'Duplicate',ident:'To identify',fait:'Applied',reco:'Recommended',lecture:'Play at',vlc:'▶ VLC',cote:'⇆ Side by side (video)',
 garderA:'Keep A',garderB:'Keep B',garderDeux:'Keep both',jsp:'I don\'t know',versDbl:'Move to _Doublons',remarque:'Note (optional)',quel:'Which movie is it? Title (Year)',
 dans:'in',extraction:'extracting…',lbaide:'<kbd>←</kbd><kbd>→</kbd> moment · <kbd>Space</kbd> A/B · <kbd>S</kbd> side by side · <kbd>Esc</kbd> close',lbcote:'Side by side',fermer:'Close',
 confirmer:'Apply {n} choice(s)?\n\nDiscarded copies will go to _Doublons (nothing is deleted); identified movies will be renamed.',rien:'Nothing to apply.',encours:'applying…',traites:'{n} case(s) done ✓',
 vide:'Everything is decided',videTous:'No cases',noteDup:'Two copies of the same movie: compare them and keep the best one.',noteId:'Unidentified movie: watch a bit and type its title if you recognise it.',noteDeja:'You identified B as “{nom}”, which is already in the library (A): it is a duplicate.',
 r_garde:'Kept: {nom} · the other → _Doublons',r_deux:'Both copies kept',r_introuvable:'File not found (already moved?)',r_doublons:'Moved to _Doublons',r_annee:'“{nom}”: the year is missing, type “Title (Year)”',r_deja:'Already there ({nom}): added as a duplicate to compare',r_renomme:'Renamed: {nom}',r_erreur:'Not applied: {nom}',
 w_h:'higher resolution',w_eq:'more detail (codec-adjusted size)',w_fmt:'newer format than .avi',w_fr:'French audio track',w_mkv:'.mkv container',min:'min',audio:'audio'}};
let L=localStorage.getItem('langue')||(navigator.language||'fr').slice(0,2);if(!T[L])L='en';
const t=(k,p={})=>(T[L][k]??k).replace(/\{(\w+)\}/g,(_,x)=>p[x]??'');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let CAS=[],CHOIX={},RES={},LB=null,FILTRE='atrancher';
const $=s=>document.querySelector(s);
const post=(u,d)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(r=>r.json());
const mmss=s=>`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
const hms=s=>{const [m,x]=String(s).split(':');return x===undefined?+m*60:(+m)*60+(+x)};
const src=(n,k,i)=>`/img?c=${CAS[n].id}&k=${k}&n=${i}`;
const ch=c=>CHOIX[c.id]||{};
const decide=c=>{const h=ch(c);return !!(h.applique||h.choix||(!c.b&&h.note))};
// recommandation : mêmes pistes audio -> résolution puis taille corrigée du codec ; sinon format récent, audio FR, .mkv, résolution, taille
const F={av1:2.5,hevc:2,vp9:1.8,avc:1,mpeg4:.6,mpeg2:.4};
function reco(a,b){if(!a||!b||a.erreur||b.erreur)return null;const eq=x=>x.go*(F[x.codec]||1),au=x=>x.audio==='?'?null:x.audio;
 const mod=x=>['.mkv','.mp4','.m4v','.mov'].includes(x.ext.toLowerCase()),fr=x=>x.audio.split(', ').includes('fr');
 const cles=au(a)&&au(a)===au(b)?[['h',x=>x.h],['eq',eq]]:[['fmt',x=>+mod(x)],['fr',x=>+fr(x)],['mkv',x=>+(x.ext.toLowerCase()==='.mkv')],['h',x=>x.h],['eq',eq]];
 for(const [w,f] of cles){const d=f(a)-f(b);if(Math.abs(d)>(w==='eq'?.05*Math.max(f(a),f(b)):0))return{k:d>0?'a':'b',w}}return null}
function puces(i,o){const m=(c,f)=>o&&!o.erreur&&f(i[c],o[c])?' class="mieux"':'';
 return`<div class="puces"><span${m('h',(x,y)=>x>y)}>${esc(i.res)}</span><span${m('codec',(x,y)=>(F[x]||1)>(F[y]||1))}>${esc(i.codec)}</span><span${m('go',(x,y)=>x>y)}>${esc(i.taille||i.go+' Go')}</span><span${m('min',(x,y)=>x>y+2)}>${i.min} ${t('min')}</span><span>${esc(i.ext)}</span><span${m('audio',(x,y)=>x.includes('fr')&&!y.includes('fr'))}>${t('audio')} ${esc(i.audio)}</span></div>`}
function caps(n,k){return`<div class="caps">${CAS[n].ts.map((s,i)=>`<figure class="att" data-att="${t('extraction')}" onclick="ouvrir(${n},'${k}',${i})"><img loading="lazy" alt="" src="${src(n,k,i)}" onload="this.classList.add('ok');this.parentNode.classList.remove('att')"><figcaption>${mmss(s)}</figcaption></figure>`).join('')}</div>`}
function copie(n,k,lettre){const c=CAS[n],i=k==='a'?c.ia:c.ib,o=k==='a'?c.ib:c.ia;if(!i)return'';const r=c.b?reco(c.ia,c.ib):null,h=ch(c);
 const garde=(h.choix==='A'&&k==='a')||(h.choix==='B'&&k==='b')||h.choix==='les deux';
 if(i.erreur)return`<div class="copie"><div class="ctete"><span class="lettre">${lettre}</span><span class="nom">${esc(i.nom)}</span></div><span class="ou">${esc(i.erreur)}</span></div>`;
 return`<div class="copie${garde?' garde':''}"><div class="ctete"><span class="lettre">${lettre}</span><span class="nom">${esc(i.nom)}</span>${r&&r.k===k?`<span class="reco" title="${t('w_'+r.w)}">★ ${t('reco')}</span>`:''}</div>
 ${puces(i,c.b?o:null)}${i.dossier&&i.dossier.startsWith('_')?`<span class="ou">${t('dans')} ${esc(i.dossier)}</span>`:''}${caps(n,k)}
 <div class="cbas"><button class="btn" onclick="vlc(${n},'${k}')">${t('vlc')}</button></div></div>`}
function note(c){if(c.note)return esc(c.note);return c.type==='deja_present'?t('noteDeja',{nom:esc(c.nom)}):c.b?t('noteDup'):t('noteId')}
function resultat(c){const h=ch(c),r=h.applique||RES[c.id];if(!r)return'';const txt=typeof r==='string'?esc(r):t('r_'+r.code,{nom:esc(r.nom)});
 return`<div class="res${h.applique?'':' ko'}">${txt}</div>`}
function carte(c,n){const h=ch(c),paire=!!c.b,fini=!!h.applique;
 const opts=paire?[['A',t('garderA')],['B',t('garderB')],['les deux',t('garderDeux')]]:[['inconnu',t('jsp')],['doublons',t('versDbl')]];
 return`<section class="cas${decide(c)?' choisi':''}${fini?' fini':''}" id="c${n}"><div class="tete"><span class="num">${n+1}</span><h2>${esc(c.titre)}</h2>
 <span class="tag ${paire?'dup':'id'}">${paire?t('dup'):t('ident')}</span>${fini?`<span class="tag fait">✓ ${t('fait')}</span>`:''}</div><div class="note">${note(c)}</div>
 <div class="cols${paire?'':' un'}">${copie(n,'a','A')}${paire?copie(n,'b','B'):''}</div>
 <div class="outils">${t('lecture')} <input type="text" id="t${n}" value="${mmss(c.ts[1])}" aria-label="${t('lecture')}">${paire?`<button class="btn p" onclick="cote(${n})">${t('cote')}</button>`:''}</div>
 <div class="decision"${fini?' inert':''}><div class="seg">${opts.map(([v,l])=>`<button class="${h.choix===v?'on':''}" onclick="choisir(${n},'${v}')">${l}</button>`).join('')}</div>
 <input class="txt" placeholder="${paire?t('remarque'):t('quel')}" value="${esc(h.note||'')}" onchange="noter(${n},this.value)"></div>${resultat(c)}</section>`}
function rendre(){document.documentElement.lang=L;document.title=t('titre');document.querySelectorAll('[data-t]').forEach(e=>e.textContent=t(e.dataset.t));
 $('#lbaide').innerHTML=t('lbaide');document.querySelectorAll('#langues button').forEach(b=>b.classList.toggle('on',b.dataset.l===L));
 const n1=CAS.filter(c=>!ch(c).applique).length,n2=CAS.length-n1;
 $('#filtres').innerHTML=[['atrancher',n1],['appliques',n2],['tous',CAS.length]].map(([f,n])=>`<button class="${FILTRE===f?'on':''}" onclick="filtrer('${f}')">${t(f)}<span class="n">${n}</span></button>`).join('');
 const vis=CAS.map((c,n)=>[c,n]).filter(([c])=>FILTRE==='tous'||(FILTRE==='appliques')===!!ch(c).applique);
 $('#liste').innerHTML=vis.length?vis.map(([c,n])=>carte(c,n)).join(''):`<div class="vide"><b>${FILTRE==='atrancher'?'🎉':'—'}</b>${FILTRE==='atrancher'?t('vide'):t('videTous')}</div>`;etat()}
function etat(){const f=CAS.filter(decide).length;$('#compte').textContent=t('compte',{n:f,t:CAS.length});$('#barre').style.width=(CAS.length?100*f/CAS.length:0)+'%';
 $('#appl').disabled=!CAS.some(c=>{const h=ch(c);return !h.applique&&(['A','B','doublons','les deux'].includes(h.choix)||(!c.b&&h.note))})}
function filtrer(f){FILTRE=f;rendre()}
document.querySelectorAll('#langues button').forEach(b=>b.onclick=()=>{L=b.dataset.l;localStorage.setItem('langue',L);rendre()});
function choisir(n,v){const h=(CHOIX[CAS[n].id]??={});h.choix=h.choix===v?undefined:v;const y=scrollY;rendre();scrollTo(0,y);sauver()}
function noter(n,v){(CHOIX[CAS[n].id]??={}).note=v;etat();sauver()}
let tm;function sauver(now){clearTimeout(tm);const go=()=>post('/api/choix',CHOIX).then(()=>{etat();if(now)$('#compte').textContent+=' · '+t('enregistre')});now?go():tm=setTimeout(go,400)}
function vlc(n,k){post('/api/vlc',{p:CAS[n][k],t:hms($('#t'+n).value)})}
function cote(n){post('/api/cote',{a:CAS[n].a,b:CAS[n].b,t:hms($('#t'+n).value)})}
function charger(){return Promise.all([fetch('/api/cas').then(r=>r.json()),fetch('/api/choix').then(r=>r.json())]).then(([c,h])=>{CHOIX=h;CAS=c;rendre()})}
async function appliquer(){const n=CAS.filter(c=>{const h=ch(c);return !h.applique&&(['A','B','doublons','les deux'].includes(h.choix)||(!c.b&&h.note))}).length;
 if(!n){alert(t('rien'));return}if(!confirm(t('confirmer',{n})))return;
 clearTimeout(tm);await post('/api/choix',CHOIX);$('#compte').textContent=t('encours');RES=await post('/api/appliquer',{});await charger();
 $('#compte').textContent+=' · '+t('traites',{n:Object.keys(RES).length});const k=Object.keys(RES).find(i=>!(CHOIX[i]||{}).applique);
 if(k)document.getElementById('c'+CAS.findIndex(c=>c.id===k))?.scrollIntoView({behavior:'smooth',block:'center'})}
function ouvrir(n,k,i){LB={n,k,i,duo:!!CAS[n].b};peindre();$('#lb').classList.add('on');document.documentElement.requestFullscreen?.().catch(()=>{})}
function fermer(){$('#lb').classList.remove('on');LB=null;if(document.fullscreenElement)document.exitFullscreen()}
function peindre(){const {n,k,i,duo}=LB,c=CAS[n],paire=!!c.b,nom=x=>(x==='a'?c.ia:c.ib).nom;
 const bloc=x=>`<div><div class="lt">${x.toUpperCase()} — ${esc(nom(x))}</div><img alt="" src="${src(n,x,i)}"></div>`;
 $('#vue').innerHTML=paire&&duo?bloc('a')+bloc('b'):bloc(k);$('#lbt').textContent=`${n+1}. ${c.titre} — ${mmss(c.ts[i])} (${i+1}/${c.ts.length})`;
 for(const x of paire?['a','b']:['a'])for(const j of [i-1,i+1])if(c.ts[j]!==undefined)new Image().src=src(n,x,j)}
function bascule(){if(!LB||!CAS[LB.n].b)return;if(LB.duo)LB.duo=false;else LB.k=LB.k==='a'?'b':'a';peindre()}
function mode(){if(!LB||!CAS[LB.n].b)return;LB.duo=!LB.duo;peindre()}
addEventListener('keydown',e=>{if(!LB)return;const N=CAS[LB.n].ts.length;
 if(e.key==='Escape')fermer();else if(e.key==='ArrowRight'){LB.i=(LB.i+1)%N;peindre()}else if(e.key==='ArrowLeft'){LB.i=(LB.i+N-1)%N;peindre()}
 else if(e.key===' ')bascule();else if(e.key.toLowerCase()==='s')mode();else return;e.preventDefault()});
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement&&LB)fermer()});
charger();
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    def log_message(self, *x):
        pass

    def envoyer(self, corps, type_='application/json'):
        b = corps.encode('utf-8') if isinstance(corps, str) else corps
        self.send_response(200); self.send_header('Content-Type', type_ + '; charset=utf-8')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == '/':
            return self.envoyer(PAGE, 'text/html')
        if self.path == '/api/cas':
            for k in [k for k in INFOS if not os.path.exists(k)]:   # fichiers déplacés par « Appliquer »
                del INFOS[k]
            return self.envoyer(json.dumps([dict(c, ia=info(c['a']), ib=info(c['b']) if c.get('b') else None, ts=instants(c))
                                            for c in CAS]))
        if self.path == '/api/choix':
            return self.envoyer(json.dumps(lire_choix()))
        if self.path.startswith('/img?'):
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            c = next((x for x in CAS if x['id'] == q.get('c')), None)
            if c and q.get('k') in ('a', 'b') and c.get(q['k']) and q.get('n', '').isdigit() and int(q['n']) < len(MOMENTS):
                try:
                    f = capture(c, q['k'], int(q['n']))
                except OSError:
                    f = None
                if f:
                    b = open(f, 'rb').read()
                    self.send_response(200); self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Cache-Control', 'max-age=86400'); self.send_header('Content-Length', str(len(b)))
                    self.end_headers(); return self.wfile.write(b)
        self.send_error(404)

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        connus = {c[k] for c in CAS for k in ('a', 'b') if c.get(k)}
        for k in ('p', 'a', 'b'):                         # seulement les fichiers de la liste
            if d.get(k) and d[k] not in connus:
                return self.send_error(403)
        if self.path == '/api/vlc':
            subprocess.Popen([VLC, '--start-time', str(int(d.get('t', 0))), video(d['p'])])
            return self.envoyer('{}')
        if self.path == '/api/cote':
            cote(d['a'], d['b'], int(d.get('t', 0)))
            return self.envoyer('{}')
        if self.path == '/api/appliquer':
            return self.envoyer(json.dumps(appliquer(), ensure_ascii=False))
        if self.path == '/api/choix':
            json.dump(d, open(a.choix, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            return self.envoyer('{}')
        self.send_error(404)


threading.Thread(target=lambda: (precharger(), precapturer()), daemon=True).start()
url = f'http://127.0.0.1:{a.port}/'
print('Page :', url, flush=True)
if os.environ.get('COMPARATEUR_OUVRIR', '1') == '1':
    webbrowser.open(url)
ThreadingHTTPServer(('127.0.0.1', a.port), H).serve_forever()
