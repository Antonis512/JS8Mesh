import re
from datetime import datetime
from callsign_utils import normalize_callsign
from topology_engine import parse_mesh_report_entries, mesh_report_entry_effective_minutes
from time_utils import utc_now_naive


CATEGORY_RANK = {
    "TURBO": 4,
    "FAST": 3,
    "NORMAL": 2,
    "SLOW": 1,
}

SUCCESS_POINTS = 1.0
FAILURE_POINTS = 0.3
MAX_RELAY_HISTORY_BONUS = 10
EXACT_PATH_SUCCESS_POINTS = 1.0
EXACT_PATH_FAILURE_POINTS = 0.25
MAX_INHERITED_HISTORY_POINTS = 1.0

PAYLOAD_SNR_RE = re.compile(r"\bSNR\s*([+-]?\d+)\b", re.IGNORECASE)


def freshness_bucket(age_minutes):
    if age_minutes <= 10:
        return "0-10 min"
    if age_minutes <= 30:
        return "10-30 min"
    if age_minutes <= 65:
        return "30-65 min"
    return ">65 min"


def freshness_factor(age_minutes):
    if age_minutes <= 10:
        return 1.0
    if age_minutes <= 30:
        return 0.8
    if age_minutes <= 65:
        return 0.5
    return 0.0


def _normalize_pathway_text(pathway_text):
    return ">".join(
        part.strip().upper()
        for part in str(pathway_text or "").replace("<", ">").split(">")
        if part.strip()
    )


def _pathway_text_from_path(path):
    return ">".join(str(part).strip().upper() for part in list(path or []) if str(part).strip())


def _pathway_relays_from_text(pathway_text):
    parts = [part for part in _normalize_pathway_text(pathway_text).split(">") if part]
    if len(parts) < 3:
        return []
    return parts[1:-1]


