"""Lit la durée (minutes) d'une vidéo dans son en-tête, sans ffprobe, en lisant peu (bibliothèque sur le NAS). Inconnue -> 0.

MKV : Info/TimecodeScale (0x2AD7B1) + Duration (0x4489, flottant) dans les premiers Ko ;
MP4/M4V/MOV : boîtes de premier niveau parcourues par sauts jusqu'à « moov », puis « mvhd » ;
AVI : « avih » (µs par image x nombre d'images).
"""
import struct

DEBUT = 256 * 1024


def mkv(b):
    i = b.find(b'\x44\x89')
    while i != -1:
        n = b[i + 2] & 0x7f if i + 2 < len(b) and b[i + 2] in (0x84, 0x88) else 0
        if n in (4, 8) and i + 3 + n <= len(b):
            v = struct.unpack('>f' if n == 4 else '>d', b[i + 3:i + 3 + n])[0]
            j = b.find(b'\x2a\xd7\xb1')
            echelle = 1000000
            if j != -1 and j + 4 < len(b) and b[j + 3] & 0x80:
                sz = b[j + 3] & 0x7f
                echelle = int.from_bytes(b[j + 4:j + 4 + sz], 'big') or 1000000
            m = v * echelle / 1e9 / 60
            if 0.5 < m < 1500:
                return m
        i = b.find(b'\x44\x89', i + 1)
    return 0


def mvhd(b):
    i = b.find(b'mvhd')
    if i == -1 or i + 36 > len(b):
        return 0
    if b[i + 4] == 0:
        ts, du = struct.unpack('>II', b[i + 16:i + 24])
    else:
        ts, du = struct.unpack('>IQ', b[i + 24:i + 36])
    return du / ts / 60 if ts else 0


def mp4(f):
    f.seek(0, 2); taille = f.tell(); pos = 0
    while pos + 8 <= taille:
        f.seek(pos); h = f.read(16)
        if len(h) < 8:
            break
        s, t = struct.unpack('>I4s', h[:8])
        if s == 1:
            s = struct.unpack('>Q', h[8:16])[0]
        elif s == 0:
            s = taille - pos
        if t == b'moov':
            f.seek(pos); return mvhd(f.read(4096))
        if s < 8:
            break
        pos += s
    return 0


def avi(b):
    i = b.find(b'avih')
    if i == -1 or i + 28 > len(b):
        return 0
    us, = struct.unpack('<I', b[i + 8:i + 12])
    images, = struct.unpack('<I', b[i + 24:i + 28])
    j = b.find(b'dmlh')                     # AVI > 1 Go (OpenDML) : avih ne compte que la 1re partie
    if j != -1 and j + 12 <= len(b):
        images = max(images, struct.unpack('<I', b[j + 8:j + 12])[0])
    return us * images / 6e7


def duree(p):
    ext = p.lower().rsplit('.', 1)[-1]
    try:
        with open(p, 'rb') as f:
            if ext in ('mp4', 'm4v', 'mov'):
                return mp4(f)
            debut = f.read(DEBUT)
            if ext in ('mkv', 'webm'):
                return mkv(debut) or mkv(debut + f.read(4 * 1024 * 1024))
            if ext in ('avi', 'divx'):
                return avi(debut)
    except (OSError, struct.error, IndexError):
        pass
    return 0


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        print(round(duree(p), 1), p)
