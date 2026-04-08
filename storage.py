import os
import json
import shutil
import sys
from datetime import datetime, timedelta


if getattr(sys, "frozen", False):
    APP_STORAGE_DIR = os.path.join(
        os.path.dirname(os.path.abspath(sys.executable)),
        "data",
    )
else:
    APP_STORAGE_DIR = os.path.join(
        os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
        "JS8Mesh",
    )


def _ensure_storage_dir():
    os.makedirs(APP_STORAGE_DIR, exist_ok=True)


def _current_build_id():
    if not getattr(sys, "frozen", False):
        return ""
    try:
        exe_path = os.path.abspath(sys.executable)
        stat = os.stat(exe_path)
        return f"{os.path.basename(exe_path)}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}"
    except Exception:
        return os.path.abspath(sys.executable)


def _legacy_candidate_paths(filename):
    if getattr(sys, "frozen", False):
        return []
    candidates = []
    cwd = os.getcwd()
    if cwd:
        candidates.append(os.path.join(cwd, filename))

    module_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(module_dir, filename))

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, filename))
        if os.path.basename(exe_dir).lower() == "dist":
            candidates.append(os.path.join(os.path.dirname(exe_dir), filename))

    seen = set()
    ordered = []
    for path in candidates:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(path)
    return ordered


def _storage_path(filename):
    _ensure_storage_dir()
    return os.path.join(APP_STORAGE_DIR, filename)


SETTINGS_FILE = _storage_path("settings.json")
RELIABILITY_FILE = _storage_path("reliability.json")
RELAY_HISTORY_FILE = _storage_path("relay_history.json")
INBOUND_ROUTES_FILE = _storage_path("inbound_routes.json")
SNR_REPORTS_FILE = _storage_path("snr_reports.json")
AUTO_RESPONDER_LOG_FILE = _storage_path("auto_responder_log.json")
TX_MESH_REPORTS_LOG_FILE = _storage_path("tx_mesh_reports_log.json")
HR_LOG_FILE = _storage_path("hr_log.json")
BUILD_INFO_FILE = _storage_path("_build_info.json")

default_settings = {
    "refresh": 1,
    "min_snr": -15,
    "max_hops": 3,
    "user_callsign": "18SV8110",
    "amateur_callsign": "",
    "special_callsign_enabled": False,
    "special_callsign": "",
    "special_callsign_frequency": "",
    "startup_frequency": "7.078 MHz",
    "directed_file": os.path.join(os.getenv("APPDATA") or "", r"Programs\Js8call\DIRECTED.TXT"),
    "js8call_host": "127.0.0.1",
    "js8call_port": 2442,
    "js8call_allow_auto_send": False,
    "sync_frequency_from_js8call": False,
    "activity_display_limit": "500",
    "mesh_station_count": 5,
    "mesh_lookback_minutes": 20,
    "mesh_broadcast_interval_minutes": 20,
    "mesh_broadcast_times_24h": "",
    "mesh_tx_mode": "NORMAL",
    "mesh_tx_time_limit_minutes": 3,
    "mesh_assisted_generation_enabled": False,
    "watched_callsigns": [],
    "relay_tx_mode": "DEFAULT",
    "js8call_speed_warning_acknowledged": False,
    "js8call_speed_warning_dont_show_again": False,
    "test_mode_recent_records_limit": "1000",
    "custom_frequency_options": [],
    "raw_activity_retention_warning_month": "",
    "log_retention_last_pruned_at": "",
}

LOG_RETENTION_DAYS = 90
LOG_PRUNE_INTERVAL_DAYS = 90