def _format_history_value(value):
    rounded = round(float(value or 0.0), 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def pathway_history_exact_stats(pathway_text, relay_history_db=None):
    target_path = _normalize_pathway_text(pathway_text)
    if not target_path:
        return 0.0, 0.0

    success_total = 0.0
    failure_total = 0.0
    for item in list(relay_history_db or []):
        item_path = _normalize_pathway_text(item.get("pathway", ""))
        if item_path != target_path:
            continue
        result = str(item.get("result", "")).strip().upper()
        if result == "S":
            success_total += EXACT_PATH_SUCCESS_POINTS
        elif result == "F":
            failure_total += EXACT_PATH_FAILURE_POINTS
    return success_total, failure_total


def pathway_history_inherited_points(pathway_text, relay_history_db=None):
    candidate_relays = _pathway_relays_from_text(pathway_text)
    relay_count = len(candidate_relays)
    if relay_count <= 0:
        return 0.0

    candidate_path = _normalize_pathway_text(pathway_text)
    candidate_relay_set = set(candidate_relays)
    weighted_points = 0.0

    for item in list(relay_history_db or []):
        item_path = _normalize_pathway_text(item.get("pathway", ""))
        if not item_path or item_path == candidate_path:
            continue
        shared_relays = len(candidate_relay_set.intersection(_pathway_relays_from_text(item_path)))
        if shared_relays <= 0:
            continue

        result = str(item.get("result", "")).strip().upper()
        inherited_scale = float(shared_relays) / float(relay_count * 2)
        if result == "S":
            weighted_points += inherited_scale
        elif result == "F":
            weighted_points -= EXACT_PATH_FAILURE_POINTS * inherited_scale

    return max(-MAX_INHERITED_HISTORY_POINTS, min(MAX_INHERITED_HISTORY_POINTS, weighted_points))


def pathway_reliability_text(pathway_text, relay_history_db=None):
    success_total, failure_total = pathway_history_exact_stats(pathway_text, relay_history_db=relay_history_db)
    return f"{_format_history_value(success_total)}/{_format_history_value(failure_total)}"


def pathway_reliability_points(pathway_text, relay_history_db=None):
    success_total, failure_total = pathway_history_exact_stats(pathway_text, relay_history_db=relay_history_db)
    return (success_total - failure_total) + pathway_history_inherited_points(pathway_text, relay_history_db=relay_history_db)


def pathway_reliability_components(pathway_text, relay_history_db=None):
    success_total, failure_total = pathway_history_exact_stats(pathway_text, relay_history_db=relay_history_db)
    inherited_points = pathway_history_inherited_points(pathway_text, relay_history_db=relay_history_db)
    exact_points = success_total - failure_total
    return {
        "exact_success_points": success_total,
        "exact_failure_points": failure_total,
        "exact_reliability_points": exact_points,
        "inherited_reliability_points": inherited_points,
        "total_reliability_points": exact_points + inherited_points,
    }


def nonlinear_relay_penalty(relays):
    relays = max(0, int(relays or 0))
    if relays <= 1:
        return 0
    return (relays - 1) * (relays + 10)


def _format_snr_text(value):
    if value is None:
        return ""
    try:
        number = float(value)
    except (ValueError, TypeError):
        return ""
    if number.is_integer():
        return f"{number:+.0f}"
    return f"{number:+.1f}"


def _parse_payload_reported_snr(msg_text):
    match = PAYLOAD_SNR_RE.search(str(msg_text or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except (ValueError, TypeError):
        return None


def _candidate_is_better(candidate, existing):
    if existing is None:
        return True
    if candidate["datetime"] > existing["datetime"]:
        return True
    if candidate["datetime"] == existing["datetime"] and candidate["snr"] > existing["snr"]:
        return True
    return False


def _observation_to_edge(obs):
    return {
        "snr": obs["snr"],
        "age_minutes": obs["age_minutes"],
        "datetime": obs["datetime"],
        "freq": obs.get("freq", ""),
        "raw": obs.get("raw", ""),
        "msg": obs.get("msg", ""),
        "src_display": obs.get("send_from_display", ""),
        "dst_display": obs.get("send_to_display", ""),
        "heard_by_display": obs.get("heard_by_display", ""),
        "heard_station_display": obs.get("heard_station_display", ""),
        "is_mesh_report": obs.get("is_mesh_report", False),
        "evidence_type": obs.get("evidence_type", ""),
        "local_monitor_snr": obs.get("local_monitor_snr"),
        "payload_reported_snr": obs.get("payload_reported_snr"),
        "source_from": obs.get("source_from", ""),
        "source_to": obs.get("source_to", ""),
    }


def _edge_to_evidence_row(edge, hop_index):
    local_snr = edge.get("local_monitor_snr")
    payload_snr = edge.get("payload_reported_snr")
    used_snr = edge.get("snr")
    age_minutes = edge.get("age_minutes")

    return {
        "hop_index": hop_index,
        "from_display": edge.get("src_display", ""),
        "to_display": edge.get("dst_display", ""),
        "evidence_type": edge.get("evidence_type", ""),
        "used_snr": used_snr,
        "used_snr_text": _format_snr_text(used_snr),
        "age_minutes": age_minutes,
        "age_text": "" if age_minutes is None else f"{int(age_minutes)}m",
        "freq": edge.get("freq", ""),
        "local_monitor_snr": local_snr,
        "local_monitor_snr_text": _format_snr_text(local_snr),
        "payload_reported_snr": payload_snr,
        "payload_reported_snr_text": _format_snr_text(payload_snr),
        "source_from": edge.get("source_from", ""),
        "source_to": edge.get("source_to", ""),
        "msg": edge.get("msg", ""),
        "raw": edge.get("raw", ""),
        "datetime": edge.get("datetime"),
    }


def _effective_hearing_observations(records, max_age_minutes=65, user_cs=""):
    """
    Yield hearing observations in a common format.

    Important directional rule:
    If station A hears station B, that supports SEND direction B -> A.

    Observation meanings:
    - LOCAL_RX: the local station heard the sender at the monitor SNR from DIRECTED.TXT
                hearing: USER heard SENDER
                send direction supported: SENDER -> USER
    - PAYLOAD_SNR: the sender reported hearing the recipient at the payload SNR
                   hearing: SENDER heard RECIPIENT
                   send direction supported: RECIPIENT -> SENDER
    - JM_REPORT: mesh report payload says SOURCE heard HEARD at Axx
                 hearing: SOURCE heard HEARD
                 send direction supported: HEARD -> SOURCE
    """
    now = utc_now_naive()
    user_display = str(user_cs or "").strip().upper()
    user_norm = normalize_callsign(user_display)

    for rec in records:
        dt = rec.get("datetime")
        if dt is None:
            continue

        age_min = (now - dt).total_seconds() / 60.0
        if age_min < 0:
            age_min = 0.0

        if age_min > max_age_minutes:
            continue

        parsed_mesh_entries = parse_mesh_report_entries(rec.get("msg", ""), fallback_source=rec.get("from", ""))
        if parsed_mesh_entries:
            for parsed_mesh in parsed_mesh_entries:
                heard_by_display = parsed_mesh["source"]
                heard_station_display = parsed_mesh["heard"]
                heard_by_norm = normalize_callsign(heard_by_display)
                heard_station_norm = normalize_callsign(heard_station_display)
                if not heard_by_norm or not heard_station_norm:
                    continue
                effective_age_min = mesh_report_entry_effective_minutes(rec, parsed_mesh, now=now)
                if effective_age_min is None:
                    continue
                if effective_age_min > max_age_minutes:
                    continue

                reported_snr = float(parsed_mesh["avg_snr"])
                common = {
                    "datetime": dt,
                    "age_minutes": effective_age_min,
                    "freq": rec.get("freq", ""),
                    "raw": rec.get("raw", ""),
                    "msg": rec.get("msg", ""),
                    "is_mesh_report": True,
                    "evidence_type": "JM_REPORT",
                    "local_monitor_snr": rec.get("snr"),
                    "payload_reported_snr": reported_snr,
                    "source_from": rec.get("from", ""),
                    "source_to": rec.get("to", ""),
                    "snr": reported_snr,
                }

                # JR entries represent curated two-way relay-usable links, so they
                # support SEND in both directions between the source and heard station.
                yield {
                    **common,
                    "heard_by_norm": heard_by_norm,
                    "heard_station_norm": heard_station_norm,
                    "heard_by_display": heard_by_display,
                    "heard_station_display": heard_station_display,
                    "send_from_norm": heard_by_norm,
                    "send_to_norm": heard_station_norm,
                    "send_from_display": heard_by_display,
                    "send_to_display": heard_station_display,
                }
                yield {
                    **common,
                    "heard_by_norm": heard_by_norm,
                    "heard_station_norm": heard_station_norm,
                    "heard_by_display": heard_by_display,
                    "heard_station_display": heard_station_display,
                    "send_from_norm": heard_station_norm,
                    "send_to_norm": heard_by_norm,
                    "send_from_display": heard_station_display,
                    "send_to_display": heard_by_display,
                }
            continue

        sender_display = rec.get("from", "")
        recipient_display = rec.get("to", "")
        sender_norm = rec.get("from_norm", normalize_callsign(sender_display))
        recipient_norm = rec.get("to_norm", normalize_callsign(recipient_display))
        if not sender_norm:
            continue

        try:
            local_snr = float(rec["snr"])
        except (ValueError, TypeError, KeyError):
            continue

        if user_norm:
            yield {
                "datetime": dt,
                "age_minutes": age_min,
                "heard_by_norm": user_norm,
                "heard_station_norm": sender_norm,
                "heard_by_display": user_display,
                "heard_station_display": sender_display,
                "send_from_norm": sender_norm,
                "send_to_norm": user_norm,
                "send_from_display": sender_display,
                "send_to_display": user_display,
                "snr": local_snr,
                "freq": rec.get("freq", ""),
                "raw": rec.get("raw", ""),
                "msg": rec.get("msg", ""),
                "is_mesh_report": False,
                "evidence_type": "LOCAL_RX",
                "local_monitor_snr": local_snr,
                "payload_reported_snr": None,
                "source_from": rec.get("from", ""),
                "source_to": rec.get("to", ""),
            }

        payload_snr = _parse_payload_reported_snr(rec.get("msg", ""))
        if recipient_norm and payload_snr is not None:
            yield {
                "datetime": dt,
                "age_minutes": age_min,
                "heard_by_norm": sender_norm,
                "heard_station_norm": recipient_norm,
                "heard_by_display": sender_display,
                "heard_station_display": recipient_display,
                "send_from_norm": recipient_norm,
                "send_to_norm": sender_norm,
                "send_from_display": recipient_display,
                "send_to_display": sender_display,
                "snr": payload_snr,
                "freq": rec.get("freq", ""),
                "raw": rec.get("raw", ""),
                "msg": rec.get("msg", ""),
                "is_mesh_report": False,
                "evidence_type": "PAYLOAD_SNR",
                "local_monitor_snr": local_snr,
                "payload_reported_snr": payload_snr,
                "source_from": rec.get("from", ""),
                "source_to": rec.get("to", ""),
            }


def latest_direct_reports(records, user_cs, target_cs, max_age_minutes=65):
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)

    user_heard_target = None
    target_heard_user = None

    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        rec_from = obs["send_from_norm"]
        rec_to = obs["send_to_norm"]

        if rec_from == user_norm and rec_to == target_norm:
            if user_heard_target is None or obs["datetime"] > user_heard_target["datetime"]:
                user_heard_target = {
                    "datetime": obs["datetime"],
                    "from": obs["send_from_display"],
                    "to": obs["send_to_display"],
                    "snr": obs["snr"],
                    "is_mesh_report": obs["is_mesh_report"],
                }

        if rec_from == target_norm and rec_to == user_norm:
            if target_heard_user is None or obs["datetime"] > target_heard_user["datetime"]:
                target_heard_user = {
                    "datetime": obs["datetime"],
                    "from": obs["send_from_display"],
                    "to": obs["send_to_display"],
                    "snr": obs["snr"],
                    "is_mesh_report": obs["is_mesh_report"],
                }

    return user_heard_target, target_heard_user


def build_send_graph(records, user_cs, min_snr=-15, max_age_minutes=65):
    graph = {}

    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        if obs["snr"] < min_snr:
            continue

        src_norm = obs["send_from_norm"]
        dst_norm = obs["send_to_norm"]
        if not src_norm or not dst_norm:
            continue

        key = (src_norm, dst_norm)
        candidate = _observation_to_edge(obs)
        existing = graph.get(key)
        if _candidate_is_better(candidate, existing):
            graph[key] = candidate

    return graph


def direct_path_evidence(records, user_cs, target_cs, max_age_minutes=65):
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)
    candidates = []

    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        rec_from = obs["send_from_norm"]
        rec_to = obs["send_to_norm"]
        if rec_from == user_norm and rec_to == target_norm:
            candidates.append(_observation_to_edge(obs))

    candidates.sort(key=lambda item: (item["datetime"], item["snr"]), reverse=True)
    return [_edge_to_evidence_row(edge, 1) for edge in candidates[:8]]


def graph_has_direct_path(records, user_cs, target_cs, max_age_minutes=65):
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)

    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        rec_from = obs["send_from_norm"]
        rec_to = obs["send_to_norm"]

        if rec_from == user_norm and rec_to == target_norm:
            return True

    return False


