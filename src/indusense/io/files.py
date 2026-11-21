"""Métadonnées d'un fichier : source, taille, empreinte, encodage."""
from __future__ import annotations

import hashlib
from pathlib import Path

# Libellé affiché dans les cartes -> nom d'encodage Python
PY_ENCODING = {
    "UTF-8 (BOM)": "utf-8-sig",
    "UTF-8": "utf-8",
    "ASCII (compatible UTF-8)": "utf-8",
    "Windows-1252": "cp1252",
    "ISO-8859-1": "latin-1",
}
# Encodages imposés dans la config (`encoding: cp1252`) -> libellé
LABEL_FROM_PY = {"utf-8-sig": "UTF-8 (BOM)", "utf-8": "UTF-8", "utf8": "UTF-8",
                 "cp1252": "Windows-1252", "latin-1": "ISO-8859-1",
                 "iso-8859-1": "ISO-8859-1", "ascii": "ASCII (compatible UTF-8)"}


def get_source(path: Path) -> str:
    """Chemin absolu du fichier source."""
    return str(path.resolve())


def human_size(n_bytes: int) -> str:
    """Taille lisible en unités binaires, à partir du Ko (1 Ko = 1024 octets)."""
    size = n_bytes / 1024
    for unit in ("Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} Go"


def sha256(path: Path) -> str:
    """Empreinte du fichier : prouve quelle version exacte a été traitée."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_charset(path: Path) -> str:
    """Détecte l'encodage sans dépendance externe.

    UTF-8 d'abord (avec ou sans BOM). Sinon encodage 8 bits : des octets 0x80-0x9F
    signent du Windows-1252 (imprimables en cp1252, caractères de contrôle en Latin-1),
    sinon ISO-8859-1.
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "UTF-8 (BOM)"
    try:
        raw.decode("utf-8")
        return "ASCII (compatible UTF-8)" if raw.isascii() else "UTF-8"
    except UnicodeDecodeError:
        if any(0x80 <= b <= 0x9F for b in raw):
            return "Windows-1252"
        return "ISO-8859-1"


def resolve_charset(path: Path, configured: str = "auto") -> tuple[str, str]:
    """(libellé, encodage Python) : détecté, ou imposé par la config."""
    if configured and configured.lower() != "auto":
        enc = configured.lower()
        return LABEL_FROM_PY.get(enc, configured), enc
    label = detect_charset(path)
    return label, PY_ENCODING[label]
