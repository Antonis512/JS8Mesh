import re
from datetime import datetime
from mesh_tx_estimator import estimate_mesh_report_seconds, normalize_mesh_mode


JM_FULL_PATTERN = re.compile(
    r"JM\|(?P<source>[^|]+)\|(?P<heard>[^|]+)\|A?(?P<avg_snr>[+-]?\d+(?:\.\d+)?)"
    r"(?:\|L(?P<minutes>\d+)m)?(?:\|N(?P<count>\d+))?",
    re.IGNORECASE,
)

JM_COMPACT_PATTERN = re.compile(
    r"J\|(?P<heard>[^|]+)\|(?P<avg_snr>[+-]?\d+(?:\.\d+)?)"
    r"(?:\|(?P<minutes>\d{1,6}))?",
    re.IGNORECASE,
)

def _safe_float(value, default=None):
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def _minutes_ago(dt_value, now=None):
    if dt_value is None:
        return None

    if now is None:
        now = datetime.now()

    minutes = int((now - dt_value).total_seconds() / 60.0)
    if minutes < 0:
        minutes = 0
    return minutes


def _normalize_callsign(value):
    return str(value).strip().upper()


def _is_group_callsign(value):
    return str(value or "").strip().upper().startswith("@")


def _is_routed_callsign(value):
    return ">" in str(value or "").strip().upper()


def _parse_frequency_mhz(value):
    raw = str(value).strip().upper()
    if not raw:
        return None

    raw = raw.replace(",", ".")

    has_mhz = "MHZ" in raw
    has_khz = "KHZ" in raw
    has_hz = "HZ" in raw and not has_mhz and not has_khz

    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None

    try:
        number = float(match.group(1))
    except (ValueError, TypeError):
        return None

    if number <= 0:
        return None

    if has_mhz:
        return number

    if has_khz:
        return number / 1000.0

    if has_hz:
        return number / 1000000.0

    if number >= 1000000:
        return number / 1000000.0

    if number >= 1000:
        return number / 1000.0

    return number


def _normalize_frequency_text(value):
    mhz_value = _parse_frequency_mhz(value)
    if mhz_value is None:
        return ""

    normalized = f"{mhz_value:.6f}".rstrip("0").rstrip(".")
    return f"{normalized} MHz"


def _frequency_matches(record_freq, selected_freq, tolerance_mhz=0.0005):
    if not selected_freq:
        return True

    rec_mhz = _parse_frequency_mhz(record_freq)
    sel_mhz = _parse_frequency_mhz(selected_freq)

    if rec_mhz is None or sel_mhz is None:
        return False

    return abs(rec_mhz - sel_mhz) <= tolerance_mhz