def path_reliability_points(path, reliability_db=None, relay_history_db=None):
    pathway_text = _pathway_text_from_path(path)
    if relay_history_db is not None:
        return pathway_reliability_points(pathway_text, relay_history_db=relay_history_db)

    relay_nodes = path[1:-1]
    if not relay_nodes:
        return 0.0

    weighted_points = 0.0
    for relay in relay_nodes:
        stats = (reliability_db or {}).get(relay, {"S": 0, "F": 0})
        successes = stats.get("S", 0)
        failures = stats.get("F", 0)
        weighted_points += (successes * SUCCESS_POINTS) - (failures * FAILURE_POINTS)

    return weighted_points


def relay_history_bonus(path, reliability_db=None, relay_history_db=None):
    weighted_points = path_reliability_points(path, reliability_db=reliability_db, relay_history_db=relay_history_db)
    return max(0, min(MAX_RELAY_HISTORY_BONUS, int(round(weighted_points))))





def _safe_parse_iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def normalize_relay_profile(stats):
    stats = dict(stats or {})
    success_count = int(stats.get("success_count", stats.get("S", 0)) or 0)
    failure_count = int(stats.get("failure_count", stats.get("F", 0)) or 0)
    participation_count = int(stats.get("participation_count", success_count + failure_count) or 0)
    return {
        "success_count": max(0, success_count),
        "failure_count": max(0, failure_count),
        "last_success": str(stats.get("last_success", "") or "").strip(),
        "last_failure": str(stats.get("last_failure", "") or "").strip(),
        "last_result": str(stats.get("last_result", "") or "").strip().upper(),
        "participation_count": max(0, participation_count),
        "last_updated": str(stats.get("last_updated", "") or "").strip(),
        "S": max(0, success_count),
        "F": max(0, failure_count),
    }


