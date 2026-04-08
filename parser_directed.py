import re
from datetime import datetime
from callsign_utils import normalize_callsign


LOCAL_SNR_TOKEN_RE = re.compile(r"^[-+]?\d+$")


def parse_dt(date_str, time_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            pass
    return None


def parse_directed_line(line):
    """
    Parse a JS8Call DIRECTED.TXT line.

    Important:
    The local heard SNR is the 4th numeric field in the line, e.g.

    2026-02-27 16:05:57 27.245000 1607 -19 161OS487: 19LV002 SNR +27

    In that example:
    - local heard SNR = -19   (what this station heard)
    - message payload contains "SNR +27" which is remote content and must NOT
      overwrite the local heard SNR.
    """
    raw = line.replace("♢", "").strip()
    if not raw:
        return None

    tokens = raw.split()
    if len(tokens) < 6:
        return None

    date_str = tokens[0]
    time_str = tokens[1]
    freq = tokens[2]

    # JS8Call local monitor SNR is the token immediately before the sender token.
    # Typical structure:
    # DATE TIME FREQ OFFSET LOCALSNR SENDER: MESSAGE...
    sender = None
    sender_idx = None

    for i, tok in enumerate(tokens):
        if tok.endswith(":"):
            sender = tok[:-1].upper()
            sender_idx = i
            break

    if not sender or sender_idx is None:
        return None

    # Need at least one token before sender for local SNR
    if sender_idx < 1:
        return None

    local_snr_token = tokens[sender_idx - 1]
    if not LOCAL_SNR_TOKEN_RE.match(local_snr_token):
        return None

    try:
        snr = int(local_snr_token)
    except ValueError:
        return None

    receiver = ""
    message_tokens = []
    if sender_idx + 1 < len(tokens):
        first_after_sender = str(tokens[sender_idx + 1] or "").strip()
        first_after_sender_upper = first_after_sender.upper()
        if re.match(r"^(JR|JRN|JRS)(?:[.|]|$)", first_after_sender_upper):
            receiver = ""
            message_tokens = tokens[sender_idx + 1:]
        else:
            receiver = first_after_sender_upper
            message_tokens = tokens[sender_idx + 2:]

    msg_text = " ".join(message_tokens).strip()

    dt = parse_dt(date_str, time_str)

    return {
        "date": date_str,
        "time": time_str,
        "datetime": dt,
        "from": sender,                        # exact heard form
        "to": receiver,                        # exact heard form
        "from_norm": normalize_callsign(sender),
        "to_norm": normalize_callsign(receiver),
        "msg": msg_text,                       # preserve full payload exactly
        "freq": freq,
        "snr": snr,                            # local heard SNR only
        "raw": raw,
    }
