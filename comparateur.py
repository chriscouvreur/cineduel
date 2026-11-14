"""Comparateur de films PLEX — page locale pour trancher, au même endroit, tous les doublons et films à identifier d'une bibliothèque.

Pour chaque cas : fiches techniques (résolution, codec, taille, durée, pistes audio), boutons « VLC » pour chaque copie,
« Côte à côte » (une seule fenêtre ffplay, les deux films synchronisés au même instant, son de A) et captures pleine résolution aux mêmes
instants pour A et B (agrandissables, bascule A/B). Les choix sont enregistrés dans --choix ; le bouton « Appliquer mes choix » les applique
(perdants -> _Doublons, jamais de suppression ; film identifié « Titre (Année) » -> renommé ; journal de la bibliothèque).

Usage : python -I comparateur.py --cas cas.json --choix choix.json [--port 8770] [--racine <bibliothèque>] [--appliquer]
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
            return 'les deux gardés' if choix == 'les deux' else None
        w, l = (c['a'], c['b']) if choix == 'A' else (c['b'], c['a'])
        if not os.path.exists(w) or not os.path.exists(l):
            return 'fichier introuvable (déjà déplacé ?)'
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
        return f'gardé : {os.path.basename(t)} · l’autre -> _Doublons'
    # film seul
    p = c['a']
    if not os.path.exists(p):
        return 'fichier introuvable (déjà déplacé ?)'
    if choix == 'doublons':
        bouger(p, libre(os.path.join(DBL, os.path.basename(p))), journal)
        return 'mis dans _Doublons'
    if note:
        m = ANNEE.match(note)
        if not m:
            return f'« {note} » : il manque l’année, écris « Titre (Année) »'
        base, ext = base_ext(p)
        suite = ANNEE.match(base)
        nom = f'{m.group(1).strip()} ({m.group(2)})' + (suite.group(3) if suite else '')
        deja = [os.path.join(R, f) for f in os.listdir(R) if os.path.splitext(f)[0].lower() == nom.lower()
                and os.path.normcase(os.path.join(R, f)) != os.path.normcase(p)]
        if deja:                                          # film déjà présent -> nouvelle paire à comparer
            if not any(x.get('b') and {x['a'], x['b']} == {deja[0], p} for x in CAS):
                CAS.append({'id': str(max(int(x['id']) for x in CAS) + 1), 'titre': nom, 'a': deja[0], 'b': p,
                            'note': f'Tu as identifié B comme « {nom} », déjà présent dans la bibliothèque (A) : doublon.'})
                json.dump(CAS, open(a.cas, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            return f'déjà présent ({os.path.basename(deja[0])}) : ajouté comme doublon à comparer'
        t = bouger(p, os.path.join(R, nom + ext), journal)
        return f'renommé : {os.path.basename(t)}'
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
            r = f'non appliqué : {e}'
        if r:
            res[c['id']] = r
            if not r.startswith(('non appliqué', 'fichier introuvable', '«')):
                ch['applique'] = r
    with open(os.path.join(R, '_journal_renommage_plex.csv'), 'a', newline='', encoding='utf-8') as h:
        csv.writer(h).writerows((s, t, time.strftime('%Y-%m-%d')) for s, t in journal)
    json.dump(choix, open(a.choix, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return res


if a.appliquer:
    for k, v in appliquer().items():
        print(k, next((c['titre'] for c in CAS if c['id'] == k), ''), '->', v)
    sys.exit(0)

PAGE = r'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Comparateur de films PLEX</title><style>
:root{color-scheme:dark;--f:#111214;--c:#1a1b1f;--c2:#212228;--t:#ececf1;--m:#9a9aa5;--l:#2e3038;--a:#5b8cff;--ok:#4cc38a;--okf:rgba(76,195,138,.12)}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--f);color:var(--t)}
header{position:sticky;top:0;z-index:5;background:rgba(17,18,20,.88);backdrop-filter:blur(10px);border-bottom:1px solid var(--l);padding:12px 28px;display:flex;gap:16px;align-items:center}
header h1{font-size:17px;margin:0;font-weight:650}#etat{color:var(--m);font-size:13px;font-variant-numeric:tabular-nums}header .sp{flex:1}
main{max-width:1280px;margin:0 auto;padding:24px 28px 80px}
.cas{background:var(--c);border:1px solid var(--l);border-radius:14px;padding:18px 20px;margin-bottom:18px}
.cas.fait{border-color:rgba(76,195,138,.45)}.cas.applique{opacity:.55}.cas.applique .choix{pointer-events:none}
.res{margin-top:10px;font-size:13px;padding:7px 11px;border-radius:8px;background:var(--okf);color:var(--ok)}.res.ko{background:rgba(255,170,60,.12);color:#ffb35c}.cas h2{font-size:16px;margin:0 0 2px;font-weight:620}.note{color:var(--m);font-size:13px;margin-bottom:12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}.cols.un{grid-template-columns:1fr}
.copie{background:var(--c2);border:1px solid var(--l);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.copie b{font-size:11px;color:var(--m);letter-spacing:.08em}.nom{font-weight:560;word-break:break-word;margin-top:-6px}
.tech{font:12.5px/1.65 ui-monospace,Consolas,monospace;color:#c9c9d1}.mieux{color:var(--ok);font-weight:650}
.caps{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.caps figure{margin:0;position:relative;aspect-ratio:16/9;background:#0b0b0d;border-radius:6px;overflow:hidden;cursor:zoom-in}
.caps img{width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .25s,transform .2s}.caps img.ok{opacity:1}
.caps figure:hover img{transform:scale(1.04)}.caps figcaption{position:absolute;left:5px;bottom:3px;font-size:10.5px;color:#fff;text-shadow:0 1px 2px #000;font-variant-numeric:tabular-nums}
.caps figure.att::before{content:"extraction…";position:absolute;inset:0;display:grid;place-items:center;font-size:11px;color:var(--m)}
button{font:inherit;font-size:13px;border:1px solid var(--l);background:var(--c2);color:var(--t);border-radius:8px;padding:6px 11px;cursor:pointer;transition:background .15s,transform .1s}
button:hover{background:#2a2c33}button:active{transform:scale(.97)}button.p{background:var(--a);border-color:var(--a);color:#fff}button.p:hover{background:#4a7aeb}
.barre{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px;color:var(--m);font-size:13px}
.barre input{font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--l);border-radius:8px;width:64px;background:var(--c2);color:var(--t)}
.choix{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;padding-top:12px;border-top:1px dashed var(--l)}
.choix label{border:1px solid var(--l);border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px;user-select:none;transition:background .15s,border-color .15s}
.choix label:hover{background:var(--c2)}.choix input{display:none}.choix label:has(input:checked){border-color:var(--ok);background:var(--okf);color:var(--ok);font-weight:650}
.choix input.txt{display:inline-block;flex:1;min-width:240px;font:inherit;font-size:13px;padding:5px 12px;border:1px solid var(--l);border-radius:999px;background:var(--c2);color:var(--t)}
#lb{position:fixed;inset:0;z-index:10;background:#000;display:none;flex-direction:column}#lb.on{display:flex}
#lb .vue{flex:1;display:flex;gap:4px;min-height:0}
#lb .vue div{flex:1;display:flex;flex-direction:column;min-width:0}#lb .vue img{flex:1;min-height:0;width:100%;object-fit:contain}
#lb .lettre{font-size:13px;color:#ddd;padding:6px 10px;background:#111;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#lb .bas{display:flex;gap:10px;align-items:center;padding:10px 16px;background:#111;color:#bbb;font-size:13px}
#lb .bas .sp{flex:1}#lb kbd{border:1px solid #444;border-radius:4px;padding:0 5px;font-size:12px;color:#ddd}
</style></head><body><header><h1>Comparateur de films PLEX</h1><span id="etat"></span><span class="sp"></span>
<button onclick="sauver(true)">Enregistrer</button><button class="p" onclick="appliquer()">Appliquer mes choix</button></header><main id="liste"></main>
<div id="lb"><div class="vue" id="vue"></div><div class="bas"><span id="lbt"></span><span class="sp"></span>
<span><kbd>←</kbd><kbd>→</kbd> instant · <kbd>Espace</kbd> bascule A/B · <kbd>S</kbd> côte à côte / une seule · <kbd>Échap</kbd> fermer</span>
<button onclick="bascule()">A ⇄ B</button><button onclick="mode()">Côte à côte</button><button onclick="fermer()">Fermer</button></div></div>
<script>
let CAS=[],CHOIX={},LB=null,RES={};
const $=s=>document.querySelector(s);
const post=(u,d)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(r=>r.json());
const mmss=s=>`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
function hms(s){const [m,x]=String(s).split(':');return x===undefined?+m*60:(+m)*60+(+x)}
const src=(n,k,i)=>`/img?c=${CAS[n].id}&k=${k}&n=${i}`;
function caps(n,k){return`<div class="caps">${CAS[n].ts.map((t,i)=>`<figure class="att" onclick="ouvrir(${n},'${k}',${i})"><img loading="lazy" src="${src(n,k,i)}" onload="this.classList.add('ok');this.parentNode.classList.remove('att')"><figcaption>${mmss(t)}</figcaption></figure>`).join('')}</div>`}
function fiche(n,k,i,lettre,autre){if(!i)return'';if(i.erreur)return`<div class="copie"><b>${lettre}</b><div class="nom">${i.nom}</div>${i.erreur}</div>`;
 const m=(c,f)=>autre&&!autre.erreur&&f(i[c],autre[c])?' class="mieux"':'';
 return`<div class="copie"><b>${lettre}</b><div class="nom">${i.nom}</div><div class="tech">
 <span${m('h',(x,y)=>x>y)}>${i.res}</span> · <span${m('codec',(x,y)=>['av1','hevc'].includes(x)&&!['av1','hevc'].includes(y))}>${i.codec}</span> · <span${m('go',(x,y)=>x>y)}>${i.taille||i.go+' Go'}</span> · ${i.min} min · ${i.ext}<br>audio : <span${m('audio',(x,y)=>x.includes('fr')&&!y.includes('fr'))}>${i.audio}</span>${i.dossier.startsWith('_')?' · dans '+i.dossier:''}</div>${caps(n,k)}</div>`}
function rendre(){$('#liste').innerHTML=CAS.map((c,n)=>{const ch=CHOIX[c.id]||{};const paire=!!c.b;
 const opts=paire?[['A','Garder A'],['B','Garder B'],['les deux','Garder les deux (versions différentes)']]:[['inconnu','Je ne sais pas'],['doublons','Mettre dans _Doublons']];
 const res=ch.applique||RES[c.id];return`<section class="cas${ch.choix?' fait':''}${ch.applique?' applique':''}" id="c${n}"><h2>${n+1}. ${c.titre}${ch.applique?' — appliqué ✓':''}</h2><div class="note">${c.note||''}</div>
 <div class="cols${paire?'':' un'}">${fiche(n,'a',c.ia,'A',c.ib)}${paire?fiche(n,'b',c.ib,'B',c.ia):''}</div>
 <div class="barre">lecture à <input type="text" id="t${n}" value="${mmss(c.ts[1])}" title="minutes:secondes"> <button onclick="vlc(${n},'a')">▶ A dans VLC</button>
 ${paire?`<button onclick="vlc(${n},'b')">▶ B dans VLC</button><button class="p" onclick="cote(${n})">⇆ Côte à côte (vidéo)</button>`:''}</div>
 <div class="choix">${opts.map(([v,l])=>`<label><input type="radio" name="r${n}" value="${v}" ${ch.choix===v?'checked':''} onchange="choisir(${n},this.value)"><span>${l}</span></label>`).join('')}
 <input class="txt" placeholder="${paire?'remarque (facultatif)':'c’est quel film ? titre (année)'}" value="${(ch.note||'').replace(/"/g,'&quot;')}" onchange="noter(${n},this.value)"></div>${res?`<div class="res${ch.applique?'':' ko'}">${res}</div>`:''}</section>`}).join('');etat()}
function charger(){return Promise.all([fetch('/api/cas').then(r=>r.json()),fetch('/api/choix').then(r=>r.json())]).then(([c,ch])=>{CHOIX=ch;const rang=x=>{const h=ch[x.id]||{};return h.applique?2:(h.choix||h.note)?1:0};CAS=c.sort((x,y)=>rang(x)-rang(y));rendre()})}
async function appliquer(){const n=CAS.filter(c=>{const h=CHOIX[c.id]||{};return !h.applique&&(['A','B','doublons'].includes(h.choix)||(!c.b&&h.note))}).length;
 if(!n){alert('Aucun choix à appliquer.');return}
 if(!confirm(`Appliquer ${n} choix ?

Les copies écartées iront dans _Doublons (rien n'est supprimé) ; les films identifiés seront renommés.`))return;
 clearTimeout(tm);await post('/api/choix',CHOIX);$('#etat').textContent='application en cours…';RES=await post('/api/appliquer',{});await charger();
 const ok=Object.values(RES).length;$('#etat').textContent+=` · ${ok} cas traité(s) ✓`;const k=Object.keys(RES).find(i=>!(CHOIX[i]||{}).applique);if(k)document.getElementById('c'+CAS.findIndex(c=>c.id===k))?.scrollIntoView({behavior:'smooth',block:'center'})}
function etat(){const f=CAS.filter(c=>(CHOIX[c.id]||{}).choix||(CHOIX[c.id]||{}).note).length;$('#etat').textContent=`${f} / ${CAS.length} tranchés`}
function choisir(n,v){(CHOIX[CAS[n].id]??={}).choix=v;$('#c'+n).classList.add('fait');sauver()}
function noter(n,v){(CHOIX[CAS[n].id]??={}).note=v;sauver()}
let tm;function sauver(now){clearTimeout(tm);const go=()=>post('/api/choix',CHOIX).then(()=>{etat();if(now)$('#etat').textContent+=' · enregistré ✓'});now?go():tm=setTimeout(go,400)}
function vlc(n,k){post('/api/vlc',{p:CAS[n][k],t:hms($('#t'+n).value)})}
function cote(n){post('/api/cote',{a:CAS[n].a,b:CAS[n].b,t:hms($('#t'+n).value)})}
function ouvrir(n,k,i){LB={n,k,i,duo:!!CAS[n].b};peindre();$('#lb').classList.add('on');document.documentElement.requestFullscreen?.().catch(()=>{})}
function fermer(){$('#lb').classList.remove('on');LB=null;if(document.fullscreenElement)document.exitFullscreen()}
function peindre(){const {n,k,i,duo}=LB,c=CAS[n],paire=!!c.b;const nom=x=>(x==='a'?c.ia:c.ib).nom;
 const bloc=x=>`<div><div class="lettre">${x.toUpperCase()} — ${nom(x)}</div><img src="${src(n,x,i)}"></div>`;
 $('#vue').innerHTML=paire&&duo?bloc('a')+bloc('b'):bloc(k);$('#lbt').textContent=`${n+1}. ${c.titre} — ${mmss(c.ts[i])} (${i+1}/${c.ts.length})`;
 for(const x of paire?['a','b']:['a'])for(const j of [i-1,i+1])if(c.ts[j]!==undefined)new Image().src=src(n,x,j)}
function bascule(){if(!LB||!CAS[LB.n].b)return;if(LB.duo)LB.duo=false;else LB.k=LB.k==='a'?'b':'a';peindre()}
function mode(){if(!LB||!CAS[LB.n].b)return;LB.duo=!LB.duo;peindre()}
addEventListener('keydown',e=>{if(!LB)return;const L=CAS[LB.n].ts.length;
 if(e.key==='Escape')fermer();else if(e.key==='ArrowRight'){LB.i=(LB.i+1)%L;peindre()}else if(e.key==='ArrowLeft'){LB.i=(LB.i+L-1)%L;peindre()}
 else if(e.key===' '){e.preventDefault();bascule()}else if(e.key.toLowerCase()==='s')mode();else return;e.preventDefault()});
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