def compute_station_relay_score(profile):
    profile = normalize_relay_profile(profile)
    successes = profile["success_count"]
    failures = profile["failure_count"]
    total = successes + failures
    now = datetime.now()

    if total <= 0:
        return 0.0

    score = ((successes / float(total)) - 0.5) * 2.0
    score += min(0.8, successes * 0.12)

    last_success_dt = _safe_parse_iso_datetime(profile.get("last_success"))
    if last_success_dt is not None:
        age = max(0.0, (now - last_success_dt).total_seconds() / 60.0)
        if age <= 180:
            score += 0.8 * (1.0 - (age / 180.0))
        elif age <= 720:
            score += 0.25 * (1.0 - ((age - 180.0) / 540.0))

    last_failure_dt = _safe_parse_iso_datetime(profile.get("last_failure"))
    if last_failure_dt is not None:
        age = max(0.0, (now - last_failure_dt).total_seconds() / 60.0)
        if age <= 45:
            score -= 0.5 * (1.0 - (age / 45.0))
        elif age <= 720:
            score -= 0.15 * (1.0 - ((age - 45.0) / 675.0))

    if last_success_dt is not None and last_failure_dt is not None and last_success_dt > last_failure_dt:
        recovery_age = max(0.0, (now - last_success_dt).total_seconds() / 60.0)
        if recovery_age <= 720:
            score += 0.35 * (1.0 - (recovery_age / 720.0))

    if profile.get("last_result") == "S":
        score += 0.1
    elif profile.get("last_result") == "F":
        score -= 0.1

    return max(-1.0, min(1.0, score))


def station_relay_status(profile):
    profile = normalize_relay_profile(profile)
    total = profile["success_count"] + profile["failure_count"]
    if total <= 0:
        return "RF Only"

    score = compute_station_relay_score(profile)
    last_result = profile.get("last_result", "")
    last_success_dt = _safe_parse_iso_datetime(profile.get("last_success"))
    last_failure_dt = _safe_parse_iso_datetime(profile.get("last_failure"))

    if last_result == "S" and last_failure_dt is not None and last_success_dt is not None and last_success_dt > last_failure_dt:
        return "Recovering"
    if last_result == "F" and score < 0.15:
        return "Recently Unresponsive"
    if score >= 0.55:
        return "Active & Helpful"
    if score >= 0.2:
        return "Historically Helpful"
    return "Historically Mixed"

def _direct_observations(records, user_cs, target_cs, max_age_minutes=65):
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)
    matches = []

    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        rec_from = obs["send_from_norm"]
        rec_to = obs["send_to_norm"]
        if rec_from == user_norm and rec_to == target_norm:
            matches.append(obs)

    return matches


def _observation_score(obs):
    if not obs:
        return 0
    snr_value = float(obs.get("snr", -30.0))
    age_minutes = float(obs.get("age_minutes", 999.0))
    return int(max(0, min(100, (snr_value + 30.0) * freshness_factor(age_minutes))))


