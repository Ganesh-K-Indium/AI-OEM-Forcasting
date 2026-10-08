from __future__ import annotations

import re
import unicodedata

LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "gmbh", "ag", "sa", "nv", "bv",
         "pte", "sdn", "bhd", "kk", "gk", "plc", "spa", "lp", "llp", "srl", "oy", "ab", "as", "pty", "pvt", "the", "of", "and", "se", "kg"}


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(t for t in s.split() if t not in LEGAL)