def parse_mesh_report_entries(msg_text, fallback_source=""):
    text = " ".join(str(msg_text or "").strip().split())
    if not text:
        return []

    entries = []
    fallback_source = _normalize_callsign(fallback_source)

    compact_segments = [segment.strip() for segment in text.replace("\r", "\n").split(";") if segment.strip()]
    if compact_segments:
        first_segment = compact_segments[0]
        report_kind = None
        kind_match = re.match(r"(?:@JS8MESH\s+)?(JR|JRN|JRS|HR)(?:\||\.)", first_segment, re.IGNORECASE)
        if kind_match:
            report_kind = str(kind_match.group(1) or "JR").strip().upper()
        if report_kind:
            for index, segment in enumerate(compact_segments):
                clean_segment = str(segment or "").strip()
                if index == 0:
                    clean_segment = re.sub(r"^(?:@JS8MESH\s+)?(?:JR|JRN|JRS|HR)(?:\||\.)", "", clean_segment, flags=re.IGNORECASE).strip()
                parts = [part.strip() for part in re.split(r"[|.]", clean_segment) if part.strip()]
                if len(parts) < 3:
                    continue

                wave = 1
                parent = ""
                heard = ""
                avg_snr = None
                minutes = None
                is_node = False

                if parts[0].isdigit():
                    try:
                        wave = max(1, int(parts[0]))
                    except (ValueError, TypeError):
                        continue

                    if wave <= 1 and len(parts) == 4:
                        heard_text = str(parts[1] or "").strip()
                        is_node = heard_text.startswith("*") or report_kind == "JRN"
                        heard = _normalize_callsign(heard_text.lstrip("*"))
                        try:
                            avg_snr = float(parts[2])
                            minutes = int(parts[3])
                        except (ValueError, TypeError):
                            continue
                    elif wave >= 2 and len(parts) == 5:
                        heard_text = str(parts[1] or "").strip()
                        is_node = heard_text.startswith("*") or report_kind == "JRN"
                        heard = _normalize_callsign(heard_text.lstrip("*"))
                        parent = _normalize_callsign(parts[2])
                        try:
                            avg_snr = float(parts[3])
                            minutes = int(parts[4])
                        except (ValueError, TypeError):
                            continue
                    else:
                        continue
                elif len(parts) == 3:
                    heard_text = str(parts[0] or "").strip()
                    is_node = heard_text.startswith("*") or report_kind == "JRN"
                    heard = _normalize_callsign(heard_text.lstrip("*"))
                    try:
                        avg_snr = float(parts[1])
                        minutes = int(parts[2])
                    except (ValueError, TypeError):
                        continue
                else:
                    continue

                if not fallback_source or not heard or _is_group_callsign(fallback_source) or _is_group_callsign(heard):
                    continue

                if wave <= 1:
                    parent = fallback_source
                elif not parent:
                    continue
                if _is_group_callsign(parent):
                    continue

                entries.append({
                    "source": fallback_source,
                    "heard": heard,
                    "parent": parent,
                    "avg_snr": avg_snr,
                    "minutes": minutes,
                    "count": 1,
                    "format": f"{report_kind}_COMPACT",
                    "report_kind": report_kind,
                    "is_node": bool(is_node),
                    "wave": wave,
                })
            if entries:
                return entries

        for segment in compact_segments:
            match = JM_FULL_PATTERN.search(segment)
            if match:
                try:
                    source = _normalize_callsign(match.group("source"))
                    heard = _normalize_callsign(match.group("heard"))
                    avg_snr = float(match.group("avg_snr"))
                    minutes_text = match.group("minutes")
                    count_text = match.group("count")
                    minutes = int(minutes_text) if minutes_text is not None else None
                    count = int(count_text) if count_text is not None else 1
                except (ValueError, TypeError):
                    continue

                if not source or not heard or _is_group_callsign(source) or _is_group_callsign(heard):
                    continue

                entries.append({
                    "source": source,
                    "heard": heard,
                    "parent": source,
                    "avg_snr": avg_snr,
                    "minutes": minutes,
                    "count": count,
                    "format": "JM_FULL",
                    "wave": 1,
                })
                continue

            match = JM_COMPACT_PATTERN.search(segment)
            if not match:
                continue

            try:
                source = fallback_source
                heard = _normalize_callsign(match.group("heard"))
                avg_snr = float(match.group("avg_snr"))
                minutes_text = match.group("minutes")
                minutes = int(minutes_text) if minutes_text is not None else None
            except (ValueError, TypeError):
                continue

            if not source or not heard or _is_group_callsign(source) or _is_group_callsign(heard):
                continue

            entries.append({
                "source": source,
                "heard": heard,
                "parent": source,
                "avg_snr": avg_snr,
                "minutes": minutes,
                "count": 1,
                "format": "JM_COMPACT",
                "wave": 1,
            })

    return entries


def parse_mesh_report_message(msg_text, fallback_source=""):
    entries = parse_mesh_report_entries(msg_text, fallback_source=fallback_source)
    if not entries:
        return None
    return entries[0]