def _best_direct_observation(records, user_cs, target_cs, max_age_minutes=65):
    observations = _direct_observations(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
    if not observations:
        return None

    return max(
        observations,
        key=lambda obs: (
            _observation_score(obs),
            obs.get("datetime"),
            obs.get("snr", -999),
        )
    )

def direct_path_freshness(records, user_cs, target_cs, max_age_minutes=65):
    best_obs = _best_direct_observation(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
    if not best_obs:
        return ">65 min"
    return freshness_bucket(float(best_obs.get("age_minutes", 999.0)))


def direct_path_score(records, user_cs, target_cs, max_age_minutes=65):
    best_obs = _best_direct_observation(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
    return _observation_score(best_obs)


def direct_path_display(records, user_cs, target_cs, max_age_minutes=65):
    """
    Return the canonical direct routing display.

    Recommendations represent the intended send route from USER to TARGET.
    Evidence may be observed in either direction, but the selectable pathway
    text must stay canonical as USER>TARGET.
    """
    return f"{str(user_cs or '').strip().upper()}>{str(target_cs or '').strip().upper()}"


def direct_path_category(records, user_cs, target_cs, max_age_minutes=65):
    best_obs = _best_direct_observation(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
    if not best_obs:
        return "SLOW"
    return snr_category_from_reported(float(best_obs.get("snr", -30.0)))


def build_neighbors(graph):
    neighbors = {}
    for (src, dst), _meta in graph.items():
        neighbors.setdefault(src, []).append(dst)
    return neighbors


def find_paths(graph, start, target, max_hops=3):
    neighbors = build_neighbors(graph)
    max_nodes = max_hops + 2
    queue = [[start]]
    found = []

    while queue:
        path = queue.pop(0)
        node = path[-1]
        if len(path) > max_nodes:
            continue
        if node == target:
            found.append(path)
            continue
        for nxt in neighbors.get(node, []):
            if nxt in path:
                continue
            queue.append(path + [nxt])

    return found


def path_display(path, graph):
    if len(path) < 2:
        return ">".join(path)

    displays = []
    first_edge = graph.get((path[0], path[1]))
    if first_edge:
        displays.append(first_edge.get("src_display", path[0]))
    else:
        displays.append(path[0])

    for i in range(len(path) - 1):
        edge = graph.get((path[i], path[i + 1]))
        if edge:
            displays.append(edge.get("dst_display", path[i + 1]))
        else:
            displays.append(path[i + 1])

    return ">".join(displays)


def path_reliability(path, reliability_db=None, relay_history_db=None):
    pathway_text = _pathway_text_from_path(path)
    if relay_history_db is not None:
        return pathway_reliability_text(pathway_text, relay_history_db=relay_history_db)

    relays = path[1:-1]
    if not relays:
        return "0/0"

    s_total = 0
    f_total = 0
    for relay in relays:
        stats = (reliability_db or {}).get(relay, {"S": 0, "F": 0})
        s_total += stats.get("S", 0)
        f_total += stats.get("F", 0)

    return f"{s_total}/{f_total}"


def path_freshness(path, graph):
    if len(path) < 2:
        return "0-10 min"
    ages = []
    for i in range(len(path) - 1):
        edge = graph.get((path[i], path[i + 1]))
        if edge:
            ages.append(edge["age_minutes"])
    if not ages:
        return ">65 min"
    return freshness_bucket(max(ages))


def snr_category_from_reported(snr_value):
    if snr_value >= 6:
        return "TURBO"
    if snr_value >= -7:
        return "FAST"
    if snr_value >= -14:
        return "NORMAL"
    return "SLOW"


def path_category(path, graph):
    if len(path) < 2:
        return "SLOW"
    edge_snrs = []
    for i in range(len(path) - 1):
        edge = graph.get((path[i], path[i + 1]))
        if edge:
            edge_snrs.append(edge["snr"])
    if not edge_snrs:
        return "SLOW"
    return snr_category_from_reported(min(edge_snrs))


def path_score(path, graph, reliability_db, relay_history_db=None):
    if len(path) < 2:
        return 0

    edge_snrs = []
    edge_ages = []
    for i in range(len(path) - 1):
        edge = graph.get((path[i], path[i + 1]))
        if edge:
            edge_snrs.append(edge["snr"])
            edge_ages.append(edge["age_minutes"])

    if not edge_snrs:
        return 0

    weakest_snr = min(edge_snrs)
    snr_score = max(0, min(100, weakest_snr + 30))
    oldest_age = max(edge_ages) if edge_ages else 999
    fresh_mult = freshness_factor(oldest_age)
    relays = max(0, len(path) - 2)
    relay_penalty = relays * 5
    relay_bonus = relay_history_bonus(path, reliability_db=reliability_db, relay_history_db=relay_history_db)

    score = int((snr_score * fresh_mult) - relay_penalty + relay_bonus)
    return max(score, 0)


def path_evidence(path, graph):
    evidence = []
    for i in range(len(path) - 1):
        edge = graph.get((path[i], path[i + 1]))
        if edge:
            evidence.append(_edge_to_evidence_row(edge, i + 1))
    return evidence




def _build_direct_target_heard_map(records, target_cs, user_cs="", min_snr=-15, max_age_minutes=65):
    target_norm = normalize_callsign(target_cs)
    best = {}
    for obs in _effective_hearing_observations(records, max_age_minutes=max_age_minutes, user_cs=user_cs):
        if obs.get("snr", -999) < min_snr:
            continue
        # Candidate station directly heard the target. In send-graph terms: TARGET -> CANDIDATE
        if obs.get("send_from_norm") != target_norm:
            continue
        candidate_norm = obs.get("send_to_norm")
        if not candidate_norm or candidate_norm == target_norm:
            continue
        edge = _observation_to_edge(obs)
        existing = best.get(candidate_norm)
        if _candidate_is_better(edge, existing):
            best[candidate_norm] = edge
    return best


def recommend_inbound_reachability_paths(records, user_cs, target_cs, max_hops=3, min_snr=-15,
                                         max_age_minutes=65, reliability_db=None,
                                         ignore_freshness=False, classify_callsign=None,
                                         relay_history_db=None):
    reliability_db = reliability_db or {}
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)
    if not user_norm or not target_norm or user_norm == target_norm:
        return []

    age_limit = 10**9 if ignore_freshness else max_age_minutes
    graph = build_send_graph(records, user_cs=user_cs, min_snr=min_snr, max_age_minutes=age_limit)
    target_heard_map = _build_direct_target_heard_map(
        records, target_cs=target_cs, user_cs=user_cs, min_snr=min_snr, max_age_minutes=age_limit
    )

    recommendations = []
    for candidate_norm, target_edge in target_heard_map.items():
        if candidate_norm == user_norm:
            continue
        # Inbound adds the convergence station as one more relay in the end-to-end route,
        # so the user-side path must stay one relay shorter than the total relay limit.
        user_paths = find_paths(graph, user_norm, candidate_norm, max_hops=max(0, max_hops - 1))
        for user_path in user_paths:
            if len(user_path) < 2:
                continue
            # Avoid target appearing as an intermediate node on the user side.
            if target_norm in user_path[:-1]:
                continue

            user_side_display = path_display(user_path, graph)
            candidate_display = target_edge.get("dst_display", user_path[-1])
            target_display = target_edge.get("src_display", target_cs)
            pathway_display = f"{user_side_display}<{target_display}"
            history_components = pathway_reliability_components(user_side_display, relay_history_db=relay_history_db)

            user_side_score = path_score(user_path, graph, reliability_db, relay_history_db=relay_history_db)
            target_side_score = max(0, min(100, int(target_edge.get("snr", -30) + 30)))
            score = min(user_side_score, target_side_score)

            user_edges_ages = []
            for i in range(len(user_path) - 1):
                edge = graph.get((user_path[i], user_path[i + 1]))
                if edge:
                    user_edges_ages.append(edge.get("age_minutes", 10**9))
            all_ages = user_edges_ages + [target_edge.get("age_minutes", 10**9)]
            oldest_age = max(all_ages) if all_ages else 10**9
            freshness = freshness_bucket(oldest_age)

            user_cat = path_category(user_path, graph)
            target_cat = snr_category_from_reported(target_edge.get("snr", -30))
            category = min((user_cat, target_cat), key=lambda c: CATEGORY_RANK.get(c, 1))

            target_path = [target_norm, candidate_norm]
            relays = max(0, (len(user_path) - 1) + (len(target_path) - 1) - 1)
            if relays > max_hops:
                continue

            evidence = path_evidence(user_path, graph)
            evidence.append(_edge_to_evidence_row(target_edge, len(evidence) + 1))

            recommendations.append({
                "pathway": pathway_display,
                "category": category,
                "relays": relays,
                "score": score,
                "reliability": path_reliability(user_path, reliability_db, relay_history_db=relay_history_db),
                "reliability_points": path_reliability_points(user_path, reliability_db, relay_history_db=relay_history_db),
                "exact_success_points": history_components["exact_success_points"],
                "exact_failure_points": history_components["exact_failure_points"],
                "exact_reliability_points": history_components["exact_reliability_points"],
                "inherited_reliability_points": history_components["inherited_reliability_points"],
                "freshness": freshness,
                "is_direct": False,
                "warning": "Inbound Reachability",
                "evidence": evidence,
                "convergence": candidate_display,
                "target": target_display,
            })

    unique = {}
    for rec in recommendations:
        prev = unique.get(rec["pathway"])
        if prev is None or (rec.get("score", 0), rec.get("reliability_points", 0.0)) > (prev.get("score", 0), prev.get("reliability_points", 0.0)):
            unique[rec["pathway"]] = rec

    recommendations = list(unique.values())
    recommendations.sort(
        key=lambda rec: (
            float(rec.get("reliability_points", 0.0)),
            int(rec.get("score", 0)),
            CATEGORY_RANK.get(rec.get("category", "SLOW"), 1),
            -int(rec.get("relays", 0)),
        ),
        reverse=True,
    )
    return recommendations




def sort_recommendations(recommendations):
    recommendations = list(recommendations or [])

    def _recommendation_sort_key(rec):
        is_direct = bool(rec.get("is_direct", False))
        exact_points = float(rec.get("exact_reliability_points", rec.get("reliability_points", 0.0)) or 0.0)
        inherited_points = float(rec.get("inherited_reliability_points", 0.0) or 0.0)
        total_reliability_points = float(rec.get("reliability_points", exact_points + inherited_points) or 0.0)
        score = int(rec.get("score", 0))
        category_rank = CATEGORY_RANK.get(rec.get("category", "SLOW"), 1)
        relays = int(rec.get("relays", 0))
        structural_penalty = nonlinear_relay_penalty(relays)
        ranking_score = (exact_points * 100.0) + score + (inherited_points * 5.0) - structural_penalty

        if is_direct:
            return (2, score, exact_points, total_reliability_points, category_rank, 0)

        return (1, ranking_score, exact_points, total_reliability_points, score, category_rank, -relays)

    recommendations.sort(key=_recommendation_sort_key, reverse=True)
    return recommendations

def recommended_inbound_reachability_paths(*args, **kwargs):
    return recommend_inbound_reachability_paths(*args, **kwargs)

def inject_promoted_inbound_routes(recommendations, promoted_inbound_routes=None):
    merged = {}

    for rec in list(recommendations or []):
        pathway = str(rec.get("pathway", "")).strip()
        if pathway:
            merged[pathway.upper()] = dict(rec)

    for candidate in list(promoted_inbound_routes or []):
        candidate = dict(candidate or {})
        pathway = str(candidate.get("pathway", "")).strip()
        if not pathway:
            continue

        key = pathway.upper()
        existing = merged.get(key)
        if existing is None:
            candidate.setdefault("origin", "inbound_promoted")
            candidate.setdefault("warning", "Promoted Inbound")
            merged[key] = candidate
            continue

        existing = dict(existing)
        evidence = list(existing.get("evidence", []) or [])
        promoted_evidence = list(candidate.get("evidence", []) or [])
        if promoted_evidence:
            existing["evidence"] = promoted_evidence + evidence

        candidate_origin = str(candidate.get("origin", "")).strip().lower()
        existing_origin = str(existing.get("origin", "")).strip().lower()
        if candidate_origin in ("manual_linear", "native_linear_manual"):
            existing["origin"] = "manual_linear"
            existing["warning"] = "Linear Pathway"
        else:
            existing["origin"] = existing.get("origin", "native_linear")
        existing["operational_path"] = candidate.get("operational_path", existing.get("pathway", pathway))
        existing["convergence_node"] = candidate.get("convergence_node", existing.get("convergence_node", ""))
        existing["target"] = candidate.get("target", existing.get("target", ""))
        existing["last_success_time"] = candidate.get("last_success_time", existing.get("last_success_time", ""))
        existing["promotion_started_at"] = candidate.get("promotion_started_at", existing.get("promotion_started_at", ""))
        if candidate_origin not in ("manual_linear", "native_linear_manual"):
            existing["warning"] = str(existing.get("warning", "")).strip() or "Linear Pathway"
        merged[key] = existing

    return sort_recommendations(list(merged.values()))


def recommend_paths(records, user_cs, target_cs, max_hops, min_snr, max_age_minutes,
                    reliability_db, classify_callsign, promoted_inbound_routes=None,
                    relay_history_db=None):
    user_norm = normalize_callsign(user_cs)
    target_norm = normalize_callsign(target_cs)

    graph = build_send_graph(records, user_cs=user_cs, min_snr=min_snr, max_age_minutes=max_age_minutes)
    recommendations = []

    if graph_has_direct_path(records, user_cs, target_cs, max_age_minutes=max_age_minutes):
        direct_display = direct_path_display(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
        direct_category = direct_path_category(records, user_cs, target_cs, max_age_minutes=max_age_minutes)
        direct_history = pathway_reliability_components(direct_display, relay_history_db=relay_history_db)
        recommendations.append({
            "pathway": direct_display,
            "category": direct_category,
            "relays": 0,
            "score": direct_path_score(records, user_cs, target_cs, max_age_minutes=max_age_minutes),
            "reliability": pathway_reliability_text(direct_display, relay_history_db=relay_history_db),
            "reliability_points": pathway_reliability_points(direct_display, relay_history_db=relay_history_db),
            "exact_success_points": direct_history["exact_success_points"],
            "exact_failure_points": direct_history["exact_failure_points"],
            "exact_reliability_points": direct_history["exact_reliability_points"],
            "inherited_reliability_points": direct_history["inherited_reliability_points"],
            "freshness": direct_path_freshness(records, user_cs, target_cs, max_age_minutes=max_age_minutes),
            "is_direct": True,
            "evidence": direct_path_evidence(records, user_cs, target_cs, max_age_minutes=max_age_minutes),
        })

    paths = find_paths(graph, user_norm, target_norm, max_hops=max_hops)

    for path in paths:
        if len(path) == 2 and path[0] == user_norm and path[1] == target_norm:
            continue

        display_path = path_display(path, graph)
        history_components = pathway_reliability_components(display_path, relay_history_db=relay_history_db)
        recommendations.append({
            "pathway": display_path,
            "category": path_category(path, graph),
            "relays": len(path) - 2,
            "score": path_score(path, graph, reliability_db, relay_history_db=relay_history_db),
            "reliability": path_reliability(path, reliability_db, relay_history_db=relay_history_db),
            "reliability_points": path_reliability_points(path, reliability_db, relay_history_db=relay_history_db),
            "exact_success_points": history_components["exact_success_points"],
            "exact_failure_points": history_components["exact_failure_points"],
            "exact_reliability_points": history_components["exact_reliability_points"],
            "inherited_reliability_points": history_components["inherited_reliability_points"],
            "freshness": path_freshness(path, graph),
            "is_direct": False,
            "evidence": path_evidence(path, graph),
        })

    unique = {}
    for rec in recommendations:
        unique[rec["pathway"]] = rec

    recommendations = list(unique.values())
    return inject_promoted_inbound_routes(recommendations, promoted_inbound_routes)


def _profile_frequency_matches(record_freq, selected_freq, tolerance_mhz=0.0005):
    text = str(selected_freq or "").strip().upper()
    if not text or text == "ALL":
        return True
    try:
        from topology_engine import _parse_frequency_mhz as _parse_freq
    except Exception:
        return str(record_freq or "").strip().upper() == text
    rec_mhz = _parse_freq(record_freq)
    sel_mhz = _parse_freq(selected_freq)
    if rec_mhz is None or sel_mhz is None:
        return False
    return abs(rec_mhz - sel_mhz) <= tolerance_mhz


def _rf_visibility_label(score):
    try:
        value = float(score)
    except Exception:
        value = 0.0
    if value >= 0.80:
        return "Strong"
    if value >= 0.60:
        return "Good"
    if value >= 0.40:
        return "Moderate"
    if value >= 0.20:
        return "Weak"
    return "Minimal"


def compute_station_operational_profiles(records, reliability_db=None, selected_frequency="ALL", user_callsign="", now=None, **_kwargs):
    if now is None:
        now = utc_now_naive()
    reliability_db = dict(reliability_db or {})
    user_norm = normalize_callsign(user_callsign)
    profiles = {}

    def ensure_row(callsign):
        norm = normalize_callsign(callsign)
        if not norm:
            return None
        row = profiles.get(norm)
        if row is None:
            stats = normalize_relay_profile(reliability_db.get(norm, {}))
            row = {
                "callsign": norm,
                "freq": str(selected_frequency or "ALL").strip() or "ALL",
                "seen_count": 0,
                "last_seen_dt": None,
                "snr_total": 0.0,
                "snr_count": 0,
                "my_s": int(stats.get("success_count", 0) or 0),
                "my_f": int(stats.get("failure_count", 0) or 0),
                "as_sender": 0,
                "as_recipient": 0,
                "relay_state": "Unknown",
            }
            profiles[norm] = row
        return row

    for rec in records or []:
        if not _profile_frequency_matches(rec.get("freq", ""), selected_frequency):
            continue
        dt = rec.get("datetime")
        try:
            snr_val = float(rec.get("snr", ""))
        except Exception:
            snr_val = None

        sender = ensure_row(rec.get("from", ""))
        if sender is not None:
            sender["seen_count"] += 1
            sender["as_sender"] += 1
            if dt is not None and (sender["last_seen_dt"] is None or dt > sender["last_seen_dt"]):
                sender["last_seen_dt"] = dt
            if snr_val is not None:
                sender["snr_total"] += snr_val
                sender["snr_count"] += 1

        recipient = ensure_row(rec.get("to", ""))
        if recipient is not None:
            recipient["seen_count"] += 1
            recipient["as_recipient"] += 1
            if dt is not None and (recipient["last_seen_dt"] is None or dt > recipient["last_seen_dt"]):
                recipient["last_seen_dt"] = dt
            if snr_val is not None:
                recipient["snr_total"] += snr_val
                recipient["snr_count"] += 1

    out = {}
    for norm, row in profiles.items():
        seen_count = int(row.get("seen_count", 0) or 0)
        last_seen_dt = row.get("last_seen_dt")
        if last_seen_dt is None:
            recency_score = 0.0
            last_seen_text = ""
        else:
            age_min = max(0.0, (now - last_seen_dt).total_seconds() / 60.0)
            if age_min < 5:
                recency_score = 1.0
            elif age_min < 15:
                recency_score = 0.8
            elif age_min < 30:
                recency_score = 0.6
            elif age_min < 60:
                recency_score = 0.4
            elif age_min < 120:
                recency_score = 0.2
            else:
                recency_score = 0.0
            last_seen_text = last_seen_dt.isoformat(timespec="seconds")

        if row["snr_count"] > 0:
            avg_snr = row["snr_total"] / float(row["snr_count"])
            if avg_snr >= 5:
                snr_score = 1.0
            elif avg_snr >= 0:
                snr_score = 0.8
            elif avg_snr >= -10:
                snr_score = 0.6
            elif avg_snr >= -20:
                snr_score = 0.4
            else:
                snr_score = 0.2
        else:
            snr_score = 0.5

        activity_score = min(1.0, seen_count / 20.0)
        rf_visibility = max(0.0, min(1.0, recency_score * 0.5 + activity_score * 0.3 + snr_score * 0.2))
        rf_text = f"{rf_visibility:.2f} ({_rf_visibility_label(rf_visibility)})"

        if norm == user_norm and user_norm:
            relay_state = "Direct"
        else:
            as_sender = int(row.get("as_sender", 0) or 0)
            as_recipient = int(row.get("as_recipient", 0) or 0)
            if as_sender > 0 and as_recipient > 0:
                relay_state = "Relay"
            elif as_recipient > 0:
                relay_state = "Endpoint"
            elif as_sender > 0:
                relay_state = "Direct"
            else:
                relay_state = "Unknown"

        out[norm] = {
            "callsign": norm,
            "freq": row.get("freq", "ALL"),
            "rf_visibility": rf_visibility,
            "rf_visibility_text": rf_text,
            "relay_state": relay_state,
            "my_s": int(row.get("my_s", 0) or 0),
            "my_f": int(row.get("my_f", 0) or 0),
            "seen_count": seen_count,
            "last_seen": last_seen_text,
            "last_seen_dt": last_seen_dt,
        }
    return out
