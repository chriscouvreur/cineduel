"""Langues des pistes audio d'une vidéo, lues dans l'en-tête (sans ffprobe ni VLC).

MKV : TrackEntry (0xAE) avec TrackType (0x83) = 2 (audio) et Language (0x22B59C, « eng » par défaut).
MP4/M4V/MOV : boîtes « trak » dont le « hdlr » vaut « soun » ; langue ISO-639-2 du « mdhd ».
AVI et autres : inconnu (liste vide).
"""
import struct

LIRE = 8 * 1024 * 1024
CODES = {'fre': 'fr', 'fra': 'fr', 'fr': 'fr', 'eng': 'en', 'en': 'en', 'ita': 'it', 'it': 'it', 'ger': 'de', 'deu': 'de',
         'de': 'de', 'spa': 'es', 'es': 'es', 'jpn': 'ja', 'ja': 'ja', 'und': 'und', '': 'und'}


def _vint(b, i):
    if i >= len(b):
        return None, 1
    first, n, mask = b[i], 1, 0x80
    while n <= 8 and not first & mask:
        mask >>= 1; n += 1
    if n > 8 or i + n > len(b):
        return None, 1
    v = first & (mask - 1)
    for k in range(1, n):
        v = (v << 8) | b[i + k]
    return v, n


def _mkv(b):
    out, i = [], b.find(b'\xae')
    while i != -1 and len(out) < 12:
        size, n = _vint(b, i + 1)
        if size and 8 <= size <= 6000:
            blk = b[i + 1 + n:i + 1 + n + size]
            t = blk.find(b'\x83\x81')
            if t != -1 and blk[t + 2:t + 3] == b'\x02':
                lang = 'eng'
                k = blk.find(b'\x22\xb5\x9c')
                if k != -1:
                    sz, m = _vint(blk, k + 3)
                    if sz and sz <= 8:
                        lang = blk[k + 3 + m:k + 3 + m + sz].decode('ascii', 'ignore').strip('\x00 ')
                out.append(lang)
                i = b.find(b'\xae', i + 1 + n + size)
                continue
        i = b.find(b'\xae', i + 1)
    return out


def _mp4(b):
    out, i = [], b.find(b'trak')
    while i != -1:
        size = struct.unpack('>I', b[i - 4:i])[0] if i >= 4 else 0
        blk = b[i - 4:i - 4 + size] if 16 < size <= len(b) else b''
        h = blk.find(b'hdlr')
        if h != -1 and blk[h + 12:h + 16] == b'soun':
            m = blk.find(b'mdhd')
            if m != -1:
                off = m + 4 + (32 if blk[m + 4] == 1 else 20)
                if off + 2 <= len(blk):
                    v = struct.unpack('>H', blk[off:off + 2])[0]
                    out.append(''.join(chr(((v >> s) & 0x1F) + 0x60) for s in (10, 5, 0)))
        i = b.find(b'trak', i + 4)
    return out


def langues(p):
    """-> liste triée des langues audio (« fr », « en »…), vide si illisible."""
    ext = p.lower().rsplit('.', 1)[-1]
    try:
        with open(p, 'rb') as f:
            debut = f.read(LIRE)
            if ext in ('mkv', 'webm'):
                brut = _mkv(debut)
            elif ext in ('mp4', 'm4v', 'mov'):
                brut = _mp4(debut)
                if not brut:
                    f.seek(0, 2); t = f.tell(); f.seek(max(0, t - LIRE)); brut = _mp4(f.read(LIRE))
            else:
                brut = []
    except Exception:  # noqa: BLE001
        brut = []
    return sorted({CODES.get(x.lower(), x.lower()) for x in brut})