def mesh_report_record_text(record):
    destination = str(record.get("to", "") or "").strip()
    payload = str(record.get("msg", "") or "").strip()
    if destination and payload:
        return f"{destination} {payload}"
    return payload or destination


def mesh_report_sender_speed(record):
    return normalize_mesh_mode(str(record.get("jr_sender_speed", "NORMAL") or "NORMAL")) or "NORMAL"


def mesh_report_entry_effective_minutes(record, parsed_entry, now=None):
    if now is None:
        now = datetime.now()

    dt_value = record.get("datetime")
    base_age = _minutes_ago(dt_value, now=now)
    if base_age is None:
        return None

    try:
        reported_minutes = float(parsed_entry.get("minutes", 0) or 0)
    except (ValueError, TypeError):
        reported_minutes = 0.0

    try:
        wave = max(1, int(parsed_entry.get("wave", 1) or 1))
    except (ValueError, TypeError):
        wave = 1

    report_text = mesh_report_record_text(record)
    sender_speed = mesh_report_sender_speed(record)
    sender_tx_minutes = float(estimate_mesh_report_seconds(report_text, sender_speed) or 0.0) / 60.0
    normal_tx_minutes = float(estimate_mesh_report_seconds(report_text, "NORMAL") or 0.0) / 60.0

    effective = float(base_age) + reported_minutes + sender_tx_minutes + max(0, wave - 1) * normal_tx_minutes
    if effective < 0:
        effective = 0.0
    return effective


def _is_mesh_report_message(msg_text):
    return bool(parse_mesh_report_entries(msg_text))


def build_topology_debug_snapshot(records, max_age_minutes=1440, frequency=None, now=None):
    if now is None:
        now = datetime.now()

    debug = {
        "records_total": len(records),
        "freq_matches": 0,
        "freshness_survivors": 0,
        "field_survivors": 0,
        "snr_survivors": 0,
        "dropped_no_datetime": 0,
        "dropped_stale": 0,
        "dropped_bad_frequency": 0,
        "dropped_no_from": 0,
        "dropped_no_to": 0,
        "dropped_bad_snr": 0,
    }

    for record in records:
        if not _frequency_matches(record.get("freq", ""), frequency):
            debug["dropped_bad_frequency"] += 1
            continue

        debug["freq_matches"] += 1

        dt_value = record.get("datetime")
        minutes = _minutes_ago(dt_value, now=now)

        if minutes is None:
            debug["dropped_no_datetime"] += 1
            continue

        if max_age_minutes is not None and minutes > max_age_minutes:
            debug["dropped_stale"] += 1
            continue

        debug["freshness_survivors"] += 1

        src = _normalize_callsign(record.get("from", ""))
        dst = _normalize_callsign(record.get("to", ""))

        if not src:
            debug["dropped_no_from"] += 1
            continue

        if not dst:
            debug["dropped_no_to"] += 1
            continue

        debug["field_survivors"] += 1

        snr_value = _safe_float(record.get("snr", ""), default=None)
        if snr_value is None:
            debug["dropped_bad_snr"] += 1
            continue

        debug["snr_survivors"] += 1

    return debug


