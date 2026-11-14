"""Lit la résolution (largeur x hauteur) d'une vidéo directement dans son en-tête, sans ffprobe.

Usage : python -I resolution.py <liste.json> <sortie.json>    (liste de chemins -> {chemin: [largeur, hauteur]})
MKV : éléments PixelWidth (0xB0) / PixelHeight (0xBA) de la piste vidéo ; AVI : en-tête « avih » ;
MP4/M4V/MOV : boîte « tkhd » de la piste vidéo (début puis fin du fichier). Inconnu -> [0, 0].
"""
import json, struct, sys

LIRE = 8 * 1024 * 1024


def vint(b, i):
    """Entier EBML à longueur variable -> (valeur, longueur)."""
    first = b[i]
    n = 1
    mask = 0x80
    while n <= 8 and not first & mask:
        mask >>= 1; n += 1
    if n > 8 or i + n > len(b):
        return None, 1
    v = first & (mask - 1)
    for k in range(1, n):
        v = (v << 8) | b[i + k]
    return v, n


def mkv(b):
    i = b.find(b'\xe0')                         # élément Video (dans TrackEntry)
    while i != -1:
        size, n = vint(b, i + 1) if i + 1 < len(b) else (None, 1)
        if size and 4 <= size <= 200:
            blk = b[i + 1 + n:i + 1 + n + size]
            w = h = 0; j = 0
            while j < len(blk) - 2:
                eid = blk[j]
                if eid in (0xB0, 0xBA):
                    sz, m = vint(blk, j + 1)
                    if sz and sz <= 4:
                        val = int.from_bytes(blk[j + 1 + m:j + 1 + m + sz], 'big')
                        if eid == 0xB0: w = val
                        else: h = val
                        j += 1 + m + sz; continue
                j += 1
            if 100 <= h <= 4400 and 100 <= w <= 8000:
                return w, h
        i = b.find(b'\xe0', i + 1)
    return 0, 0


def avi(b):
    i = b.find(b'avih')
    if i != -1 and i + 48 <= len(b):
        w, h = struct.unpack('<II', b[i + 8 + 32:i + 8 + 40])
        if 100 <= h <= 4400:
            return w, h
    return 0, 0


def mp4(b):
    best = (0, 0); i = b.find(b'tkhd')
    while i != -1:
        size = struct.unpack('>I', b[i - 4:i])[0] if i >= 4 else 0
        end = i - 4 + size
        if 84 <= size <= 120 and end <= len(b):
            w, h = struct.unpack('>II', b[end - 8:end])
            w, h = w >> 16, h >> 16
            if 100 <= h <= 4400 and h > best[1]:
                best = (w, h)
        i = b.find(b'tkhd', i + 4)
    return best


def resolution(p):
    ext = p.lower().rsplit('.', 1)[-1]
    try:
        return _resolution(p, ext)
    except Exception:  # noqa: BLE001 — en-tête illisible : résolution inconnue
        return 0, 0


def _resolution(p, ext):
    try:
        with open(p, 'rb') as f:
            debut = f.read(LIRE)
            if ext in ('mkv', 'webm'):
                return mkv(debut)
            if ext in ('avi', 'divx'):
                return avi(debut)
            if ext in ('mp4', 'm4v', 'mov'):
                r = mp4(debut)
                if r == (0, 0):
                    f.seek(0, 2); taille = f.tell(); f.seek(max(0, taille - LIRE))
                    r = mp4(f.read(LIRE))
                return r
    except OSError:
        pass
    return 0, 0


if __name__ == '__main__':
    chemins = json.load(open(sys.argv[1], encoding='utf-8-sig'))
    json.dump({c: list(resolution(c)) for c in chemins}, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False)