def _load_build_info():
    if not os.path.exists(BUILD_INFO_FILE):
        return {}
    try:
        with open(BUILD_INFO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_build_info(info):
    _ensure_storage_dir()
    with open(BUILD_INFO_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(info or {}), f, indent=4)


def _reset_portable_storage_for_new_build():
    if not getattr(sys, "frozen", False):
        return
    current_build_id = _current_build_id()
    if not current_build_id:
        return
    previous_build_id = str(_load_build_info().get("build_id", "") or "").strip()
    if previous_build_id == current_build_id:
        return
    _ensure_storage_dir()
    for filename in (
        "settings.json",
        "reliability.json",
        "relay_history.json",
        "inbound_routes.json",
        "snr_reports.json",
        "auto_responder_log.json",
        "tx_mesh_reports_log.json",
        "hr_log.json",
    ):
        try:
            path = _storage_path(filename)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    _save_build_info({"build_id": current_build_id})


_reset_portable_storage_for_new_build()


def load_json_or_default(filename, default_value):
    _ensure_storage_dir()
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    for legacy_path in _legacy_candidate_paths(os.path.basename(filename)):
        try:
            if os.path.exists(legacy_path):
                if os.path.normcase(os.path.abspath(legacy_path)) == os.path.normcase(os.path.abspath(filename)):
                    continue
                shutil.copyfile(legacy_path, filename)
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(default_value, f, indent=4)

    return default_value


settings = load_json_or_default(SETTINGS_FILE, default_settings)
legacy_user_callsign = str(settings.get("user_callsign", default_settings["user_callsign"])).strip().upper()
if "amateur_callsign" not in settings:
    settings["amateur_callsign"] = ""
if "special_callsign_enabled" not in settings:
    settings["special_callsign_enabled"] = False
if "special_callsign" not in settings:
    settings["special_callsign"] = ""
if "special_callsign_frequency" not in settings:
    settings["special_callsign_frequency"] = ""
if "startup_frequency" not in settings:
    settings["startup_frequency"] = settings.get("selected_frequency", default_settings["startup_frequency"])
if "js8call_host" not in settings:
    settings["js8call_host"] = default_settings["js8call_host"]
if "js8call_port" not in settings:
    settings["js8call_port"] = default_settings["js8call_port"]
if "js8call_allow_auto_send" not in settings:
    settings["js8call_allow_auto_send"] = default_settings["js8call_allow_auto_send"]
if "sync_frequency_from_js8call" not in settings:
    settings["sync_frequency_from_js8call"] = default_settings["sync_frequency_from_js8call"]
if "relay_tx_mode" not in settings:
    settings["relay_tx_mode"] = default_settings["relay_tx_mode"]
if "mesh_tx_time_limit_minutes" not in settings:
    settings["mesh_tx_time_limit_minutes"] = default_settings["mesh_tx_time_limit_minutes"]
if "mesh_assisted_generation_enabled" not in settings:
    settings["mesh_assisted_generation_enabled"] = default_settings["mesh_assisted_generation_enabled"]
if "watched_callsigns" not in settings or not isinstance(settings.get("watched_callsigns"), list):
    settings["watched_callsigns"] = list(default_settings["watched_callsigns"])
settings["user_callsign"] = legacy_user_callsign or settings.get("amateur_callsign", default_settings["user_callsign"])
reliability_db = load_json_or_default(RELIABILITY_FILE, {})
relay_history_db = load_json_or_default(RELAY_HISTORY_FILE, [])
inbound_routes_db = load_json_or_default(INBOUND_ROUTES_FILE, {})
snr_reports_db = load_json_or_default(SNR_REPORTS_FILE, [])
auto_responder_log_db = load_json_or_default(AUTO_RESPONDER_LOG_FILE, [])
tx_mesh_reports_log_db = load_json_or_default(TX_MESH_REPORTS_LOG_FILE, [])
hr_log_db = load_json_or_default(HR_LOG_FILE, [])


def _safe_parse_iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _prune_list_entries_older_than(items, cutoff_dt, timestamp_fields):
    kept = []
    changed = False
    for item in list(items or []):
        if not isinstance(item, dict):
            kept.append(item)
            continue
        item_dt = None
        for field_name in list(timestamp_fields or []):
            item_dt = _safe_parse_iso_datetime(item.get(field_name, ""))
            if item_dt is not None:
                break
        if item_dt is None:
            kept.append(item)
            continue
        if item_dt < cutoff_dt:
            changed = True
            continue
        kept.append(item)
    return kept, changed


def should_prune_retained_logs():
    last_pruned_dt = _safe_parse_iso_datetime(settings.get("log_retention_last_pruned_at", ""))
    now_dt = datetime.now()
    if last_pruned_dt is None:
        return False
    return not ((now_dt - last_pruned_dt) < timedelta(days=LOG_PRUNE_INTERVAL_DAYS))


def initialize_log_retention_schedule_if_needed():
    if _safe_parse_iso_datetime(settings.get("log_retention_last_pruned_at", "")) is not None:
        return False
    settings["log_retention_last_pruned_at"] = datetime.now().isoformat(timespec="seconds")
    save_settings(settings)
    return True


def prune_retained_logs():
    now_dt = datetime.now()
    if not should_prune_retained_logs():
        return False

    cutoff_dt = now_dt - timedelta(days=LOG_RETENTION_DAYS)
    changed_any = False

    pruned_relay_history, relay_changed = _prune_list_entries_older_than(
        relay_history_db,
        cutoff_dt,
        ("timestamp", "created_at", "resolved_at", "ack_datetime"),
    )
    if relay_changed:
        relay_history_db[:] = pruned_relay_history
        changed_any = True

    pruned_requested_jr_log, requested_jr_changed = _prune_list_entries_older_than(
        auto_responder_log_db,
        cutoff_dt,
        ("timestamp",),
    )
    if requested_jr_changed:
        auto_responder_log_db[:] = pruned_requested_jr_log
        changed_any = True

    pruned_tx_mesh_log, tx_mesh_changed = _prune_list_entries_older_than(
        tx_mesh_reports_log_db,
        cutoff_dt,
        ("timestamp",),
    )
    if tx_mesh_changed:
        tx_mesh_reports_log_db[:] = pruned_tx_mesh_log
        changed_any = True

    pruned_hr_log, hr_changed = _prune_list_entries_older_than(
        hr_log_db,
        cutoff_dt,
        ("timestamp",),
    )
    if hr_changed:
        hr_log_db[:] = pruned_hr_log
        changed_any = True

    settings["log_retention_last_pruned_at"] = now_dt.isoformat(timespec="seconds")
    save_settings(settings)

    if changed_any:
        save_relay_history(relay_history_db)
        save_auto_responder_log(auto_responder_log_db)
        save_tx_mesh_reports_log(tx_mesh_reports_log_db)
        save_hr_log(hr_log_db)
    return changed_any


def save_settings(settings_dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings_dict, f, indent=4)


def save_reliability(reliability_dict):
    with open(RELIABILITY_FILE, "w", encoding="utf-8") as f:
        json.dump(reliability_dict, f, indent=4)


def save_relay_history(relay_history_list):
    with open(RELAY_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(relay_history_list, f, indent=4)


def save_inbound_routes(inbound_routes_dict):
    with open(INBOUND_ROUTES_FILE, "w", encoding="utf-8") as f:
        json.dump(inbound_routes_dict, f, indent=4)


def save_snr_reports(snr_reports_list):
    with open(SNR_REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(snr_reports_list, f, indent=4)


def save_hr_log(hr_log_list):
    with open(HR_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(hr_log_list, f, indent=4)


def save_auto_responder_log(auto_responder_log_list):
    with open(AUTO_RESPONDER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(auto_responder_log_list, f, indent=4)


def save_tx_mesh_reports_log(tx_mesh_reports_log_list):
    with open(TX_MESH_REPORTS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(tx_mesh_reports_log_list, f, indent=4)