def build_hearing_graph(records, max_age_minutes=1440, frequency=None, now=None):
    if now is None:
        now = datetime.now()

    graph = {}

    for record in records:
        dt_value = record.get("datetime")
        minutes = _minutes_ago(dt_value, now=now)

        if minutes is None:
            continue

        if max_age_minutes is not None and minutes > max_age_minutes:
            continue

        if not _frequency_matches(record.get("freq", ""), frequency):
            continue

        src = _normalize_callsign(record.get("from", ""))
        dst = _normalize_callsign(record.get("to", ""))

        if not src or not dst or _is_group_callsign(src) or _is_group_callsign(dst) or _is_routed_callsign(src) or _is_routed_callsign(dst):
            continue

        snr_value = _safe_float(record.get("snr", ""), default=None)
        if snr_value is None:
            continue

        normalized_freq = _normalize_frequency_text(record.get("freq", ""))

        src_edges = graph.setdefault(src, {})
        edge = src_edges.setdefault(
            dst,
            {
                "count": 0,
                "sum_snr": 0.0,
                "best_snr": snr_value,
                "last_minutes_ago": minutes,
                "frequency": normalized_freq,
            }
        )

        edge["count"] += 1
        edge["sum_snr"] += snr_value

        if snr_value > edge["best_snr"]:
            edge["best_snr"] = snr_value

        if minutes < edge["last_minutes_ago"]:
            edge["last_minutes_ago"] = minutes

    for src, edges in graph.items():
        for dst, edge in edges.items():
            count = edge["count"]
            edge["avg_snr"] = edge["sum_snr"] / float(count) if count > 0 else 0.0
            del edge["sum_snr"]

    return graph


def build_station_summary(records, max_age_minutes=1440, frequency=None, now=None, exclude_callsigns=None):
    if now is None:
        now = datetime.now()
    exclude_norms = {_normalize_callsign(item) for item in (exclude_callsigns or []) if _normalize_callsign(item)}

    summary = {}

    def ensure_station(callsign):
        callsign = _normalize_callsign(callsign)
        if not callsign or _is_group_callsign(callsign) or _is_routed_callsign(callsign) or callsign in exclude_norms:
            return None

        return summary.setdefault(
            callsign,
            {
                "heard_count": 0,
                "sum_snr": 0.0,
                "latest_minutes_ago": None,
                "neighbors": set(),
                "tx_direct_count": 0,
                "tx_group_count": 0,
                "rx_direct_count": 0,
            }
        )

    for record in records:
        dt_value = record.get("datetime")
        minutes = _minutes_ago(dt_value, now=now)

        if minutes is None:
            continue

        if max_age_minutes is not None and minutes > max_age_minutes:
            continue

        if not _frequency_matches(record.get("freq", ""), frequency):
            continue

        src = _normalize_callsign(record.get("from", ""))
        dst = _normalize_callsign(record.get("to", ""))

        if not src or _is_group_callsign(src) or _is_routed_callsign(src):
            continue

        snr_value = _safe_float(record.get("snr", ""), default=None)
        if snr_value is None:
            continue

        station = ensure_station(src)
        if station is None:
            continue

        station["heard_count"] += 1
        station["sum_snr"] += snr_value
        station["latest_minutes_ago"] = (
            minutes if station.get("latest_minutes_ago") is None
            else min(station.get("latest_minutes_ago"), minutes)
        )
        if dst and _is_group_callsign(dst):
            station["tx_group_count"] += 1
        elif not _is_routed_callsign(dst):
            station["tx_direct_count"] += 1
        if dst and not _is_group_callsign(dst) and not _is_routed_callsign(dst):
            station["neighbors"].add(dst)
            recipient = ensure_station(dst)
            if recipient is not None:
                recipient["heard_count"] += 1
                recipient["sum_snr"] += snr_value
                recipient["rx_direct_count"] += 1
                recipient["neighbors"].add(src)
                recipient["latest_minutes_ago"] = (
                    minutes if recipient.get("latest_minutes_ago") is None
                    else min(recipient.get("latest_minutes_ago"), minutes)
                )

    for callsign, station in summary.items():
        count = station["heard_count"]
        station["avg_snr"] = station["sum_snr"] / float(count) if count > 0 else None
        del station["sum_snr"]
        station["neighbor_count"] = len(station["neighbors"])
        labels = []
        if int(station.get("tx_direct_count", 0) or 0) > 0:
            labels.append("TX DIRECT")
        if int(station.get("tx_group_count", 0) or 0) > 0:
            labels.append("TX GROUP")
        if int(station.get("rx_direct_count", 0) or 0) > 0:
            labels.append("RX DIRECT")
        station["traffic_type"] = " + ".join(labels)

    return summary


