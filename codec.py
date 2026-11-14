"""Codec vidéo lu dans l'en-tête (sans ffprobe) et « taille équivalente » pour départager deux copies.

Un codec récent compresse mieux : à qualité égale, un HEVC pèse environ moitié moins qu'un H.264 et un AV1 encore moins.
On compare donc taille x facteur du codec plutôt que la taille brute.
"""
import os, re

FACTEUR = {'av1': 2.5, 'hevc': 2.0, 'vp9': 1.8, 'avc': 1.0, 'mpeg4': 0.6, 'mpeg2': 0.4, '?': 1.0}
MOTIFS = [('av1', rb'V_AV1|av01|AV01'), ('hevc', rb'V_MPEGH/ISO/HEVC|hvc1|hev1|HEVC|x265'),
          ('vp9', rb'V_VP9|vp09'), ('avc', rb'V_MPEG4/ISO/AVC|avc1|H264|h264|x264'),
          ('mpeg4', rb'V_MPEG4/ISO/(?:ASP|SP)|XVID|xvid|DIVX|divx|DX50|FMP4'), ('mpeg2', rb'V_MPEG2|mpg2')]


def codec(p):
    try:
        with open(p, 'rb') as f:
            b = f.read(4 * 1024 * 1024)
            if p.lower().endswith(('.mp4', '.m4v', '.mov')) and not re.search(rb'avc1|hvc1|hev1|av01|vp09', b):
                f.seek(max(0, os.path.getsize(p) - 8 * 1024 * 1024)); b += f.read()   # moov en fin de fichier
    except OSError:
        return '?'
    for nom, m in MOTIFS:
        if re.search(m, b):
            return nom
    return '?'


def taille_equivalente(p):
    return os.path.getsize(p) * FACTEUR[codec(p)]


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        print(codec(p), round(os.path.getsize(p) / 2**30, 1), 'Go ->', round(taille_equivalente(p) / 2**30, 1), 'Go éq.', p)
