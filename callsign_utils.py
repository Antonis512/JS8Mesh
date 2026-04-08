import re


CB_BASE_RE = re.compile(r"^\d{1,3}[A-Z]{1,4}\d{1,4}$")
HAM_BASE_RE = re.compile(r"^[A-Z]{1,3}\d[A-Z]{1,4}$")


def is_group_callsign(cs):
    text = str(cs or "").upper().strip()
    return text.startswith("@")


def _is_cb_base(cs):
    return bool(CB_BASE_RE.match(cs))


def _is_ham_base(cs):
    return bool(HAM_BASE_RE.match(cs))


def normalize_callsign(cs):
    cs = str(cs).upper().strip()
    if not cs:
        return ""

    if is_group_callsign(cs):
        return ""

    parts = [part.strip().upper() for part in cs.split("/") if part.strip()]
    if not parts:
        return cs

    # Prefer the real base callsign from right to left
    # Examples:
    # SV3/SV8TTL/M -> SV8TTL
    # SV3/SV8TTL   -> SV8TTL
    # SV8TTL/T     -> SV8TTL
    # 304FC43/503  -> 304FC43
    # 34DC998/T1   -> 34DC998
    for part in reversed(parts):
        if _is_cb_base(part) or _is_ham_base(part):
            return part

    return cs


def classify_callsign(cs):
    cs = str(cs).upper().strip()
    if not cs:
        return "UNKNOWN"

    if is_group_callsign(cs):
        return "GROUP"

    norm = normalize_callsign(cs)

    if _is_cb_base(norm):
        return "CB"

    if _is_ham_base(norm):
        return "HAM"

    return "UNKNOWN"