def rank_best_relays_for_target(
    graph,
    user_cs,
    target_cs,
    reliability_db=None,
    max_candidates=10
):
    reliability_db = reliability_db or {}

    user_cs = _normalize_callsign(user_cs)
    target_cs = _normalize_callsign(target_cs)

    candidates = []

    user_edges = graph.get(user_cs, {})
    for relay, edge1 in user_edges.items():
        relay_edges = graph.get(relay, {})
        edge2 = relay_edges.get(target_cs)

        if not edge2:
            continue

        stats = reliability_db.get(relay, {"S": 0, "F": 0})
        s_count = int(stats.get("S", 0))
        f_count = int(stats.get("F", 0))

        reliability_bonus = (s_count * 1.5) - (f_count * 1.0)

        freshness_penalty = (
            float(edge1.get("last_minutes_ago", 9999)) * 0.08 +
            float(edge2.get("last_minutes_ago", 9999)) * 0.08
        )

        snr_score = (
            float(edge1.get("avg_snr", -30.0)) +
            float(edge2.get("avg_snr", -30.0))
        )

        activity_bonus = (
            float(edge1.get("count", 0)) * 0.4 +
            float(edge2.get("count", 0)) * 0.4
        )

        score = snr_score + activity_bonus + reliability_bonus - freshness_penalty

        candidates.append(
            {
                "relay": relay,
                "score": round(score, 2),
                "user_to_relay": edge1,
                "relay_to_target": edge2,
                "reliability": f"{s_count}/{f_count}",
            }
        )

    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["relay"],
        )
    )

    return candidates[:max_candidates]


def export_topology_nodes_and_edges(records, max_age_minutes=1440, frequency=None, now=None):
    graph = build_hearing_graph(
        records=records,
        max_age_minutes=max_age_minutes,
        frequency=frequency,
        now=now,
    )

    summary = build_station_summary(
        records=records,
        max_age_minutes=max_age_minutes,
        frequency=frequency,
        now=now,
    )

    nodes = []
    for callsign, station in summary.items():
        nodes.append(
            {
                "id": callsign,
                "activity": station.get("heard_count", 0),
                "seen_count": station.get("heard_count", 0),
                "avg_snr": station.get("avg_snr"),
                "latest_minutes_ago": station.get("latest_minutes_ago"),
                "neighbor_count": station.get("neighbor_count", 0),
            }
        )

    edges = []
    for src, destinations in graph.items():
        for dst, edge in destinations.items():
            edges.append(
                {
                    "source": src,
                    "target": dst,
                    "count": edge.get("count", 0),
                    "avg_snr": edge.get("avg_snr"),
                    "best_snr": edge.get("best_snr"),
                    "last_minutes_ago": edge.get("last_minutes_ago"),
                    "frequency": edge.get("frequency", ""),
                }
            )

    nodes.sort(key=lambda item: item["id"])
    edges.sort(key=lambda item: (item["source"], item["target"]))

    return {
        "nodes": nodes,
        "edges": edges,
    }


def _mesh_role_sort_key(role):
    order = {
        "mesh_core": 0,
        "mesh_active": 1,
        "mesh_known": 2,
        "observed_only": 3,
    }
    return order.get(role, 99)


