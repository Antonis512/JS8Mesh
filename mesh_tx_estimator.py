import re
import math

MODE_SECONDS = {
    "TURBO": 6,
    "FAST": 10,
    "NORMAL": 15,
    "SLOW": 30,
}

# Empirical chars-per-frame fit from measured JS8Call compact-report timing.
# Turbo appears to carry slightly fewer useful characters per frame than
# Fast/Normal on JR dot-separated reports, so the estimator is mode-aware.
MODE_CHARS_PER_FRAME = {
    "TURBO": 8.0,
    "FAST": 8.0,
    "NORMAL": 8.0,
    "SLOW": 9.0,
}

# Accept both old long-form suffixes and new compact suffixes.
MODE_TOKEN_TO_NAME = {
    "T": "TURBO",
    "F": "FAST",
    "N": "NORMAL",
    "S": "SLOW",
    "TURBO": "TURBO",
    "FAST": "FAST",
    "NORMAL": "NORMAL",
    "SLOW": "SLOW",
}

MODE_NAME_TO_TOKEN = {
    "TURBO": "T",
    "FAST": "F",
    "NORMAL": "N",
    "SLOW": "S",
}

MODE_SUFFIX_RE = re.compile(r'(?:^|[;\s])M\|(?P<mode>TURBO|FAST|NORMAL|SLOW|T|F|N|S)\b', re.IGNORECASE)
ENTRY_RE = re.compile(
    r'J\|(?P<call>[^|;\s]+)\|(?P<snr>[+-]?\d+(?:\.\d+)?)\|(?P<minutes>\d+)',
    re.IGNORECASE,
)


def _is_hr_message(message_text):
    text = str(message_text or "").strip()
    return bool(re.match(r'(?:@JS8MESH|[^\s;]+)\s+HR(?:\||\.)', text, re.IGNORECASE) or re.match(r'(?:@JS8MESH\s+)?HR(?:\||\.)', text, re.IGNORECASE))
def normalize_mesh_mode(value, default="NORMAL"):
    mode = str(value or "").strip().upper()
    normalized = MODE_TOKEN_TO_NAME.get(mode)
    if normalized:
        return normalized
    fallback = str(default or "NORMAL").strip().upper()
    return MODE_TOKEN_TO_NAME.get(fallback, "NORMAL")


def parse_mesh_mode_suffix(message_text, default="NORMAL"):
    text = str(message_text or "")
    match = MODE_SUFFIX_RE.search(text)
    if not match:
        return normalize_mesh_mode(default)
    return normalize_mesh_mode(match.group("mode"), default=default)


def append_mesh_mode_suffix(message_text, mode):
    mode_name = normalize_mesh_mode(mode)
    token = MODE_NAME_TO_TOKEN[mode_name]
    text = str(message_text or "").strip()
    text = MODE_SUFFIX_RE.sub("", text).strip(" ;")
    if not text:
        return f"M|{token}"
    return f"{text};M|{token}"


def extract_mesh_entries(message_text):
    text = str(message_text or "").strip()
    if not text:
        return []
    segments = [segment.strip() for segment in text.replace("\r", "\n").split(";") if segment.strip()]
    if segments and re.match(r'(?:@JS8MESH|[^\s;]+)\s+(?:JR|JRN|JRS|HR)(?:\||\.)', segments[0], re.IGNORECASE):
        return [{"segment": segment} for segment in segments]
    if segments and re.match(r'(?:@JS8MESH\s+)?(?:JR|JRN|JRS|HR)(?:\||\.)', segments[0], re.IGNORECASE):
        return [{"segment": segment} for segment in segments]
    return [m.groupdict() for m in ENTRY_RE.finditer(text)]


def estimate_mesh_report_frames_from_text(message_text, mode="NORMAL"):
    text = str(message_text or "").strip()
    entries = extract_mesh_entries(text)
    if not entries:
        return 0

    # Empirical fit to user-measured JS8Call results for compact JS8Mesh
    # reports. The per-mode payload density improves the estimate across
    # Normal/Fast/Turbo without hard-coding any single sample length.
    total_chars = len(text)
    mode_name = normalize_mesh_mode(mode)
    chars_per_frame = float(MODE_CHARS_PER_FRAME.get(mode_name, 9.0))
    return max(1, int(math.ceil(total_chars / chars_per_frame)))


def estimate_mesh_report_seconds(message_text, mode):
    mode = normalize_mesh_mode(mode)
    frames = estimate_mesh_report_frames_from_text(message_text, mode=mode)
    seconds = frames * MODE_SECONDS[mode]
    if mode == "TURBO" and seconds > 0:
        seconds += 6
    if mode == "FAST" and seconds > 0:
        seconds += 10
    if mode == "NORMAL" and seconds > 0:
        seconds += 15
    if seconds > 0 and _is_hr_message(message_text):
        if mode == "FAST":
            seconds += 20
        elif mode == "NORMAL":
            seconds += 30
    return seconds


def format_duration(seconds):
    total = int(round(float(seconds or 0)))
    if total < 60:
        return f"{total} sec"
    minutes, secs = divmod(total, 60)
    if secs == 0:
        return f"{minutes} min"
    return f"{minutes} min {secs} sec"