def _build_mesh_topology_from_reports(
    records,
    mesh_activity_minutes=60,
    mesh_core_threshold=2,
    frequency=None,
    now=None,
    exclude_callsigns=None,
):
    if now is None:
        now = datetime.now()
    exclude_norms = {_normalize_callsign(item) for item in (exclude_callsigns or []) if _normalize_callsign(item)}

    node_map = {}
    edge_map = {}
    report_count = 0

    def ensure_node(callsign):
        callsign = _normalize_callsign(callsign)
        if not callsign or callsign in exclude_norms:
            return None

        node = node_map.get(callsign)
        if node is None:
            node = {
                "id": callsign,
                "activity": 0,
                "seen_count": 0,
                "avg_snr_sum": 0.0,
                "avg_snr_count": 0,
                "latest_minutes_ago": None,
                "neighbor_set": set(),
                "mesh_role": "observed_only",
                "mesh_report_count_total": 0,
                "mesh_report_count_recent": 0,
                "last_mesh_report_minutes_ago": None,
                "is_mesh_sender": False,
                "is_mesh_node": False,
                "wave_depth": None,
                "parent_node": "",
            }
            node_map[callsign] = node

        return node

    for record in records:
        if not _frequency_matches(record.get("freq", ""), frequency):
            continue

        dt_value = record.get("datetime")
        minutes = _minutes_ago(dt_value, now=now)
        if minutes is None:
            continue

        parsed_entries = parse_mesh_report_entries(record.get("msg", ""), fallback_source=record.get("from", ""))
        if not parsed_entries:
            continue

        report_count += 1

        source = _normalize_callsign(record.get("from", ""))
        if not source or source in exclude_norms:
            continue

        src_node = ensure_node(source)
        if src_node is None:
            continue
        src_node["is_mesh_sender"] = True
        src_node["is_mesh_node"] = True
        src_node["mesh_report_count_total"] += 1
        if minutes <= mesh_activity_minutes:
            src_node["mesh_report_count_recent"] += 1

        current_last = src_node.get("last_mesh_report_minutes_ago")
        if current_last is None or minutes < current_last:
            src_node["last_mesh_report_minutes_ago"] = minutes

        current_source_wave = src_node.get("wave_depth")
        if current_source_wave is None or 1 < current_source_wave:
            src_node["wave_depth"] = 1
            src_node["parent_node"] = ""

        src_node["activity"] += 1
        src_node["seen_count"] += 1
        local_snr = _safe_float(record.get("snr", ""), default=None)
        if local_snr is not None:
            src_node["avg_snr_sum"] += local_snr
            src_node["avg_snr_count"] += 1
        current_latest = src_node.get("latest_minutes_ago")
        if current_latest is None or minutes < current_latest:
            src_node["latest_minutes_ago"] = minutes

        explicit_mixed_nodes = any(
            str(entry.get("report_kind", "") or "").strip().upper() == "JR" and bool(entry.get("is_node"))
            for entry in parsed_entries
        )

        for parsed in parsed_entries:
            effective_minutes = mesh_report_entry_effective_minutes(record, parsed, now=now)
            if effective_minutes is None:
                continue

            report_kind = str(parsed.get("report_kind", "") or "JR").strip().upper() or "JR"
            is_node = bool(parsed.get("is_node"))
            if report_kind == "JRS":
                is_node = False

            parent = _normalize_callsign(parsed.get("parent") or source)
            heard = parsed["heard"]
            avg_snr = parsed["avg_snr"]
            source_wave = int(parsed.get("wave", 1) or 1)
            local_wave = source_wave + 1

            parent_callsign = source if source_wave <= 1 else parent
            parent_node = ensure_node(parent_callsign)
            dst_node = ensure_node(heard)
            if dst_node is None:
                continue
            if parent_node is None:
                parent_node = src_node

            if dst_node is not None:
                current_wave = dst_node.get("wave_depth")
                if current_wave is None or local_wave < current_wave:
                    dst_node["wave_depth"] = local_wave
                    dst_node["parent_node"] = parent_callsign
                elif local_wave == current_wave and effective_minutes <= (dst_node.get("latest_minutes_ago") if dst_node.get("latest_minutes_ago") is not None else 999999):
                    dst_node["parent_node"] = parent_callsign
                if is_node:
                    dst_node["is_mesh_node"] = True

            dst_node["activity"] += 1
            dst_node["seen_count"] += 1
            dst_node["avg_snr_sum"] += avg_snr
            dst_node["avg_snr_count"] += 1
            current_latest = dst_node.get("latest_minutes_ago")
            if current_latest is None or effective_minutes < current_latest:
                dst_node["latest_minutes_ago"] = effective_minutes

            if parent_node is not None:
                parent_node["neighbor_set"].add(heard)

            edge_key = (parent_callsign, heard)
            edge = edge_map.get(edge_key)
            if edge is None:
                edge = {
                    "source": parent_callsign,
                    "target": heard,
                    "count": 0,
                    "sum_snr": 0.0,
                    "best_snr": avg_snr,
                    "last_minutes_ago": minutes,
                    "frequency": _normalize_frequency_text(record.get("freq", "")),
                }
                edge_map[edge_key] = edge

            edge["count"] += 1
            edge["sum_snr"] += avg_snr

            if avg_snr > edge["best_snr"]:
                edge["best_snr"] = avg_snr

            if effective_minutes < edge["last_minutes_ago"]:
                edge["last_minutes_ago"] = effective_minutes

    for callsign, node in node_map.items():
        recent = int(node.get("mesh_report_count_recent", 0) or 0)
        total = int(node.get("mesh_report_count_total", 0) or 0)
        last_mesh = node.get("last_mesh_report_minutes_ago")

        if total <= 0:
            role = "observed_only"
        elif recent >= mesh_core_threshold:
            role = "mesh_core"
        elif recent >= 1:
            role = "mesh_active"
        elif last_mesh is not None:
            role = "mesh_known"
        else:
            role = "observed_only"

        node["mesh_role"] = role
        node["neighbor_count"] = len(node["neighbor_set"])

        if node["avg_snr_count"] > 0:
            node["avg_snr"] = node["avg_snr_sum"] / float(node["avg_snr_count"])
        else:
            node["avg_snr"] = None

        del node["neighbor_set"]
        del node["avg_snr_sum"]
        del node["avg_snr_count"]

    def build_path_text(callsign):
        parts = []
        seen = set()
        current = callsign
        while current and current not in seen:
            seen.add(current)
            parts.append(current)
            current_node = node_map.get(current)
            if not current_node:
                break
            parent = str(current_node.get("parent_node", "") or "").strip().upper()
            if not parent:
                break
            current = parent
        return " > ".join(reversed(parts))

    for callsign, node in node_map.items():
        node["path_text"] = build_path_text(callsign)

    nodes = list(node_map.values())
    nodes.sort(key=lambda item: (_mesh_role_sort_key(item.get("mesh_role")), item["id"]))

    edges = []
    for edge in edge_map.values():
        count = edge["count"]
        edge["avg_snr"] = edge["sum_snr"] / float(count) if count > 0 else 0.0
        del edge["sum_snr"]
        edges.append(edge)

    edges.sort(key=lambda item: (item["source"], item["target"]))

    stats = {
        "mesh_reports_seen": report_count,
        "traffic_only": 0,
        "mesh_known": 0,
        "mesh_active": 0,
        "mesh_core": 0,
        "observed_only": 0,
    }

    for node in nodes:
        role = node.get("mesh_role", "observed_only")
        stats[role] = stats.get(role, 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": stats,
    }


def export_dual_topology_snapshot(
    records,
    traffic_max_age_minutes=65,
    mesh_activity_minutes=60,
    mesh_core_threshold=2,
    frequency=None,
    now=None,
    exclude_callsigns=None,
):
    if now is None:
        now = datetime.now()

    traffic_graph = build_hearing_graph(
        records=records,
        max_age_minutes=traffic_max_age_minutes,
        frequency=frequency,
        now=now,
    )

    traffic_summary = build_station_summary(
        records=records,
        max_age_minutes=traffic_max_age_minutes,
        frequency=frequency,
        now=now,
        exclude_callsigns=exclude_callsigns,
    )

    traffic_nodes = []
    for callsign, station in traffic_summary.items():
        traffic_nodes.append(
            {
                "id": callsign,
                "traffic_type": station.get("traffic_type", ""),
                "activity": station.get("heard_count", 0),
                "seen_count": station.get("heard_count", 0),
                "avg_snr": station.get("avg_snr"),
                "latest_minutes_ago": station.get("latest_minutes_ago"),
                "neighbor_count": station.get("neighbor_count", 0),
                "mesh_role": "observed_only",
                "wave_depth": None,
                "parent_node": "",
                "path_text": "",
                "mesh_report_count_total": 0,
                "mesh_report_count_recent": 0,
                "last_mesh_report_minutes_ago": None,
            }
        )

    traffic_edges = []
    for src, destinations in traffic_graph.items():
        for dst, edge in destinations.items():
            traffic_edges.append(
                {
                    "source": src,
                    "target": dst,
                    "count": edge.get("count", 0),
                    "avg_snr": edge.get("avg_snr"),
                    "best_snr": edge.get("best_snr"),
                    "last_minutes_ago": edge.get("last_minutes_ago"),
                    "frequency": edge.get("frequency", ""),
                }
            )

    mesh_export = _build_mesh_topology_from_reports(
        records=records,
        mesh_activity_minutes=mesh_activity_minutes,
        mesh_core_threshold=mesh_core_threshold,
        frequency=frequency,
        now=now,
        exclude_callsigns=exclude_callsigns,
    )

    mesh_node_map = {
        str(node.get("id", "")).strip().upper(): dict(node)
        for node in mesh_export["nodes"]
        if str(node.get("id", "")).strip()
    }
    for callsign, station in traffic_summary.items():
        if callsign in mesh_node_map:
            node = mesh_node_map[callsign]
            if node.get("latest_minutes_ago") is None and station.get("latest_minutes_ago") is not None:
                node["latest_minutes_ago"] = station.get("latest_minutes_ago")
            if node.get("avg_snr") is None and station.get("avg_snr") is not None:
                node["avg_snr"] = station.get("avg_snr")
            if not node.get("neighbor_count"):
                node["neighbor_count"] = station.get("neighbor_count", 0)
            if not node.get("activity"):
                node["activity"] = station.get("heard_count", 0)
            if not node.get("seen_count"):
                node["seen_count"] = station.get("heard_count", 0)
            continue

        mesh_node_map[callsign] = {
            "id": callsign,
            "traffic_type": station.get("traffic_type", ""),
            "activity": station.get("heard_count", 0),
            "seen_count": station.get("heard_count", 0),
            "avg_snr": station.get("avg_snr"),
            "latest_minutes_ago": station.get("latest_minutes_ago"),
            "neighbor_count": station.get("neighbor_count", 0),
            "mesh_role": "observed_only",
            "wave_depth": None,
            "parent_node": "",
            "path_text": "",
            "mesh_report_count_total": 0,
            "mesh_report_count_recent": 0,
            "last_mesh_report_minutes_ago": None,
        }

    mesh_nodes = sorted(mesh_node_map.values(), key=lambda item: str(item.get("id", "")))
    mesh_stats = {
        "mesh_core": 0,
        "mesh_active": 0,
        "mesh_known": 0,
        "observed_only": 0,
    }
    for node in mesh_nodes:
        role = str(node.get("mesh_role", "observed_only") or "observed_only")
        mesh_stats[role] = mesh_stats.get(role, 0) + 1

    traffic_nodes.sort(key=lambda item: item["id"])
    traffic_edges.sort(key=lambda item: (item["source"], item["target"]))

    return {
        "traffic": {
            "nodes": traffic_nodes,
            "edges": traffic_edges,
        },
        "mesh": {
            "nodes": mesh_nodes,
            "edges": mesh_export["edges"],
        },
        "mesh_stats": mesh_stats,
    }
