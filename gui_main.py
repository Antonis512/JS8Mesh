import os
import re
import csv
import json
import hashlib
import time
import random
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta

FAILURE_COOLDOWN_MINUTES = 30
ACK_RECOGNITION_WINDOW_MINUTES = 5
RAW_ACTIVITY_RETENTION_DAYS = 30
FIND_REQUEST_EXPIRY_HOURS = 24
ACK_POSITIVE_TOKENS = {"RR", "QSL", "ACK", "YES", "FB", "RGR", "ROGER"}
ACK_NEGATIVE_TOKENS = {"NO", "NACK", "ERR", "ERROR"}

from storage import (
    settings,
    reliability_db,
    relay_history_db,
    inbound_routes_db,
    snr_reports_db,
    auto_responder_log_db,
    tx_mesh_reports_log_db,
    hr_log_db,
    my_find_searches_db,
    held_find_searches_db,
    APP_STORAGE_DIR,
    save_settings,
    save_reliability,
    save_relay_history,
    save_inbound_routes,
    save_snr_reports,
    save_auto_responder_log,
    save_tx_mesh_reports_log,
    save_hr_log,
    save_my_find_searches,
    save_held_find_searches,
    initialize_log_retention_schedule_if_needed,
    should_prune_retained_logs,
    prune_retained_logs,
)

from callsign_utils import classify_callsign, normalize_callsign
from activity_window import ActivityWindow
from mesh_network_window import MeshNetworkWindow
from request_jr_window import RequestJRWindow
from mesh_report_activity_window import MeshReportActivityWindow
from topology_window import TopologyWindow
from auto_responder_log_window import AutoResponderLogWindow
from find_search_log_window import FindSearchLogWindow
from relay_message_builder import RelayMessageBuilder
from past_relays_window import (
    build_relay_history_entry,
    classify_display_category,
    clear_past_relays_search as _clear_past_relays_search_impl,
    show_past_relays_window as _show_past_relays_window_impl,
    update_past_relays_table as _update_past_relays_table_impl,
)
from pathways_panel import PathwaysPanel
from parser_directed import parse_directed_line
from pathway_evidence_window import PathwayEvidenceWindow
from relay_profiles_window import RelayProfilesWindow
from mesh_tx_estimator import estimate_mesh_report_seconds, format_duration, normalize_mesh_mode, MODE_SECONDS
from js8call_bridge import JS8CallBridge, JS8CallBridgeError, normalize_incoming_speed_name
from time_utils import utc_now_naive

from pathway_engine import (
    recommend_paths,
    recommend_inbound_reachability_paths,
    latest_direct_reports,
    direct_path_score,
    direct_path_freshness,
    direct_path_category,
    direct_path_evidence,
    sort_recommendations,
    compute_station_operational_profiles,
    pathway_reliability_text,
    pathway_reliability_points,
    pathway_reliability_components,
    build_send_graph,
    snr_category_from_reported,
)

from topology_engine import (
    build_hearing_graph,
    build_topology_debug_snapshot,
    export_dual_topology_snapshot,
    parse_mesh_report_entries,
    mesh_report_entry_effective_minutes,
)


class JS8MeshGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("JS8Mesh v0.10.2-beta by SV8TTL, 18SV8110")

        self.bg_color = "#222222"
        self.fg_color = "#ffffff"
        self.highlight_color = "#444444"

        self.root.configure(bg=self.bg_color)
        self._ensure_root_visible_normal()
        self._apply_initial_root_geometry()

        self.directed_file = settings.get("directed_file", "")
        self._raw_activity_pruned_count = 0
        self._directed_tail_position = 0
        self._directed_partial_line = ""
        self._directed_last_mtime_ns = None
        self._directed_last_size = 0
        self.records = []
        self.processed = set()
        self.record_keys = set()

        self.activity_maintenance_dialog = None
        self.activity_warn_threshold = 3000
        self.activity_trim_amount = 1500
        self.activity_window = None
        self.js8call_rx_monitor_window = None
        self.js8call_rx_monitor_text = None
        self.js8call_rx_monitor_status_var = tk.StringVar(value="JS8Call RX monitor is closed.")
        self._js8call_rx_monitor_after_id = None
        self._js8call_rx_monitor_last_text = None
        self._js8call_rx_monitor_last_status = None
        self._js8call_rx_monitor_follow_tail = True
        self._watched_callsign_alerts = []
        self.mesh_network_window = None
        self.mesh_report_activity_window = None
        self.auto_responder_log_window = None
        self.hr_log_window = None
        self.topology_window = None
        self.relay_profiles_window = None
        self._startup_completed = False
        self._background_loops_started = False

        self.past_relays_window = None
        self.pathway_evidence_window = None
        self.last_pathway_recommendations = {}
        self._current_pathway_rows = []
        self._last_retry_warning_pathway = ""
        self._last_retry_warning_at = None
        self.past_relays_count_var = tk.StringVar(value="20")
        self.past_relays_search_var = tk.StringVar(value="")
        self.past_relays_result_filter_var = tk.StringVar(value="All")
        self.past_relays_tree = None
        self._past_relays_drag_start = None
        self._past_relays_row_data = {}
        self._pending_ack_relays = []
        self._pending_find_rebroadcasts = []
        self.auto_responder_debug_window = None
        self.auto_responder_debug_text = None
        self._jr_speed_cache = []
        self._jr_speed_cache_lock = threading.Lock()
        self.mesh_report_activity_session_date = self._now().date()
        self.requested_jr_window = None
        self.requested_jr_requester_var = tk.StringVar(value="")
        self.requested_jr_request_path_var = tk.StringVar(value="")
        self.requested_jr_requested_target_var = tk.StringVar(value="")
        self.requested_jr_requester_callsign = ""
        self.requested_jr_reply_target = ""
        self.requested_jr_first_hop = ""
        self.requested_jr_requested_target_callsign = ""
        self.requested_jr_response_log_event_id = ""
        self.requested_jr_hr_log_event_id = ""
        self.requested_jr_kind_var = tk.StringVar(value="General")
        self.requested_jr_mode_var = tk.StringVar(value="DEFAULT")
        self.requested_jr_lookback_var = tk.StringVar(value="50")
        self.requested_jr_lookback_help_var = tk.StringVar(value="")
        self.requested_jr_estimated_tx_var = tk.StringVar(value="Estimated TX Time: n/a")
        self.requested_jr_default_mode_var = tk.StringVar(value="Default Speed Mode: n/a")
        self.requested_jr_send_effects_var = tk.StringVar(value="")
        self.requested_jr_preview_widget = None
        self.requested_jr_lookback_entry = None
        self.requested_jr_mode_buttons = []
        self.requested_jr_saved_lookback_minutes = 50
        self.requested_jr_hr_limit_blocked = False
        self._find_tick_running = False
        self.requested_jr_lookback_var.trace_add("write", self._on_requested_jr_input_changed)
        self.request_jr_window = None
        self.request_jr_picker_window = None
        self.request_jr_type_var = tk.StringVar(value="GENERAL")
        self.request_jr_target_mode_vars = {
            "GENERAL": tk.StringVar(value="RECIPIENT"),
            "NODES_ONLY": tk.StringVar(value="RECIPIENT"),
            "STATIONS_ONLY": tk.StringVar(value="RECIPIENT"),
            "NEXT_WAVE": tk.StringVar(value="RECIPIENT"),
        }
        self.request_jr_recipient_vars = {
            "GENERAL": tk.StringVar(value=""),
            "NODES_ONLY": tk.StringVar(value=""),
            "STATIONS_ONLY": tk.StringVar(value=""),
            "NEXT_WAVE": tk.StringVar(value=""),
        }
        self.request_jr_preview_var = tk.StringVar(value="")
        self.request_jr_picker_search_var = tk.StringVar(value="")
        self.request_jr_picker_tree = None
        self.request_jr_picker_type = None
        self.request_jr_preview_widget = None
        self.request_jr_frame_widgets = {}
        self.request_jr_entry_widgets = []
        self.request_jr_selected_node_info = {
            "GENERAL": {},
            "NODES_ONLY": {},
            "STATIONS_ONLY": {},
            "NEXT_WAVE": {},
        }
        self.request_jr_window_ui = RequestJRWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            known_nodes_provider=self._known_mesh_nodes_for_request_jr,
            state_changed_callback=self._update_request_jr_preview,
            send_callback=self._send_request_jr_preview_to_js8call,
            node_selected_callback=self._on_request_jr_node_selected,
        )

        self.current_pathway_view = "linear"
        self.current_view_title_var = tk.StringVar(value="VIEWING: LINEAR PATHWAYS")

        self.last_mesh_broadcast_time = None
        self.last_mesh_broadcast_lines = []
        self.last_mesh_scheduled_slot_key = None

        default_frequency_values = [
            "1.842 MHz",
            "3.578 MHz",
            "7.078 MHz",
            "10.130 MHz",
            "14.078 MHz",
            "18.104 MHz",
            "21.078 MHz",
            "24.922 MHz",
            "28.078 MHz",
        ]
        self.default_frequency_options = self._sorted_unique_frequency_options(default_frequency_values)
        stored_custom = settings.get("custom_frequency_options", [])
        normalized_custom = []
        for item in stored_custom:
            norm = self._normalize_frequency_text(item)
            if norm and not self._find_existing_frequency_option(norm, self.default_frequency_options) and norm not in normalized_custom:
                normalized_custom.append(norm)
        normalized_custom = self._sorted_unique_frequency_options(normalized_custom)
        settings["custom_frequency_options"] = normalized_custom
        self.frequency_options = self._sorted_unique_frequency_options(list(self.default_frequency_options) + normalized_custom)

        self.selected_frequency_var = tk.StringVar(
            value=settings.get("selected_frequency") or settings.get("startup_frequency", "7.078 MHz")
        )
        self._ensure_callsign_settings()
        startup_frequency = self._normalize_frequency_text(self.selected_frequency_var.get())
        if startup_frequency and not self._find_existing_frequency_option(startup_frequency):
            normalized_custom = list(settings.get("custom_frequency_options", []))
            normalized_custom.append(startup_frequency)
            settings["custom_frequency_options"] = self._sorted_unique_frequency_options(normalized_custom)
            self.frequency_options = self._sorted_unique_frequency_options(list(self.default_frequency_options) + list(settings.get("custom_frequency_options", [])))
        self._last_callsign_warning_key = None
        self.sync_frequency_from_js8call_var = tk.BooleanVar(
            value=bool(settings.get("sync_frequency_from_js8call", False))
        )
        self.sync_frequency_check = None

        self.topology_mode_var = tk.StringVar(
            value=settings.get("topology_mode", "traffic")
        )

        settings["refresh"] = 1
        save_settings(settings)

        self.create_treeview_style()
        self.create_menu_bar()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_root_close)

        self.activity_window = ActivityWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            select_file_callback=self.select_file,
            force_read_directed_txt_callback=self.force_read_directed_txt,
            update_display_callback=self.update_activity_display_limit,
            initial_display_limit=settings.get("activity_display_limit", "500"),
        )

        self.mesh_network_window = MeshNetworkWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            initial_station_count=settings.get("mesh_station_count", 5),
            initial_lookback_minutes=settings.get("mesh_lookback_minutes", 20),
            initial_broadcast_interval=settings.get("mesh_broadcast_interval_minutes", 20),
            initial_broadcast_times_24h=settings.get("mesh_broadcast_times_24h", ""),
            initial_tx_mode=settings.get("mesh_tx_mode", "NORMAL"),
            initial_tx_time_limit_minutes=settings.get("mesh_tx_time_limit_minutes", 0),
            save_callback=self.save_mesh_settings,
            broadcast_now_callback=self.broadcast_mesh_report_now,
            copy_preview_callback=self.copy_mesh_preview,
            send_to_js8call_callback=self.send_mesh_preview_to_js8call,
            show_activity_callback=self.show_mesh_report_activity_today,
            input_changed_callback=self._on_mesh_window_inputs_changed,
        )

        self.mesh_report_activity_window = MeshReportActivityWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
        )
        self.auto_responder_log_window = AutoResponderLogWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            clear_callback=self.clear_auto_responder_log,
            export_callback=self.export_auto_responder_log_txt,
            export_csv_callback=self.export_auto_responder_log_csv,
            title_text="Requested Report Responds Log",
            empty_summary_text="No Requested Report response activity loaded.",
            summary_prefix="Requested Report response log entries",
        )
        self.tx_mesh_reports_log_window = AutoResponderLogWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            clear_callback=self.clear_tx_mesh_reports_log,
            export_callback=self.export_tx_mesh_reports_log_txt,
            export_csv_callback=self.export_tx_mesh_reports_log_csv,
            title_text="TX Mesh Reports Log",
            empty_summary_text="No TX Mesh Reports activity loaded.",
            summary_prefix="TX Mesh Reports log entries",
            heading_help={
                "timestamp": "When the TX Mesh Reports scheduler event was recorded.",
                "requester": "Usually blank for TX Mesh Reports; reserved for future use.",
                "request_type": "Event type such as MESH reminder or scheduled generation.",
                "frequency": "Frequency used for the scheduled event.",
                "reply_text": "Generated mesh report text, if any.",
                "speed": "Selected send speed for the scheduled mesh report.",
                "status": "REMINDER, GENERATED, SENT, or SKIPPED.",
                "reason": "Reason describing what happened during the scheduled event.",
            },
        )
        self.hr_log_window = AutoResponderLogWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            clear_callback=self.clear_hr_log,
            export_callback=self.export_hr_log_txt,
            export_csv_callback=self.export_hr_log_csv,
            title_text="HR Log",
            empty_summary_text="No HR activity loaded.",
            summary_prefix="HR log entries",
            heading_help={
                "timestamp": "When the HR event was recorded.",
                "requester": "The station that requested the HR report.",
                "request_type": "HR for direct or relayed Heard 4 Stations requests.",
                "frequency": "Frequency used for this HR event.",
                "reply_text": "Generated HR reply text, if any.",
                "speed": "Selected or calculated send speed used for the HR reply.",
                "status": "RECEIVED = HR request accepted for handling. QUEUED = user chose to respond and reply is ready. STAGED = text was loaded into JS8Call only. SENT = response was handed to JS8Call. SKIPPED = ignored, canceled, duplicate, or failed.",
                "reason": "Reason describing what happened during the HR flow.",
            },
        )
        self.my_find_searches_window = FindSearchLogWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            clear_callback=self.clear_my_find_searches_log,
            export_callback=self.export_my_find_searches_log_txt,
            export_csv_callback=self.export_my_find_searches_log_csv,
            title_text="My Find Searches",
            empty_summary_text="No active find searches started by me.",
            summary_prefix="My find search entries",
            heading_help={
                "created_at": "When I started or refreshed this FIND request.",
                "target_callsign": "The callsign I am trying to locate.",
                "requester": "Always me in this logger.",
                "frequency": "Frequency this search applies to.",
                "return_path": "Where I sent the FIND request, such as a node or @JS8MESH.",
                "status": "ACTIVE, FOUND, EXPIRED, or SKIPPED.",
                "expires_in": "Time remaining before this 24-hour search expires.",
                "details": "Who reported hearing the target, or why the search was skipped or expired.",
            },
        )
        self.held_find_searches_window = FindSearchLogWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            clear_callback=self.clear_held_find_searches_log,
            export_callback=self.export_held_find_searches_log_txt,
            export_csv_callback=self.export_held_find_searches_log_csv,
            send_selected_callback=self.send_selected_held_find_search_now,
            title_text="Held Find Searches",
            empty_summary_text="No active find searches being held for other nodes.",
            summary_prefix="Held find search entries",
            heading_help={
                "created_at": "When this remote FIND request was first received or refreshed.",
                "target_callsign": "The callsign another node is trying to locate.",
                "requester": "The node that asked for this FIND search.",
                "frequency": "Frequency this search applies to.",
                "return_path": "Preferred current return path for sending FINDR back.",
                "status": "ACTIVE, FOUND, SENT, EXPIRED, or SKIPPED.",
                "expires_in": "Time remaining before this held search expires.",
                "details": "What happened, including who was heard and how FINDR was routed.",
            },
        )

        self.topology_window = TopologyWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            topology_mode_changed_callback=self._on_topology_mode_changed_from_window,
            wave_filter_changed_callback=self._on_topology_wave_filter_changed_from_window,
            explain_node_callback=self._explain_topology_node,
            initial_topology_mode=settings.get("topology_mode", "traffic"),
        )

        self.pathway_evidence_window = PathwayEvidenceWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
        )

        self.relay_profiles_window = RelayProfilesWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            refresh_callback=self.refresh_relay_profiles_window,
            frequency_options=["ALL"] + list(self.frequency_options),
            initial_frequency="ALL",
        )

        self._restore_pending_relay_ack_state()
        self.root.after(0, self._ensure_root_visible_normal)
        self.root.after(250, self._ensure_root_visible_normal)
        self.root.after(1000, self._ensure_root_visible_normal)
        self.root.after(150, self._complete_startup_initialization)

    # ------------------------------------------------
    # Storage helpers
    # ------------------------------------------------

    def _ensure_root_visible_normal(self):
        try:
            self.root.deiconify()
        except Exception:
            pass
        try:
            self.root.state("normal")
        except Exception:
            pass
        try:
            self.root.lift()
        except Exception:
            pass

    def _apply_initial_root_geometry(self):
        try:
            screen_w = int(self.root.winfo_screenwidth() or 1366)
            screen_h = int(self.root.winfo_screenheight() or 768)
        except Exception:
            screen_w = 1366
            screen_h = 768

        width = min(max(1180, int(screen_w * 0.82)), max(1000, screen_w - 80))
        height = min(max(820, int(screen_h * 0.80)), max(700, screen_h - 120))
        pos_x = max(0, (screen_w - width) // 2)
        pos_y = max(0, (screen_h - height) // 2)

        try:
            self.root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        except Exception:
            pass
        try:
            self.root.minsize(980, 620)
        except Exception:
            pass

    def _start_background_loops_once(self):
        if self._background_loops_started:
            return
        self._background_loops_started = True
        threading.Thread(target=self._monitor_js8call_incoming_messages, daemon=True).start()
        threading.Thread(target=self.monitor_directed, daemon=True).start()
        self.root.after(3000, self._sync_frequency_from_js8call_tick)
        self.root.after(1000, self._mesh_scheduler_tick)
        self.root.after(1000, self._find_search_tick)
        self.root.after(30000, self._last_success_tick)
        self.root.after(30000, self._relay_ack_tick)
        self.root.after(30000, self._topology_tick)

    def _complete_startup_initialization(self):
        if self._startup_completed:
            return
        self._startup_completed = True
        self._ensure_root_visible_normal()
        self._handle_startup_log_retention()
        self._handle_startup_raw_activity_retention()
        self.records = self._load_records_from_storage()
        self.processed = self._load_processed_lines_from_storage()
        self.record_keys = {self._record_key(r) for r in self.records}
        self._bootstrap_records_from_directed_if_needed()
        self._initialize_directed_tail()
        self._refresh_current_pathway_view()
        self.refresh_topology_window()
        self._start_background_loops_once()

    def _handle_startup_log_retention(self):
        if initialize_log_retention_schedule_if_needed():
            return
        if not should_prune_retained_logs():
            return
        should_prune = self._dark_choice_dialog(
            "Log Maintenance",
            "JS8MESH is doing maintenance. Entries older than 90 days will be deleted.",
            choices=[("OK", True), ("Later", False)],
            parent=self.root,
            refocus_widget=self.root,
        )
        if should_prune:
            prune_retained_logs()

    def _raw_activity_retention_groups(self):
        cutoff_dt = datetime.now() - timedelta(days=RAW_ACTIVITY_RETENTION_DAYS)
        groups = {
            "older_than_30_days": {
                "label": f"raw activity/JR records older than {RAW_ACTIVITY_RETENTION_DAYS} days",
                "default_name": f"untitled_raw_activity_older_than_{RAW_ACTIVITY_RETENTION_DAYS}_days.txt",
                "items": [],
            },
            "invalid_record_shape": {
                "label": "raw activity/JR records with invalid structure",
                "default_name": "untitled_raw_activity_invalid_structure.txt",
                "items": [],
            },
            "missing_datetime": {
                "label": "raw activity/JR records with missing datetime",
                "default_name": "untitled_raw_activity_missing_datetime.txt",
                "items": [],
            },
            "invalid_datetime": {
                "label": "raw activity/JR records with invalid datetime",
                "default_name": "untitled_raw_activity_invalid_datetime.txt",
                "items": [],
            },
        }

        for stored in list(snr_reports_db):
            if not isinstance(stored, dict):
                groups["invalid_record_shape"]["items"].append(stored)
                continue

            datetime_iso = str(stored.get("datetime_iso", "") or "").strip()
            if not datetime_iso:
                groups["missing_datetime"]["items"].append(stored)
                continue

            try:
                record_dt = datetime.fromisoformat(datetime_iso)
            except ValueError:
                groups["invalid_datetime"]["items"].append(stored)
                continue

            if record_dt < cutoff_dt:
                groups["older_than_30_days"]["items"].append(stored)

        ordered_keys = (
            "older_than_30_days",
            "invalid_record_shape",
            "missing_datetime",
            "invalid_datetime",
        )
        return [
            {
                "key": key,
                "label": groups[key]["label"],
                "default_name": groups[key]["default_name"],
                "items": list(groups[key]["items"]),
            }
            for key in ordered_keys
            if groups[key]["items"]
        ]

    def _current_calendar_month_key(self):
        return datetime.now().strftime("%Y-%m")

    def _save_prune_group_to_file(self, group):
        label = str(group.get("label", "raw activity/JR records")).strip()
        default_name = str(group.get("default_name", "untitled.txt")).strip() or "untitled.txt"
        path = filedialog.asksaveasfilename(
            title=f"Select a file to store {label}",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[
                ("Text files", "*.txt"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return False

        with open(path, "w", encoding="utf-8") as f:
            for item in list(group.get("items", [])):
                if isinstance(item, dict):
                    f.write(json.dumps(item, ensure_ascii=False))
                else:
                    f.write(str(item))
                f.write("\n")
        return True

    def _prune_old_raw_activity_records_on_startup(self, retention_groups=None):
        groups = list(retention_groups) if retention_groups is not None else self._raw_activity_retention_groups()
        if not groups:
            self._raw_activity_pruned_count = 0
            return 0

        prune_keys = set()
        pruned_count = 0
        for group in groups:
            for item in list(group.get("items", [])):
                prune_keys.add(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))
                pruned_count += 1

        kept = []
        for stored in list(snr_reports_db):
            storage_key = json.dumps(stored, sort_keys=True, ensure_ascii=False, default=str)
            if storage_key in prune_keys:
                continue
            kept.append(stored)

        self._raw_activity_pruned_count = pruned_count
        if len(kept) != len(snr_reports_db):
            snr_reports_db[:] = kept
            save_snr_reports(snr_reports_db)
        return pruned_count

    def _handle_startup_raw_activity_retention(self):
        retention_groups = self._raw_activity_retention_groups()
        pending_count = sum(len(group.get("items", [])) for group in retention_groups)
        self._raw_activity_pending_prune_count = pending_count
        if pending_count <= 0:
            self._raw_activity_pruned_count = 0
            return

        month_key = self._current_calendar_month_key()
        last_warned_month = str(settings.get("raw_activity_retention_warning_month", "") or "").strip()
        if last_warned_month != month_key:
            should_save = self._dark_choice_dialog(
                "Raw Activity Retention",
                (
                    "JS8Mesh will prune raw activity/JR records older than 30 days and any invalid raw activity records.\n\n"
                    f"Records to be removed now: {int(pending_count)}\n\n"
                    "Do you want to save the data that will be pruned first?"
                ),
                choices=[("Yes", True), ("No", False)],
                parent=self.root,
                refocus_widget=self.root,
            )
            if should_save:
                for group in retention_groups:
                    self._save_prune_group_to_file(group)
            settings["raw_activity_retention_warning_month"] = month_key
            save_settings(settings)

        self._prune_old_raw_activity_records_on_startup(retention_groups=retention_groups)

    def _load_records_from_storage(self):
        loaded = []

        for stored in snr_reports_db:
            record = self._record_from_storage(stored)
            if record:
                loaded.append(record)

        return self._dedup_records(loaded)

    def _initialize_directed_tail(self):
        try:
            if self.directed_file and os.path.exists(self.directed_file):
                stat_result = os.stat(self.directed_file)
                self._directed_last_mtime_ns = getattr(stat_result, "st_mtime_ns", None)
                self._directed_last_size = int(getattr(stat_result, "st_size", 0) or 0)
                with open(self.directed_file, encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    self._directed_tail_position = f.tell()
        except Exception:
            self._directed_tail_position = 0
            self._directed_last_mtime_ns = None
            self._directed_last_size = 0
        self._directed_partial_line = ""

    def _load_processed_lines_from_storage(self):
        processed = set()

        for stored in snr_reports_db:
            source_line = str(stored.get("source_line", "")).strip()
            if source_line:
                processed.add(source_line)

        return processed

    def _bootstrap_records_from_directed_if_needed(self):
        if not self.directed_file or not os.path.exists(self.directed_file):
            return

        added_count = 0
        try:
            with open(self.directed_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parsed = parse_directed_line(line)
                    if not parsed:
                        continue
                    classify_callsign(parsed["from"])
                    classify_callsign(parsed["to"])
                    if self._append_record(parsed, source_line=line, save_immediately=False):
                        added_count += 1
        except Exception:
            return

        if added_count > 0:
            self._sort_records_and_storage(save_immediately=False)
            save_snr_reports(snr_reports_db)

    def _on_root_close(self):
        try:
            if hasattr(self, "selected_frequency_var"):
                current_frequency = self._normalize_frequency_text(self.selected_frequency_var.get()) or self.selected_frequency_var.get().strip()
                if current_frequency:
                    settings["selected_frequency"] = current_frequency
            if hasattr(self, "user_call_var"):
                settings["user_callsign"] = self._active_callsign_for_frequency(self.selected_frequency_var.get())
            settings["directed_file"] = self.directed_file
            save_settings(settings)
        except Exception:
            pass
        self.root.destroy()

    def _record_from_storage(self, stored):
        if not isinstance(stored, dict):
            return None

        freq_text = self._normalize_frequency_text(stored.get("freq", ""))
        if freq_text is None:
            freq_text = str(stored.get("freq", "")).strip()

        record = {
            "date": stored.get("date", ""),
            "time": stored.get("time", ""),
            "from": stored.get("from", ""),
            "to": stored.get("to", ""),
            "msg": stored.get("msg", ""),
            "freq": freq_text,
            "snr": stored.get("snr", ""),
        }

        if "from_norm" in stored:
            record["from_norm"] = stored.get("from_norm", "")
        if "to_norm" in stored:
            record["to_norm"] = stored.get("to_norm", "")
        if "raw" in stored:
            record["raw"] = stored.get("raw", "")
        if "jr_sender_speed" in stored:
            record["jr_sender_speed"] = stored.get("jr_sender_speed", "")

        datetime_iso = stored.get("datetime_iso", "")

        if datetime_iso:
            try:
                record["datetime"] = datetime.fromisoformat(datetime_iso)
            except ValueError:
                record["datetime"] = None
        else:
            record["datetime"] = None

        return record

    def _record_to_storage(self, record, source_line=""):
        dt = record.get("datetime")
        freq_text = self._normalize_frequency_text(record.get("freq", ""))
        if freq_text is None:
            freq_text = str(record.get("freq", "")).strip()

        return {
            "date": record.get("date", ""),
            "time": record.get("time", ""),
            "from": record.get("from", ""),
            "to": record.get("to", ""),
            "from_norm": record.get("from_norm", ""),
            "to_norm": record.get("to_norm", ""),
            "msg": record.get("msg", ""),
            "freq": freq_text,
            "snr": record.get("snr", ""),
            "raw": record.get("raw", ""),
            "jr_sender_speed": record.get("jr_sender_speed", ""),
            "datetime_iso": dt.isoformat() if dt else "",
            "source_line": source_line,
        }

    def _record_key(self, record):
        dt = record.get("datetime")
        dt_iso = dt.isoformat() if dt else ""

        return (
            dt_iso,
            str(record.get("from", "")).strip().upper(),
            str(record.get("to", "")).strip().upper(),
            str(record.get("msg", "")).strip(),
            self._normalize_frequency_text(record.get("freq", "")) or str(record.get("freq", "")).strip(),
            str(record.get("snr", "")).strip(),
        )

    def _dedup_records(self, records):
        seen = set()
        out = []

        for record in records:
            key = self._record_key(record)
            if key in seen:
                continue
            seen.add(key)
            out.append(record)

        return out

    def _record_sort_key(self, record):
        dt = record.get("datetime")
        if dt is not None:
            return (
                dt,
                str(record.get("from", "") or ""),
                str(record.get("to", "") or ""),
                str(record.get("msg", "") or ""),
                str(record.get("freq", "") or ""),
                str(record.get("snr", "") or ""),
            )
        return (
            datetime.min,
            str(record.get("date", "") or ""),
            str(record.get("time", "") or ""),
            str(record.get("from", "") or ""),
            str(record.get("to", "") or ""),
            str(record.get("msg", "") or ""),
        )

    def _sort_records_and_storage(self, save_immediately=False):
        self.records = sorted(list(self.records or []), key=self._record_sort_key)
        self.record_keys = {self._record_key(r) for r in self.records}
        snr_reports_db[:] = [
            self._record_to_storage(record, source_line=str(record.get("source_line", "") or ""))
            for record in self.records
        ]
        if save_immediately:
            save_snr_reports(snr_reports_db)

    def _jr_speed_cache_key(self, sender, destination, payload, freq_text):
        return (
            normalize_callsign(sender),
            str(destination or "").strip().upper(),
            " ".join(str(payload or "").strip().split()),
            self._normalize_frequency_text(freq_text) or str(freq_text or "").strip(),
        )

    def _cache_incoming_jr_speed(self, sender, destination, payload, freq_text, speed_name, timestamp=None):
        sender = normalize_callsign(sender)
        destination = str(destination or "").strip().upper()
        payload = " ".join(str(payload or "").strip().split())
        speed_name = normalize_incoming_speed_name(speed_name)
        if not sender or not destination or not payload or not speed_name:
            return
        if not parse_mesh_report_entries(payload, fallback_source=sender):
            return

        if timestamp is None:
            timestamp = time.time()
        try:
            timestamp = float(timestamp)
        except Exception:
            timestamp = time.time()

        item = {
            "key": self._jr_speed_cache_key(sender, destination, payload, freq_text),
            "timestamp": timestamp,
            "speed": speed_name,
        }

        with self._jr_speed_cache_lock:
            cutoff = time.time() - 1800.0
            self._jr_speed_cache = [entry for entry in self._jr_speed_cache if float(entry.get("timestamp", 0.0) or 0.0) >= cutoff]
            self._jr_speed_cache.append(item)
            if len(self._jr_speed_cache) > 500:
                self._jr_speed_cache = self._jr_speed_cache[-500:]

    def _apply_live_jr_speed_to_record(self, record):
        if not isinstance(record, dict):
            return
        if str(record.get("jr_sender_speed", "")).strip():
            return

        sender = str(record.get("from", "")).strip().upper()
        destination = str(record.get("to", "")).strip().upper()
        payload = " ".join(str(record.get("msg", "")).strip().split())
        if not sender or not destination or not payload:
            return
        if not parse_mesh_report_entries(payload, fallback_source=sender):
            return

        dt_value = record.get("datetime")
        if dt_value is not None:
            try:
                target_ts = float(dt_value.timestamp())
            except Exception:
                target_ts = time.time()
        else:
            target_ts = time.time()

        key = self._jr_speed_cache_key(sender, destination, payload, record.get("freq", ""))

        best_match = None
        best_delta = None
        with self._jr_speed_cache_lock:
            cutoff = time.time() - 1800.0
            self._jr_speed_cache = [entry for entry in self._jr_speed_cache if float(entry.get("timestamp", 0.0) or 0.0) >= cutoff]
            for item in self._jr_speed_cache:
                if item.get("key") != key:
                    continue
                delta = abs(float(item.get("timestamp", target_ts) or target_ts) - target_ts)
                if delta > 180.0:
                    continue
                if best_match is None or delta < best_delta:
                    best_match = item
                    best_delta = delta

        if best_match is not None:
            record["jr_sender_speed"] = str(best_match.get("speed", "") or "").strip().upper()

    def _monitor_js8call_incoming_messages(self):
        while True:
            try:
                with self._create_js8call_bridge() as bridge:
                    while True:
                        msg = bridge.read_message(timeout=10.0)
                        if msg is None:
                            continue
                        if str(getattr(msg, "type", "") or "").strip().upper() != "RX.DIRECTED":
                            continue

                        sender = getattr(msg, "origin", None) or getattr(msg, "from", None) or ""
                        destination = getattr(msg, "destination", None) or getattr(msg, "to", None) or ""
                        payload = getattr(msg, "text", None) or getattr(msg, "value", None) or ""
                        freq_text = getattr(msg, "dial", None) or getattr(msg, "freq", None) or ""
                        speed_name = normalize_incoming_speed_name(getattr(msg, "speed", None))
                        timestamp = getattr(msg, "timestamp", None) or time.time()
                        self._cache_incoming_jr_speed(sender, destination, payload, freq_text, speed_name, timestamp=timestamp)
            except Exception:
                time.sleep(5)

    def _append_record(self, record, source_line="", save_immediately=True):
        normalized_record = dict(record)
        self._apply_live_jr_speed_to_record(normalized_record)

        freq_text = self._normalize_frequency_text(normalized_record.get("freq", ""))
        if freq_text is not None:
            normalized_record["freq"] = freq_text

        record_key = self._record_key(normalized_record)

        if record_key in self.record_keys:
            if source_line:
                self.processed.add(source_line)
            return False

        self.records.append(normalized_record)
        self.record_keys.add(record_key)

        if source_line:
            self.processed.add(source_line)

        snr_reports_db.append(
            self._record_to_storage(normalized_record, source_line=source_line)
        )

        if save_immediately:
            save_snr_reports(snr_reports_db)

        return True

    # ------------------------------------------------
    # Utility helpers
    # ------------------------------------------------

    def create_menu_bar(self):
        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="Find DIRECTED.txt", command=self.select_file)
        settings_menu.add_command(label="Callsign", command=self.show_callsign_dialog)
        settings_menu.add_command(label="Report TX Time Limit", command=self.show_jr_tx_time_limit_dialog)
        settings_menu.add_command(label="TX Mesh Reports", command=self.show_tx_mesh_reports_settings_dialog)
        settings_menu.add_command(label="Add / Remove Frequency", command=self.show_frequency_management_dialog)
        settings_menu.add_command(label="API Settings", command=self.show_api_settings_dialog)
        settings_menu.add_command(label="JS8Call Control", command=self.show_js8call_control_dialog)
        settings_menu.add_checkbutton(
            label="Sync Frequency from JS8Call",
            variable=self.sync_frequency_from_js8call_var,
            command=self._on_sync_frequency_from_js8call_toggled,
        )
        settings_menu.add_command(label="Watch Callsigns", command=self.show_watch_callsigns_dialog)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        mesh_reports_menu = tk.Menu(menu_bar, tearoff=0)
        mesh_reports_menu.add_command(label="TX Mesh Reports", command=self.show_mesh_network_window)
        mesh_reports_menu.add_command(label="Request Report", command=self.show_request_jr_window)
        menu_bar.add_cascade(label="Mesh Reports", menu=mesh_reports_menu)

        loggers_menu = tk.Menu(menu_bar, tearoff=0)
        loggers_menu.add_command(label="TX Mesh Reports Log", command=self.show_tx_mesh_reports_log_window)
        loggers_menu.add_command(label="Requested Report Responds Log", command=self.show_auto_responder_log_window)
        loggers_menu.add_command(label="HR Log", command=self.show_hr_log_window)
        loggers_menu.add_command(label="My Find Searches", command=self.show_my_find_searches_window)
        loggers_menu.add_command(label="Held Find Searches", command=self.show_held_find_searches_window)
        loggers_menu.add_command(label="Past Relays Log", command=self.show_past_relays_window)
        menu_bar.add_cascade(label="Loggers", menu=loggers_menu)

        self.root.config(menu=menu_bar)

    def _current_max_age_minutes(self):
        if self.ignore_freshness_var.get():
            return 999999
        return 65

    def _ensure_callsign_settings(self):
        legacy_user = str(settings.get("user_callsign", "18SV8110")).strip().upper()
        amateur_callsign = str(settings.get("amateur_callsign", "")).strip().upper()
        special_callsign = str(settings.get("special_callsign", "")).strip().upper()
        special_callsign_frequency = self._normalize_frequency_text(
            settings.get("special_callsign_frequency", "")
        ) or ""
        special_callsign_enabled = bool(settings.get("special_callsign_enabled", False))

        settings["amateur_callsign"] = amateur_callsign
        settings["special_callsign"] = special_callsign
        settings["special_callsign_frequency"] = special_callsign_frequency
        settings["special_callsign_enabled"] = special_callsign_enabled
        startup_frequency = self._normalize_frequency_text(settings.get("startup_frequency", "")) or "7.078 MHz"
        settings["startup_frequency"] = startup_frequency
        settings["selected_frequency"] = self._normalize_frequency_text(settings.get("selected_frequency", startup_frequency)) or startup_frequency
        settings["user_callsign"] = self._active_callsign_for_frequency(self.selected_frequency_var.get())

    def _special_callsign_matches_frequency(self, frequency_text):
        if not bool(settings.get("special_callsign_enabled", False)):
            return False
        target_frequency = self._normalize_frequency_text(settings.get("special_callsign_frequency", "")) or ""
        current_frequency = self._normalize_frequency_text(frequency_text) or str(frequency_text or "").strip()
        return bool(target_frequency and current_frequency and target_frequency == current_frequency)

    def _active_callsign_for_frequency(self, frequency_text):
        amateur_callsign = str(settings.get("amateur_callsign", "")).strip().upper()
        special_callsign = str(settings.get("special_callsign", "")).strip().upper()
        if self._special_callsign_matches_frequency(frequency_text) and special_callsign:
            return special_callsign
        return amateur_callsign

    def _sync_user_callsign_for_frequency(self, save=False):
        active_callsign = self._active_callsign_for_frequency(self.selected_frequency_var.get())
        settings["user_callsign"] = active_callsign

        if hasattr(self, "user_call_var"):
            self.user_call_var.set(active_callsign)

        if save:
            save_settings(settings)

        return active_callsign

    def _update_sync_frequency_from_js8call_indicator(self):
        widget = getattr(self, "sync_frequency_check", None)
        if widget is None:
            return
        enabled = bool(self.sync_frequency_from_js8call_var.get())
        color = "#66ff66" if enabled else "#ff6666"
        try:
            widget.configure(fg=color, activeforeground=color)
        except Exception:
            pass

    def _on_sync_frequency_from_js8call_toggled(self):
        enabled = bool(self.sync_frequency_from_js8call_var.get())
        settings["sync_frequency_from_js8call"] = enabled
        save_settings(settings)
        self._update_sync_frequency_from_js8call_indicator()
        if enabled:
            self._sync_frequency_from_js8call_once()

    def _sync_frequency_from_js8call_once(self):
        if not bool(self.sync_frequency_from_js8call_var.get()):
            return False
        try:
            with self._new_js8call_bridge() as bridge:
                dial_text = bridge.get_dial_frequency()
        except Exception:
            return False

        normalized = self._normalize_frequency_text(dial_text)
        if not normalized:
            return False

        current = self._normalize_frequency_text(self.selected_frequency_var.get()) or str(self.selected_frequency_var.get() or "").strip()
        if current == normalized:
            return False

        existing_option = self._find_existing_frequency_option(normalized)
        display_frequency = existing_option or normalized
        if existing_option is None:
            self._add_custom_frequency_option(normalized)
        self.selected_frequency_var.set(display_frequency)
        settings["selected_frequency"] = display_frequency
        self._sync_user_callsign_for_frequency(save=False)
        save_settings(settings)
        self._warn_missing_callsign_for_frequency(display_frequency)
        try:
            self.refresh_topology_window()
        except Exception:
            pass
        return True

    def _callsign_requirement_key_for_frequency(self, frequency_text):
        if self._special_callsign_matches_frequency(frequency_text):
            normalized = self._normalize_frequency_text(frequency_text) or str(frequency_text or "").strip()
            return f"special:{normalized}"
        return "amateur"

    def _warn_missing_callsign_for_frequency(self, frequency_text):
        requirement_key = self._callsign_requirement_key_for_frequency(frequency_text)
        active_callsign = self._active_callsign_for_frequency(frequency_text)
        if active_callsign:
            self._last_callsign_warning_key = None
            return
        if self._last_callsign_warning_key == requirement_key:
            return
        self._last_callsign_warning_key = requirement_key
        frequency_label = str(frequency_text or "").strip() or "this frequency"
        if requirement_key.startswith("special:"):
            message = (
                f"{frequency_label} is configured to use your special callsign, but no special callsign is saved yet.\n\n"
                "Please open Settings > Callsign and fill in your special callsign."
            )
        else:
            message = (
                f"{frequency_label} has no Amateur Radio callsign configured yet.\n\n"
                "Please open Settings > Callsign and fill in your Amateur Radio callsign."
            )
        self._dark_info_dialog("Missing Callsign", message)

    def _current_mesh_activity_minutes(self):
        return 60

    def _current_mesh_core_threshold(self):
        return 3

    def _minutes_ago(self, record):
        dt = record.get("datetime")

        if dt is None:
            return 0

        minutes = int((self._record_now() - dt).total_seconds() / 60.0)

        if minutes < 0:
            minutes = 0

        return minutes

    def _safe_positive_int(self, value, fallback, minimum=1):
        try:
            parsed = int(str(value).strip())
        except (ValueError, TypeError):
            return fallback

        if parsed < minimum:
            return minimum
        return parsed

    def _safe_float(self, value):
        try:
            return float(str(value).strip())
        except (ValueError, TypeError):
            return None

    def _frequency_to_mhz(self, text):
        raw = str(text).strip().upper()
        if not raw:
            return None

        raw = raw.replace(",", ".")

        has_mhz = "MHZ" in raw
        has_khz = "KHZ" in raw
        has_hz = "HZ" in raw and not has_mhz and not has_khz

        raw = raw.replace("MHZ", "").replace("KHZ", "").replace("HZ", "").strip()

        try:
            value = float(raw)
        except ValueError:
            return None

        if value <= 0:
            return None

        if has_mhz:
            return value
        if has_khz:
            return value / 1000.0
        if has_hz:
            return value / 1000000.0

        if value >= 1000000:
            return value / 1000000.0
        if value >= 1000:
            return value / 1000.0
        return value

    def _frequency_matches(self, freq_a, freq_b, tolerance_mhz=0.0005):
        a = self._frequency_to_mhz(freq_a)
        b = self._frequency_to_mhz(freq_b)

        if a is None or b is None:
            return False

        return abs(a - b) <= tolerance_mhz

    def _records_for_selected_frequency(self):
        selected = self.selected_frequency_var.get().strip()
        if not selected:
            base_records = list(self.records)
        else:
            base_records = []
            for record in self.records:
                if self._frequency_matches(record.get("freq", ""), selected):
                    base_records.append(record)

        if not self.ignore_freshness_var.get():
            return base_records

        test_limit = self._safe_positive_int(
            self.test_mode_recent_records_var.get(),
            fallback=0,
            minimum=0
        )

        if test_limit <= 0:
            return base_records

        return base_records[-test_limit:]

    def _current_topology_mode(self):
        value = str(self.topology_mode_var.get()).strip().lower()
        if value not in ("traffic", "mesh"):
            return "traffic"
        return value

    def _parse_mesh_times_text(self, text):
        raw = str(text or "").strip()
        if not raw:
            return "", []

        valid = []
        invalid = []
        seen = set()

        for part in raw.split(","):
            original = str(part).strip()
            if not original:
                continue

            try:
                hh_str, mm_str = original.split(":")
                hh = int(hh_str)
                mm = int(mm_str)
            except (ValueError, TypeError):
                invalid.append(original)
                continue

            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                invalid.append(original)
                continue

            normalized = f"{hh:02d}:{mm:02d}"
            if normalized not in seen:
                seen.add(normalized)
                valid.append(normalized)

        valid.sort()
        normalized_text = ", ".join(valid)
        return normalized_text, invalid

    def _normalize_mesh_times_text(self, text):
        normalized_text, _invalid = self._parse_mesh_times_text(text)
        return normalized_text

    def _mesh_broadcast_slots(self):
        text = settings.get("mesh_broadcast_times_24h", "")
        normalized = self._normalize_mesh_times_text(text)
        if not normalized:
            return []
        return [part.strip() for part in normalized.split(",") if part.strip()]

    def _mesh_slot_minutes_list(self, raw_text=None):
        if raw_text is None:
            slots = self._mesh_broadcast_slots()
        else:
            normalized, _invalid = self._parse_mesh_times_text(raw_text)
            slots = [part.strip() for part in normalized.split(",") if part.strip()]
        minutes_list = []
        for slot in slots:
            try:
                hh_text, mm_text = slot.split(":")
                minutes_list.append(int(hh_text) * 60 + int(mm_text))
            except Exception:
                continue
        return sorted(set(minutes_list))

    def _mesh_derived_lookback_minutes(self, raw_times_text=None, broadcast_interval=None, now_dt=None, for_slot_hhmm=None):
        try:
            interval_value = int(broadcast_interval)
        except Exception:
            interval_value = self._safe_positive_int(settings.get("mesh_broadcast_interval_minutes", 20), 20, minimum=1)
        interval_value = max(1, interval_value)

        slot_minutes = self._mesh_slot_minutes_list(raw_times_text)
        if not slot_minutes:
            return min(40, interval_value)
        gaps = []
        for index, slot_min in enumerate(slot_minutes):
            next_slot = slot_minutes[(index + 1) % len(slot_minutes)]
            gap = next_slot - slot_min
            if gap <= 0:
                gap += 24 * 60
            gaps.append(gap)
        smallest_gap = min(gaps) if gaps else interval_value
        return min(40, max(1, smallest_gap))

    def _current_mesh_lookback_minutes(self, for_slot_hhmm=None):
        if self.ignore_freshness_var.get():
            value = self._safe_positive_int(
                self.mesh_network_window.get_lookback_minutes_text() if getattr(self, "mesh_network_window", None) else settings.get("mesh_lookback_minutes", 10),
                settings.get("mesh_lookback_minutes", 10),
                minimum=1,
            )
            return value

        raw_times = self.mesh_network_window.get_broadcast_times_24h_text() if getattr(self, "mesh_network_window", None) else settings.get("mesh_broadcast_times_24h", "")
        interval = self._safe_positive_int(
                self.mesh_network_window.get_broadcast_interval_text() if getattr(self, "mesh_network_window", None) else settings.get("mesh_broadcast_interval_minutes", 20),
            settings.get("mesh_broadcast_interval_minutes", 20),
            minimum=1,
        )
        return self._mesh_derived_lookback_minutes(raw_times_text=raw_times, broadcast_interval=interval, for_slot_hhmm=for_slot_hhmm)

    def _sync_mesh_lookback_controls(self):
        window = getattr(self, "mesh_network_window", None)
        if window is None:
            return
        editable = bool(self.ignore_freshness_var.get())
        window.set_lookback_editable(editable)
        if not editable:
            if self._mesh_broadcast_slots():
                window.set_lookback_minutes_text("TIMES")
            else:
                window.set_lookback_minutes_text(self._current_mesh_lookback_minutes())

    # ------------------------------------------------
    def _watched_callsigns_matching_record(self, record):
        watched = set(self._normalized_watched_callsigns())
        if not watched:
            return []

        matched = set()
        from_call = normalize_callsign(record.get("from", ""))
        to_call = normalize_callsign(record.get("to", ""))
        if from_call in watched:
            matched.add(from_call)
        if to_call in watched:
            matched.add(to_call)

        msg_tokens = [normalize_callsign(token) for token in re.findall(r"[A-Z0-9/]+", str(record.get("msg", "")).upper())]
        for token in msg_tokens:
            if token in watched:
                matched.add(token)

        return sorted(item for item in matched if item)

    def _show_watched_callsign_alert(self, record, matched_callsigns):
        matched_callsigns = [str(item).strip().upper() for item in list(matched_callsigns or []) if str(item).strip()]
        if not matched_callsigns:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Watched Callsign Seen")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)

        outer = tk.Frame(dialog, bg=self.bg_color, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Watched Callsign Seen",
            bg=self.bg_color,
            fg="#66ff66",
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")

        lines = [
            f"Matched: {', '.join(matched_callsigns)}",
            f"From: {str(record.get('from', '')).strip()}",
            f"To: {str(record.get('to', '')).strip()}",
            f"Frequency: {str(record.get('freq', '')).strip()}",
            f"Message: {str(record.get('msg', '')).strip()}",
        ]

        tk.Label(
            outer,
            text="\n".join(lines),
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            wraplength=460,
        ).pack(anchor="w", pady=(10, 0))

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def close_dialog():
            try:
                dialog.destroy()
            finally:
                self._watched_callsign_alerts = [item for item in self._watched_callsign_alerts if item is not dialog]

        tk.Button(button_row, text="Close", command=close_dialog, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left")

        self._watched_callsign_alerts = [item for item in self._watched_callsign_alerts if item.winfo_exists()]
        offset = len(self._watched_callsign_alerts) * 28
        self._watched_callsign_alerts.append(dialog)

        dialog.update_idletasks()
        width = dialog.winfo_width() or 520
        height = dialog.winfo_height() or 180
        screen_w = dialog.winfo_screenwidth()
        x = max(20, screen_w - width - 30)
        y = 80 + offset
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        try:
            dialog.attributes("-topmost", True)
            dialog.after(3000, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
        except Exception:
            pass
        try:
            dialog.lift()
            dialog.focus_force()
            self.root.bell()
        except Exception:
            pass
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    # ------------------------------------------------
    # Treeview style
    # ------------------------------------------------

    def create_treeview_style(self):
        style = ttk.Style()
        style.theme_use("default")

        style.configure(
            "Treeview",
            background=self.bg_color,
            foreground=self.fg_color,
            fieldbackground=self.bg_color,
            rowheight=22,
        )

        style.map(
            "Treeview",
            background=[("selected", "#555555")],
            foreground=[("selected", "#ffffff")],
        )

    # ------------------------------------------------
    # Main GUI creation
    # ------------------------------------------------

    def create_widgets(self):
        top = tk.Frame(self.root, bg=self.bg_color)
        top.pack(fill="x", padx=5, pady=5)

        tk.Label(top, text="User Callsign:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0)

        self.user_call_var = tk.StringVar(value=self._active_callsign_for_frequency(self.selected_frequency_var.get()))

        self.user_entry = tk.Entry(top, textvariable=self.user_call_var, width=12, state="readonly")
        self.user_entry.grid(row=0, column=1)

        tk.Label(top, text="Target Callsign:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=2)

        self.target_call_var = tk.StringVar()

        self.target_entry = tk.Entry(top, textvariable=self.target_call_var, width=14)
        self.target_entry.grid(row=0, column=3)
        self.target_entry.bind("<Return>", self._enter_pressed)

        tk.Button(
            top,
            text="Linear Pathways",
            command=self.show_pathways,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=4, padx=5)

        tk.Button(
            top,
            text="Inbound Reachability",
            command=self.show_inbound_reachability_pathways,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=5, padx=5)

        tk.Button(
            top,
            text="RX Monitor",
            command=self.show_js8call_rx_monitor_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=6, padx=5)

        tk.Button(
            top,
            text="Relay Profiles",
            command=self.show_relay_profiles_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=7, padx=5)

        tk.Button(
            top,
            text="Topology",
            command=self.show_topology_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=8, padx=5)

        tk.Button(
            top,
            text="Activity",
            command=self.show_activity_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=9, padx=5)

        top.grid_columnconfigure(10, weight=1)

        tk.Button(
            top,
            text="About",
            command=self.show_about,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=11, padx=5, sticky="e")

        tk.Label(top, text="Min SNR:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0)

        self.min_snr_var = tk.IntVar(value=settings.get("min_snr", -15))
        tk.Entry(top, textvariable=self.min_snr_var, width=6).grid(row=1, column=1)

        tk.Label(top, text="Max Relays:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=2)

        self.max_hops_var = tk.IntVar(value=settings.get("max_hops", 3))
        tk.Entry(top, textvariable=self.max_hops_var, width=6).grid(row=1, column=3)

        tk.Button(
            top,
            text="Save Settings",
            command=self.save_settings,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=1, column=4, padx=5)

        self.ignore_freshness_var = tk.BooleanVar(value=False)

        tk.Checkbutton(
            top,
            text="Ignore Freshness (Test Mode)",
            variable=self.ignore_freshness_var,
            command=self._on_ignore_freshness_toggled,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color
        ).grid(row=1, column=5, sticky="w")

        self.test_mode_recent_records_var = tk.StringVar(
            value=str(settings.get("test_mode_recent_records_limit", "1000"))
        )

        tk.Label(
            top,
            text="Test Mode recent records:",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=1, column=6, sticky="e", padx=(12, 6))

        self.test_mode_recent_records_entry = tk.Entry(
            top,
            textvariable=self.test_mode_recent_records_var,
            width=10
        )
        self.test_mode_recent_records_entry.grid(row=1, column=7, sticky="w")

        tk.Label(
            top,
            text="0 = all",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=1, column=8, sticky="w")

        self.main_pane = tk.PanedWindow(
            self.root,
            orient=tk.VERTICAL,
            bg=self.bg_color,
            sashwidth=8,
            sashrelief=tk.RAISED
        )
        self.main_pane.pack(fill="both", expand=True, padx=5, pady=5)

        self.pathways_panel = PathwaysPanel(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            selected_frequency_var=self.selected_frequency_var,
            frequency_options=self.frequency_options,
            frequency_changed_callback=self._on_frequency_changed,
            frequency_focus_out_callback=self._on_frequency_focus_out,
            frequency_enter_callback=self._on_frequency_enter,
            pathway_selected_callback=self._on_pathway_selected,
            pathway_search_changed_callback=self._on_pathway_search_changed,
            pathway_search_prev_callback=self._select_previous_filtered_pathway,
            pathway_search_next_callback=self._select_next_filtered_pathway,
            copy_selection_callback=self._copy_treeview_selection,
            explain_selected_pathway_callback=self.show_selected_pathway_evidence,
            mark_success_callback=lambda: self.mark_relay("S"),
            mark_failure_callback=lambda: self.mark_relay("F"),
            current_view_title_var=self.current_view_title_var,
        )

        pathways_frame = self.pathways_panel.build_ui(self.main_pane)
        self.pathways_tree = self.pathways_panel.tree
        self.frequency_combo = self.pathways_panel.frequency_combo

        self.main_pane.add(pathways_frame, minsize=180)

        message_frame = tk.LabelFrame(
            self.main_pane,
            text="Relay Message Builder",
            bg=self.bg_color,
            fg=self.fg_color
        )

        message_inner = tk.Frame(message_frame, bg=self.bg_color)
        message_inner.pack(fill="both", expand=True, padx=5, pady=5)

        self.relay_builder = RelayMessageBuilder(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            get_selected_pathway_callback=self._get_selected_pathway,
            send_to_js8call_callback=self.send_prepared_relay_to_js8call,
            show_past_relays_callback=self.show_past_relays_window,
            mark_success_callback=lambda: self.mark_relay("S"),
            mark_failure_callback=lambda: self.mark_relay("F"),
            initial_tx_mode=settings.get("relay_tx_mode", "DEFAULT"),
        )
        self.relay_builder.build_ui(message_inner)


        self.main_pane.add(message_frame, minsize=180)
        self._sync_user_callsign_for_frequency(save=False)
        self.root.after(200, lambda: self._warn_missing_callsign_for_frequency(self.selected_frequency_var.get()))

    # ------------------------------------------------
    # Frequency helpers
    # ------------------------------------------------

    def _normalize_frequency_text(self, text):
        mhz_value = self._frequency_to_mhz(text)
        if mhz_value is None:
            return None

        normalized = f"{mhz_value:.6f}".rstrip("0").rstrip(".")
        return f"{normalized} MHz"

    def _sorted_unique_frequency_options(self, values):
        unique = []
        for item in values:
            normalized = self._normalize_frequency_text(item)
            if normalized and not self._find_existing_frequency_option(normalized, unique):
                unique.append(normalized)
        unique.sort(key=lambda value: self._frequency_to_mhz(value) or 0.0)
        return unique

    def _find_existing_frequency_option(self, frequency_text, options=None):
        normalized = self._normalize_frequency_text(frequency_text)
        if normalized is None:
            return None

        for option in list(options if options is not None else self.frequency_options):
            if self._frequency_matches(option, normalized):
                return option
        return None

    def _add_custom_frequency_option(self, normalized_frequency):
        freq = str(normalized_frequency or "").strip()
        if not freq:
            return "invalid"

        custom = []
        for item in settings.get("custom_frequency_options", []):
            norm = self._normalize_frequency_text(item)
            if norm and not self._find_existing_frequency_option(norm, self.default_frequency_options) and not self._find_existing_frequency_option(norm, custom):
                custom.append(norm)

        if self._find_existing_frequency_option(freq, self.default_frequency_options):
            self.frequency_options = self._sorted_unique_frequency_options(list(self.default_frequency_options) + custom)
            self._refresh_frequency_option_widgets()
            return "exists"

        if self._find_existing_frequency_option(freq, custom):
            self.frequency_options = self._sorted_unique_frequency_options(list(self.default_frequency_options) + custom)
            self._refresh_frequency_option_widgets()
            return "exists"

        custom.append(freq)
        custom = self._sorted_unique_frequency_options(custom)
        settings["custom_frequency_options"] = custom
        self.frequency_options = self._sorted_unique_frequency_options(list(self.default_frequency_options) + custom)
        self._refresh_frequency_option_widgets()
        return "added"

    def _apply_frequency_value(self, show_error=False, show_duplicate_warning=False):
        raw_frequency_text = self.selected_frequency_var.get()
        normalized = self._normalize_frequency_text(raw_frequency_text)
        if normalized is None:
            if show_error:
                self._dark_info_dialog(
                    "Invalid Frequency",
                    "Enter a valid frequency.\n\nExamples:\n7.078\n14.078\n27.245\n27.245000\n7078000\n27245\n27245000"
                )
            return False

        existing_option = self._find_existing_frequency_option(normalized)
        display_frequency = existing_option or normalized
        add_result = self._add_custom_frequency_option(normalized) if existing_option is None else "exists"
        self.selected_frequency_var.set(display_frequency)
        settings["selected_frequency"] = display_frequency
        self._sync_user_callsign_for_frequency(save=False)
        save_settings(settings)
        self._warn_missing_callsign_for_frequency(display_frequency)

        if show_duplicate_warning and existing_option and str(raw_frequency_text).strip() != existing_option:
            self._dark_info_dialog(
                "Frequency Already Exists",
                f"{existing_option} is already available in the frequency menu."
            )
        return True

    def _on_frequency_changed(self, event=None):
        self._apply_frequency_value(show_error=False, show_duplicate_warning=False)
        self.refresh_topology_window()

    def _on_frequency_focus_out(self, event=None):
        self._apply_frequency_value(show_error=False, show_duplicate_warning=False)
        self.refresh_topology_window()

    def _on_frequency_enter(self, event=None):
        self._apply_frequency_value(show_error=True, show_duplicate_warning=True)
        self.refresh_topology_window()

    def _on_pathway_search_changed(self, event=None):
        self._render_pathway_rows()

    def _select_relative_filtered_pathway(self, direction):
        tree = getattr(self, "pathways_tree", None)
        if tree is None:
            return
        children = list(tree.get_children(""))
        if not children:
            return
        selection = list(tree.selection())
        if selection and selection[0] in children:
            current_index = children.index(selection[0])
        else:
            current_index = 0 if direction >= 0 else len(children) - 1
        next_index = max(0, min(len(children) - 1, current_index + direction))
        item_id = children[next_index]
        tree.selection_set(item_id)
        tree.focus(item_id)
        tree.see(item_id)
        self._on_pathway_selected()

    def _select_previous_filtered_pathway(self):
        self._select_relative_filtered_pathway(-1)

    def _select_next_filtered_pathway(self):
        self._select_relative_filtered_pathway(1)

    def _on_ignore_freshness_toggled(self):
        self._sync_mesh_lookback_controls()
        self._update_requested_jr_lookback_help()
        self._update_requested_jr_preview()
        self._refresh_current_pathway_view()

    def _on_topology_mode_changed(self, event=None):
        settings["topology_mode"] = self._current_topology_mode()
        save_settings(settings)
        self.refresh_topology_window()

    def _on_topology_mode_changed_from_window(self, new_mode):
        mode = str(new_mode or "traffic").strip().lower()
        if mode not in ("traffic", "mesh"):
            mode = "traffic"

        self.topology_mode_var.set(mode)
        settings["topology_mode"] = mode
        save_settings(settings)
        self.refresh_topology_window()

    def _on_topology_wave_filter_changed_from_window(self, _wave_filter=None):
        self.refresh_topology_window()

    def _explain_topology_node(self, node_id, topology_mode):
        node_key = str(node_id or "").strip().upper()
        node = getattr(self, "_last_topology_nodes", {}).get(node_key)
        if not node:
            self._dark_info_dialog(
                "Node Details",
                "No details available for the selected node.",
                parent=self.topology_window.window if getattr(self, "topology_window", None) else None,
                refocus_widget=self.topology_window.window if getattr(self, "topology_window", None) else None,
            )
            return

        mode = str(topology_mode or "traffic").strip().lower()
        avg_snr = node.get("avg_snr")
        avg_snr_text = "" if avg_snr is None else f"{float(avg_snr):.1f}"
        latest = node.get("latest_minutes_ago")
        latest_text = "" if latest is None else f"{int(latest)}m"
        last_mesh = node.get("last_mesh_report_minutes_ago")
        last_mesh_text = "" if last_mesh is None else f"{int(last_mesh)}m"
        lines = [
            f"Node: {node.get('id', '')}",
        ]

        if mode == "mesh":
            lines.extend([
                f"Role: {node.get('mesh_role', 'observed_only')}",
                f"Wave: {node.get('wave_depth', '')}",
                f"Parent: {node.get('parent_node', '')}",
                f"Path: {node.get('path_text', '')}",
                f"Average SNR: {avg_snr_text}",
                f"Latest activity: {latest_text}",
                f"Neighbors: {int(node.get('neighbor_count', 0) or 0)}",
                f"JR total: {int(node.get('mesh_report_count_total', 0) or 0)}",
                f"JR recent: {int(node.get('mesh_report_count_recent', 0) or 0)}",
                f"Last JR TX: {last_mesh_text}",
            ])
        else:
            lines.extend([
                f"Traffic type: {node.get('traffic_type', '')}",
                f"Seen count: {int(node.get('seen_count', 0) or 0)}",
                f"Average SNR: {avg_snr_text}",
                f"Latest activity: {latest_text}",
                f"Neighbors: {int(node.get('neighbor_count', 0) or 0)}",
            ])

        self._dark_info_dialog(
            "Node Details",
            "\n".join(lines),
            parent=self.topology_window.window if getattr(self, "topology_window", None) else None,
            refocus_widget=self.topology_window.window if getattr(self, "topology_window", None) else None,
        )

    # ------------------------------------------------
    # Selection helpers
    # ------------------------------------------------

    def _enter_pressed(self, event):
        self.show_pathways()

    def _on_pathway_selected(self, event=None):
        rec = self._selected_pathway_recommendation()
        if not self._apply_retry_warning_if_needed(rec):
            return
        if rec is not None:
            self.relay_builder.set_default_tx_mode(rec.get("category", "NORMAL"))
        self.relay_builder.on_pathway_selected(event)
        if self.pathway_evidence_window is not None and self.pathway_evidence_window.has_window():
            self.show_selected_pathway_evidence()

    def _selected_pathway_recommendation(self):
        pathway = self._get_selected_pathway()
        if not pathway:
            return None
        return self.last_pathway_recommendations.get(pathway)

    def show_selected_pathway_evidence(self):
        rec = self._selected_pathway_recommendation()

        if rec is None:
            messagebox.showwarning(
                "No pathway selected",
                "Select a pathway first, then click Explain Selected Pathway."
            )
            return

        evidence_rows = rec.get("evidence", [])
        if not evidence_rows:
            evidence_rows = direct_path_evidence(
                self._records_for_selected_frequency(),
                self.user_call_var.get().strip().upper(),
                self.target_call_var.get().strip().upper(),
                max_age_minutes=self._current_max_age_minutes(),
            )

        summary = (
            f"Pathway: {rec.get('pathway', '')}   |   "
            f"Category: {rec.get('category', '')}   |   "
            f"Score: {rec.get('score', '')}   |   "
            f"Relays: {rec.get('relays', 0)}   |   "
            f"Freshness: {rec.get('freshness', '')}   |   "
            f"S/F: {rec.get('reliability', '')}   |   "
            f"Confidence: {self._confidence_display(rec)}"
        )

        if rec.get("origin"):
            summary += f"   |   Origin: {rec.get('origin', '')}"
        if rec.get("last_success_time"):
            summary += f"   |   Last Success: {rec.get('last_success_time', '')}"
        if rec.get("last_failure_time"):
            summary += f"   |   Last Failure: {rec.get('last_failure_time', '')}"

        self.pathway_evidence_window.update_pathway(
            rec.get("pathway", ""),
            evidence_rows,
            summary_text=summary,
        )

    def _get_selected_pathway(self):
        return self.pathways_panel.get_selected_pathway()

    def _pathway_row_matches_search(self, row):
        search_text = str(self.pathways_panel.get_search_text() or "").strip().upper()
        if not search_text:
            return True
        values = row.get("values", ())
        haystack = " ".join(str(value) for value in values).upper()
        return search_text in haystack

    def _render_pathway_rows(self):
        self.pathways_panel.clear_rows()
        visible_count = 0
        for row in self._current_pathway_rows:
            if not self._pathway_row_matches_search(row):
                continue
            self.pathways_panel.insert_row(values=row.get("values", ()), tag=row.get("tag"))
            visible_count += 1
        if visible_count > 0:
            self.pathways_panel.focus_first_row()
        return visible_count

    def _set_current_pathway_rows(self, rows):
        self._current_pathway_rows = list(rows or [])
        return self._render_pathway_rows()

    def _copy_treeview_selection(self, tree):
        if tree is None:
            return

        selection = tree.selection()
        if not selection:
            return

        lines = []

        for item_id in selection:
            item = tree.item(item_id)
            values = item.get("values", [])
            text_values = [str(value) for value in values]
            lines.append("\t".join(text_values))

        text = "\n".join(lines).strip()
        if not text:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _select_treeview_drag_range(self, tree, start_item, current_item):
        if tree is None or not start_item or not current_item:
            return

        children = list(tree.get_children(""))
        if not children:
            return

        try:
            start_index = children.index(start_item)
            current_index = children.index(current_item)
        except ValueError:
            return

        low = min(start_index, current_index)
        high = max(start_index, current_index)

        selected_items = children[low:high + 1]
        tree.selection_set(selected_items)
        tree.focus(current_item)

    def _past_relays_drag_select_start(self, event):
        if self.past_relays_tree is None:
            return
        row_id = self.past_relays_tree.identify_row(event.y)
        self._past_relays_drag_start = row_id if row_id else None

    def _past_relays_drag_select_motion(self, event):
        if self.past_relays_tree is None:
            return
        row_id = self.past_relays_tree.identify_row(event.y)
        if self._past_relays_drag_start and row_id:
            self._select_treeview_drag_range(self.past_relays_tree, self._past_relays_drag_start, row_id)

    def _show_past_relays_context_menu(self, event):
        if self.past_relays_tree is None:
            return

        row_id = self.past_relays_tree.identify_row(event.y)
        if row_id:
            if row_id not in self.past_relays_tree.selection():
                self.past_relays_tree.selection_set(row_id)
            self.past_relays_tree.focus(row_id)

        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color
        )
        menu.add_command(
            label="Copy Selected Row(s)",
            command=lambda: self._copy_treeview_selection(self.past_relays_tree)
        )
        menu.add_command(
            label="Select All",
            command=lambda: self.past_relays_tree.selection_set(self.past_relays_tree.get_children(""))
        )
        if row_id:
            menu.add_separator()
            menu.add_command(label="Set Result: S", command=lambda: self._set_past_relay_result(row_id, "S"))
            menu.add_command(label="Set Result: F", command=lambda: self._set_past_relay_result(row_id, "F"))
            menu.add_command(label="Set Result: P", command=lambda: self._set_past_relay_result(row_id, "P"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _rebuild_reliability_from_history(self):
        reliability_db.clear()
        for item in list(relay_history_db or []):
            result = str(item.get("result", "")).strip().upper()
            if result not in ("S", "F"):
                continue
            for relay in self._relay_nodes_from_pathway_text(item.get("pathway", "")):
                stats = dict(reliability_db.get(relay, {"S": 0, "F": 0}) or {})
                stats["S"] = int(stats.get("S", 0) or 0)
                stats["F"] = int(stats.get("F", 0) or 0)
                stats[result] += 1
                stats["success_count"] = stats["S"]
                stats["failure_count"] = stats["F"]
                stats["participation_count"] = stats["S"] + stats["F"]
                stats["last_result"] = result
                if result == "S":
                    stats["last_success"] = str(item.get("resolved_at", "")).strip() or str(item.get("ack_datetime", "")).strip() or str(item.get("timestamp", "")).strip()
                else:
                    stats["last_failure"] = str(item.get("resolved_at", "")).strip() or str(item.get("timestamp", "")).strip()
                stats["last_updated"] = str(item.get("resolved_at", "")).strip() or str(item.get("timestamp", "")).strip()
                reliability_db[relay] = stats

    def _set_past_relay_result(self, row_id, new_result):
        item = dict(self._past_relays_row_data.get(row_id, {}) or {})
        if not item:
            return

        new_result = str(new_result or "").strip().upper()
        if new_result not in ("S", "F", "P"):
            return

        event_id = str(item.get("event_id", "")).strip()
        history_index = self._find_relay_history_index_by_event_id(event_id) if event_id else None
        if history_index is None:
            target_timestamp = str(item.get("timestamp", "")).strip()
            target_pathway = str(item.get("pathway", "")).strip()
            for index, candidate in enumerate(list(relay_history_db or [])):
                if str(candidate.get("timestamp", "")).strip() == target_timestamp and str(candidate.get("pathway", "")).strip() == target_pathway:
                    history_index = index
                    event_id = str(candidate.get("event_id", "")).strip()
                    break
        if history_index is None:
            return

        updated = dict(relay_history_db[history_index] or {})
        updated["result"] = new_result
        now_text = self._now().isoformat(timespec="seconds")
        if new_result == "P":
            updated["pending_until"] = (self._now() + timedelta(minutes=ACK_RECOGNITION_WINDOW_MINUTES)).isoformat(timespec="seconds")
            updated["resolved_at"] = ""
            updated["ack_datetime"] = ""
            pending_item = {
                "event_id": event_id,
                "pathway": self._operational_path_from_inbound_display(updated.get("pathway", "")),
                "target": normalize_callsign(updated.get("ack_from_expected", "")),
                "expected_ack_chain": str(updated.get("expected_ack_chain", "")).strip().upper(),
                "tx_mode": str(updated.get("tx_mode", "")).strip(),
                "message_mode": str(updated.get("message_mode", "")).strip(),
                "message_text": str(updated.get("message_text", "")).strip(),
                "prepared_message": str(updated.get("prepared_message", "")).strip(),
                "created_at": str(updated.get("timestamp", "")).strip(),
                "expires_at": str(updated.get("pending_until", "")).strip(),
            }
            self._pending_ack_relays = [
                existing for existing in list(self._pending_ack_relays or [])
                if str(existing.get("event_id", "")).strip() != event_id
            ]
            self._pending_ack_relays.append(pending_item)
        else:
            updated["resolved_at"] = now_text
            self._pending_ack_relays = [
                existing for existing in list(self._pending_ack_relays or [])
                if str(existing.get("event_id", "")).strip() != event_id
            ]

        relay_history_db[history_index] = updated
        self._rebuild_reliability_from_history()
        save_relay_history(relay_history_db)
        save_reliability(reliability_db)
        self._refresh_current_pathway_view()
        self.update_past_relays_table()
        if getattr(self, "relay_profiles_window", None) is not None and self.relay_profiles_window.has_window():
            self.refresh_relay_profiles_window()

    # ------------------------------------------------
    # Dialog helpers
    # ------------------------------------------------

    def _dark_confirm_dialog(self, title, prompt_text):
        result = {"value": False}

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text=prompt_text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10)
        ).pack(anchor="w")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def do_ok():
            result["value"] = True
            dialog.destroy()

        def do_cancel():
            result["value"] = False
            dialog.destroy()

        tk.Button(
            button_row,
            text="OK",
            command=do_ok,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Cancel",
            command=do_cancel,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", do_cancel)
        dialog.update_idletasks()

        x = self.root.winfo_rootx() + 80
        y = self.root.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")

        self.root.wait_window(dialog)
        return result["value"]

    def _dark_choice_dialog(self, title, prompt_text, choices, parent=None, refocus_widget=None):
        result = {"value": None}
        parent_widget = parent if parent is not None else self.root

        try:
            if isinstance(parent_widget, (tk.Tk, tk.Toplevel)) and parent_widget.state() == "iconic":
                parent_widget.deiconify()
            if isinstance(self.root, tk.Tk) and self.root.state() == "iconic":
                self.root.deiconify()
            parent_widget.lift()
            self.root.lift()
        except Exception:
            pass

        dialog = tk.Toplevel(parent_widget)
        dialog.title(title)
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        try:
            if parent_widget.state() != "iconic":
                dialog.transient(parent_widget)
        except Exception:
            pass
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text=prompt_text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10)
        ).pack(anchor="w")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def choose(value):
            result["value"] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        for index, (label, value) in enumerate(choices):
            tk.Button(
                button_row,
                text=label,
                command=lambda v=value: choose(v),
                bg=self.highlight_color,
                fg=self.fg_color,
                width=max(12, len(label) + 2)
            ).pack(side="left", padx=(0, 8) if index < len(choices) - 1 else 0)

        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        dialog.update_idletasks()
        self._center_dialog_on_screen(dialog, parent_widget)
        dialog.deiconify()
        dialog.lift()
        try:
            dialog.attributes("-topmost", True)
            dialog.after(1000, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
        except Exception:
            pass
        try:
            dialog.focus_force()
            dialog.after(100, lambda: dialog.lift() if dialog.winfo_exists() else None)
            dialog.after(150, lambda: dialog.focus_force() if dialog.winfo_exists() else None)
        except Exception:
            pass

        self.root.wait_window(dialog)

        target = refocus_widget
        if target is not None:
            try:
                if target.winfo_exists():
                    if isinstance(target, (tk.Tk, tk.Toplevel)):
                        target.deiconify()
                        target.lift()
                    target.focus_force()
                    if hasattr(target, "selection_range"):
                        try:
                            target.selection_range(0, tk.END)
                        except Exception:
                            pass
            except Exception:
                pass

        return result["value"]

    def _dark_info_dialog(self, title, prompt_text, parent=None, refocus_widget=None):
        parent_widget = parent if parent is not None else self.root
        try:
            if isinstance(parent_widget, (tk.Tk, tk.Toplevel)) and parent_widget.state() == "iconic":
                parent_widget.deiconify()
            if isinstance(self.root, tk.Tk) and self.root.state() == "iconic":
                self.root.deiconify()
            parent_widget.lift()
            self.root.lift()
        except Exception:
            pass
        dialog = tk.Toplevel(parent_widget)
        dialog.title(title)
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        try:
            if parent_widget.state() != "iconic":
                dialog.transient(parent_widget)
        except Exception:
            pass
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text=prompt_text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10)
        ).pack(anchor="w")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        ok_button = tk.Button(
            button_row,
            text="OK",
            command=lambda: (
                dialog.grab_release() if dialog.winfo_exists() else None,
                dialog.destroy() if dialog.winfo_exists() else None
            ),
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        )
        ok_button.pack(side="left")

        def _close_info_dialog():
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", _close_info_dialog)
        dialog.update_idletasks()
        dialog.bind("<Return>", lambda _event: _close_info_dialog())
        dialog.bind("<KP_Enter>", lambda _event: _close_info_dialog())
        self._center_dialog_on_screen(dialog, parent_widget)
        dialog.deiconify()
        dialog.lift()
        try:
            dialog.attributes("-topmost", True)
            dialog.after(1000, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
        except Exception:
            pass
        try:
            dialog.focus_force()
            ok_button.focus_set()
            dialog.after(100, lambda: dialog.lift() if dialog.winfo_exists() else None)
            dialog.after(150, lambda: dialog.focus_force() if dialog.winfo_exists() else None)
        except Exception:
            pass

        self.root.wait_window(dialog)

        target = refocus_widget
        if target is not None:
            try:
                if target.winfo_exists():
                    target.deiconify()
                    target.lift()
                    target.focus_force()
            except Exception:
                pass

    def _center_dialog_on_screen(self, dialog, parent_widget=None):
        try:
            dialog.update_idletasks()
            width = max(1, int(dialog.winfo_width() or dialog.winfo_reqwidth() or 1))
            height = max(1, int(dialog.winfo_height() or dialog.winfo_reqheight() or 1))
            host = parent_widget if parent_widget is not None else dialog
            try:
                if isinstance(host, (tk.Tk, tk.Toplevel)) and host.state() == "iconic":
                    host = dialog
            except Exception:
                host = dialog
            try:
                origin_x = int(host.winfo_vrootx())
                origin_y = int(host.winfo_vrooty())
                screen_width = max(1, int(host.winfo_vrootwidth() or host.winfo_screenwidth() or 1))
                screen_height = max(1, int(host.winfo_vrootheight() or host.winfo_screenheight() or 1))
            except Exception:
                origin_x = 0
                origin_y = 0
                screen_width = max(1, int(dialog.winfo_screenwidth() or 1))
                screen_height = max(1, int(dialog.winfo_screenheight() or 1))
            max_x = max(origin_x, origin_x + screen_width - width)
            max_y = max(origin_y, origin_y + screen_height - height)
            x = int(origin_x + (screen_width - width) / 2)
            y = int(origin_y + (screen_height - height) / 2)
            x = min(max(origin_x, x), max_x)
            y = min(max(origin_y, y), max_y)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _js8call_speed_warning_dialog(self, parent=None, refocus_widget=None):
        return True

    def _refocus_mesh_window(self):
        try:
            win = self.mesh_network_window.window
        except Exception:
            win = None
        if win is None:
            return
        try:
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
        except Exception:
            pass

    def show_callsign_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Callsign")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Amateur Radio Callsign:",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        amateur_callsign_var = tk.StringVar(value=str(settings.get("amateur_callsign", "")).strip().upper())
        amateur_entry = tk.Entry(outer, textvariable=amateur_callsign_var, width=18)
        amateur_entry.pack(anchor="w", pady=(8, 12))

        special_enabled_var = tk.BooleanVar(value=bool(settings.get("special_callsign_enabled", False)))
        special_callsign_var = tk.StringVar(value=str(settings.get("special_callsign", "")).strip().upper())
        special_frequency_var = tk.StringVar(
            value=self._normalize_frequency_text(settings.get("special_callsign_frequency", "")) or self.selected_frequency_var.get().strip()
        )
        special_summary_var = tk.StringVar(value="")

        tk.Checkbutton(
            outer,
            text="Use special callsign. ex. /T,/M, special event callsign",
            variable=special_enabled_var,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color,
            activebackground=self.bg_color,
            activeforeground=self.fg_color,
            highlightthickness=0,
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            outer,
            text="Special Callsign:",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        special_entry = tk.Entry(outer, textvariable=special_callsign_var, width=18)
        special_entry.pack(anchor="w", pady=(8, 12))

        tk.Label(
            outer,
            text="Choose frequency to use the above special callsign.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        special_frequency_combo = ttk.Combobox(
            outer,
            textvariable=special_frequency_var,
            values=list(self.frequency_options),
            state="normal",
            width=16,
        )
        special_frequency_combo.pack(anchor="w", pady=(8, 12))

        tk.Label(
            outer,
            textvariable=special_summary_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(0, 12))

        def refresh_special_summary(*_args):
            enabled = bool(special_enabled_var.get())
            callsign_text = str(special_callsign_var.get() or "").strip().upper()
            frequency_text = self._normalize_frequency_text(special_frequency_var.get()) or str(special_frequency_var.get() or "").strip()
            if not enabled:
                special_summary_var.set("Special callsign is disabled.")
            elif not callsign_text or not frequency_text:
                special_summary_var.set("Special callsign is enabled but not fully configured yet.")
            else:
                special_summary_var.set(
                    f"Special callsign {callsign_text} will be used on {frequency_text}."
                )

        special_enabled_var.trace_add("write", refresh_special_summary)
        special_callsign_var.trace_add("write", refresh_special_summary)
        special_frequency_var.trace_add("write", refresh_special_summary)
        refresh_special_summary()

        amateur_entry.focus_set()
        amateur_entry.selection_range(0, tk.END)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x")

        def do_save():
            special_frequency = self._normalize_frequency_text(special_frequency_var.get())
            if special_enabled_var.get() and not special_frequency:
                self._dark_info_dialog(
                    "Invalid Special Callsign Frequency",
                    "Choose a stored frequency for the special callsign."
                )
                return

            settings["amateur_callsign"] = amateur_callsign_var.get().strip().upper()
            settings["special_callsign_enabled"] = bool(special_enabled_var.get())
            settings["special_callsign"] = special_callsign_var.get().strip().upper()
            settings["special_callsign_frequency"] = special_frequency or ""
            self._sync_user_callsign_for_frequency(save=True)
            dialog.destroy()

        tk.Button(
            button_row,
            text="Save",
            command=do_save,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Cancel",
            command=dialog.destroy,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()

    def show_startup_frequency_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Start Up Frequency")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Choose the frequency JS8Mesh should select when the app starts.",
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
        ).pack(anchor="w")

        startup_var = tk.StringVar(value=str(settings.get("startup_frequency", self.selected_frequency_var.get())).strip())
        freq_combo = ttk.Combobox(
            outer,
            textvariable=startup_var,
            values=list(self.frequency_options),
            state="normal",
            width=16,
        )
        freq_combo.pack(anchor="w", pady=(10, 12))
        freq_combo.focus_set()
        freq_combo.selection_range(0, tk.END)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x")

        def do_save():
            normalized = self._normalize_frequency_text(startup_var.get())
            if normalized is None:
                self._dark_info_dialog("Invalid Frequency", "Enter a valid startup frequency.")
                return
            self._add_custom_frequency_option(normalized)
            startup_display = self._find_existing_frequency_option(normalized) or normalized
            settings["startup_frequency"] = startup_display
            save_settings(settings)
            dialog.destroy()

        tk.Button(button_row, text="OK", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left", padx=(0, 8))
        tk.Button(button_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")

    def show_jr_tx_time_limit_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Report TX Time Limit")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Set the default Report TX time limit in minutes.\nThis limit is used when generating JR, JRN, JRS, HR, and HRC replies.",
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
        ).pack(anchor="w")

        limit_row = tk.Frame(outer, bg=self.bg_color)
        limit_row.pack(anchor="w", pady=(10, 12))

        limit_var = tk.StringVar(value=str(settings.get("mesh_tx_time_limit_minutes", 3)))
        tk.Entry(limit_row, textvariable=limit_var, width=10).pack(side="left")
        tk.Label(
            limit_row,
            text="0 = No limit",
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
        ).pack(side="left", padx=(8, 0))

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x")

        def do_save():
            limit_minutes = self._safe_positive_int(
                limit_var.get(),
                settings.get("mesh_tx_time_limit_minutes", 3),
                minimum=0
            )
            settings["mesh_tx_time_limit_minutes"] = limit_minutes
            save_settings(settings)
            if getattr(self, "mesh_network_window", None) is not None:
                self.mesh_network_window.set_tx_time_limit_minutes_text(limit_minutes)
            if getattr(self, "mesh_network_window", None) is not None and self.mesh_network_window.has_window():
                self._update_mesh_status_text()
                self._on_mesh_window_inputs_changed()
            dialog.destroy()

        tk.Button(button_row, text="OK", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left", padx=(0, 8))
        tk.Button(button_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")

    def show_api_settings_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("API Settings")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="IP address:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky="w")
        host_var = tk.StringVar(value=str(settings.get("js8call_host", "127.0.0.1")).strip() or "127.0.0.1")
        host_entry = tk.Entry(outer, textvariable=host_var, width=18)
        host_entry.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 10))

        tk.Label(outer, text="TCP Port:", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky="w")
        port_var = tk.StringVar(value=str(settings.get("js8call_port", 2442)))
        port_entry = tk.Entry(outer, textvariable=port_var, width=10)
        port_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(0, 12))

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.grid(row=2, column=0, columnspan=2, sticky="w")

        def do_save():
            host = str(host_var.get() or "").strip() or "127.0.0.1"
            try:
                port = int(str(port_var.get() or "").strip())
            except Exception:
                port = None
            if port is None or port <= 0:
                self._dark_info_dialog("Invalid TCP Port", "Enter a valid TCP port number.")
                return
            settings["js8call_host"] = host
            settings["js8call_port"] = port
            save_settings(settings)
            dialog.destroy()

        tk.Button(button_row, text="Save", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left", padx=(0, 8))
        tk.Button(button_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")
        host_entry.focus_set()

    def show_js8call_control_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("JS8Call Control")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        choice_var = tk.StringVar(value="YES" if settings.get("js8call_allow_auto_send", False) else "NO")

        tk.Label(
            outer,
            text="Allow JS8Mesh to press Send button in JS8Call.",
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")

        choice_row = tk.Frame(outer, bg=self.bg_color)
        choice_row.pack(anchor="w", pady=(10, 12))

        for option in ("YES", "NO"):
            tk.Radiobutton(
                choice_row,
                text=option,
                variable=choice_var,
                value=option,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
            ).pack(side="left", padx=(0, 16))

        tk.Label(
            outer,
            text="DISCLAIMER: If you choose YES you do so at your own risk.",
            bg=self.bg_color,
            fg="#ff4444",
            justify="left",
            anchor="w",
            wraplength=520,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(fill="x", pady=(0, 8))

        warning_text = (
            "Always monitor your staton. Long TX times may harm your tranceiver. The TX time estimator "
            "on this software is an estimator only and not an accurate one. Real TX time in JS8Call "
            "me be longer than what this software estimates. Always monitor your station."
        )
        tk.Label(
            outer,
            text=warning_text,
            bg=self.bg_color,
            fg="#ff6666",
            justify="left",
            anchor="w",
            wraplength=520,
        ).pack(fill="x")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def do_save():
            settings["js8call_allow_auto_send"] = (choice_var.get().strip().upper() == "YES")
            save_settings(settings)
            dialog.destroy()

        tk.Button(button_row, text="Save", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left", padx=(0, 8))
        tk.Button(button_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")

        x = self.root.winfo_rootx() + 80
        y = self.root.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")

        self.root.wait_window(dialog)

    def _normalized_watched_callsigns(self):
        raw_items = settings.get("watched_callsigns", [])
        normalized = []
        for item in list(raw_items or []):
            call = normalize_callsign(item)
            if call and call not in normalized:
                normalized.append(call)
        return normalized

    def show_watch_callsigns_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Watch Callsigns")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Add callsigns you want JS8Mesh to watch. When one appears in new activity, you will get an on-screen notification.",
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            wraplength=420,
        ).pack(anchor="w")

        list_frame = tk.Frame(outer, bg=self.bg_color)
        list_frame.pack(fill="both", expand=True, pady=(12, 8))

        watched_var = tk.StringVar(value=self._normalized_watched_callsigns())
        listbox = tk.Listbox(
            list_frame,
            listvariable=watched_var,
            selectmode="extended",
            height=10,
            bg="#111111",
            fg=self.fg_color,
            selectbackground=self.highlight_color,
            selectforeground=self.fg_color,
        )
        listbox.pack(side="left", fill="both", expand=True)

        scroll = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)

        entry_row = tk.Frame(outer, bg=self.bg_color)
        entry_row.pack(fill="x", pady=(0, 8))

        new_call_var = tk.StringVar(value="")
        tk.Label(entry_row, text="Callsign:", bg=self.bg_color, fg=self.fg_color).pack(side="left")
        entry = tk.Entry(entry_row, textvariable=new_call_var, width=18)
        entry.pack(side="left", padx=(6, 8))

        def refresh_listbox(items):
            watched_var.set(list(items))

        def current_items():
            return [str(item).strip().upper() for item in listbox.get(0, tk.END) if str(item).strip()]

        def do_add():
            call = normalize_callsign(new_call_var.get())
            if not call:
                self._dark_info_dialog("Watch Callsigns", "Enter a valid callsign.", parent=dialog, refocus_widget=dialog)
                return
            items = current_items()
            if call not in items:
                items.append(call)
                items.sort()
                refresh_listbox(items)
            new_call_var.set("")
            entry.focus_set()

        def do_remove():
            selected = list(listbox.curselection())
            if not selected:
                return
            items = current_items()
            remaining = [item for idx, item in enumerate(items) if idx not in selected]
            refresh_listbox(remaining)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(4, 0))

        tk.Button(button_row, text="Add", command=do_add, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")
        tk.Button(button_row, text="Remove Selected", command=do_remove, bg=self.highlight_color, fg=self.fg_color, width=16).pack(side="left", padx=(8, 0))

        action_row = tk.Frame(outer, bg=self.bg_color)
        action_row.pack(fill="x", pady=(16, 0))

        def do_save():
            settings["watched_callsigns"] = current_items()
            save_settings(settings)
            dialog.destroy()

        tk.Button(action_row, text="Save", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left")
        tk.Button(action_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="left", padx=(8, 0))

        entry.bind("<Return>", lambda _event: do_add())

        x = self.root.winfo_rootx() + 80
        y = self.root.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")

        entry.focus_set()
        self.root.wait_window(dialog)

    def show_frequency_management_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Add / Remove Frequency")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text=(
                "Add a frequency by typing it in the pathway frequency box and pressing Enter.\n\n"
                "Use Remove to delete a custom frequency."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w"
        ).pack(anchor="w")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def do_add_focus():
            dialog.destroy()
            try:
                self.frequency_combo.focus_set()
                self.frequency_combo.selection_range(0, tk.END)
            except Exception:
                pass

        def do_remove():
            dialog.destroy()
            self.show_remove_frequency_dialog()

        tk.Button(
            button_row,
            text="Add",
            command=do_add_focus,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Remove",
            command=do_remove,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Close",
            command=dialog.destroy,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()

        x = self.root.winfo_rootx() + 80
        y = self.root.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")

        self.root.wait_window(dialog)

    def _refresh_frequency_option_widgets(self):
        values = list(self.frequency_options)
        try:
            self.frequency_combo.configure(values=values)
        except Exception:
            pass
        try:
            self.pathways_panel.frequency_options = list(values)
        except Exception:
            pass
        try:
            if getattr(self, "relay_profiles_window", None) is not None:
                self.relay_profiles_window.set_frequency_options(["ALL"] + list(values))
        except Exception:
            pass

    def show_remove_frequency_dialog(self):
        removable = [f for f in self.frequency_options if f not in self.default_frequency_options]
        if not removable:
            self._dark_info_dialog(
                "Remove Frequency",
                "There are no removable custom frequencies.\n\n"
                "Default built-in frequencies are protected."
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Remove Frequency")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Choose which frequency to remove:",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(anchor="w")

        remove_var = tk.StringVar(value=removable[0])
        ttk.Combobox(
            outer,
            textvariable=remove_var,
            values=removable,
            state="readonly",
            width=16
        ).pack(anchor="w", pady=(8, 12))

        action_var = tk.StringVar(value="keep")
        tk.Radiobutton(
            outer,
            text="Keep data from this frequency",
            variable=action_var,
            value="keep",
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color,
            activebackground=self.bg_color,
            activeforeground=self.fg_color,
            highlightthickness=0,
        ).pack(anchor="w")
        tk.Radiobutton(
            outer,
            text="Delete data from this frequency",
            variable=action_var,
            value="delete",
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color,
            activebackground=self.bg_color,
            activeforeground=self.fg_color,
            highlightthickness=0,
        ).pack(anchor="w", pady=(4, 0))

        def do_remove():
            freq = str(remove_var.get()).strip()
            if not freq:
                dialog.destroy()
                return

            if action_var.get() == "delete":
                self.records = [r for r in self.records if not self._frequency_matches(r.get("freq", ""), freq)]
                self.record_keys = {self._record_key(r) for r in self.records}
                snr_reports_db[:] = [
                    stored for stored in snr_reports_db
                    if not self._frequency_matches(stored.get("freq", ""), freq)
                ]
                save_snr_reports(snr_reports_db)
                self.rebuild_activity_window_from_records()

            self.frequency_options = [f for f in self.frequency_options if f != freq]
            custom = [f for f in settings.get("custom_frequency_options", []) if f != freq]
            settings["custom_frequency_options"] = custom
            if self.selected_frequency_var.get().strip() == freq:
                fallback = self.frequency_options[-1] if self.frequency_options else "7.078 MHz"
                self.selected_frequency_var.set(fallback)
                settings["selected_frequency"] = fallback
            self._sync_user_callsign_for_frequency(save=True)

            self._refresh_frequency_option_widgets()
            dialog.destroy()

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        tk.Button(
            button_row,
            text="Remove",
            command=do_remove,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="Cancel",
            command=dialog.destroy,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + 80
        y = self.root.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")
        self.root.wait_window(dialog)

    # ------------------------------------------------
    # About / window openers
    # ------------------------------------------------

    def show_about(self):
        about = tk.Toplevel(self.root)
        about.title("About JS8Mesh v0.10.2-beta")
        about.configure(bg=self.bg_color)

        outer = tk.Frame(about, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="JS8Mesh v0.10.2-beta by SV8TTL, 18SV8110",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w", pady=(0, 12))

        tk.Label(
            outer,
            text=(
                "Started as an idea to connect stations of distant islands in Greece "
                "through the use of the Relay feature of JS8Call."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 14))

        tk.Label(
            outer,
            text=(
                'JS8Mesh is not an actual Mesh Network. It is a Human-supervised "mesh" '
                "awareness and Relay tool for JS8Call. In JS8Call all relay stations are "
                "human operators that decide if they want to be part of a relay or not, so "
                "no chance to ever automate routing. I borrowed words from mesh networks "
                "terminology because they help decribe the position each station can take "
                "in a JS8Call relay chain and make a distinction about operators that use "
                "JS8Mesh and those who do not. A node is a station that uses JS8Mesh and "
                "can interact with an other station using JS8Mesh when the human operator "
                "decides to send certain messages through JS8Call and the other operator "
                "chooses to answer. I hope this makes it clear enough, JS8Mesh is not an "
                "automated mesh."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 12))

        tk.Label(
            outer,
            text=(
                "JS8Mesh is a software that replaces taking notes on paper about who hears "
                "who and how do this station hears that station. Then, as any operator would "
                "do, checks the strongest, freshest SNRs for relaying a message through "
                'JS8Call Relay feature. An operator would think "I have tried a relay '
                'through this station before and did not work." JS8Mesh takes that into '
                "account too."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 14))

        tk.Label(
            outer,
            text="Thanks to my family for waiting for me all the long hours it took to build this.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text=(
                "Thanks to SV1SJJ, 18SV1231 for his patience, his help, his teaching, "
                "and his heartful and unstoppable guidance in everything I need about our "
                "common hobby. Thank you for testing this software too."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text="Thanks to SV1DKT, 18SV1514 for testing this software.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text=(
                "Thanks to those members of my DX group, the Sierra Victor DX Group for "
                "their support: 18SV8295, 59SV5295"
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text=(
                "The International Sierra Victor DX Group is part of https://pasixeracb.com/  "
                "We are only a few but restless! Join us! (Yes, I very much enjoy DXing on 11m)"
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 10))

        tk.Label(
            outer,
            text=(
                "DISCLAIMER: Use according to your local laws. Use at your own risk. "
                "The operator is responsible for all transmissions. I am not a programmer; "
                "this app was built with the help of AI."
            ),
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w", fill="x", pady=(0, 10))

        tk.Label(
            outer,
            text="Licensed under GPL-3.0-only.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 6))

        tk.Label(
            outer,
            text="Modified versions and forks should use a different name to avoid confusion with the original project.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 10))

        tk.Label(
            outer,
            text='Uses pyjs8call for JS8Call integration. "JC" concepts were inspired in part by JS8Spotter.',
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10, "italic")
        ).pack(anchor="w", fill="x", pady=(0, 14))

        tk.Button(
            outer,
            text="Close",
            command=about.destroy,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(anchor="e")


    def _decode_mesh_entries_from_record(self, record):
        msg_text = str(record.get("msg", "") or "").strip()
        if not msg_text:
            return []
        if self._is_own_mesh_report_record(record):
            return []

        entries = []
        sender = str(record.get("from", "")).strip().upper()
        date_text = str(record.get("date", "")).strip()
        time_text = str(record.get("time", "")).strip()
        freq_text = str(record.get("freq", "")).strip()
        for parsed in parse_mesh_report_entries(msg_text, fallback_source=sender):
            try:
                snr_val = float(parsed.get("avg_snr"))
            except (ValueError, TypeError):
                continue

            source = str(parsed.get("source", "") or sender).strip().upper()
            heard = str(parsed.get("heard", "") or "").strip().upper()
            parent = str(parsed.get("parent", "") or source).strip().upper()
            minutes_value = parsed.get("minutes")
            wave = int(parsed.get("wave", 1) or 1)
            effective_minutes = mesh_report_entry_effective_minutes(record, parsed, now=self._now())
            parsed_format = str(parsed.get("format", "") or "").strip().upper()
            report_kind = str(parsed.get("report_kind", "") or "JR").strip().upper() or "JR"
            is_node = bool(parsed.get("is_node"))
            if not heard:
                continue

            if parsed_format.startswith("JM"):
                decoded = f"JM|{source}|{heard}|A{int(round(snr_val)):+d}"
                if minutes_value is not None:
                    decoded += f"|L{int(minutes_value)}m"
                format_text = "JM"
            elif wave <= 1:
                heard_text = f"*{heard}" if report_kind == "JR" and is_node else heard
                decoded = f"{report_kind}.1.{heard_text}.{int(round(snr_val)):+d}.{int(minutes_value or 0)}"
                format_text = report_kind
            else:
                heard_text = f"*{heard}" if report_kind == "JR" and is_node else heard
                decoded = f"{report_kind}.{wave}.{heard_text}.{parent}.{int(round(snr_val)):+d}.{int(minutes_value or 0)}"
                format_text = report_kind

            entries.append({
                "date": date_text,
                "time": time_text,
                "sender": source,
                "heard": heard,
                "parent": parent,
                "wave": wave,
                "snr": snr_val,
                "snr_text": f"{int(snr_val):+d}" if float(snr_val).is_integer() else f"{snr_val:+.1f}",
                "minutes_text": "" if effective_minutes is None else str(int(round(effective_minutes))),
                "minutes": effective_minutes,
                "reported_minutes": minutes_value,
                "format": format_text,
                "decoded": decoded,
                "freq": freq_text,
            })

        return entries

    def show_mesh_report_activity_today(self):
        if not self._apply_frequency_value(show_error=False):
            return

        today = self.mesh_report_activity_session_date
        rows = []

        for record in self._records_for_selected_frequency():
            dt_value = record.get("datetime")
            if dt_value is None or dt_value.date() != today:
                continue

            rows.extend(self._decode_mesh_entries_from_record(record))

        rows.sort(
            key=lambda row: (
                row.get("date", ""),
                row.get("time", ""),
                row.get("sender", ""),
                row.get("heard", ""),
            ),
            reverse=True,
        )

        self.mesh_report_activity_window.set_rows(
            rows,
            frequency_text=self.selected_frequency_var.get().strip(),
        )

    def show_activity_window(self):
        self.activity_window.show()
        self.rebuild_activity_window_from_records()

    def show_mesh_network_window(self):
        self.mesh_network_window.show()
        self._sync_mesh_lookback_controls()
        self._update_mesh_status_text()
        self._on_mesh_window_inputs_changed()

    def _close_request_jr_picker_window(self):
        try:
            if self.request_jr_picker_window is not None and self.request_jr_picker_window.winfo_exists():
                self.request_jr_picker_window.destroy()
        except Exception:
            pass
        self.request_jr_picker_window = None
        self.request_jr_picker_tree = None
        self.request_jr_picker_type = None

    def _close_request_jr_window(self):
        self.request_jr_window_ui.close()

    def _current_request_jr_sender(self):
        return normalize_callsign(self.user_call_var.get().strip().upper())

    def _request_jr_type_label(self, type_key):
        labels = {
            "GENERAL": "General",
            "NODES_ONLY": "Nodes Only",
            "STATIONS_ONLY": "Stations Only",
            "HEARD_STATIONS": "Heard 4 Stations",
            "HEARD_RELAY_CANDIDATE": "Can Relay to Callsign",
            "FIND_CALLSIGN": "Find Callsign",
            "NEXT_WAVE": "Next Wave",
        }
        return labels.get(str(type_key or "").strip().upper(), "General")

    def _request_jr_command_text(self):
        scope = self.request_jr_window_ui.get_report_scope()
        mapping = {
            "GENERAL": "JC JR",
            "NODES_ONLY": "JC JRN",
            "STATIONS_ONLY": "JC JRS",
            "HEARD_STATIONS": "JC HR",
            "HEARD_RELAY_CANDIDATE": "JC HRC",
            "FIND_CALLSIGN": "JC FIND",
        }
        return mapping.get(scope, "JC JR")

    def _on_request_jr_node_selected(self, type_key, node_info):
        key = str(type_key or "GENERAL").strip().upper()
        if key in self.request_jr_selected_node_info:
            self.request_jr_selected_node_info[key] = dict(node_info or {})
        self._update_request_jr_preview()

    def _request_jr_mode_for_first_hop(self, first_hop_callsign, max_age_minutes=None):
        first_hop = normalize_callsign(first_hop_callsign)
        user_cs = self.user_call_var.get().strip().upper()
        if not user_cs or not first_hop:
            return "NORMAL"
        effective_max_age = self._current_max_age_minutes() if max_age_minutes is None else max(1, int(max_age_minutes))
        send_graph = build_send_graph(
            self._records_for_selected_frequency(),
            user_cs=user_cs,
            min_snr=-30,
            max_age_minutes=effective_max_age,
        )
        direct_edge = send_graph.get((normalize_callsign(user_cs), first_hop))
        if direct_edge:
            direct_snr = direct_edge.get("snr")
            try:
                return snr_category_from_reported(float(direct_snr))
            except Exception:
                pass
        graph = build_hearing_graph(
            records=self._records_for_selected_frequency(),
            max_age_minutes=effective_max_age,
            frequency=self.selected_frequency_var.get().strip(),
        )
        link = self._mesh_link_info(graph, user_cs, first_hop)
        if not link:
            return "NORMAL"
        category = str(link.get("category", "NORMAL") or "NORMAL").strip().upper()
        if category in ("TURBO", "FAST", "NORMAL", "SLOW"):
            return category
        return "NORMAL"

    def _requested_jr_default_mode(self):
        if str(self.requested_jr_reply_target or "").strip().upper() == "@JS8MESH":
            return "NORMAL"
        first_hop = normalize_callsign(self.requested_jr_first_hop or self.requested_jr_requester_callsign)
        if not first_hop:
            return "NORMAL"
        calculated = str(
            self._request_jr_mode_for_first_hop(first_hop, max_age_minutes=self._current_max_age_minutes()) or "NORMAL"
        ).strip().upper()
        if calculated in ("TURBO", "FAST", "NORMAL", "SLOW"):
            return calculated
        return "NORMAL"

    def _current_request_jr_send_mode(self):
        selected = self.request_jr_window_ui.get_speed_mode()
        if selected in ("TURBO", "FAST", "NORMAL"):
            return selected
        target_mode = self.request_jr_window_ui.get_target_mode("GENERAL")
        if target_mode == "GROUP":
            return "NORMAL"
        selected_info = dict(self.request_jr_selected_node_info.get("GENERAL", {}) or {})
        selected_mode = str(selected_info.get("mode", "") or "").strip().upper()
        if selected_mode in ("T", "F", "N"):
            return {"T": "TURBO", "F": "FAST", "N": "NORMAL"}[selected_mode]
        path_text = str(selected_info.get("path_text", "") or "").strip()
        recipient = normalize_callsign(self.request_jr_window_ui.get_recipient("GENERAL"))
        first_hop = ""
        if path_text:
            parts = [part.strip().upper() for part in path_text.split(">") if str(part).strip()]
            if parts:
                first_hop = parts[0]
        if not first_hop:
            first_hop = recipient
        return self._request_jr_mode_for_first_hop(first_hop)

    def _known_mesh_nodes_for_request_jr(self, ignore_freshness=None):
        selected_frequency = self.selected_frequency_var.get().strip()
        user_cs = self.user_call_var.get().strip().upper()
        if not selected_frequency:
            records = list(self.records)
        else:
            records = [
                record
                for record in self.records
                if self._frequency_matches(record.get("freq", ""), selected_frequency)
            ]
        if ignore_freshness is None:
            max_age_minutes = self._current_max_age_minutes()
        else:
            max_age_minutes = 999999 if bool(ignore_freshness) else 65
        records_for_mesh = [
            record for record in records
            if not self._is_own_mesh_report_record(record)
        ]
        direct_graph = build_hearing_graph(
            records=records,
            max_age_minutes=max_age_minutes,
            frequency=selected_frequency,
        )
        send_graph = build_send_graph(
            records,
            user_cs=user_cs,
            min_snr=-30,
            max_age_minutes=max_age_minutes,
        )
        dual_snapshot = export_dual_topology_snapshot(
            records=records_for_mesh,
            traffic_max_age_minutes=max_age_minutes,
            mesh_activity_minutes=self._current_mesh_activity_minutes(),
            mesh_core_threshold=self._current_mesh_core_threshold(),
            frequency=selected_frequency,
            now=self._record_now(),
            exclude_callsigns=self._own_known_callsigns(),
        )
        mesh_nodes = [
            dict(node)
            for node in list(dual_snapshot.get("mesh", {}).get("nodes", []))
            if node.get("wave_depth") is not None and bool(node.get("is_mesh_node"))
        ]
        if not bool(ignore_freshness):
            filtered_mesh_nodes = []
            for node in mesh_nodes:
                latest_minutes = node.get("latest_minutes_ago")
                try:
                    latest_value = float(latest_minutes)
                except Exception:
                    latest_value = None
                if latest_value is None or latest_value <= float(max_age_minutes):
                    filtered_mesh_nodes.append(node)
            mesh_nodes = filtered_mesh_nodes
        mesh_ids = {
            normalize_callsign(node.get("id", ""))
            for node in mesh_nodes
            if normalize_callsign(node.get("id", ""))
        }
        reports_by_source = self._mesh_reports_by_source(records=records_for_mesh)
        rows = []
        seen_keys = set()

        def format_freshness_text(minutes_value):
            if minutes_value is None:
                return ""
            try:
                numeric = float(minutes_value)
            except Exception:
                return ""
            if abs(numeric - round(numeric)) < 0.05:
                return f"{int(round(numeric))}m"
            return f"{numeric:.1f}m"

        def add_row(callsign, known_as, path_parts, freshness_minutes=None):
            norm = normalize_callsign(callsign)
            if not norm or norm not in mesh_ids:
                return
            normalized_parts = [normalize_callsign(part) for part in list(path_parts or []) if normalize_callsign(part)]
            if not normalized_parts:
                return
            path_text = " > ".join(normalized_parts)
            wave = len(normalized_parts)
            parent_node = normalized_parts[-2] if len(normalized_parts) >= 2 else ""
            first_hop = normalized_parts[0]
            direct_edge = send_graph.get((normalize_callsign(user_cs), norm))
            if known_as == "Directly Heard" and direct_edge:
                try:
                    mode_name = snr_category_from_reported(float(direct_edge.get("snr", -30.0)))
                except Exception:
                    mode_name = self._request_jr_mode_for_first_hop(first_hop)
            else:
                mode_name = self._request_jr_mode_for_first_hop(first_hop)
            mode_token = {"TURBO": "T", "FAST": "F", "NORMAL": "N"}.get(mode_name, "N")
            key = (norm, known_as, path_text)
            if key in seen_keys:
                return
            seen_keys.add(key)
            rows.append({
                "callsign": norm,
                "known_as": known_as,
                "path_text": path_text,
                "wave_depth": wave,
                "parent_node": parent_node,
                "mode": mode_token,
                "freshness_minutes": freshness_minutes,
                "freshness_text": format_freshness_text(freshness_minutes),
            })

        mesh_wave_one = set()
        for node in mesh_nodes:
            callsign = normalize_callsign(node.get("id", ""))
            if not callsign:
                continue
            try:
                wave_depth = int(node.get("wave_depth", 0) or 0)
            except Exception:
                wave_depth = 0
            path_text = str(node.get("path_text", "") or "").strip()
            if path_text:
                path_parts = [
                    normalize_callsign(part)
                    for part in path_text.split(">")
                    if normalize_callsign(part)
                ]
            else:
                path_parts = [callsign]
            if not path_parts:
                path_parts = [callsign]
            direct_edge = send_graph.get((normalize_callsign(user_cs), callsign))
            if direct_edge:
                direct_age = direct_edge.get("age_minutes", node.get("latest_minutes_ago"))
                add_row(callsign, "Directly Heard", [callsign], direct_age)
                mesh_wave_one.add(callsign)
                if path_parts != [callsign]:
                    add_row(callsign, "Reported", path_parts, node.get("latest_minutes_ago"))
            else:
                known_as = "Directly Heard" if wave_depth <= 1 else "Reported"
                add_row(callsign, known_as, path_parts, node.get("latest_minutes_ago"))
            if wave_depth == 1 or direct_edge:
                mesh_wave_one.add(callsign)

        for source, report in reports_by_source.items():
            source_norm = normalize_callsign(source)
            if not source_norm or source_norm not in mesh_wave_one:
                continue

            entries = list(report.get("entries", []))
            entries.sort(key=lambda item: (int(item.get("wave", 1) or 1), str(item.get("heard", ""))))
            sender_local_paths = {}

            for entry in entries:
                heard = normalize_callsign(entry.get("heard", ""))
                parent = normalize_callsign(entry.get("parent") or source_norm)
                try:
                    wave = int(entry.get("wave", 1) or 1)
                except Exception:
                    wave = 1
                if not heard:
                    continue
                if wave <= 1:
                    candidate_paths = [[heard]]
                else:
                    parent_paths = sender_local_paths.get(parent, [[parent]] if parent else [])
                    candidate_paths = [list(parent_path) + [heard] for parent_path in parent_paths if parent_path]
                    if not candidate_paths and parent:
                        candidate_paths = [[parent, heard]]
                if not candidate_paths:
                    continue
                sender_local_paths.setdefault(heard, [])
                for sender_path in candidate_paths:
                    if sender_path not in sender_local_paths[heard]:
                        sender_local_paths[heard].append(sender_path)
                    effective_minutes = mesh_report_entry_effective_minutes(
                        report.get("record", {}),
                        entry,
                        now=self._record_now(),
                    )
                    if (
                        not bool(ignore_freshness)
                        and effective_minutes is not None
                        and float(effective_minutes) > float(max_age_minutes)
                    ):
                        continue
                    add_row(heard, "Reported", [source_norm] + list(sender_path), effective_minutes)

        rows.sort(key=lambda item: (0 if item.get("known_as") == "Directly Heard" else 1, int(item.get("wave_depth", 99) or 99), item.get("callsign", ""), item.get("path_text", "")))
        return rows

    def _set_request_jr_frame_enabled(self, type_key, enabled):
        widgets = self.request_jr_frame_widgets.get(type_key, {})
        frame = widgets.get("frame")
        if frame is not None:
            try:
                frame.configure(fg=self.fg_color if enabled else "#888888")
            except Exception:
                pass
        state = "normal" if enabled else "disabled"
        fg = self.fg_color if enabled else "#888888"
        for widget in widgets.get("widgets", []):
            try:
                widget.configure(state=state)
            except Exception:
                pass
            try:
                widget.configure(fg=fg)
            except Exception:
                pass
            try:
                widget.configure(disabledforeground="#888888")
            except Exception:
                pass

    def _update_request_jr_frame_states(self):
        active_type = str(self.request_jr_type_var.get() or "GENERAL").strip().upper()
        for type_key in ("GENERAL", "NODES_ONLY", "STATIONS_ONLY", "NEXT_WAVE"):
            self._set_request_jr_frame_enabled(type_key, type_key == active_type)
        self._update_request_jr_preview()

    def _build_request_jr_preview_text(self):
        type_key = self.request_jr_window_ui.get_type_key()
        sender = self._current_request_jr_sender()
        if not sender:
            return "Set your User Callsign first."

        target_mode = self.request_jr_window_ui.get_target_mode(type_key)

        if type_key != "GENERAL":
            return (
                f"Request Report: {self._request_jr_type_label(type_key)}\n\n"
                "This request type UI is ready.\n"
                "Command preview will be added in the next step."
            )

        command_text = self._request_jr_command_text()
        hrc_target = normalize_callsign(self.request_jr_window_ui.get_hrc_target_callsign())
        if target_mode == "GROUP":
            if command_text in ("JC HR", "JC HRC"):
                return f"{command_text} requests must be sent to a recipient node."
            if command_text == "JC FIND" and not hrc_target:
                return "Enter the target callsign you want other nodes to search for."
            return f"@JS8MESH {command_text} {hrc_target}".strip()

        recipient = normalize_callsign(self.request_jr_window_ui.get_recipient(type_key))
        if not recipient:
            return "Enter a recipient callsign or choose Send to @JS8MESH."
        if command_text == "JC HRC" and not hrc_target:
            return "Enter the target callsign you want the recipient node to check for relay reachability."
        if command_text == "JC FIND" and not hrc_target:
            return "Enter the target callsign you want other nodes to search for."
        selected_info = dict(self.request_jr_selected_node_info.get(type_key, {}) or {})
        if normalize_callsign(selected_info.get("callsign", "")) != recipient:
            selected_info = {}
        path_text = str(selected_info.get("path_text", "") or "").strip()
        if path_text:
            routed = ">".join(part.strip().upper() for part in path_text.split(">") if str(part).strip())
            if routed:
                return f"{routed} {command_text} {hrc_target}".strip()
        return f"{recipient} {command_text} {hrc_target}".strip()

    def _update_request_jr_preview(self, *_args):
        preview_text = self._build_request_jr_preview_text()
        self.request_jr_preview_var.set(preview_text)
        self.request_jr_window_ui.set_preview_text(preview_text)
        self.request_jr_window_ui.set_send_effects_text(
            self._js8call_send_effects_text(self._current_request_jr_send_mode())
        )

    def _copy_request_jr_preview(self):
        widget = self.request_jr_preview_widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                self.request_jr_preview_widget = None
                return
        except Exception:
            self.request_jr_preview_widget = None
            return

        try:
            selected = widget.get("sel.first", "sel.last")
        except Exception:
            selected = str(self.request_jr_preview_var.get() or "").strip()
        if not selected:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        self.root.update()

    def _show_request_jr_preview_context_menu(self, event):
        widget = self.request_jr_preview_widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                self.request_jr_preview_widget = None
                return
        except Exception:
            self.request_jr_preview_widget = None
            return

        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_request_jr_preview)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _send_request_jr_preview_to_js8call(self):
        preview_text = str(self.request_jr_window_ui.get_preview_text() or self.request_jr_preview_var.get() or "").strip()
        if not preview_text or preview_text.startswith("Enter a recipient") or preview_text.startswith("Set your User Callsign") or preview_text.startswith("Request Report:"):
            self._dark_info_dialog(
                "Nothing to Send",
                "There is no valid Request JR command to send yet.",
                parent=self.request_jr_window_ui.get_window(),
                refocus_widget=self.request_jr_window_ui.get_window(),
            )
            return
        target_mode = self._current_request_jr_send_mode()
        report_scope = self.request_jr_window_ui.get_report_scope()
        find_target = normalize_callsign(self.request_jr_window_ui.get_hrc_target_callsign())
        sender = normalize_callsign(self._current_request_jr_sender())
        frequency_text = self._normalize_frequency_text(self.selected_frequency_var.get()) or str(self.selected_frequency_var.get() or "").strip()

        def _on_find_request_started(_result):
            if report_scope != "FIND_CALLSIGN" or not find_target or not sender:
                return
            target_mode_text = self.request_jr_window_ui.get_target_mode("GENERAL")
            return_path = ""
            if target_mode_text == "GROUP":
                return_path = "@JS8MESH"
            else:
                recipient = normalize_callsign(self.request_jr_window_ui.get_recipient("GENERAL"))
                selected_info = dict(self.request_jr_selected_node_info.get("GENERAL", {}) or {})
                path_text = str(selected_info.get("path_text", "") or "").strip()
                if path_text:
                    return_path = ">".join(
                        normalize_callsign(part)
                        for part in path_text.split(">")
                        if normalize_callsign(part)
                    )
                else:
                    return_path = recipient
            created_at = self._now().isoformat(timespec="seconds")
            self._upsert_my_find_search({
                "event_id": f"MYFIND-{int(time.time() * 1000)}",
                "find_id": self._find_search_id(sender, find_target, frequency_text),
                "created_at": created_at,
                "updated_at": created_at,
                "requester": sender,
                "target_callsign": find_target,
                "frequency": frequency_text,
                "return_path": return_path,
                "status": "ACTIVE",
                "expires_at": self._find_expiry_iso(created_at),
                "details": "FIND request sent.",
            })

        self._send_text_to_js8call_async(
            text=preview_text,
            target_mode=target_mode,
            settings_key=None,
            parent_window=self.request_jr_window_ui.get_window(),
            early_success_callback=_on_find_request_started,
        )

    def _refresh_request_jr_picker_rows(self):
        tree = self.request_jr_picker_tree
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                self.request_jr_picker_tree = None
                return
        except Exception:
            self.request_jr_picker_tree = None
            return

        search_text = str(self.request_jr_picker_search_var.get() or "").strip().upper()
        tree.delete(*tree.get_children(""))
        for callsign in self._known_mesh_nodes_for_request_jr():
            if search_text and search_text not in callsign.upper():
                continue
            tree.insert("", "end", values=(callsign,))

    def _apply_request_jr_picker_selection(self):
        tree = self.request_jr_picker_tree
        type_key = self.request_jr_picker_type
        if tree is None or not type_key:
            return
        selection = tree.selection()
        if not selection:
            return
        item = tree.item(selection[0])
        values = item.get("values", [])
        if not values:
            return
        callsign = str(values[0] or "").strip().upper()
        recipient_var = self.request_jr_recipient_vars.get(type_key)
        if recipient_var is not None:
            recipient_var.set(callsign)
        target_mode_var = self.request_jr_target_mode_vars.get(type_key)
        if target_mode_var is not None:
            target_mode_var.set("RECIPIENT")
        self._close_request_jr_picker_window()
        self._update_request_jr_frame_states()

    def _open_request_jr_known_nodes_picker(self, type_key):
        self.request_jr_picker_type = type_key
        if self.request_jr_picker_window is not None:
            try:
                if self.request_jr_picker_window.winfo_exists():
                    self.request_jr_picker_window.deiconify()
                    self.request_jr_picker_window.lift()
                    self.request_jr_picker_window.focus_force()
                    self._refresh_request_jr_picker_rows()
                    return
            except Exception:
                pass
            self.request_jr_picker_window = None
            self.request_jr_picker_tree = None

        dialog = tk.Toplevel(self.root)
        dialog.title("Known Nodes")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.request_jr_window if self.request_jr_window is not None else self.root)
        self.request_jr_picker_window = dialog

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Select from known nodes",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")

        search_row = tk.Frame(outer, bg=self.bg_color)
        search_row.pack(fill="x", pady=(10, 0))
        tk.Label(search_row, text="Search:", bg=self.bg_color, fg=self.fg_color).pack(side="left", padx=(0, 8))
        search_entry = tk.Entry(search_row, textvariable=self.request_jr_picker_search_var, width=24)
        search_entry.configure(bg="#ffffff", fg="#000000", insertbackground="#000000")
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<KeyRelease>", lambda _event: self._refresh_request_jr_picker_rows())

        tree_frame = tk.Frame(outer, bg=self.bg_color)
        tree_frame.pack(fill="both", expand=True, pady=(12, 0))
        tree = ttk.Treeview(tree_frame, columns=("callsign",), show="headings", height=10, selectmode="browse")
        tree.heading("callsign", text="Known Node")
        tree.column("callsign", width=220, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        self.request_jr_picker_tree = tree
        tree.bind("<Double-1>", lambda _event: self._apply_request_jr_picker_selection())

        scroll_y = tk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        scroll_y.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll_y.set)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(12, 0))
        tk.Button(
            button_row,
            text="Request JR",
            command=self._apply_request_jr_picker_selection,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12,
        ).pack(side="left")
        tk.Button(
            button_row,
            text="Close",
            command=self._close_request_jr_picker_window,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12,
        ).pack(side="left", padx=(8, 0))

        dialog.protocol("WM_DELETE_WINDOW", self._close_request_jr_picker_window)
        dialog.update_idletasks()
        x = (self.request_jr_window if self.request_jr_window is not None else self.root).winfo_rootx() + 110
        y = (self.request_jr_window if self.request_jr_window is not None else self.root).winfo_rooty() + 110
        dialog.geometry(f"+{x}+{y}")
        self._refresh_request_jr_picker_rows()
        dialog.lift()
        dialog.focus_force()
        search_entry.focus_set()

    def show_request_jr_window(self):
        self.request_jr_window_ui.show()
        self._update_request_jr_preview()

    def show_auto_responder_log_window(self):
        self.refresh_auto_responder_log_window()
        self.auto_responder_log_window.show()

    def show_hr_log_window(self):
        self.refresh_hr_log_window()
        self.hr_log_window.show()

    def _auto_responder_debug_log_path(self):
        return os.path.join(APP_STORAGE_DIR, "auto_responder_debug.log")

    def _append_auto_responder_debug(self, message):
        timestamp = self._now().isoformat(timespec="seconds")
        line = f"{timestamp} | {str(message or '').strip()}"
        try:
            os.makedirs(APP_STORAGE_DIR, exist_ok=True)
            with open(self._auto_responder_debug_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        widget = self.auto_responder_debug_text
        if widget is not None:
            try:
                if widget.winfo_exists():
                    widget.configure(state="normal")
                    widget.insert("end", line + "\n")
                    widget.see("end")
                    widget.configure(state="disabled")
            except Exception:
                self.auto_responder_debug_text = None

    def _load_auto_responder_debug_text(self):
        path = self._auto_responder_debug_log_path()
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def clear_auto_responder_debug(self):
        try:
            path = self._auto_responder_debug_log_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        widget = self.auto_responder_debug_text
        if widget is not None:
            try:
                if widget.winfo_exists():
                    widget.configure(state="normal")
                    widget.delete("1.0", "end")
                    widget.configure(state="disabled")
            except Exception:
                self.auto_responder_debug_text = None

    def _copy_auto_responder_debug(self):
        text = self._load_auto_responder_debug_text().strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def show_auto_responder_debug_window(self):
        if self.auto_responder_debug_window is not None:
            try:
                if self.auto_responder_debug_window.winfo_exists():
                    self.auto_responder_debug_window.deiconify()
                    self.auto_responder_debug_window.lift()
                    self.auto_responder_debug_window.focus_force()
                    return
            except Exception:
                pass
        self.auto_responder_debug_window = None
        self.auto_responder_debug_text = None

        dialog = tk.Toplevel(self.root)
        dialog.title("Assisted Responder Debug")
        dialog.configure(bg=self.bg_color)
        dialog.geometry("1100x520")
        self.auto_responder_debug_window = dialog

        outer = tk.Frame(dialog, bg=self.bg_color, padx=8, pady=8)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=self.bg_color)
        top.pack(fill="x", pady=(0, 6))

        tk.Label(
            top,
            text="Trace of ASSISTED JR reply handling. Use Copy All and send the text back for debugging.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        tk.Button(top, text="Copy All", command=self._copy_auto_responder_debug, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="right")
        tk.Button(top, text="Clear", command=self.clear_auto_responder_debug, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="right", padx=(8, 0))
        tk.Button(top, text="Close", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="right", padx=(8, 0))

        text = tk.Text(
            outer,
            wrap="word",
            bg="#111111",
            fg=self.fg_color,
            insertbackground=self.fg_color,
        )
        text.pack(side="left", fill="both", expand=True)
        self.auto_responder_debug_text = text

        scroll_y = tk.Scrollbar(outer, orient="vertical", command=text.yview)
        scroll_y.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll_y.set)

        content = self._load_auto_responder_debug_text()
        text.insert("1.0", content)
        text.configure(state="disabled")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.focus_force()

    def _load_js8call_rx_text(self):
        dial_text = ""
        rx_text = ""
        try:
            with self._new_js8call_bridge() as bridge:
                dial_text = bridge.get_dial_frequency() or ""
                rx_text = bridge.get_rx_text() or ""
        except Exception as exc:
            raise JS8CallBridgeError(str(exc)) from exc
        return str(dial_text or "").strip(), str(rx_text or "")

    def _copy_js8call_rx_monitor_text(self):
        widget = self.js8call_rx_monitor_text
        if widget is None:
            return
        text = str(widget.get("1.0", "end-1c") or "")
        if not text.strip():
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _select_all_js8call_rx_monitor_text(self):
        widget = self.js8call_rx_monitor_text
        if widget is None:
            return "break"
        widget.tag_add("sel", "1.0", "end")
        widget.mark_set("insert", "1.0")
        widget.see("1.0")
        return "break"

    def _show_js8call_rx_monitor_context_menu(self, event=None):
        widget = self.js8call_rx_monitor_text
        if widget is None:
            return
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_js8call_rx_monitor_text)
        menu.add_command(label="Select All", command=self._select_all_js8call_rx_monitor_text)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _close_js8call_rx_monitor_window(self):
        if self._js8call_rx_monitor_after_id is not None:
            try:
                self.root.after_cancel(self._js8call_rx_monitor_after_id)
            except Exception:
                pass
            self._js8call_rx_monitor_after_id = None
        window = self.js8call_rx_monitor_window
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
        self.js8call_rx_monitor_window = None
        self.js8call_rx_monitor_text = None
        self.js8call_rx_monitor_status_var.set("JS8Call RX monitor is closed.")
        self._js8call_rx_monitor_last_text = None
        self._js8call_rx_monitor_last_status = None
        self._js8call_rx_monitor_follow_tail = True
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _refresh_js8call_rx_monitor(self):
        window = self.js8call_rx_monitor_window
        widget = self.js8call_rx_monitor_text
        if window is None or widget is None:
            self._js8call_rx_monitor_after_id = None
            return
        try:
            if not window.winfo_exists():
                self._close_js8call_rx_monitor_window()
                return
        except Exception:
            self._close_js8call_rx_monitor_window()
            return

        try:
            dial_text, rx_text = self._load_js8call_rx_text()
            normalized_frequency = self._normalize_frequency_text(dial_text)
            raw_frequency = str(dial_text or "").strip()
            if normalized_frequency:
                display_frequency = normalized_frequency
            elif raw_frequency:
                display_frequency = raw_frequency
            else:
                display_frequency = "Unknown"

            status_text = f"JS8Call dial frequency: {display_frequency}"
            if status_text != self._js8call_rx_monitor_last_status:
                self.js8call_rx_monitor_status_var.set(status_text)
                self._js8call_rx_monitor_last_status = status_text

            if rx_text != self._js8call_rx_monitor_last_text:
                current_yview = widget.yview()
                was_following_tail = bool(self._js8call_rx_monitor_follow_tail)
                if current_yview and len(current_yview) >= 2:
                    was_following_tail = was_following_tail or float(current_yview[1]) >= 0.98
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("1.0", rx_text)
                widget.configure(state="disabled")
                if was_following_tail:
                    try:
                        widget.see("end-1c")
                    except Exception:
                        pass
                    self._js8call_rx_monitor_follow_tail = True
                elif current_yview:
                    try:
                        widget.yview_moveto(current_yview[0])
                    except Exception:
                        pass
                    self._js8call_rx_monitor_follow_tail = False
                self._js8call_rx_monitor_last_text = rx_text
        except Exception as exc:
            status_text = f"JS8Call RX monitor unavailable: {exc}"
            if status_text != self._js8call_rx_monitor_last_status:
                self.js8call_rx_monitor_status_var.set(status_text)
                self._js8call_rx_monitor_last_status = status_text

        self._js8call_rx_monitor_after_id = self.root.after(2000, self._refresh_js8call_rx_monitor)

    def show_js8call_rx_monitor_window(self):
        if self.js8call_rx_monitor_window is not None:
            try:
                if self.js8call_rx_monitor_window.winfo_exists():
                    self.js8call_rx_monitor_window.deiconify()
                    self.js8call_rx_monitor_window.lift()
                    self.js8call_rx_monitor_window.focus_force()
                    return
            except Exception:
                pass

        dialog = tk.Toplevel(self.root)
        dialog.title("JS8Call RX Monitor")
        dialog.configure(bg=self.bg_color)
        dialog.geometry("980x520")
        self.js8call_rx_monitor_window = dialog
        self._js8call_rx_monitor_follow_tail = True

        outer = tk.Frame(dialog, bg=self.bg_color, padx=8, pady=8)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=self.bg_color)
        top.pack(fill="x", pady=(0, 6))

        tk.Label(
            top,
            text="JS8Call receive-text monitor. This mirrors the JS8Call message box so you can watch incoming text while working in JS8Mesh.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        tk.Button(top, text="Copy", command=self._copy_js8call_rx_monitor_text, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="right")
        tk.Button(top, text="Close", command=self._close_js8call_rx_monitor_window, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="right", padx=(8, 0))

        tk.Label(
            outer,
            textvariable=self.js8call_rx_monitor_status_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(0, 6))

        text = tk.Text(
            outer,
            wrap="word",
            bg="#111111",
            fg=self.fg_color,
            insertbackground=self.fg_color,
        )
        text.pack(side="left", fill="both", expand=True)
        self.js8call_rx_monitor_text = text

        scroll_y = tk.Scrollbar(outer, orient="vertical", command=text.yview)
        scroll_y.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll_y.set)

        text.bind("<Control-a>", self._select_all_js8call_rx_monitor_text)
        text.bind("<Control-A>", self._select_all_js8call_rx_monitor_text)
        text.bind("<Button-3>", self._show_js8call_rx_monitor_context_menu)

        dialog.protocol("WM_DELETE_WINDOW", self._close_js8call_rx_monitor_window)
        dialog.focus_force()
        self._refresh_js8call_rx_monitor()

    def refresh_auto_responder_log_window(self):
        if getattr(self, "auto_responder_log_window", None) is None:
            return
        rows = [
            {
                "timestamp": str(item.get("timestamp", "") or ""),
                "requester": str(item.get("requester", "") or ""),
                "request_type": str(item.get("request_type", "") or ""),
                "frequency": str(item.get("frequency", "") or ""),
                "reply_text": str(item.get("reply_text", "") or ""),
                "speed": str(item.get("speed", "") or ""),
                "status": str(item.get("status", "") or ""),
                "reason": str(item.get("reason", "") or ""),
            }
            for item in list(auto_responder_log_db)
        ]
        self.auto_responder_log_window.set_rows(rows)

    def show_tx_mesh_reports_log_window(self):
        self.refresh_tx_mesh_reports_log_window()
        self.tx_mesh_reports_log_window.show()

    def refresh_tx_mesh_reports_log_window(self):
        if getattr(self, "tx_mesh_reports_log_window", None) is None:
            return
        rows = [
            {
                "timestamp": str(item.get("timestamp", "") or ""),
                "requester": str(item.get("requester", "") or ""),
                "request_type": str(item.get("request_type", "") or ""),
                "frequency": str(item.get("frequency", "") or ""),
                "reply_text": str(item.get("reply_text", "") or ""),
                "speed": str(item.get("speed", "") or ""),
                "status": str(item.get("status", "") or ""),
                "reason": str(item.get("reason", "") or ""),
            }
            for item in list(tx_mesh_reports_log_db)
        ]
        self.tx_mesh_reports_log_window.set_rows(rows)

    def refresh_hr_log_window(self):
        if getattr(self, "hr_log_window", None) is None:
            return
        rows = [
            {
                "timestamp": str(item.get("timestamp", "") or ""),
                "requester": str(item.get("requester", "") or ""),
                "request_type": str(item.get("request_type", "") or ""),
                "frequency": str(item.get("frequency", "") or ""),
                "reply_text": str(item.get("reply_text", "") or ""),
                "speed": str(item.get("speed", "") or ""),
                "status": str(item.get("status", "") or ""),
                "reason": str(item.get("reason", "") or ""),
            }
            for item in list(hr_log_db)
        ]
        self.hr_log_window.set_rows(rows)

    def _expires_in_text(self, expires_at_text):
        expires_dt = self._safe_parse_iso_datetime(expires_at_text)
        if expires_dt is None:
            return ""
        delta_seconds = int((expires_dt - self._now()).total_seconds())
        if delta_seconds <= 0:
            return "expired"
        total_minutes = max(1, int((delta_seconds + 59) / 60))
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0 and minutes > 0:
            return f"{hours}h {minutes}m"
        if hours > 0:
            return f"{hours}h"
        return f"{minutes}m"

    def show_my_find_searches_window(self):
        self.refresh_my_find_searches_window()
        self.my_find_searches_window.show()

    def show_held_find_searches_window(self):
        self.refresh_held_find_searches_window()
        self.held_find_searches_window.show()

    def refresh_my_find_searches_window(self):
        if getattr(self, "my_find_searches_window", None) is None:
            return
        rows = [
            {
                "find_id": str(item.get("find_id", "") or ""),
                "created_at": str(item.get("created_at", item.get("timestamp", "")) or ""),
                "target_callsign": str(item.get("target_callsign", "") or ""),
                "requester": str(item.get("requester", "") or ""),
                "frequency": str(item.get("frequency", "") or ""),
                "return_path": str(item.get("return_path", "") or ""),
                "status": str(item.get("status", "") or ""),
                "expires_in": self._expires_in_text(item.get("expires_at", "")),
                "details": str(item.get("details", "") or ""),
            }
            for item in list(my_find_searches_db)
        ]
        self.my_find_searches_window.set_rows(rows)

    def refresh_held_find_searches_window(self):
        if getattr(self, "held_find_searches_window", None) is None:
            return
        rows = [
            {
                "find_id": str(item.get("find_id", "") or ""),
                "created_at": str(item.get("created_at", item.get("timestamp", "")) or ""),
                "target_callsign": str(item.get("target_callsign", "") or ""),
                "requester": str(item.get("requester", "") or ""),
                "frequency": str(item.get("frequency", "") or ""),
                "return_path": str(item.get("return_path", "") or ""),
                "status": (
                    "FOUND - CANCELED TX"
                    if str(item.get("status", "") or "").strip().upper() == "CANCELED"
                    else str(item.get("status", "") or "")
                ),
                "expires_in": self._expires_in_text(item.get("expires_at", "")),
                "details": str(item.get("details", "") or ""),
            }
            for item in list(held_find_searches_db)
        ]
        self.held_find_searches_window.set_rows(rows)

    def clear_my_find_searches_log(self):
        my_find_searches_db[:] = []
        save_my_find_searches(my_find_searches_db)
        self.refresh_my_find_searches_window()

    def clear_held_find_searches_log(self):
        held_find_searches_db[:] = []
        save_held_find_searches(held_find_searches_db)
        self.refresh_held_find_searches_window()

    def send_selected_held_find_search_now(self):
        window = getattr(self, "held_find_searches_window", None)
        if window is None:
            return
        selected_rows = window.selected_row_dicts()
        if not selected_rows:
            self._dark_info_dialog(
                "Nothing Selected",
                "Select one held FIND search first.",
                parent=window.window if window.has_window() else self.root,
                refocus_widget=window.window if window.has_window() else self.root,
            )
            return
        row = dict(selected_rows[0] or {})
        target_id = str(row.get("find_id", "")).strip()
        held_item = None
        for item in reversed(held_find_searches_db):
            if str(item.get("find_id", "")).strip() == target_id:
                held_item = dict(item)
                break
        if held_item is None:
            self._dark_info_dialog(
                "Missing Entry",
                "The selected held FIND search could not be found anymore.",
                parent=window.window if window.has_window() else self.root,
                refocus_widget=window.window if window.has_window() else self.root,
            )
            return
        self._upsert_held_find_search({
            "find_id": target_id,
            "status": "FOUND",
            "updated_at": self._now().isoformat(timespec="seconds"),
            "details": "FINDR resend requested manually from Held Find Searches.",
            "next_attempt_at": "",
            "send_attempted_at": "",
        })
        refreshed = None
        for item in reversed(held_find_searches_db):
            if str(item.get("find_id", "")).strip() == target_id:
                refreshed = dict(item)
                break
        if refreshed is not None:
            self._maybe_send_find_result(refreshed, force=True)

    def _export_find_search_rows_txt(self, path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for item in list(rows or []):
                line = "\t".join(
                    [
                        str(item.get("created_at", item.get("timestamp", "")) or ""),
                        str(item.get("target_callsign", "") or ""),
                        str(item.get("requester", "") or ""),
                        str(item.get("frequency", "") or ""),
                        str(item.get("return_path", "") or ""),
                        str(item.get("status", "") or ""),
                        self._expires_in_text(item.get("expires_at", "")),
                        str(item.get("details", "") or ""),
                    ]
                ).rstrip()
                f.write(line + "\n")

    def _export_find_search_rows_csv(self, path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["TIME", "TARGET", "REQUESTER", "FREQ", "RETURN PATH", "STATUS", "EXPIRES IN", "DETAILS"])
            for item in list(rows or []):
                writer.writerow([
                    str(item.get("created_at", item.get("timestamp", "")) or ""),
                    str(item.get("target_callsign", "") or ""),
                    str(item.get("requester", "") or ""),
                    str(item.get("frequency", "") or ""),
                    str(item.get("return_path", "") or ""),
                    str(item.get("status", "") or ""),
                    self._expires_in_text(item.get("expires_at", "")),
                    str(item.get("details", "") or ""),
                ])

    def export_my_find_searches_log_txt(self):
        target_window = self.my_find_searches_window.window if getattr(self, "my_find_searches_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export My Find Searches",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        self._export_find_search_rows_txt(path, my_find_searches_db)
        self._refocus_window(target_window)

    def export_my_find_searches_log_csv(self):
        target_window = self.my_find_searches_window.window if getattr(self, "my_find_searches_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export My Find Searches",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        self._export_find_search_rows_csv(path, my_find_searches_db)
        self._refocus_window(target_window)

    def export_held_find_searches_log_txt(self):
        target_window = self.held_find_searches_window.window if getattr(self, "held_find_searches_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export Held Find Searches",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        self._export_find_search_rows_txt(path, held_find_searches_db)
        self._refocus_window(target_window)

    def export_held_find_searches_log_csv(self):
        target_window = self.held_find_searches_window.window if getattr(self, "held_find_searches_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export Held Find Searches",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        self._export_find_search_rows_csv(path, held_find_searches_db)
        self._refocus_window(target_window)

    def _append_my_find_search(self, entry):
        my_find_searches_db.append(dict(entry or {}))
        save_my_find_searches(my_find_searches_db)
        if getattr(self, "my_find_searches_window", None) is not None and self.my_find_searches_window.has_window():
            self.refresh_my_find_searches_window()

    def _append_held_find_search(self, entry):
        held_find_searches_db.append(dict(entry or {}))
        save_held_find_searches(held_find_searches_db)
        if getattr(self, "held_find_searches_window", None) is not None and self.held_find_searches_window.has_window():
            self.refresh_held_find_searches_window()

    def _save_my_find_searches(self):
        save_my_find_searches(my_find_searches_db)
        if getattr(self, "my_find_searches_window", None) is not None and self.my_find_searches_window.has_window():
            self.refresh_my_find_searches_window()

    def _save_held_find_searches(self):
        save_held_find_searches(held_find_searches_db)
        if getattr(self, "held_find_searches_window", None) is not None and self.held_find_searches_window.has_window():
            self.refresh_held_find_searches_window()

    def _find_search_id(self, requester, target_callsign, frequency):
        base = "|".join(
            [
                normalize_callsign(requester),
                normalize_callsign(target_callsign),
                self._normalize_frequency_text(frequency) or str(frequency or "").strip(),
            ]
        )
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16].upper()

    def _find_expiry_iso(self, created_at=None):
        created_dt = self._safe_parse_iso_datetime(created_at) if created_at else None
        if created_dt is None:
            created_dt = self._now()
        return (created_dt + timedelta(hours=FIND_REQUEST_EXPIRY_HOURS)).isoformat(timespec="seconds")

    def _is_find_entry_active(self, entry):
        expires_dt = self._safe_parse_iso_datetime((entry or {}).get("expires_at", ""))
        if expires_dt is None:
            return False
        return self._now() < expires_dt

    def _find_return_target_via_pathways(self, requester):
        requester_norm = normalize_callsign(requester)
        source_station = normalize_callsign(self.user_call_var.get().strip().upper())
        if not requester_norm or not source_station:
            return ""
        frequency_records = self._records_for_selected_frequency()
        if direct_path_evidence(
            frequency_records,
            source_station,
            requester_norm,
            max_age_minutes=self._current_max_age_minutes(),
        ):
            return requester_norm
        try:
            recommendations = recommend_paths(
                records=frequency_records,
                user_cs=source_station,
                target_cs=requester_norm,
                max_hops=self.max_hops_var.get(),
                min_snr=self.min_snr_var.get(),
                max_age_minutes=self._current_max_age_minutes(),
                reliability_db=reliability_db,
                classify_callsign=classify_callsign,
                relay_history_db=relay_history_db,
            )
        except Exception:
            recommendations = []
        for rec in list(recommendations or []):
            path_text = str(rec.get("pathway", "") or "").strip()
            path_parts = [normalize_callsign(part) for part in path_text.split(">") if normalize_callsign(part)]
            if len(path_parts) >= 2 and path_parts[0] == source_station and path_parts[-1] == requester_norm:
                return ">".join(path_parts[1:])
        return ""

    def _current_find_return_target(self, requester, preferred_reply_target, frequency_text):
        preferred = str(preferred_reply_target or "").strip().upper()
        if preferred and preferred != "@JS8MESH":
            return preferred
        return self._find_return_target_via_pathways(requester)

    def _latest_local_hearing_of_callsign(self, target_callsign, frequency_text, max_age_minutes=None):
        source_station = normalize_callsign(self.user_call_var.get().strip().upper())
        target_norm = normalize_callsign(target_callsign)
        if not source_station or not target_norm:
            return None
        effective_max_age = self._current_max_age_minutes() if max_age_minutes is None else max(1, int(max_age_minutes))
        _user_heard_target, target_heard_user = latest_direct_reports(
            self._records_for_selected_frequency(),
            source_station,
            target_norm,
            max_age_minutes=effective_max_age,
        )
        if not target_heard_user:
            return None
        try:
            snr_value = float(target_heard_user.get("snr", -30.0))
        except Exception:
            snr_value = -30.0
        try:
            heard_dt = target_heard_user.get("datetime")
            minutes_ago = max(0, int(round((self._now() - heard_dt).total_seconds() / 60.0))) if heard_dt is not None else 0
        except Exception:
            minutes_ago = 0
        return {
            "target_callsign": target_norm,
            "snr": snr_value,
            "minutes_ago": minutes_ago,
            "heard_at": self._now().isoformat(timespec="seconds"),
            "frequency": self._normalize_frequency_text(frequency_text) or str(frequency_text or "").strip(),
        }

    def _build_findr_payload(self, target_callsign, snr_value, minutes_ago):
        target_norm = normalize_callsign(target_callsign)
        if not target_norm:
            return ""
        try:
            snr_text = f"{int(round(float(snr_value))):+d}"
        except Exception:
            snr_text = "+0"
        try:
            minutes_text = str(max(0, int(round(float(minutes_ago)))))
        except Exception:
            minutes_text = "0"
        return f"FINDR.{target_norm}.{snr_text}.{minutes_text}"

    def _show_find_result_dialog(self, held_entry, preview_text, mode_name):
        target_callsign = normalize_callsign((held_entry or {}).get("target_callsign", ""))
        requester = normalize_callsign((held_entry or {}).get("requester", ""))
        return_target = str((held_entry or {}).get("return_path", "") or "").strip()
        details = str((held_entry or {}).get("details", "") or "").strip()
        body_text = (
            f"Requester: {requester or '-'}\n"
            f"Target Callsign: {target_callsign or '-'}\n"
            f"Return Path: {return_target or '-'}\n"
            f"Mode: {str(mode_name or 'NORMAL').strip().upper()}\n\n"
            f"Prepared FINDR message:\n{str(preview_text or '').strip()}"
        )
        footer_lines = [
            self._js8call_send_effects_text(mode_name),
            "",
            "Press Send to JS8Call to continue or Cancel to leave it pending.",
        ]
        if not bool(settings.get("js8call_allow_auto_send", False)):
            footer_lines.extend([
                "",
                "JS8Call Control is OFF. JS8Mesh will load the FINDR text into JS8Call only.",
            ])
        if details:
            footer_lines.extend(["", details])
        return self._show_send_confirmation_dialog(
            dialog_title="FIND Result",
            title_text="JS8Mesh has prepared this FINDR response.",
            body_text=body_text,
            footer_text="\n".join(footer_lines).strip(),
            parent_widget=self.root,
            confirm_label="Send to JS8Call",
            cancel_label="Cancel",
        )

    def _show_find_rebroadcast_dialog(self, held_entry, preview_text, mode_name):
        target_callsign = normalize_callsign((held_entry or {}).get("target_callsign", ""))
        requester = normalize_callsign((held_entry or {}).get("requester", ""))
        details = str((held_entry or {}).get("details", "") or "").strip()
        body_text = (
            f"Original requester: {requester or '-'}\n"
            f"Target callsign: {target_callsign or '-'}\n"
            f"Send mode: {str(mode_name or 'NORMAL').strip().upper()}\n\n"
            f"Group rebroadcast ready to send to JS8Call:\n{str(preview_text or '').strip()}"
        )
        footer_lines = [
            self._js8call_send_effects_text(mode_name),
            "",
            "Press Send to JS8Call to continue or Cancel to stop this rebroadcast.",
        ]
        if not bool(settings.get("js8call_allow_auto_send", False)):
            footer_lines.extend([
                "",
                "JS8Call Control is OFF. JS8Mesh will load the rebroadcast text into JS8Call only.",
            ])
        if details:
            footer_lines.extend(["", details])
        return self._show_send_confirmation_dialog(
            dialog_title="FIND Rebroadcast",
            title_text="JS8Mesh has prepared this delayed @JS8MESH FIND rebroadcast.",
            body_text=body_text,
            footer_text="\n".join(footer_lines).strip(),
            parent_widget=self.root,
            confirm_label="Send to JS8Call",
            cancel_label="Cancel",
        )

    def _upsert_my_find_search(self, entry):
        new_entry = dict(entry or {})
        find_id = str(new_entry.get("find_id", "")).strip() or self._find_search_id(
            new_entry.get("requester", ""),
            new_entry.get("target_callsign", ""),
            new_entry.get("frequency", ""),
        )
        new_entry["find_id"] = find_id
        for item in reversed(my_find_searches_db):
            if str(item.get("find_id", "")).strip() != find_id:
                continue
            item.update({k: v for k, v in new_entry.items() if v is not None})
            self._save_my_find_searches()
            return dict(item)
        my_find_searches_db.append(new_entry)
        self._save_my_find_searches()
        return dict(new_entry)

    def _upsert_held_find_search(self, entry):
        new_entry = dict(entry or {})
        find_id = str(new_entry.get("find_id", "")).strip() or self._find_search_id(
            new_entry.get("requester", ""),
            new_entry.get("target_callsign", ""),
            new_entry.get("frequency", ""),
        )
        new_entry["find_id"] = find_id
        for item in reversed(held_find_searches_db):
            if str(item.get("find_id", "")).strip() != find_id:
                continue
            item.update({k: v for k, v in new_entry.items() if v is not None})
            self._save_held_find_searches()
            return dict(item)
        held_find_searches_db.append(new_entry)
        self._save_held_find_searches()
        return dict(new_entry)

    def _append_hr_log(self, entry):
        hr_log_db.append(dict(entry or {}))
        save_hr_log(hr_log_db)
        if getattr(self, "hr_log_window", None) is not None and self.hr_log_window.has_window():
            self.refresh_hr_log_window()

    def _update_hr_log_status(self, event_id, status, reason="", reply_text=None, speed=None):
        target_id = str(event_id or "").strip()
        if not target_id:
            return
        updated = False
        for item in reversed(hr_log_db):
            if str(item.get("event_id", "")).strip() != target_id:
                continue
            item["status"] = str(status or "").strip().upper()
            if reason != "":
                item["reason"] = str(reason)
            if reply_text is not None:
                item["reply_text"] = str(reply_text)
            if speed is not None:
                item["speed"] = str(speed)
            updated = True
            break
        if updated:
            save_hr_log(hr_log_db)
            if getattr(self, "hr_log_window", None) is not None and self.hr_log_window.has_window():
                self.refresh_hr_log_window()

    def _append_tx_mesh_reports_log(self, entry):
        tx_mesh_reports_log_db.append(dict(entry or {}))
        save_tx_mesh_reports_log(tx_mesh_reports_log_db)
        if getattr(self, "tx_mesh_reports_log_window", None) is not None and self.tx_mesh_reports_log_window.has_window():
            self.refresh_tx_mesh_reports_log_window()

    def _refocus_window(self, window):
        try:
            if window is not None and window.winfo_exists():
                if isinstance(window, (tk.Tk, tk.Toplevel)):
                    window.deiconify()
                    window.lift()
                window.focus_force()
        except Exception:
            pass

    def _update_tx_mesh_reports_log_status(self, event_id, status, reason="", reply_text=None):
        event_id = str(event_id or "").strip()
        if not event_id:
            return
        updated = False
        for item in reversed(tx_mesh_reports_log_db):
            if str(item.get("event_id", "")).strip() != event_id:
                continue
            item["status"] = str(status or "")
            if reason != "":
                item["reason"] = str(reason)
            if reply_text is not None:
                item["reply_text"] = str(reply_text)
            updated = True
            break
        if updated:
            save_tx_mesh_reports_log(tx_mesh_reports_log_db)
            if getattr(self, "tx_mesh_reports_log_window", None) is not None and self.tx_mesh_reports_log_window.has_window():
                self.refresh_tx_mesh_reports_log_window()

    def clear_tx_mesh_reports_log(self):
        tx_mesh_reports_log_db[:] = []
        save_tx_mesh_reports_log(tx_mesh_reports_log_db)
        self.refresh_tx_mesh_reports_log_window()

    def export_tx_mesh_reports_log_txt(self):
        target_window = self.tx_mesh_reports_log_window.window if getattr(self, "tx_mesh_reports_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export TX Mesh Reports Log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8") as f:
            for item in list(tx_mesh_reports_log_db):
                line = "\t".join(
                    [
                        str(item.get("timestamp", "") or ""),
                        str(item.get("requester", "") or ""),
                        str(item.get("request_type", "") or ""),
                        str(item.get("frequency", "") or ""),
                        str(item.get("reply_text", "") or ""),
                        str(item.get("speed", "") or ""),
                        str(item.get("status", "") or ""),
                        str(item.get("reason", "") or ""),
                    ]
                ).rstrip()
                f.write(line + "\n")
        self._refocus_window(target_window)

    def export_tx_mesh_reports_log_csv(self):
        target_window = self.tx_mesh_reports_log_window.window if getattr(self, "tx_mesh_reports_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export TX Mesh Reports Log",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["TIME", "REQUESTER", "TYPE", "FREQ", "GENERATED REPLY", "SPEED", "STATUS", "REASON"])
            for item in list(tx_mesh_reports_log_db):
                writer.writerow([
                    str(item.get("timestamp", "") or ""),
                    str(item.get("requester", "") or ""),
                    str(item.get("request_type", "") or ""),
                    str(item.get("frequency", "") or ""),
                    str(item.get("reply_text", "") or ""),
                    str(item.get("speed", "") or ""),
                    str(item.get("status", "") or ""),
                    str(item.get("reason", "") or ""),
                ])
        self._refocus_window(target_window)

    def clear_auto_responder_log(self):
        auto_responder_log_db[:] = []
        save_auto_responder_log(auto_responder_log_db)
        self.refresh_auto_responder_log_window()

    def export_auto_responder_log_txt(self):
        target_window = self.auto_responder_log_window.window if getattr(self, "auto_responder_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export Requested Report Responds Log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8") as f:
            for item in list(auto_responder_log_db):
                line = "\t".join(
                    [
                        str(item.get("timestamp", "") or ""),
                        str(item.get("requester", "") or ""),
                        str(item.get("request_type", "") or ""),
                        str(item.get("frequency", "") or ""),
                        str(item.get("reply_text", "") or ""),
                        str(item.get("speed", "") or ""),
                        str(item.get("status", "") or ""),
                        str(item.get("reason", "") or ""),
                    ]
                ).rstrip()
                f.write(line + "\n")
        self._refocus_window(target_window)

    def export_auto_responder_log_csv(self):
        target_window = self.auto_responder_log_window.window if getattr(self, "auto_responder_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export Requested Report Responds Log",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["TIME", "REQUESTER", "TYPE", "FREQ", "GENERATED REPLY", "SPEED", "STATUS", "REASON"])
            for item in list(auto_responder_log_db):
                writer.writerow([
                    str(item.get("timestamp", "") or ""),
                    str(item.get("requester", "") or ""),
                    str(item.get("request_type", "") or ""),
                    str(item.get("frequency", "") or ""),
                    str(item.get("reply_text", "") or ""),
                    str(item.get("speed", "") or ""),
                    str(item.get("status", "") or ""),
                    str(item.get("reason", "") or ""),
                ])
        self._refocus_window(target_window)

    def clear_hr_log(self):
        hr_log_db[:] = []
        save_hr_log(hr_log_db)
        self.refresh_hr_log_window()

    def export_hr_log_txt(self):
        target_window = self.hr_log_window.window if getattr(self, "hr_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export HR Log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8") as f:
            for item in list(hr_log_db):
                line = "\t".join(
                    [
                        str(item.get("timestamp", "") or ""),
                        str(item.get("requester", "") or ""),
                        str(item.get("request_type", "") or ""),
                        str(item.get("frequency", "") or ""),
                        str(item.get("reply_text", "") or ""),
                        str(item.get("speed", "") or ""),
                        str(item.get("status", "") or ""),
                        str(item.get("reason", "") or ""),
                    ]
                ).rstrip()
                f.write(line + "\n")
        self._refocus_window(target_window)

    def export_hr_log_csv(self):
        target_window = self.hr_log_window.window if getattr(self, "hr_log_window", None) is not None else None
        path = filedialog.asksaveasfilename(
            title="Export HR Log",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["TIME", "REQUESTER", "TYPE", "FREQ", "GENERATED REPLY", "SPEED", "STATUS", "REASON"])
            for item in list(hr_log_db):
                writer.writerow([
                    str(item.get("timestamp", "") or ""),
                    str(item.get("requester", "") or ""),
                    str(item.get("request_type", "") or ""),
                    str(item.get("frequency", "") or ""),
                    str(item.get("reply_text", "") or ""),
                    str(item.get("speed", "") or ""),
                    str(item.get("status", "") or ""),
                    str(item.get("reason", "") or ""),
                ])
        self._refocus_window(target_window)

    def clear_past_relays_log(self):
        relay_history_db[:] = []
        save_relay_history(relay_history_db)
        self.update_past_relays_table()

    def export_past_relays_log_txt(self):
        target_window = self.past_relays_window
        path = filedialog.asksaveasfilename(
            title="Export Past Relays Log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8") as f:
            for item in list(relay_history_db):
                line = "\t".join([
                    str(item.get("timestamp", "") or ""),
                    str(item.get("result", "") or ""),
                    str(item.get("tx_mode", "") or ""),
                    str(item.get("pathway", "") or ""),
                    str(item.get("message_text", "") or ""),
                    str(item.get("prepared_message", "") or ""),
                ]).rstrip()
                f.write(line + "\n")
        self._refocus_window(target_window)

    def export_past_relays_log_csv(self):
        target_window = self.past_relays_window
        path = filedialog.asksaveasfilename(
            title="Export Past Relays Log",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            self._refocus_window(target_window)
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["TIME", "RESULT", "MODE", "PATHWAY", "MESSAGE", "PREPARED MESSAGE"])
            for item in list(relay_history_db):
                writer.writerow([
                    str(item.get("timestamp", "") or ""),
                    str(item.get("result", "") or ""),
                    str(item.get("tx_mode", "") or ""),
                    str(item.get("pathway", "") or ""),
                    str(item.get("message_text", "") or ""),
                    str(item.get("prepared_message", "") or ""),
                ])
        self._refocus_window(target_window)

    def show_topology_window(self):
        self._apply_frequency_value(show_error=False)
        self.topology_window.show()
        self.refresh_topology_window()

    # ------------------------------------------------
    # Activity window / settings
    # ------------------------------------------------

    def update_activity_display_limit(self):
        settings["activity_display_limit"] = self.activity_window.get_display_limit_text()
        save_settings(settings)
        self.rebuild_activity_window_from_records()

    def rebuild_activity_window_from_records(self):
        if not self.activity_window.has_window():
            return

        self.activity_window.clear_rows()

        limit = self.activity_window.get_display_limit()

        if limit is None:
            records_to_show = self.records
        else:
            records_to_show = self.records[-limit:]

        for record in records_to_show:
            self.add_to_activity(record)

    def _activity_message_text(self, record):
        msg_text = str(record.get("msg", "")).strip()
        receiver = str(record.get("to", "")).strip()

        if not msg_text:
            return ""

        # JS8Call directed lines often look like "SENDER: RECEIVER payload".
        # Activity should show the message body, not repeat the receiver callsign.
        if receiver and not msg_text.startswith("@"):
            receiver_upper = receiver.upper()
            msg_upper = msg_text.upper()
            if msg_upper == receiver_upper:
                return ""
            prefix = f"{receiver_upper} "
            if msg_upper.startswith(prefix):
                return msg_text[len(receiver) + 1:].strip()

        return msg_text

    def save_settings(self):
        if not self._apply_frequency_value(show_error=True):
            return

        settings["amateur_callsign"] = str(settings.get("amateur_callsign", "")).strip().upper()
        settings["user_callsign"] = self._active_callsign_for_frequency(self.selected_frequency_var.get())
        settings["directed_file"] = self.directed_file
        settings["min_snr"] = self.min_snr_var.get()
        settings["max_hops"] = self.max_hops_var.get()
        settings["refresh"] = 1
        settings["selected_frequency"] = self.selected_frequency_var.get().strip()
        settings["topology_mode"] = self._current_topology_mode()
        settings["test_mode_recent_records_limit"] = self.test_mode_recent_records_var.get().strip()

        if self.activity_window is not None:
            settings["activity_display_limit"] = self.activity_window.get_display_limit_text()

        save_settings(settings)

        messagebox.showinfo("Saved", "Settings saved!")
        self.refresh_topology_window()

    # ------------------------------------------------
    # Topology helpers
    # ------------------------------------------------

    def refresh_topology_window(self):
        if not self.topology_window.has_window():
            return

        selected_frequency = self.selected_frequency_var.get().strip()
        max_age_minutes = self._current_max_age_minutes()
        user_cs = self.user_call_var.get().strip().upper()
        target_cs = self.target_call_var.get().strip().upper()
        topology_mode = self._current_topology_mode()

        records_for_view = self._records_for_selected_frequency()
        records_for_mesh = [record for record in records_for_view if not self._is_own_mesh_report_record(record)]

        debug_snapshot = build_topology_debug_snapshot(
            records=records_for_mesh,
            max_age_minutes=max_age_minutes,
            frequency=selected_frequency,
            now=self._record_now(),
        )

        dual_snapshot = export_dual_topology_snapshot(
            records=records_for_mesh,
            traffic_max_age_minutes=max_age_minutes,
            mesh_activity_minutes=self._current_mesh_activity_minutes(),
            mesh_core_threshold=self._current_mesh_core_threshold(),
            frequency=selected_frequency,
            now=self._record_now(),
            exclude_callsigns=self._own_known_callsigns(),
        )

        traffic_export = dual_snapshot["traffic"]
        mesh_export = dual_snapshot["mesh"]
        mesh_stats = dual_snapshot["mesh_stats"]
        own_callsigns = self._own_known_callsigns()

        def _filter_topology_nodes_and_edges(nodes, edges):
            filtered_nodes = [
                node for node in list(nodes or [])
                if normalize_callsign(node.get("id", "")) not in own_callsigns
            ]
            allowed_ids = {
                normalize_callsign(node.get("id", ""))
                for node in filtered_nodes
                if normalize_callsign(node.get("id", ""))
            }
            filtered_edges = [
                edge for edge in list(edges or [])
                if normalize_callsign(edge.get("source", "")) in allowed_ids
                and normalize_callsign(edge.get("target", "")) in allowed_ids
            ]
            return filtered_nodes, filtered_edges

        if topology_mode == "mesh":
            nodes_to_show = mesh_export["nodes"]
            edges_to_show = mesh_export["edges"]
            nodes_to_show, edges_to_show = _filter_topology_nodes_and_edges(nodes_to_show, edges_to_show)
            wave_filter = self.topology_window.get_mesh_wave_filter() if self.topology_window is not None else {"mode": "all", "value": None}
            if wave_filter and wave_filter.get("mode") != "all":
                wave_value = wave_filter.get("value")
                allowed_ids = {
                    str(node.get("id", "")).strip().upper()
                    for node in nodes_to_show
                    if node.get("wave_depth") is not None and (
                        (wave_filter.get("mode") == "upto" and int(node.get("wave_depth")) <= int(wave_value))
                        or (wave_filter.get("mode") == "exact" and int(node.get("wave_depth")) == int(wave_value))
                        or (wave_filter.get("mode") == "min" and int(node.get("wave_depth")) >= int(wave_value))
                    )
                }
                nodes_to_show = [node for node in nodes_to_show if str(node.get("id", "")).strip().upper() in allowed_ids]
                edges_to_show = [
                    edge for edge in edges_to_show
                    if str(edge.get("source", "")).strip().upper() in allowed_ids
                    and str(edge.get("target", "")).strip().upper() in allowed_ids
                ]
            visible_role_text = (
                f"Known Nodes: {mesh_stats.get('mesh_known', 0)}   |   "
                f"Active Nodes: {mesh_stats.get('mesh_active', 0)}   |   "
                f"Core Nodes: {mesh_stats.get('mesh_core', 0)}"
            )
        else:
            nodes_to_show = traffic_export["nodes"]
            edges_to_show = traffic_export["edges"]
            nodes_to_show, edges_to_show = _filter_topology_nodes_and_edges(nodes_to_show, edges_to_show)
            visible_role_text = f"Traffic stations: {len(nodes_to_show)}"

        self.topology_window.populate(
            nodes=nodes_to_show,
            topology_mode=topology_mode,
        )
        self._last_topology_nodes = {
            str(node.get("id", "")).strip().upper(): dict(node)
            for node in nodes_to_show
        }
        self._last_topology_edges = list(edges_to_show)

        status_text = (
            f"View: {topology_mode.upper()}   |   "
            f"Mode: {'TEST MODE' if self.ignore_freshness_var.get() else 'NORMAL MODE'}   |   "
            f"Frequency: {selected_frequency or 'N/A'}   |   "
            f"Visible Nodes: {len(nodes_to_show)}   |   "
            f"Visible Edges: {len(edges_to_show)}   |   "
            f"Records: {debug_snapshot['records_total']}   |   "
            f"Freq matches: {debug_snapshot['freq_matches']}   |   "
            f"Fresh survivors: {debug_snapshot['freshness_survivors']}   |   "
            f"{visible_role_text}"
        )
        self.topology_window.set_status_text(status_text)

    # ------------------------------------------------
    # Mesh report logic
    # ------------------------------------------------

    def _mesh_station_summaries(self, lookback_minutes):
        now = self._record_now()
        own_callsign = self.user_call_var.get().strip().upper()
        summaries = {}

        for record in self._records_for_selected_frequency():
            dt = record.get("datetime")
            if dt is None:
                continue

            minutes_ago = int((now - dt).total_seconds() / 60.0)
            if minutes_ago < 0:
                minutes_ago = 0

            if minutes_ago > lookback_minutes:
                continue

            heard_station = str(record.get("from", "")).strip().upper()
            if not heard_station:
                continue

            if own_callsign and heard_station == own_callsign:
                continue

            snr_value = self._safe_float(record.get("snr", ""))
            if snr_value is None:
                continue

            candidate = {
                "heard_station": heard_station,
                "snr": snr_value,
                "minutes_ago": minutes_ago,
            }

            existing = summaries.get(heard_station)
            if existing is None:
                summaries[heard_station] = candidate
            else:
                if candidate["minutes_ago"] < existing["minutes_ago"]:
                    summaries[heard_station] = candidate
                elif candidate["minutes_ago"] == existing["minutes_ago"] and candidate["snr"] > existing["snr"]:
                    summaries[heard_station] = candidate

        final_items = list(summaries.values())
        final_items.sort(
            key=lambda item: (
                item["minutes_ago"],
                -item["snr"],
                item["heard_station"],
            )
        )

        return final_items

    def _hr_candidate_summaries(self, lookback_minutes, excluded_callsigns=None):
        source_station = self.user_call_var.get().strip().upper()
        if not source_station:
            return []

        excluded_norms = {
            normalize_callsign(item)
            for item in list(excluded_callsigns or [])
            if normalize_callsign(item)
        }

        records_for_view = self._records_for_selected_frequency()
        graph = build_send_graph(records_for_view, user_cs=source_station, min_snr=-30, max_age_minutes=99999999)
        selected_frequency = self.selected_frequency_var.get().strip()
        dual_snapshot = export_dual_topology_snapshot(
            records=records_for_view,
            traffic_max_age_minutes=self._current_max_age_minutes(),
            mesh_activity_minutes=self._current_mesh_activity_minutes(),
            mesh_core_threshold=self._current_mesh_core_threshold(),
            frequency=selected_frequency,
            now=self._record_now(),
            exclude_callsigns=self._own_known_callsigns(),
        )
        wave_node_calls = {
            normalize_callsign(node.get("id", ""))
            for node in list(dual_snapshot.get("mesh", {}).get("nodes", []))
            if normalize_callsign(node.get("id", "")) and node.get("wave_depth") is not None and bool(node.get("is_mesh_node"))
        }

        direct_candidates = set()
        source_norm = normalize_callsign(source_station)
        for left_callsign, right_callsign in graph.keys():
            if left_callsign == source_norm:
                direct_candidates.add(right_callsign)
            elif right_callsign == source_norm:
                direct_candidates.add(left_callsign)

        summaries = []
        for heard_station in direct_candidates:
            heard_norm = normalize_callsign(heard_station)
            if not heard_norm or heard_norm == source_norm:
                continue
            if heard_norm in excluded_norms:
                continue

            link = self._mesh_link_info(graph, source_station, heard_norm)
            if not link:
                continue
            if str(link.get("category", "")).strip().upper() not in ("TURBO", "FAST", "NORMAL"):
                continue
            if int(link.get("minutes_ago", 999999) or 999999) > int(lookback_minutes):
                continue

            summaries.append({
                "heard_station": heard_norm,
                "snr": float(link.get("snr", -30.0)),
                "minutes_ago": int(link.get("minutes_ago", 0) or 0),
                "is_node": heard_norm in wave_node_calls,
                "category": str(link.get("category", "")).strip().upper(),
            })

        summaries.sort(
            key=lambda item: (
                0 if item.get("category") == "TURBO" else 1,
                int(item.get("minutes_ago", 999999) or 999999),
                -float(item.get("snr", -30.0)),
                str(item.get("heard_station", "")),
            )
        )
        return summaries

    def _mesh_known_node_calls(self):
        reports_by_source = self._mesh_reports_by_source()
        return {
            normalize_callsign(source)
            for source in reports_by_source.keys()
            if normalize_callsign(source)
        }

    def _own_known_callsigns(self):
        values = {
            normalize_callsign(settings.get("amateur_callsign", "")),
            normalize_callsign(settings.get("special_callsign", "")),
            normalize_callsign(self.user_call_var.get().strip().upper()),
        }
        return {item for item in values if item}

    def _is_local_directed_command_record(self, record):
        if not isinstance(record, dict):
            return False
        raw_recipient = str(record.get("to", "") or "").strip().upper()
        own_calls = self._own_known_callsigns()
        if raw_recipient == "@JS8MESH":
            pass
        elif ">" in raw_recipient:
            parts = [part.strip().upper() for part in raw_recipient.split(">") if str(part).strip()]
            if not parts:
                return False
            final_part = parts[-1]
            if final_part != "@JS8MESH":
                recipient = normalize_callsign(final_part)
                if not recipient or recipient not in own_calls:
                    return False
        else:
            recipient = normalize_callsign(raw_recipient)
            if not recipient or recipient not in own_calls:
                return False
        selected_frequency = str(self.selected_frequency_var.get() or "").strip()
        if selected_frequency and not self._frequency_matches(record.get("freq", ""), selected_frequency):
            return False
        return True

    def _js8mesh_command_tokens(self, msg_text):
        return [
            token.strip().upper()
            for token in re.findall(r"[A-Z0-9]+", str(msg_text or "").upper())
            if token.strip()
        ]

    def _interpret_js8mesh_command_route(self, record):
        raw_recipient = str(record.get("to", "") or "").strip().upper()
        requester_from_sender = normalize_callsign(record.get("from", "")) or "UNKNOWN"
        if raw_recipient == "@JS8MESH":
            return {
                "supported": True,
                "requester": requester_from_sender,
                "reply_target": "@JS8MESH",
                "first_return_hop": requester_from_sender,
                "raw_recipient": raw_recipient,
                "is_group": True,
                "is_relay": False,
            }

        if ">" not in raw_recipient:
            return {
                "supported": True,
                "requester": requester_from_sender,
                "reply_target": requester_from_sender,
                "first_return_hop": requester_from_sender,
                "raw_recipient": raw_recipient,
                "is_group": False,
                "is_relay": False,
            }

        parts = [part.strip().upper() for part in raw_recipient.split(">") if str(part).strip()]
        if len(parts) < 2:
            return {
                "supported": False,
                "reason": "Malformed relayed JC recipient path.",
                "requester": requester_from_sender,
                "reply_target": "",
                "first_return_hop": "",
                "raw_recipient": raw_recipient,
                "is_group": False,
                "is_relay": True,
            }

        final_part = parts[-1]
        requester = requester_from_sender
        sender_norm = normalize_callsign(record.get("from", "")) or ""
        relay_norms = [normalize_callsign(part) for part in parts[:-1]]
        if sender_norm and sender_norm in relay_norms:
            return {
                "supported": False,
                "reason": "Looped relayed JC request: heard sender also appears inside the relay path.",
                "requester": requester,
                "reply_target": "",
                "first_return_hop": "",
                "raw_recipient": raw_recipient,
                "is_group": False,
                "is_relay": True,
            }
        if final_part == "@JS8MESH":
            return {
                "supported": False,
                "reason": "Relayed @JS8MESH JR requests are not supported.",
                "requester": requester,
                "reply_target": "@JS8MESH",
                "first_return_hop": "",
                "raw_recipient": raw_recipient,
                "is_group": True,
                "is_relay": True,
            }

        reply_parts = list(reversed(parts[:-1]))
        reply_parts.append(requester)
        reply_target = ">".join(reply_parts)
        first_return_hop = normalize_callsign(reply_parts[0]) if reply_parts else requester
        return {
            "supported": True,
            "requester": requester,
            "reply_target": reply_target,
            "first_return_hop": first_return_hop or requester,
            "raw_recipient": raw_recipient,
            "is_group": False,
            "is_relay": True,
        }

    def _auto_responder_event_key(self, requester, request_type, frequency, reply_target):
        return (
            normalize_callsign(requester),
            str(request_type or "").strip().upper(),
            self._normalize_frequency_text(frequency) or str(frequency or "").strip(),
        )

    def _auto_responder_hash_value(self, text):
        digest = hashlib.md5(str(text or "").encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _auto_responder_delay_seconds(self, request_type, requester, reply_target):
        target = str(reply_target or "").strip().upper()
        if target != "@JS8MESH":
            return 0
        base_frames = {"JR": 0, "JRN": 1, "JRS": 2}
        base_frame = base_frames.get(str(request_type or "").strip().upper(), 0)
        extra_frames = self._auto_responder_hash_value(normalize_callsign(requester) or "UNKNOWN") % 3
        jitter_seconds = 1 + (self._auto_responder_hash_value(f"{requester}|{request_type}|{reply_target}") % 4)
        return ((base_frame + extra_frames) * 15) + jitter_seconds

    def _append_auto_responder_log(self, entry):
        auto_responder_log_db.append(dict(entry or {}))
        save_auto_responder_log(auto_responder_log_db)
        if getattr(self, "auto_responder_log_window", None) is not None and self.auto_responder_log_window.has_window():
            self.refresh_auto_responder_log_window()

    def _update_auto_responder_log_status(self, event_id, status, reason="", reply_text=None, speed=None):
        target_id = str(event_id or "").strip()
        if not target_id:
            return
        updated = False
        for item in reversed(auto_responder_log_db):
            if str(item.get("event_id", "")).strip() != target_id:
                continue
            item["status"] = str(status or "").strip().upper()
            if reason:
                item["reason"] = str(reason)
            if reply_text is not None:
                item["reply_text"] = str(reply_text)
            if speed is not None:
                item["speed"] = str(speed or "").strip().upper()
            updated = True
            break
        if updated:
            save_auto_responder_log(auto_responder_log_db)
            if getattr(self, "auto_responder_log_window", None) is not None and self.auto_responder_log_window.has_window():
                self.refresh_auto_responder_log_window()

    def _recent_auto_response_exists(self, requester, request_type, frequency, reply_target, window_minutes=15):
        now_dt = self._now()
        target_key = self._auto_responder_event_key(requester, request_type, frequency, reply_target)
        for item in reversed(auto_responder_log_db):
            if str(item.get("status", "")).strip().upper() != "SENT":
                continue
            item_dt = self._safe_parse_iso_datetime(item.get("timestamp", ""))
            if item_dt is None:
                continue
            if (now_dt - item_dt).total_seconds() > (window_minutes * 60):
                continue
            item_key = self._auto_responder_event_key(
                item.get("requester", ""),
                item.get("request_type", ""),
                item.get("frequency", ""),
                item.get("reply_target", item.get("requester", "")),
            )
            if item_key == target_key:
                return True
        return False

    def _pending_auto_response_exists(self, requester, request_type, frequency, reply_target):
        target_key = self._auto_responder_event_key(requester, request_type, frequency, reply_target)
        for item in list(self._auto_responder_pending):
            item_key = self._auto_responder_event_key(
                item.get("requester", ""),
                item.get("request_type", ""),
                item.get("frequency", ""),
                item.get("reply_target", ""),
            )
            if item_key == target_key:
                return True
        return False

    def _recent_hr_response_exists(self, requester, frequency, window_minutes=20):
        now_dt = self._now()
        target_requester = normalize_callsign(requester)
        target_frequency = self._normalize_frequency_text(frequency) or str(frequency or "").strip()
        for item in reversed(hr_log_db):
            if str(item.get("status", "")).strip().upper() != "SENT":
                continue
            if normalize_callsign(item.get("requester", "")) != target_requester:
                continue
            item_frequency = self._normalize_frequency_text(item.get("frequency", "")) or str(item.get("frequency", "")).strip()
            if item_frequency != target_frequency:
                continue
            item_dt = self._safe_parse_iso_datetime(item.get("timestamp", ""))
            if item_dt is None:
                continue
            if (now_dt - item_dt).total_seconds() <= (window_minutes * 60):
                return True
        return False

    def _register_pending_find_rebroadcast(self, held_entry):
        held = dict(held_entry or {})
        find_id = str(held.get("find_id", "")).strip()
        due_text = str(held.get("rebroadcast_due_at", "")).strip()
        if not find_id or not due_text:
            return
        self._pending_find_rebroadcasts = [
            item for item in list(self._pending_find_rebroadcasts or [])
            if str(item.get("find_id", "")).strip() != find_id
        ]
        self._pending_find_rebroadcasts.append({
            "find_id": find_id,
            "due_at": due_text,
        })

    def _cancel_pending_find_rebroadcast(self, find_id):
        target_id = str(find_id or "").strip()
        if not target_id:
            return
        self._pending_find_rebroadcasts = [
            item for item in list(self._pending_find_rebroadcasts or [])
            if str(item.get("find_id", "")).strip() != target_id
        ]

    def _store_incoming_find_request(self, record, route_info, target_callsign, requester_override=None):
        requester = normalize_callsign(requester_override or route_info.get("requester", "")) or "UNKNOWN"
        target_norm = normalize_callsign(target_callsign)
        frequency_text = self._normalize_frequency_text(record.get("freq", "")) or str(record.get("freq", "")).strip()
        raw_recipient = str(route_info.get("raw_recipient", str(record.get("to", "")) or "")).strip().upper()
        is_group = bool(route_info.get("is_group", False))
        is_relay = bool(route_info.get("is_relay", False))
        if not target_norm or requester in self._own_known_callsigns():
            return True

        now_dt = self._now()
        created_at = now_dt.isoformat(timespec="seconds")
        expires_at = self._find_expiry_iso(created_at)
        find_id = self._find_search_id(requester, target_norm, frequency_text)
        existing = None
        for item in reversed(held_find_searches_db):
            if str(item.get("find_id", "")).strip() == find_id:
                existing = item
                break

        local_snr = None
        try:
            local_snr = float(record.get("snr"))
        except Exception:
            local_snr = None

        entry = {
            "event_id": str(existing.get("event_id", "")).strip() if existing else f"FIND-{int(time.time() * 1000)}",
            "find_id": find_id,
            "created_at": str(existing.get("created_at", created_at)).strip() if existing else created_at,
            "updated_at": created_at,
            "requester": requester,
            "target_callsign": target_norm,
            "frequency": frequency_text,
            "return_path": str(route_info.get("reply_target", "") or "").strip(),
            "first_return_hop": str(route_info.get("first_return_hop", "") or "").strip(),
            "original_recipient": raw_recipient,
            "request_text": str(record.get("msg", "")).strip(),
            "is_group": bool(is_group),
            "is_relay": bool(is_relay),
            "status": "ACTIVE",
            "expires_at": expires_at,
            "best_request_snr": local_snr if local_snr is not None else existing.get("best_request_snr", ""),
            "details": "Stored FIND request.",
            "rebroadcast_status": "N/A",
            "rebroadcast_due_at": "",
            "rebroadcast_sent_at": str(existing.get("rebroadcast_sent_at", "")).strip() if existing else "",
            "found_at": str(existing.get("found_at", "")).strip() if existing else "",
            "found_snr": existing.get("found_snr", "") if existing else "",
            "found_minutes": existing.get("found_minutes", "") if existing else "",
            "reply_text": str(existing.get("reply_text", "")).strip() if existing else "",
            "next_attempt_at": str(existing.get("next_attempt_at", "")).strip() if existing else "",
        }

        stronger_refresh = False
        if existing is not None and local_snr is not None:
            try:
                previous_snr = float(existing.get("best_request_snr"))
            except Exception:
                previous_snr = None
            if previous_snr is None or local_snr > previous_snr:
                stronger_refresh = True

        if is_group:
            delay_seconds = random.randint(10, 60)
            should_schedule = existing is None or stronger_refresh
            if should_schedule:
                due_at = (now_dt + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")
                entry["rebroadcast_status"] = "PENDING"
                entry["rebroadcast_due_at"] = due_at
                entry["details"] = f"Stored group FIND request. Rebroadcast scheduled in {delay_seconds}s."
            else:
                pending_due = str(existing.get("rebroadcast_due_at", "")).strip()
                pending_sent = str(existing.get("rebroadcast_sent_at", "")).strip()
                if pending_due and not pending_sent:
                    entry["rebroadcast_status"] = "CANCELED"
                    entry["rebroadcast_due_at"] = ""
                    entry["details"] = "Stored group FIND request. Local rebroadcast canceled because another copy was heard first."
                else:
                    entry["rebroadcast_status"] = str(existing.get("rebroadcast_status", "N/A")).strip() or "N/A"
                    entry["rebroadcast_due_at"] = str(existing.get("rebroadcast_due_at", "")).strip()
                    entry["details"] = "Stored group FIND request. No new rebroadcast scheduled."
        else:
            entry["details"] = "Stored direct FIND request for this node only."

        saved = self._upsert_held_find_search(entry)
        if str(saved.get("rebroadcast_status", "")).strip().upper() == "PENDING":
            self._register_pending_find_rebroadcast(saved)
        else:
            self._cancel_pending_find_rebroadcast(find_id)
        self._maybe_refresh_find_search(saved)
        return True

    def _mark_my_find_result(self, record, target_callsign, snr_value, minutes_ago):
        target_norm = normalize_callsign(target_callsign)
        frequency_text = self._normalize_frequency_text(record.get("freq", "")) or str(record.get("freq", "")).strip()
        sender = normalize_callsign(record.get("from", ""))
        now_text = self._now().isoformat(timespec="seconds")
        updated = False
        for item in reversed(my_find_searches_db):
            if normalize_callsign(item.get("target_callsign", "")) != target_norm:
                continue
            if (self._normalize_frequency_text(item.get("frequency", "")) or str(item.get("frequency", "")).strip()) != frequency_text:
                continue
            if not self._is_find_entry_active(item):
                continue
            item["status"] = "FOUND"
            item["updated_at"] = now_text
            item["found_at"] = now_text
            item["found_by"] = sender
            item["found_snr"] = snr_value
            item["found_minutes"] = minutes_ago
            item["details"] = f"{sender} reported hearing {target_norm} at {minutes_ago}m / {int(round(float(snr_value))):+d}."
            updated = True
            break
        if updated:
            self._save_my_find_searches()
        return updated

    def _maybe_refresh_find_search(self, held_entry):
        held = dict(held_entry or {})
        if not self._is_find_entry_active(held):
            return
        if str(held.get("status", "")).strip().upper() in ("SENT", "STAGED", "CANCELED"):
            return
        hearing = self._latest_local_hearing_of_callsign(
            held.get("target_callsign", ""),
            held.get("frequency", ""),
        )
        if not hearing:
            return
        held["status"] = "FOUND"
        held["updated_at"] = self._now().isoformat(timespec="seconds")
        held["found_at"] = hearing.get("heard_at", "")
        held["found_snr"] = hearing.get("snr", "")
        held["found_minutes"] = hearing.get("minutes_ago", "")
        held["details"] = (
            f"Heard {normalize_callsign(held.get('target_callsign', ''))} locally at "
            f"{hearing.get('minutes_ago', 0)}m / {int(round(float(hearing.get('snr', 0.0)))):+d}."
        )
        held["next_attempt_at"] = held["updated_at"]
        self._upsert_held_find_search(held)

    def _maybe_send_find_result(self, held_entry, force=False):
        held = dict(held_entry or {})
        current_status = str(held.get("status", "")).strip().upper()
        if current_status in ("SENT", "STAGED"):
            return
        if current_status == "CANCELED" and not bool(force):
            return
        if not self._is_find_entry_active(held):
            return
        found_at = str(held.get("found_at", "")).strip()
        if not found_at:
            return
        next_attempt_dt = self._safe_parse_iso_datetime(held.get("next_attempt_at", ""))
        if next_attempt_dt is not None and self._now() < next_attempt_dt and not bool(force):
            return
        send_attempted_at = str(held.get("send_attempted_at", "")).strip()
        if send_attempted_at and not bool(force):
            return
        requester = normalize_callsign(held.get("requester", ""))
        return_target = self._current_find_return_target(
            requester,
            held.get("return_path", ""),
            held.get("frequency", ""),
        )
        if not return_target:
            held["status"] = "FOUND"
            held["details"] = f"Target was heard, but no return path to {requester} is available yet."
            held["next_attempt_at"] = (self._now() + timedelta(minutes=5)).isoformat(timespec="seconds")
            self._upsert_held_find_search(held)
            return
        payload = self._build_findr_payload(
            held.get("target_callsign", ""),
            held.get("found_snr", 0),
            held.get("found_minutes", 0),
        )
        if not payload:
            return
        full_text = f"{return_target} {payload}".strip()
        first_hop = normalize_callsign(return_target.split(">")[0]) if ">" in return_target else normalize_callsign(return_target)
        send_mode = self._request_jr_mode_for_first_hop(first_hop) if first_hop else "NORMAL"
        if send_mode == "SLOW":
            held["status"] = "FOUND"
            held["details"] = "Target was heard, but the available return path currently falls to SLOW."
            held["next_attempt_at"] = (self._now() + timedelta(minutes=5)).isoformat(timespec="seconds")
            self._upsert_held_find_search(held)
            return
        held["reply_text"] = full_text
        held["details"] = f"Prepared FINDR back to {return_target}."
        held["send_attempted_at"] = self._now().isoformat(timespec="seconds")
        self._upsert_held_find_search(held)

        should_send = self._show_find_result_dialog(held, full_text, send_mode)
        if not should_send:
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "status": "CANCELED",
                "updated_at": self._now().isoformat(timespec="seconds"),
                "details": "FINDR was canceled by the user. Use Held Find Searches > Send Selected Now to send it later.",
                "next_attempt_at": "",
                "send_attempted_at": self._now().isoformat(timespec="seconds"),
                "reply_text": full_text,
            })
            return

        def _on_find_send_started(_result):
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "updated_at": self._now().isoformat(timespec="seconds"),
                "reply_text": full_text,
                "details": f"Sending FINDR to {return_target}...",
            })

        def _on_find_send_success(result):
            if bool(result.get("manual_send_only")):
                self._upsert_held_find_search({
                    "find_id": held.get("find_id", ""),
                    "status": "STAGED",
                    "updated_at": self._now().isoformat(timespec="seconds"),
                    "reply_text": full_text,
                    "details": "FINDR was loaded into JS8Call only. JS8Call Control is OFF.",
                    "next_attempt_at": "",
                    "send_attempted_at": self._now().isoformat(timespec="seconds"),
                })
            else:
                self._upsert_held_find_search({
                    "find_id": held.get("find_id", ""),
                    "status": "SENT",
                    "updated_at": self._now().isoformat(timespec="seconds"),
                    "reply_text": full_text,
                    "details": (
                        f"FINDR sent to {return_target} and transmission completed cleanly."
                        if bool(result.get("tx_completed"))
                        else f"FINDR was handed to JS8Call for {return_target}, but JS8Mesh could not confirm a clean TX finish."
                    ),
                    "next_attempt_at": "",
                    "send_attempted_at": self._now().isoformat(timespec="seconds"),
                })

        def _on_find_send_error(error_text):
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "status": "FOUND",
                "updated_at": self._now().isoformat(timespec="seconds"),
                "details": f"FINDR send failed: {error_text}",
                "next_attempt_at": (self._now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
                "send_attempted_at": "",
                "reply_text": full_text,
            })

        self._send_text_to_js8call_async(
            text=full_text,
            target_mode=send_mode,
            settings_key=None,
            parent_window=self.root,
            early_success_callback=_on_find_send_started,
            success_callback=_on_find_send_success,
            error_callback=_on_find_send_error,
        )

    def _send_group_find_rebroadcast(self, held_entry):
        held = dict(held_entry or {})
        if not self._is_find_entry_active(held):
            return
        if not bool(held.get("is_group", False)):
            return
        requester = normalize_callsign(held.get("requester", ""))
        target_callsign = normalize_callsign(held.get("target_callsign", ""))
        payload = f"@JS8MESH JC FIND {requester} {target_callsign}".strip()
        if not requester or not target_callsign:
            return
        self._upsert_held_find_search({
            "find_id": held.get("find_id", ""),
            "rebroadcast_status": "READY",
            "updated_at": self._now().isoformat(timespec="seconds"),
            "details": "Prepared delayed group FIND rebroadcast.",
        })
        should_send = self._show_find_rebroadcast_dialog(held, payload, "NORMAL")
        if not should_send:
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "rebroadcast_status": "CANCELED",
                "updated_at": self._now().isoformat(timespec="seconds"),
                "details": "Delayed group FIND rebroadcast was canceled by the user.",
                "rebroadcast_due_at": "",
            })
            self._cancel_pending_find_rebroadcast(held.get("find_id", ""))
            return
        self._upsert_held_find_search({
            "find_id": held.get("find_id", ""),
            "rebroadcast_status": "SENDING",
            "updated_at": self._now().isoformat(timespec="seconds"),
            "details": "Sending group FIND rebroadcast.",
        })

        def _on_rebroadcast_started(_result):
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "updated_at": self._now().isoformat(timespec="seconds"),
                "details": "Sending group FIND rebroadcast.",
            })

        def _on_rebroadcast_success(result):
            if bool(result.get("manual_send_only")):
                self._upsert_held_find_search({
                    "find_id": held.get("find_id", ""),
                    "rebroadcast_status": "STAGED",
                    "rebroadcast_due_at": "",
                    "updated_at": self._now().isoformat(timespec="seconds"),
                    "details": "Group FIND rebroadcast was loaded into JS8Call only. JS8Call Control is OFF.",
                })
            else:
                self._upsert_held_find_search({
                    "find_id": held.get("find_id", ""),
                    "rebroadcast_status": "SENT",
                    "rebroadcast_sent_at": self._now().isoformat(timespec="seconds"),
                    "rebroadcast_due_at": "",
                    "updated_at": self._now().isoformat(timespec="seconds"),
                    "details": (
                        "Group FIND rebroadcast sent and transmission completed cleanly."
                        if bool(result.get("tx_completed"))
                        else "Group FIND rebroadcast was handed to JS8Call, but JS8Mesh could not confirm a clean TX finish."
                    ),
                })
            self._cancel_pending_find_rebroadcast(held.get("find_id", ""))

        def _on_rebroadcast_error(error_text):
            self._upsert_held_find_search({
                "find_id": held.get("find_id", ""),
                "rebroadcast_status": "FAILED",
                "updated_at": self._now().isoformat(timespec="seconds"),
                "details": f"Group FIND rebroadcast failed: {error_text}",
                "rebroadcast_due_at": (self._now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
            })
            self._register_pending_find_rebroadcast({
                "find_id": held.get("find_id", ""),
                "rebroadcast_due_at": (self._now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
            })

        self._send_text_to_js8call_async(
            text=payload,
            target_mode="NORMAL",
            settings_key=None,
            parent_window=self.root,
            early_success_callback=_on_rebroadcast_started,
            success_callback=_on_rebroadcast_success,
            error_callback=_on_rebroadcast_error,
        )

    def _find_search_tick(self):
        try:
            self._find_tick_running = True
            now_dt = self._now()
            for item in list(my_find_searches_db):
                if str(item.get("status", "")).strip().upper() == "EXPIRED":
                    continue
                expires_dt = self._safe_parse_iso_datetime(item.get("expires_at", ""))
                if expires_dt is not None and now_dt >= expires_dt:
                    item["status"] = "EXPIRED"
                    item["updated_at"] = now_dt.isoformat(timespec="seconds")
                    item["details"] = "Search expired after 24 hours."
            self._save_my_find_searches()

            for item in list(held_find_searches_db):
                if str(item.get("status", "")).strip().upper() == "EXPIRED":
                    continue
                expires_dt = self._safe_parse_iso_datetime(item.get("expires_at", ""))
                if expires_dt is not None and now_dt >= expires_dt:
                    item["status"] = "EXPIRED"
                    item["updated_at"] = now_dt.isoformat(timespec="seconds")
                    item["details"] = "Held search expired after 24 hours."
                    self._cancel_pending_find_rebroadcast(item.get("find_id", ""))
                    continue
                self._maybe_refresh_find_search(item)
                self._maybe_send_find_result(item)

            pending_remaining = []
            for item in list(self._pending_find_rebroadcasts or []):
                due_dt = self._safe_parse_iso_datetime(item.get("due_at", ""))
                if due_dt is None or now_dt < due_dt:
                    pending_remaining.append(item)
                    continue
                target_id = str(item.get("find_id", "")).strip()
                held_item = None
                for candidate in reversed(held_find_searches_db):
                    if str(candidate.get("find_id", "")).strip() == target_id:
                        held_item = dict(candidate)
                        break
                if held_item is None:
                    continue
                if str(held_item.get("rebroadcast_status", "")).strip().upper() != "PENDING":
                    continue
                self._send_group_find_rebroadcast(held_item)
            self._pending_find_rebroadcasts = pending_remaining
            self._save_held_find_searches()
        finally:
            self._find_tick_running = False
            self.root.after(1000, self._find_search_tick)

    def _generate_requested_jr_reply(self, requested_kind, requester, reply_target, mode_name):
        station_count = self._safe_positive_int(
            settings.get("mesh_station_count", 5),
            5,
            minimum=1,
        )
        lines, preview_text = self._build_mesh_preview_data(
            station_count=station_count,
            lookback_minutes=self._current_requested_jr_lookback_minutes(),
            mode_name=mode_name,
            tx_limit_minutes=settings.get("mesh_tx_time_limit_minutes", 0),
            copy_to_clipboard=False,
            update_window=False,
            excluded_callsigns={normalize_callsign(requester)} if requester else None,
            report_scope=str(requested_kind or "General").strip().upper().replace(" ", "_"),
            target_prefix=reply_target or requester or "@JS8MESH",
        )
        if not lines:
            return "", []
        return str(preview_text or "").strip(), list(lines)

    def _js8call_has_pending_tx_text(self):
        try:
            with self._new_js8call_bridge() as bridge:
                tx_text = bridge.get_tx_text()
                has_text = bool(str(tx_text or "").strip())
                preview = str(tx_text or "").strip().replace("\n", " ")[:80]
                self._append_auto_responder_debug(
                    f"JS8Call TX text check: has_text={has_text} preview='{preview}'"
                )
                return has_text
        except Exception as exc:
            self._append_auto_responder_debug(
                f"JS8Call TX text check failed: {exc}"
            )
            return True

    def _format_assisted_request_record(self, record):
        sender = str(record.get("from", "") or "").strip().upper()
        recipient = str(record.get("to", "") or "").strip().upper()
        message_text = str(record.get("msg", "") or "").strip()
        if sender and recipient:
            return f"{sender}: {recipient} {message_text}".strip()
        return message_text

    def _assisted_request_type_label(self, request_type, reply_target):
        req = str(request_type or "").strip().upper()
        target = str(reply_target or "").strip().upper()
        if target == "@JS8MESH":
            return f"{req} @JS8MESH type"
        return req or "Unknown"

    def _center_toplevel_on_screen(self, window, width=760, height=420):
        try:
            window.update_idletasks()
            screen_w = int(window.winfo_screenwidth() or width)
            screen_h = int(window.winfo_screenheight() or height)
            pos_x = max(0, int((screen_w - width) / 2))
            pos_y = max(0, int((screen_h - height) / 2))
            window.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        except Exception:
            pass

    def _show_assisted_send_dialog(self, title_text, body_text, footer_text, confirm_label="Send to JS8Call", cancel_label="Cancel"):
        return self._show_send_confirmation_dialog(
            dialog_title="Assisted Response",
            title_text=title_text,
            body_text=body_text,
            footer_text=footer_text,
            parent_widget=self.root,
            confirm_label=confirm_label,
            cancel_label=cancel_label,
        )

    def _js8call_send_effects_text(self, target_mode):
        effective_mode = normalize_mesh_mode(target_mode)
        allow_auto_send = bool(settings.get("js8call_allow_auto_send", False))
        if allow_auto_send:
            return (
                f"JS8Mesh will try to change JS8Call speed mode to {effective_mode.title()} before sending.\n"
                "After transmission, JS8Mesh will try to restore the previous JS8Call speed mode."
            )
        return (
            "JS8Call Control is set to NO.\n"
            f"JS8Mesh will only load the text into JS8Call. It will not press Send.\n"
            f"If needed, change JS8Call to {effective_mode.title()} manually before transmitting."
        )

    def _show_send_confirmation_dialog(
        self,
        dialog_title,
        title_text,
        body_text,
        footer_text,
        parent_widget=None,
        confirm_label="Send to JS8Call",
        cancel_label="Cancel",
    ):
        result = {"continue": False}
        parent_widget = parent_widget or self.root
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        try:
            if parent_widget.state() == "iconic":
                parent_widget.deiconify()
            parent_widget.lift()
        except Exception:
            pass
        dialog = tk.Toplevel(parent_widget)
        dialog.title(dialog_title)
        dialog.configure(bg=self.bg_color)
        dialog.resizable(True, True)
        try:
            if parent_widget.state() != "iconic":
                dialog.transient(parent_widget)
        except Exception:
            pass
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text=title_text,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=700,
            font=("TkDefaultFont", 12, "bold"),
        ).pack(fill="x", pady=(0, 14))

        message_box = tk.Text(
            outer,
            height=8,
            wrap="word",
            bg="#111111",
            fg=self.fg_color,
            insertbackground=self.fg_color,
        )
        message_box.pack(fill="both", expand=True)
        message_box.insert("1.0", str(body_text or "").strip())
        message_box.configure(state="disabled")

        footer_label = tk.Label(
            outer,
            text=footer_text,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=700,
        )
        footer_label.pack(fill="x", pady=(14, 4))

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x")

        def do_ok():
            result["continue"] = True
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        def do_cancel():
            result["continue"] = False
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        ok_button = tk.Button(button_row, text=confirm_label, command=do_ok, bg=self.highlight_color, fg=self.fg_color, width=12)
        ok_button.pack(side="left", padx=(0, 8))
        tk.Button(button_row, text=cancel_label, command=do_cancel, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left")

        def _do_ok_event(_event=None):
            do_ok()
            return "break"

        def _do_cancel_event(_event=None):
            do_cancel()
            return "break"

        dialog.bind("<Return>", _do_ok_event)
        dialog.bind("<KP_Enter>", _do_ok_event)
        dialog.bind("<Escape>", _do_cancel_event)
        dialog.protocol("WM_DELETE_WINDOW", do_cancel)
        self._center_toplevel_on_screen(dialog, width=780, height=430)
        dialog.deiconify()
        dialog.lift()
        try:
            dialog.attributes("-topmost", True)
            dialog.after(1000, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
        except Exception:
            pass
        try:
            ok_button.focus_set()
            dialog.focus_force()
        except Exception:
            pass
        self.root.wait_window(dialog)
        return bool(result.get("continue", False))

    def _queue_auto_jr_reply(self, record, requested_kind, request_type, requester, reply_target):
        return self._queue_auto_jr_reply_with_first_hop(
            record,
            requested_kind,
            request_type,
            requester,
            reply_target,
            requester,
        )

    def _queue_auto_jr_reply_with_first_hop(self, record, requested_kind, request_type, requester, reply_target, first_return_hop):
        frequency_text = self._normalize_frequency_text(record.get("freq", "")) or str(record.get("freq", "")).strip()
        event_id = f"AUTOJR-{int(time.time() * 1000)}-{len(self._auto_responder_pending)}"
        incoming_message = self._format_assisted_request_record(record)
        default_mode = self._request_jr_mode_for_first_hop(first_return_hop, max_age_minutes=self._current_max_age_minutes())
        effective_mode = str(default_mode or "NORMAL").strip().upper() or "NORMAL"
        if effective_mode not in ("TURBO", "FAST", "NORMAL", "SLOW"):
            effective_mode = "NORMAL"
        self._append_auto_responder_debug(
            f"Queue request event_id={event_id} requester={requester} type={request_type} "
            f"kind={requested_kind} target={reply_target} first_hop={first_return_hop} "
            f"freq={frequency_text} default_mode={effective_mode}"
        )

        if self._recent_auto_response_exists(requester, request_type, frequency_text, reply_target, window_minutes=15):
            self._append_auto_responder_debug(
                f"Queue skipped event_id={event_id}: duplicate sent in last 15 minutes."
            )
            self._append_auto_responder_log({
                "event_id": event_id,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": request_type,
                "frequency": frequency_text,
                "reply_target": reply_target,
                "reply_text": "",
                "speed": effective_mode,
                "status": "SKIPPED",
                "reason": "Skipped: same requester/type/frequency was already answered in the past 15 minutes.",
            })
            return True
        if self._pending_auto_response_exists(requester, request_type, frequency_text, reply_target):
            self._append_auto_responder_debug(
                f"Queue skipped event_id={event_id}: matching response already pending."
            )
            self._append_auto_responder_log({
                "event_id": event_id,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": request_type,
                "frequency": frequency_text,
                "reply_target": reply_target,
                "reply_text": "",
                "speed": "",
                "status": "SKIPPED",
                "reason": "Skipped: matching assisted response is already queued.",
            })
            return True

        preview_text, lines = self._generate_requested_jr_reply(requested_kind, requester, reply_target, effective_mode)
        self._append_auto_responder_debug(
            f"Generated reply event_id={event_id}: lines={len(lines)} preview='{str(preview_text or '')[:160]}'"
        )
        if not lines or not preview_text:
            self._append_auto_responder_debug(
                f"Queue skipped event_id={event_id}: no JR could be generated."
            )
            self._append_auto_responder_log({
                "event_id": event_id,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": request_type,
                "frequency": frequency_text,
                "reply_target": reply_target,
                "reply_text": "",
                "speed": effective_mode,
                "status": "SKIPPED",
                    "reason": "Skipped: no Report can be generated at the moment.",
            })
            return True

        if effective_mode == "SLOW":
            self._append_auto_responder_debug(
                f"Queue skipped event_id={event_id}: default mode resolved to SLOW."
            )
            self._append_auto_responder_log({
                "event_id": event_id,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": request_type,
                "frequency": frequency_text,
                "reply_target": reply_target,
                "reply_text": preview_text,
                "speed": effective_mode,
                "status": "SKIPPED",
                "reason": "Skipped: calculated default speed is SLOW.",
            })
            return True

        delay_seconds = self._auto_responder_delay_seconds(request_type, requester, reply_target)
        due_dt = self._now() + timedelta(seconds=delay_seconds)
        pending_item = {
            "event_id": event_id,
            "requester": requester,
            "request_type": request_type,
            "requested_kind": requested_kind,
            "reply_target": reply_target,
            "frequency": frequency_text,
            "reply_text": preview_text,
            "speed": effective_mode,
            "due_at": due_dt.isoformat(timespec="seconds"),
            "created_at": self._now().isoformat(timespec="seconds"),
            "incoming_request_text": incoming_message,
        }
        self._auto_responder_pending.append(pending_item)
        self._append_auto_responder_debug(
            f"Queued event_id={event_id}: delay={delay_seconds}s due_at={pending_item['due_at']}"
        )
        self._append_auto_responder_log({
            "event_id": event_id,
            "timestamp": pending_item["created_at"],
            "requester": requester,
            "request_type": request_type,
            "frequency": frequency_text,
            "reply_target": reply_target,
            "reply_text": preview_text,
            "speed": effective_mode,
            "status": "QUEUED",
            "reason": f"Queued for assisted send in {delay_seconds}s.",
        })
        return True

    def _auto_responder_tick(self):
        try:
            now_dt = self._now()
            remaining = []
            for item in list(self._auto_responder_pending):
                event_id = str(item.get("event_id", "")).strip()
                due_dt = self._safe_parse_iso_datetime(item.get("due_at", ""))
                if due_dt is None or now_dt < due_dt:
                    self._append_auto_responder_debug(
                        f"Tick keep queued event_id={event_id}: now={now_dt.isoformat(timespec='seconds')} due_at={item.get('due_at', '')}"
                    )
                    remaining.append(item)
                    continue
                if self._recent_auto_response_exists(
                    item.get("requester", ""),
                    item.get("request_type", ""),
                    item.get("frequency", ""),
                    item.get("reply_target", ""),
                    window_minutes=15,
                ):
                    self._append_auto_responder_debug(
                        f"Tick skipped event_id={event_id}: duplicate already sent within 15 minutes."
                    )
                    self._update_auto_responder_log_status(
                        item.get("event_id", ""),
                        "SKIPPED",
                        "Skipped: duplicate request was already answered in the past 15 minutes.",
                    )
                    continue
                if self._js8call_has_pending_tx_text():
                    self._append_auto_responder_debug(
                        f"Tick keep queued event_id={event_id}: JS8Call TX text is not empty."
                    )
                    self._update_auto_responder_log_status(
                        item.get("event_id", ""),
                        "QUEUED",
                        "Queued: waiting for JS8Call TX text to become empty.",
                    )
                    remaining.append(item)
                    continue

                self._append_auto_responder_debug(
                    f"Awaiting assisted send confirmation event_id={event_id}: "
                    f"text='{str(item.get('reply_text', '') or '')[:160]}'"
                )
                dialog_body = (
                    f"Station requesting report: {str(item.get('requester', '') or '').strip()}\n\n"
                    f"Received request message:\n{str(item.get('incoming_request_text', '') or '').strip()}\n\n"
                    f"Request type: {self._assisted_request_type_label(item.get('request_type', ''), item.get('reply_target', ''))}\n\n"
                    f"Generated message ready to send to JS8Call:\n{str(item.get('reply_text', '') or '').strip()}"
                )
                if not self._show_assisted_send_dialog(
                    title_text="JS8Mesh has generated this response to a requested report.",
                    body_text=dialog_body,
                    footer_text=(
                        f"{self._js8call_send_effects_text(str(item.get('speed', '') or 'NORMAL'))}\n\n"
                        "Press Send to JS8Call to continue or Cancel to abort."
                    ),
                    confirm_label="Send to JS8Call",
                    cancel_label="Cancel",
                ):
                    self._append_auto_responder_debug(
                        f"Tick canceled event_id={event_id}: user canceled assisted send."
                    )
                    self._update_auto_responder_log_status(
                        item.get("event_id", ""),
                        "SKIPPED",
                        "Skipped: user canceled assisted send.",
                    )
                    continue

                self._append_auto_responder_debug(
                    f"Tick sending event_id={event_id}: target={item.get('reply_target', '')} speed={item.get('speed', '')} "
                    f"text='{str(item.get('reply_text', '') or '')[:160]}'"
                )

                def _on_success(_result, eid=event_id):
                    self._append_auto_responder_debug(
                        f"Send success event_id={eid}"
                    )
                    if bool(_result.get("tx_started")) and not bool(_result.get("tx_completed")):
                        self._update_auto_responder_log_status(
                            eid,
                            "SENT",
                            "Sent after assisted confirmation, but JS8Mesh could not confirm that JS8Call finished transmitting cleanly.",
                        )
                    else:
                        self._update_auto_responder_log_status(eid, "SENT", "Sent after assisted confirmation.")

                def _on_error(error_text, eid=event_id):
                    self._append_auto_responder_debug(
                        f"Send failed event_id={eid}: {error_text}"
                    )
                    self._update_auto_responder_log_status(eid, "SKIPPED", f"Send failed: {error_text}")

                self._send_text_to_js8call_async(
                    text=item.get("reply_text", ""),
                    target_mode=item.get("speed", "NORMAL"),
                    settings_key=None,
                    parent_window=None,
                    success_callback=_on_success,
                    error_callback=_on_error,
                    silent_failure=True,
                    skip_speed_warning_dialog=True,
                )
            self._auto_responder_pending = remaining
        finally:
            self.root.after(1000, self._auto_responder_tick)

    def _close_requested_jr_window(self):
        try:
            if self.requested_jr_window is not None and self.requested_jr_window.winfo_exists():
                self.requested_jr_window.destroy()
        except Exception:
            pass
        self.requested_jr_window = None
        self.requested_jr_preview_widget = None
        self.requested_jr_lookback_entry = None
        self.requested_jr_requester_callsign = ""
        self.requested_jr_request_path_var.set("")
        self.requested_jr_requested_target_var.set("")
        self.requested_jr_reply_target = ""
        self.requested_jr_first_hop = ""
        self.requested_jr_requested_target_callsign = ""
        self.requested_jr_response_log_event_id = ""
        self.requested_jr_hr_log_event_id = ""
        self.requested_jr_hr_limit_blocked = False

    def _current_requested_jr_lookback_minutes(self):
        value = self._safe_positive_int(
            self.requested_jr_saved_lookback_minutes,
            15,
            minimum=1,
        )
        if self.ignore_freshness_var.get():
            return value
        return min(50, max(15, value))

    def _current_requested_jr_send_mode(self):
        selected = str(self.requested_jr_mode_var.get() or "").strip().upper()
        if selected in ("TURBO", "FAST", "NORMAL"):
            return selected
        return self._requested_jr_default_mode()

    def _update_requested_jr_mode_states(self):
        group_reply = str(self.requested_jr_reply_target or "").strip().upper() == "@JS8MESH"
        if group_reply:
            self.requested_jr_mode_var.set("DEFAULT")
        for button in list(self.requested_jr_mode_buttons or []):
            try:
                text = str(button.cget("text") or "").strip().upper()
            except Exception:
                text = ""
            if group_reply:
                try:
                    button.configure(
                        state="normal" if text == "DEFAULT" else "disabled",
                        disabledforeground="#888888"
                    )
                except Exception:
                    pass
            else:
                try:
                    button.configure(state="normal", disabledforeground="#888888")
                except Exception:
                    pass

    def _update_requested_jr_lookback_help(self):
        raw_value = str(self.requested_jr_lookback_var.get() or "").strip()
        if self.ignore_freshness_var.get():
            self.requested_jr_lookback_help_var.set(
                "Test Mode ON: choose any positive look back period."
            )
        else:
            self.requested_jr_lookback_help_var.set(
                "Test Mode OFF: enter a look back period from 15 to 50 minutes, then press Save."
            )

    def _save_requested_jr_lookback(self):
        raw_value = str(self.requested_jr_lookback_var.get() or "").strip()
        if self.ignore_freshness_var.get():
            try:
                parsed = int(raw_value)
            except (ValueError, TypeError):
                parsed = None
            if parsed is None or parsed <= 0:
                self.requested_jr_lookback_var.set("")
                self._dark_info_dialog(
                    "Invalid Look Back Period",
                    "Enter a positive look back period in minutes.",
                    parent=self.requested_jr_window if self.requested_jr_window is not None else None,
                    refocus_widget=self.requested_jr_lookback_entry if self.requested_jr_lookback_entry is not None else self.requested_jr_window,
                )
                return False
            self.requested_jr_saved_lookback_minutes = parsed
            self.requested_jr_lookback_var.set(str(parsed))
        else:
            try:
                parsed = int(raw_value)
            except (ValueError, TypeError):
                parsed = None
            if parsed is None or parsed < 15 or parsed > 50:
                self.requested_jr_lookback_var.set("")
                self._dark_info_dialog(
                    "Invalid Look Back Period",
                    "Enter a value from 15 to 50 minutes.",
                    parent=self.requested_jr_window if self.requested_jr_window is not None else None,
                    refocus_widget=self.requested_jr_lookback_entry if self.requested_jr_lookback_entry is not None else self.requested_jr_window,
                )
                return False
            self.requested_jr_saved_lookback_minutes = parsed
            self.requested_jr_lookback_var.set(str(parsed))

        self._update_requested_jr_lookback_help()
        self._update_requested_jr_preview()
        return True

    def _on_requested_jr_input_changed(self, *_args):
        self._update_requested_jr_lookback_help()

    def _copy_requested_jr_preview(self):
        preview_widget = self.requested_jr_preview_widget
        if preview_widget is None:
            return
        try:
            if not preview_widget.winfo_exists():
                self.requested_jr_preview_widget = None
                return
        except Exception:
            self.requested_jr_preview_widget = None
            return

        try:
            selected = preview_widget.cget("text")
        except Exception:
            selected = ""
        selected = str(selected or "").strip()
        if not selected:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        self.root.update()

    def _show_requested_jr_preview_context_menu(self, event):
        preview_widget = self.requested_jr_preview_widget
        if preview_widget is None:
            return
        try:
            if not preview_widget.winfo_exists():
                self.requested_jr_preview_widget = None
                return
        except Exception:
            self.requested_jr_preview_widget = None
            return

        menu = tk.Menu(preview_widget, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_requested_jr_preview)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _build_mesh_preview_data(self, station_count, lookback_minutes, mode_name=None, tx_limit_minutes=None,
                                 copy_to_clipboard=False, update_window=True, excluded_callsigns=None,
                                 report_scope="GENERAL", target_callsign=None, target_prefix="@JS8MESH"):
        raw_target_callsign = str(target_callsign or "").strip()
        if raw_target_callsign.upper() in ("", "NONE", "NULL"):
            target_callsign = None
        lines = self.generate_mesh_broadcast_lines(
            station_count=station_count,
            lookback_minutes=lookback_minutes,
            excluded_callsigns=excluded_callsigns,
            report_scope=report_scope,
            target_callsign=target_callsign,
        )
        effective_mode = str(mode_name or self._mesh_preview_mode_name()).strip().upper() or "NORMAL"
        effective_limit = self._current_mesh_tx_time_limit_minutes() if tx_limit_minutes is None else self._safe_positive_int(
            tx_limit_minutes,
            settings.get("mesh_tx_time_limit_minutes", 0),
            minimum=0,
        )
        lines = self._apply_mesh_tx_time_limit(
            lines,
            effective_mode,
            effective_limit,
        )

        self.last_mesh_broadcast_time = datetime.now()
        self.last_mesh_broadcast_lines = list(lines)

        if lines:
            prefix_text = str(target_prefix or "@JS8MESH").strip()
            preview_text = f"{prefix_text} " + ";".join(lines)
        else:
            preview_text = (
                "No mesh report lines could be generated.\n\n"
                "Check that:\n"
                "- your User Callsign is set\n"
                "- recent records exist\n"
                "- recent records contain numeric SNR values"
            )

        if update_window:
            self.mesh_network_window.set_preview_text(preview_text)
            self._update_mesh_tx_estimate(preview_text)
            self._update_mesh_status_text()

        if lines and copy_to_clipboard:
            self.root.clipboard_clear()
            self.root.clipboard_append(preview_text)
            self.root.update()

        return lines, preview_text

    def _generate_requested_hr_reply(self, requester, reply_target):
        lookback_minutes = self._current_requested_jr_lookback_minutes()
        effective_mode = self._current_requested_jr_send_mode()
        tx_limit_minutes = self._safe_positive_int(
            settings.get("mesh_tx_time_limit_minutes", 0),
            0,
            minimum=0,
        )
        self.requested_jr_hr_limit_blocked = False
        excluded_norms = {
            normalize_callsign(item)
            for item in (requester,)
            if normalize_callsign(item)
        }
        summaries = []
        for item in self._hr_candidate_summaries(lookback_minutes, excluded_callsigns=excluded_norms):
            heard_station = normalize_callsign(item.get("heard_station", ""))
            if not heard_station or heard_station in excluded_norms:
                continue
            summaries.append(dict(item))
            if len(summaries) >= 4:
                break

        if not summaries:
            node_calls = self._mesh_known_node_calls()
            for item in self._mesh_station_summaries(lookback_minutes):
                heard_station = normalize_callsign(item.get("heard_station", ""))
                if not heard_station or heard_station in excluded_norms:
                    continue
                try:
                    snr_value = float(item.get("snr", -30.0))
                except Exception:
                    continue
                category = snr_category_from_reported(snr_value)
                if category not in ("TURBO", "FAST"):
                    continue
                summaries.append({
                    "heard_station": heard_station,
                    "snr": snr_value,
                    "minutes_ago": int(item.get("minutes_ago", 0) or 0),
                    "is_node": heard_station in node_calls,
                    "category": category,
                })
                if len(summaries) >= 4:
                    break

        lines = []
        for item in summaries:
            heard_norm = normalize_callsign(item.get("heard_station", ""))
            try:
                snr_text = f"{int(round(float(item.get('snr', 0.0)))):+d}"
            except Exception:
                snr_text = "+0"
            minutes_text = str(max(0, int(item.get("minutes_ago", 0) or 0)))
            heard_token = str(item.get('heard_station', '')).strip().upper()
            if bool(item.get("is_node")):
                heard_token = f"*{heard_token}"
            entry_text = f"1.{heard_token}.{snr_text}.{minutes_text}"
            if not lines:
                lines.append(f"HR.{entry_text}")
            else:
                lines.append(entry_text)

        if lines:
            prefix_text = str(reply_target or requester or "").strip()
            limited_lines = list(lines)
            if tx_limit_minutes > 0:
                while limited_lines:
                    limited_preview = f"{prefix_text} " + ";".join(limited_lines)
                    estimated_seconds = estimate_mesh_report_seconds(limited_preview, effective_mode)
                    if estimated_seconds <= (tx_limit_minutes * 60):
                        break
                    limited_lines.pop()
            lines = limited_lines
            if not lines:
                self.requested_jr_hr_limit_blocked = True

        if lines:
            preview_text = f"{prefix_text} " + ";".join(lines)
        else:
            preview_text = ""

        return lines, preview_text

    def _generate_requested_hrc_reply(self, target_callsign, reply_target):
        target_norm = normalize_callsign(target_callsign)
        source_station = self.user_call_var.get().strip().upper()
        if not source_station or not target_norm:
            return [], ""

        lookback_minutes = self._current_requested_jr_lookback_minutes()
        max_relays = self._safe_positive_int(self.max_hops_var.get(), 3, minimum=0)
        tx_limit_minutes = self._safe_positive_int(
            settings.get("mesh_tx_time_limit_minutes", 0),
            0,
            minimum=0,
        )
        frequency_records = self._records_for_selected_frequency()
        direct_evidence_rows = direct_path_evidence(
            frequency_records,
            source_station,
            target_norm,
            max_age_minutes=lookback_minutes,
        )
        if not direct_evidence_rows:
            return [], ""
        latest_evidence = direct_evidence_rows[0] if direct_evidence_rows else {}
        try:
            snr_value = float(latest_evidence.get("used_snr", -30.0))
        except Exception:
            snr_value = -30.0
        minutes_ago = max(0, int(latest_evidence.get("age_minutes", 0) or 0))

        node_calls = self._mesh_known_node_calls()
        target_token = target_norm
        if target_norm in node_calls:
            target_token = f"*{target_token}"

        snr_text = f"{int(round(snr_value)):+d}"
        line = f"JR.1.{target_token}.{snr_text}.{minutes_ago}"

        lines = [line]
        effective_mode = self._current_requested_jr_send_mode()
        prefix_text = str(reply_target or target_norm).strip()
        if tx_limit_minutes > 0:
            estimated_seconds = estimate_mesh_report_seconds(f"{prefix_text} {line}", effective_mode)
            if estimated_seconds > (tx_limit_minutes * 60):
                return [], ""

        return lines, f"{prefix_text} {line}"

    def _update_requested_jr_preview(self):
        preview_widget = self.requested_jr_preview_widget
        if preview_widget is None:
            return "", []
        try:
            if not preview_widget.winfo_exists():
                self.requested_jr_preview_widget = None
                return "", []
        except Exception:
            self.requested_jr_preview_widget = None
            return "", []
        self._update_requested_jr_mode_states()

        station_count = self._safe_positive_int(
            settings.get("mesh_station_count", 5),
            5,
            minimum=1,
        )
        lookback_minutes = self._current_requested_jr_lookback_minutes()
        requester = normalize_callsign(self.requested_jr_requester_callsign)
        kind_key = str(self.requested_jr_kind_var.get() or "General").strip().upper().replace(" ", "_")
        if kind_key == "HEARD_4_STATIONS":
            lines, preview_text = self._generate_requested_hr_reply(
                requester=self.requested_jr_requester_callsign,
                reply_target=self.requested_jr_reply_target or requester or "",
            )
            if not lines:
                if self.requested_jr_hr_limit_blocked:
                    preview_text = "No HR reply fits within the Report TX time limit at this speed."
                else:
                    preview_text = "No HR can be generated at the moment."
        elif kind_key == "CAN_RELAY_TO_CALLSIGN":
            lines, preview_text = self._generate_requested_hrc_reply(
                target_callsign=self.requested_jr_requested_target_callsign,
                reply_target=self.requested_jr_reply_target or requester or "",
            )
            if not lines:
                if self.requested_jr_requested_target_callsign:
                    preview_text = f"No Report can be generated about {self.requested_jr_requested_target_callsign} at the moment."
                else:
                    preview_text = "No Report can be generated at the moment."
        else:
            lines, preview_text = self._build_mesh_preview_data(
                station_count=station_count,
                lookback_minutes=lookback_minutes,
                mode_name=self._current_requested_jr_send_mode(),
                tx_limit_minutes=settings.get("mesh_tx_time_limit_minutes", 0),
                copy_to_clipboard=False,
                update_window=False,
                excluded_callsigns={requester} if requester else None,
                report_scope=kind_key,
                target_callsign=None,
                target_prefix=self.requested_jr_reply_target or requester or "@JS8MESH",
            )
        self._append_auto_responder_debug(
            "Requested Report preview generated: "
            f"kind={kind_key} requester={requester} reply_target={self.requested_jr_reply_target or requester or ''} "
            f"lookback={lookback_minutes} mode={self._current_requested_jr_send_mode()} "
            f"default_mode={self._requested_jr_default_mode()} lines_count={len(lines)} "
            f"preview='{str(preview_text or '')[:200]}'"
        )
        if not lines:
            preview_text = "No Report can be generated at the moment."
        try:
            preview_widget.configure(text=preview_text)
        except Exception:
            self.requested_jr_preview_widget = None
        requester = normalize_callsign(self.requested_jr_requester_callsign)
        if requester:
            self.requested_jr_default_mode_var.set(
                f"Default Speed Mode: {self._requested_jr_default_mode()}"
            )
        else:
            self.requested_jr_default_mode_var.set("Default Speed Mode: n/a")
        self.requested_jr_send_effects_var.set(
            self._js8call_send_effects_text(self._current_requested_jr_send_mode())
        )
        seconds = estimate_mesh_report_seconds(preview_text, self._current_requested_jr_send_mode())
        if lines and seconds > 0:
            self.requested_jr_estimated_tx_var.set(f"Estimated TX Time: {format_duration(seconds)}")
        else:
            if kind_key == "HEARD_4_STATIONS" and self.requested_jr_hr_limit_blocked:
                self.requested_jr_estimated_tx_var.set("Estimated TX Time: exceeds Report TX time limit at this speed")
            else:
                self.requested_jr_estimated_tx_var.set("Estimated TX Time: n/a")
        return preview_text, lines

    def _requested_jr_request_type_code(self):
        kind_text = str(self.requested_jr_kind_var.get() or "").strip().upper()
        if kind_text == "NODES ONLY":
            return "JRN"
        if kind_text == "STATIONS ONLY":
            return "JRS"
        if kind_text == "CAN RELAY TO CALLSIGN":
            return "HRC"
        if kind_text == "HEARD 4 STATIONS":
            return "HR"
        if kind_text == "FIND CALLSIGN":
            return "FIND"
        return "JR"

    def _append_requested_jr_response_log(self, preview_text, speed, status, reason, event_id=None):
        event_id = str(event_id or "").strip() or f"REQJR-{int(time.time() * 1000)}"
        self._append_auto_responder_log({
            "event_id": event_id,
            "timestamp": self._now().isoformat(timespec="seconds"),
            "requester": normalize_callsign(self.requested_jr_requester_callsign) or "",
            "request_type": self._requested_jr_request_type_code(),
            "frequency": str(self.selected_frequency_var.get() or "").strip(),
            "reply_target": str(self.requested_jr_reply_target or "").strip(),
            "reply_text": str(preview_text or "").strip(),
            "speed": str(speed or "").strip().upper(),
            "status": str(status or "").strip().upper(),
            "reason": str(reason or ""),
        })
        return event_id

    def _show_requested_jr_window(self):
        if self.requested_jr_window is not None:
            try:
                if self.requested_jr_window.winfo_exists():
                    self.requested_jr_window.deiconify()
                    self.requested_jr_window.lift()
                    try:
                        self.requested_jr_window.attributes("-topmost", True)
                        self.requested_jr_window.after(
                            1000,
                            lambda: self.requested_jr_window.attributes("-topmost", False)
                            if self.requested_jr_window is not None and self.requested_jr_window.winfo_exists()
                            else None
                        )
                    except Exception:
                        pass
                    self.requested_jr_window.focus_force()
                    self._update_requested_jr_preview()
                    return
            except Exception:
                pass
            self.requested_jr_window = None
            self.requested_jr_preview_widget = None
            self.requested_jr_lookback_entry = None

        dialog = tk.Toplevel(self.root)
        dialog.title("Requested Report")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        self.requested_jr_window = dialog

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=self.bg_color)
        header.pack(fill="x")

        tk.Label(
            header,
            text="Requested Report:",
            bg=self.bg_color,
            fg="#ff4444",
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 11, "bold")
        ).pack(side="left")

        tk.Label(
            header,
            textvariable=self.requested_jr_kind_var,
            bg=self.bg_color,
            fg="#ff4444",
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 11, "bold")
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            outer,
            textvariable=self.requested_jr_requester_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(anchor="w", pady=(10, 0))

        tk.Label(
            outer,
            textvariable=self.requested_jr_request_path_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            outer,
            textvariable=self.requested_jr_requested_target_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

        mode_row = tk.Frame(outer, bg=self.bg_color)
        mode_row.pack(fill="x", pady=(12, 0))

        tk.Label(
            mode_row,
            text="Send mode:",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", padx=(0, 8))

        self.requested_jr_mode_buttons = []
        for mode_name in ("DEFAULT", "TURBO", "FAST", "NORMAL"):
            mode_button = tk.Radiobutton(
                mode_row,
                text=mode_name.title(),
                variable=self.requested_jr_mode_var,
                value=mode_name,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
                anchor="w",
                justify="left",
                command=self._update_requested_jr_preview,
            )
            mode_button.pack(side="left", padx=(0, 8))
            self.requested_jr_mode_buttons.append(mode_button)

        lookback_row = tk.Frame(outer, bg=self.bg_color)
        lookback_row.pack(fill="x", pady=(12, 0))

        tk.Label(
            lookback_row,
            text="Look back period (minutes):",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", padx=(0, 8))

        lookback_entry = tk.Entry(
            lookback_row,
            textvariable=self.requested_jr_lookback_var,
            width=8
        )
        lookback_entry.pack(side="left")
        self.requested_jr_lookback_entry = lookback_entry
        lookback_entry.bind("<Return>", lambda _event: self._save_requested_jr_lookback())
        lookback_entry.bind("<KP_Enter>", lambda _event: self._save_requested_jr_lookback())

        tk.Button(
            lookback_row,
            text="Save",
            command=self._save_requested_jr_lookback,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            outer,
            textvariable=self.requested_jr_lookback_help_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=520
        ).pack(anchor="w", pady=(6, 0))

        estimate_row = tk.Frame(outer, bg=self.bg_color)
        estimate_row.pack(fill="x", pady=(6, 0))

        tk.Label(
            estimate_row,
            textvariable=self.requested_jr_estimated_tx_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left")

        tk.Label(
            estimate_row,
            textvariable=self.requested_jr_default_mode_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", padx=(16, 0))

        tk.Label(
            outer,
            textvariable=self.requested_jr_send_effects_var,
            bg=self.bg_color,
            fg="#ffcc66",
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(8, 0))

        preview_frame = tk.LabelFrame(
            outer,
            text="Requested Report Preview",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=10,
            pady=10
        )
        preview_frame.pack(fill="both", expand=True, pady=(12, 0))

        preview_text = tk.Label(
            preview_frame,
            text="",
            width=72,
            height=10,
            bg="#ffffff",
            fg="#000000",
            anchor="nw",
            justify="left",
            wraplength=560,
            padx=6,
            pady=6,
        )
        preview_text.pack(side="left", fill="both", expand=True)
        self.requested_jr_preview_widget = preview_text
        preview_text.bind("<Button-3>", self._show_requested_jr_preview_context_menu)
        preview_text.bind("<Control-c>", lambda _event: self._copy_requested_jr_preview())

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(14, 0))

        def do_send():
            preview_text_value, lines = self._update_requested_jr_preview()
            preview_text_value = str(preview_text_value or "").strip()
            if not preview_text_value:
                self._dark_info_dialog(
                    "No Mesh Report Data",
                    "There is no response text to send for this request.",
                    parent=dialog,
                    refocus_widget=dialog,
                )
                return
            selected_mode = str(self.requested_jr_mode_var.get() or "").strip().upper()
            effective_mode = self._current_requested_jr_send_mode()
            if selected_mode == "DEFAULT" and effective_mode == "SLOW":
                self._dark_info_dialog(
                "Requested Report Default Speed",
                    (
                        "The default speed for this report was calculated as SLOW.\n\n"
                        "Transmission has been aborted.\n\n"
                        "If you still want to try sending this report, choose Turbo, Fast, or Normal manually."
                    ),
                    parent=dialog,
                    refocus_widget=dialog,
                )
                return
            log_event_id = str(self.requested_jr_response_log_event_id or "").strip()
            if log_event_id:
                self._update_auto_responder_log_status(
                    log_event_id,
                    "QUEUED",
                    "Requested Report response queued for send to JS8Call.",
                    reply_text=preview_text_value,
                )
            else:
                log_event_id = self._append_requested_jr_response_log(
                    preview_text=preview_text_value,
                    speed=effective_mode,
                    status="QUEUED",
                    reason="Requested Report response queued for send to JS8Call.",
                )
            hr_event_id = str(self.requested_jr_hr_log_event_id or "").strip()
            if hr_event_id:
                self._update_hr_log_status(
                    hr_event_id,
                    "QUEUED",
                    "HR response queued for send to JS8Call.",
                    reply_text=preview_text_value,
                    speed=effective_mode,
                )
            def _on_requested_jr_send_triggered(_result):
                try:
                    self._close_requested_jr_window()
                except Exception:
                    pass
            def _on_requested_jr_send_success(result):
                if bool(result.get("manual_send_only")):
                    self._update_auto_responder_log_status(
                        log_event_id,
                        "STAGED",
                        "Requested Report response was loaded into JS8Call only. JS8Call Control is OFF.",
                    )
                    if hr_event_id:
                        self._update_hr_log_status(
                            hr_event_id,
                            "STAGED",
                            "HR response was loaded into JS8Call only. JS8Call Control is OFF.",
                            reply_text=preview_text_value,
                            speed=effective_mode,
                        )
                    try:
                        self._close_requested_jr_window()
                    except Exception:
                        pass
                    return
                if bool(result.get("tx_completed")):
                    self._update_auto_responder_log_status(
                        log_event_id,
                        "SENT",
                        "Requested Report response was sent to JS8Call and transmission completed cleanly.",
                    )
                    if hr_event_id:
                        self._update_hr_log_status(
                            hr_event_id,
                            "SENT",
                            "HR response was sent to JS8Call and transmission completed cleanly.",
                            reply_text=preview_text_value,
                            speed=effective_mode,
                        )
                elif bool(result.get("tx_started", False)):
                    self._update_auto_responder_log_status(
                        log_event_id,
                        "SENT",
                        "Requested Report response began transmitting in JS8Call, but JS8Mesh could not confirm that the transmission finished cleanly.",
                    )
                    if hr_event_id:
                        self._update_hr_log_status(
                            hr_event_id,
                            "SENT",
                            "HR response began transmitting in JS8Call, but JS8Mesh could not confirm that the transmission finished cleanly.",
                            reply_text=preview_text_value,
                            speed=effective_mode,
                        )
                else:
                    self._update_auto_responder_log_status(
                        log_event_id,
                        "SENT",
                        "Requested Report response was handed off to JS8Call, but JS8Mesh could not confirm that transmission actually began.",
                    )
                    if hr_event_id:
                        self._update_hr_log_status(
                            hr_event_id,
                            "SENT",
                            "HR response was handed off to JS8Call, but JS8Mesh could not confirm that transmission actually began.",
                            reply_text=preview_text_value,
                            speed=effective_mode,
                        )
                try:
                    self._close_requested_jr_window()
                except Exception:
                    pass
            def _on_requested_jr_send_error(error_text):
                self._update_auto_responder_log_status(
                    log_event_id,
                    "SKIPPED",
                    f"Send failed: {error_text}",
                )
                if hr_event_id:
                    self._update_hr_log_status(
                        hr_event_id,
                        "SKIPPED",
                        f"Send failed: {error_text}",
                        reply_text=preview_text_value,
                        speed=effective_mode,
                    )
            self._send_text_to_js8call_async(
                text=preview_text_value,
                target_mode=effective_mode,
                settings_key=None,
                parent_window=dialog,
                early_success_callback=_on_requested_jr_send_triggered,
                success_callback=_on_requested_jr_send_success,
                error_callback=_on_requested_jr_send_error,
            )

        def _do_send_event(_event=None):
            do_send()
            return "break"

        tk.Button(
            button_row,
            text="Send to JS8Call",
            command=do_send,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=16
        ).pack(side="left")

        tk.Button(
            button_row,
            text="Copy to Clipboard",
            command=self._copy_requested_jr_preview,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=16
        ).pack(side="left", padx=(8, 0))

        tk.Button(
            button_row,
            text="Close",
            command=self._close_requested_jr_window,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="left", padx=(8, 0))

        dialog.bind("<Return>", _do_send_event)
        dialog.bind("<KP_Enter>", _do_send_event)

        dialog.protocol("WM_DELETE_WINDOW", self._close_requested_jr_window)
        dialog.update_idletasks()
        self._center_dialog_on_screen(dialog, self.root)
        dialog.deiconify()
        dialog.lift()
        try:
            dialog.attributes("-topmost", True)
            dialog.after(1000, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
        except Exception:
            pass
        dialog.focus_force()
        self.requested_jr_lookback_var.set(str(self._current_requested_jr_lookback_minutes()))
        self.requested_jr_estimated_tx_var.set("Estimated TX Time: n/a")
        self.requested_jr_default_mode_var.set("Default Speed Mode: n/a")
        self.requested_jr_send_effects_var.set("")
        self._update_requested_jr_mode_states()
        self._update_requested_jr_lookback_help()
        self._update_requested_jr_preview()

    def _handle_js8mesh_command_record(self, record):
        if not self._is_local_directed_command_record(record):
            return False
        command_tokens = self._js8mesh_command_tokens(record.get("msg", ""))
        self._append_auto_responder_debug(
            f"Command record seen: from={record.get('from', '')} to={record.get('to', '')} "
            f"freq={self._normalize_frequency_text(record.get('freq', '')) or str(record.get('freq', '')).strip()} "
            f"msg='{str(record.get('msg', '')).strip()}' tokens={command_tokens}"
        )
        if len(command_tokens) >= 4 and command_tokens[0] == "FINDR":
            target_callsign = normalize_callsign(command_tokens[1])
            try:
                snr_value = float(command_tokens[2])
            except Exception:
                snr_value = 0.0
            try:
                minutes_ago = max(0, int(command_tokens[3]))
            except Exception:
                minutes_ago = 0
            self._append_auto_responder_debug(
                f"FINDR parsed: target={target_callsign} snr={snr_value} minutes={minutes_ago}"
            )
            self._mark_my_find_result(record, target_callsign, snr_value, minutes_ago)
            return True
        requested_kind = None
        requested_jr_event_id = ""
        hr_event_id = ""
        hrc_target_callsign = ""
        find_requester_override = ""
        if command_tokens[:2] == ["JC", "JR"]:
            requested_kind = "General"
        elif command_tokens[:2] == ["JC", "JRN"]:
            requested_kind = "Nodes Only"
        elif command_tokens[:2] == ["JC", "JRS"]:
            requested_kind = "Stations Only"
        elif command_tokens[:2] == ["JC", "HR"]:
            requested_kind = "Heard 4 Stations"
        elif len(command_tokens) >= 3 and command_tokens[:2] == ["JC", "HRC"]:
            requested_kind = "Can Relay to Callsign"
            hrc_target_callsign = normalize_callsign(command_tokens[2])
            if not hrc_target_callsign:
                self._append_auto_responder_debug("Command ignored: HRC target callsign is missing or invalid.")
                return True
        elif len(command_tokens) >= 3 and command_tokens[:2] == ["JC", "FIND"]:
            requested_kind = "Find Callsign"
            raw_to_text = str(record.get("to", "") or "").strip().upper()
            if len(command_tokens) >= 4 and raw_to_text == "@JS8MESH":
                find_requester_override = normalize_callsign(command_tokens[2])
                hrc_target_callsign = normalize_callsign(command_tokens[3])
            else:
                hrc_target_callsign = normalize_callsign(command_tokens[2])
            if not hrc_target_callsign:
                self._append_auto_responder_debug("Command ignored: FIND target callsign is missing or invalid.")
                return True
        if requested_kind is None:
            return False
        route_info = self._interpret_js8mesh_command_route(record)
        requester = str(route_info.get("requester", "") or "UNKNOWN").strip().upper() or "UNKNOWN"
        raw_recipient = str(route_info.get("raw_recipient", str(record.get("to", "") or "").strip().upper()) or "").strip().upper()
        reply_target = str(route_info.get("reply_target", "") or "").strip()
        first_return_hop = str(route_info.get("first_return_hop", requester) or requester).strip().upper()
        request_type = {
            "General": "JR",
            "Nodes Only": "JRN",
            "Stations Only": "JRS",
            "Heard 4 Stations": "HR",
            "Can Relay to Callsign": "HRC",
            "Find Callsign": "FIND",
        }.get(requested_kind, "JR")
        frequency_text = self._normalize_frequency_text(record.get("freq", "")) or str(record.get("freq", "")).strip()
        self._append_auto_responder_debug(
            f"Command parsed: requester={requester} recipient={raw_recipient} reply_target={reply_target} "
            f"first_return_hop={first_return_hop} request_type={request_type} "
            f"js8call_allow_auto_send={bool(settings.get('js8call_allow_auto_send', False))}"
        )
        if not bool(route_info.get("supported", True)):
            reason = str(route_info.get("reason", "") or "Unsupported JC command route.")
            self._append_auto_responder_debug(f"Command ignored: {reason}")
            return True
        if requested_kind == "Find Callsign":
            self._append_auto_responder_debug(
                f"Handling FIND request: requester={find_requester_override or requester} target={hrc_target_callsign} group={bool(route_info.get('is_group', False))}"
            )
            self._store_incoming_find_request(record, route_info, hrc_target_callsign, requester_override=find_requester_override)
            return True
        if requested_kind == "Heard 4 Stations" and bool(route_info.get("is_group", False)):
            self._append_hr_log({
                "event_id": f"HR-{int(time.time() * 1000)}",
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": "HR",
                "frequency": frequency_text,
                "reply_text": "",
                "speed": "",
                "status": "SKIPPED",
                "reason": "Skipped: HR requests to @JS8MESH are not supported.",
            })
            self._append_auto_responder_debug("Command ignored: HR requests to @JS8MESH are not supported.")
            return True
        if requested_kind == "Heard 4 Stations":
            hr_event_id = f"HR-{int(time.time() * 1000)}"
            if self._recent_hr_response_exists(requester, frequency_text, window_minutes=20):
                self._append_hr_log({
                    "event_id": hr_event_id,
                    "timestamp": self._now().isoformat(timespec="seconds"),
                    "requester": requester,
                    "request_type": "HR",
                    "frequency": frequency_text,
                    "reply_text": "",
                    "speed": "",
                    "status": "SKIPPED",
                    "reason": "Skipped: the same requester was already answered with HR on this frequency in the past 20 minutes.",
                })
                return True
            self._append_hr_log({
                "event_id": hr_event_id,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "requester": requester,
                "request_type": "HR",
                "frequency": frequency_text,
                "reply_text": "",
                "speed": "",
                "status": "RECEIVED",
                "reason": "HR request received.",
            })
        self.requested_jr_requester_callsign = requester
        self.requested_jr_reply_target = reply_target
        self.requested_jr_first_hop = first_return_hop
        self.requested_jr_requested_target_callsign = hrc_target_callsign
        self.requested_jr_hr_log_event_id = hr_event_id if requested_kind == "Heard 4 Stations" else ""
        self.requested_jr_requester_var.set(f"Requested by: {requester}")
        if raw_recipient == "@JS8MESH":
            path_text = "Request path: @JS8MESH"
        elif ">" in raw_recipient:
            path_text = f"Request path: {raw_recipient.replace('>', ' > ')}"
        else:
            path_text = f"Request path: direct to {raw_recipient}"
        self.requested_jr_request_path_var.set(path_text)
        if hrc_target_callsign:
            self.requested_jr_requested_target_var.set(f"Requested target callsign: {hrc_target_callsign}")
        else:
            self.requested_jr_requested_target_var.set("")
        self.requested_jr_kind_var.set(requested_kind)
        self.requested_jr_mode_var.set("DEFAULT")
        requested_jr_event_id = self._append_requested_jr_response_log(
            preview_text="",
            speed="",
            status="RECEIVED",
            reason=f"{request_type} request received.",
        )
        self.requested_jr_response_log_event_id = requested_jr_event_id
        effective_default_mode = self._requested_jr_default_mode()
        if effective_default_mode == "SLOW":
            if requested_jr_event_id:
                self._update_auto_responder_log_status(
                    requested_jr_event_id,
                    "SKIPPED",
                    "Skipped: calculated default speed is SLOW.",
                )
            if requested_kind == "Heard 4 Stations" and hr_event_id:
                self._update_hr_log_status(
                    hr_event_id,
                    "SKIPPED",
                    "Skipped: calculated default speed is SLOW.",
                )
            return True
        self._show_requested_jr_window()
        preview_text, lines = self._update_requested_jr_preview()
        if not lines:
            if requested_jr_event_id:
                self._update_auto_responder_log_status(
                    requested_jr_event_id,
                    "SKIPPED",
                    str(preview_text or "No Report can be generated at the moment."),
                )
            self._close_requested_jr_window()
            if requested_kind == "Heard 4 Stations" and hr_event_id:
                self._update_hr_log_status(hr_event_id, "SKIPPED", str(preview_text or "No HR can be generated at the moment."))
            return True
        return True

    def _is_own_mesh_report_record(self, record):
        sender = normalize_callsign(record.get("from", ""))
        if not sender or sender not in self._own_known_callsigns():
            return False
        return bool(parse_mesh_report_entries(record.get("msg", ""), fallback_source=sender))

    def _mesh_link_info(self, graph, left_callsign, right_callsign):
        left_norm = normalize_callsign(left_callsign)
        right_norm = normalize_callsign(right_callsign)
        if not left_norm or not right_norm:
            return None

        forward = graph.get((left_norm, right_norm))
        reverse = graph.get((right_norm, left_norm))
        if not forward or not reverse:
            return None

        worst_snr = min(float(forward.get("snr", -30.0)), float(reverse.get("snr", -30.0)))
        age_minutes = max(float(forward.get("age_minutes", 999.0)), float(reverse.get("age_minutes", 999.0)))
        category = snr_category_from_reported(worst_snr)
        return {
            "snr": worst_snr,
            "minutes_ago": int(max(0, round(age_minutes))),
            "category": category,
        }

    def _mesh_reports_by_source(self, records=None):
        reports = {}

        base_records = list(records) if records is not None else self._records_for_selected_frequency()

        for record in base_records:
            if self._is_own_mesh_report_record(record):
                continue
            source = normalize_callsign(record.get("from", ""))
            if not source:
                continue

            parsed_entries = parse_mesh_report_entries(record.get("msg", ""), fallback_source=source)
            if not parsed_entries:
                continue

            dt_value = record.get("datetime")
            existing = reports.get(source)
            if existing is None or (dt_value is not None and (existing.get("datetime") is None or dt_value > existing.get("datetime"))):
                reports[source] = {
                    "datetime": dt_value,
                    "entries": parsed_entries,
                    "record": record,
                }

        return reports

    def _mesh_children_for_parent(self, parent_callsign, reports_by_source, seen_nodes):
        source_report = reports_by_source.get(normalize_callsign(parent_callsign))
        if not source_report:
            return []

        source_record = source_report.get("record", {})
        candidates = []
        for entry in source_report.get("entries", []):
            if int(entry.get("wave", 1) or 1) != 1:
                continue

            child = normalize_callsign(entry.get("heard", ""))
            if not child or child in seen_nodes:
                continue

            minutes_value = entry.get("minutes")
            effective_minutes = mesh_report_entry_effective_minutes(source_record, entry, now=self._now())
            if effective_minutes is None or float(effective_minutes) > 50:
                continue

            try:
                snr_value = float(entry.get("avg_snr", -30.0))
            except (ValueError, TypeError):
                continue

            category = snr_category_from_reported(snr_value)
            if category not in ("TURBO", "FAST", "NORMAL"):
                continue

            candidates.append({
                "child": child,
                "snr": snr_value,
                "minutes_ago": int(round(effective_minutes)),
                "category": category,
            })

        candidates.sort(
            key=lambda item: (
                0 if item["category"] == "TURBO" else 1,
                item["minutes_ago"],
                -item["snr"],
                item["child"],
            )
        )
        return candidates

    def generate_mesh_broadcast_lines(self, station_count, lookback_minutes, excluded_callsigns=None, report_scope="GENERAL", target_callsign=None):
        source_station = self.user_call_var.get().strip().upper()
        if not source_station:
            self._append_auto_responder_debug(
                f"Mesh broadcast skipped: missing source station for scope={report_scope}."
            )
            return []

        excluded_norms = {
            normalize_callsign(item)
            for item in list(excluded_callsigns or [])
            if normalize_callsign(item)
        }

        records_for_view = self._records_for_selected_frequency()
        raw_target_callsign = str(target_callsign or "").strip()
        if raw_target_callsign.upper() in ("", "NONE", "NULL"):
            target_norm = ""
        else:
            target_norm = normalize_callsign(raw_target_callsign)
        graph = build_send_graph(records_for_view, user_cs=source_station, min_snr=-30, max_age_minutes=99999999)
        reports_by_source = self._mesh_reports_by_source()
        scope_name = str(report_scope or "GENERAL").strip().upper()
        selected_frequency = self.selected_frequency_var.get().strip()
        dual_snapshot = export_dual_topology_snapshot(
            records=records_for_view,
            traffic_max_age_minutes=self._current_max_age_minutes(),
            mesh_activity_minutes=self._current_mesh_activity_minutes(),
            mesh_core_threshold=self._current_mesh_core_threshold(),
            frequency=selected_frequency,
            now=self._record_now(),
            exclude_callsigns=self._own_known_callsigns(),
        )
        wave_node_calls = {
            normalize_callsign(node.get("id", ""))
            for node in list(dual_snapshot.get("mesh", {}).get("nodes", []))
            if normalize_callsign(node.get("id", "")) and node.get("wave_depth") is not None and bool(node.get("is_mesh_node"))
        }

        def scope_allows(callsign):
            norm = normalize_callsign(callsign)
            if not norm:
                return False
            if target_norm and norm != target_norm:
                return False
            if scope_name == "NODES_ONLY":
                return norm in wave_node_calls
            if scope_name == "STATIONS_ONLY":
                return norm not in wave_node_calls
            return True

        wave1_candidates = []
        direct_candidates = set()
        candidate_debug = []
        for left_callsign, right_callsign in graph.keys():
            if left_callsign == normalize_callsign(source_station):
                direct_candidates.add(right_callsign)
            elif right_callsign == normalize_callsign(source_station):
                direct_candidates.add(left_callsign)

        for candidate_source in direct_candidates:
            if candidate_source == normalize_callsign(source_station):
                candidate_debug.append((candidate_source, "skip:self"))
                continue
            if candidate_source in excluded_norms:
                candidate_debug.append((candidate_source, "skip:excluded"))
                continue
            if not scope_allows(candidate_source):
                candidate_debug.append((candidate_source, "skip:scope"))
                continue

            link = self._mesh_link_info(graph, source_station, candidate_source)
            if not link:
                candidate_debug.append((candidate_source, "skip:no_two_way_link"))
                continue
            if link["category"] not in ("TURBO", "FAST", "NORMAL"):
                candidate_debug.append(
                    (candidate_source, f"skip:category:{link['category']} snr={round(float(link['snr']), 1)} age={link['minutes_ago']}")
                )
                continue
            if int(link["minutes_ago"]) > int(lookback_minutes):
                candidate_debug.append(
                    (candidate_source, f"skip:age:{link['minutes_ago']}>{lookback_minutes} category={link['category']} snr={round(float(link['snr']), 1)}")
                )
                continue

            wave1_candidates.append({
                "node": candidate_source,
                "wave": 1,
                "parent": "",
                "snr": link["snr"],
                "minutes_ago": link["minutes_ago"],
                "category": link["category"],
            })
            candidate_debug.append(
                (candidate_source, f"keep:{link['category']} snr={round(float(link['snr']), 1)} age={link['minutes_ago']}")
            )

        self._append_auto_responder_debug(
            "Mesh broadcast inputs: "
            f"scope={scope_name} source={source_station} lookback={lookback_minutes} "
            f"station_count={station_count} tx_limit={settings.get('mesh_tx_time_limit_minutes', 0)} "
            f"target={target_norm or ''} excluded={sorted(item for item in excluded_norms if item)} "
            f"direct_candidates={sorted(direct_candidates)} wave_nodes={sorted(wave_node_calls)} "
            f"wave1_candidates={[(item['node'], item['category'], item['minutes_ago'], round(float(item['snr']), 1)) for item in wave1_candidates]} "
            f"candidate_debug={candidate_debug[:40]}"
        )

        wave1_candidates.sort(
            key=lambda item: (
                0 if item["category"] == "TURBO" else 1,
                item["minutes_ago"],
                -item["snr"],
                item["node"],
            )
        )

        selected_nodes = []
        seen_nodes = {normalize_callsign(source_station)} | set(excluded_norms)
        frontier = []

        for candidate in wave1_candidates:
            if len(selected_nodes) >= station_count:
                break
            node = candidate["node"]
            if node in seen_nodes:
                continue
            selected_nodes.append(candidate)
            seen_nodes.add(node)
            frontier.append(candidate)

        while frontier and len(selected_nodes) < station_count:
            candidate_children = []
            next_frontier = []

            for parent_item in frontier:
                parent_wave = int(parent_item.get("wave", 1) or 1)
                if parent_wave >= 3:
                    continue

                for child in self._mesh_children_for_parent(parent_item["node"], reports_by_source, seen_nodes):
                    if not scope_allows(child["child"]):
                        continue
                    child_item = {
                        "node": child["child"],
                        "wave": parent_wave + 1,
                        "parent": parent_item["node"],
                        "snr": child["snr"],
                        "minutes_ago": child["minutes_ago"],
                        "category": child["category"],
                    }
                    candidate_children.append(child_item)

            if not candidate_children:
                break

            candidate_children.sort(
                key=lambda item: (
                    int(item.get("wave", 99)),
                    0 if item["category"] == "TURBO" else 1,
                    item["minutes_ago"],
                    -item["snr"],
                    item["node"],
                )
            )

            for child_item in candidate_children:
                if len(selected_nodes) >= station_count:
                    break
                child_node = child_item["node"]
                if child_node in seen_nodes:
                    continue
                selected_nodes.append(child_item)
                seen_nodes.add(child_node)
                next_frontier.append(child_item)

            frontier = next_frontier

        prefix = "JR"
        if scope_name == "NODES_ONLY":
            prefix = "JRN"
        elif scope_name == "STATIONS_ONLY":
            prefix = "JRS"

        lines = []
        for item in selected_nodes[:station_count]:
            snr_text = f"{int(round(item['snr'])):+d}"
            minutes_text = str(int(item["minutes_ago"]))
            wave = int(item.get("wave", 1) or 1)
            node_token = item["node"]
            if scope_name == "GENERAL" and normalize_callsign(item["node"]) in wave_node_calls:
                node_token = f"*{node_token}"
            if wave <= 1:
                entry_text = f"1.{node_token}.{snr_text}.{minutes_text}"
            else:
                entry_text = f"{wave}.{node_token}.{item['parent']}.{snr_text}.{minutes_text}"
            if not lines:
                lines.append(f"{prefix}.{entry_text}")
            else:
                lines.append(entry_text)

        self._append_auto_responder_debug(
            f"Mesh broadcast result: scope={scope_name} selected_nodes={[(item['node'], item['wave'], item['parent']) for item in selected_nodes]} "
            f"lines={lines}"
        )

        return lines

    def _format_mesh_timestamp(self, dt_value):
        if dt_value is None:
            return "never"
        return dt_value.strftime("%Y-%m-%d %H:%M:%S")

    def _update_mesh_status_text(self):
        slots = self._mesh_broadcast_slots()
        interval_minutes = self._safe_positive_int(
            settings.get("mesh_broadcast_interval_minutes", 20),
            10,
            minimum=1
        )
        lookback_minutes = self._current_mesh_lookback_minutes()

        if slots:
            schedule_mode_text = "SCHEDULE MODE: SPECIFIC TIMES"
            details_text = f"Broadcast times: {', '.join(slots)}.\n"
        else:
            schedule_mode_text = "SCHEDULE MODE: INTERVAL"
            details_text = f"Broadcast interval: every {interval_minutes} minute(s).\n"
        if self.ignore_freshness_var.get():
            details_text += f"Look back period: {lookback_minutes} minute(s).\n"
        else:
            details_text += f"Look back period: {lookback_minutes} minute(s) (max 40).\n"
        tx_limit_minutes = self._current_mesh_tx_time_limit_minutes()
        if tx_limit_minutes <= 0:
            details_text += "JR/HR/HRC TX time limit: No limit.\n"
        else:
            details_text += f"JR/HR/HRC TX time limit: {tx_limit_minutes} minute(s).\n"

        if self.last_mesh_broadcast_time is None:
            details_text += (
                "Last prepared mesh report: never.\n"
                "Next automatic preparation: waiting for scheduler trigger."
            )
        else:
            details_text += (
                f"Last prepared mesh report: {self._format_mesh_timestamp(self.last_mesh_broadcast_time)}.\n"
            )

            if slots:
                details_text += "Next automatic preparation: at the next matching scheduled time."
            else:
                next_time = self.last_mesh_broadcast_time + timedelta(minutes=interval_minutes)
                details_text += f"Next automatic preparation: {self._format_mesh_timestamp(next_time)}."

        self.mesh_network_window.set_status_text(schedule_mode_text, details_text)

    def _mesh_preview_mode_name(self):
        try:
            mode = str(self.mesh_network_window.tx_mode_var.get() or "").strip().upper()
        except Exception:
            mode = ""
        if mode in ("TURBO", "FAST", "NORMAL", "SLOW"):
            return mode
        return "NORMAL"

    def _save_mesh_settings_values(self, station_count, broadcast_interval, raw_times_text, tx_time_limit_minutes,
                                   tx_mode=None, assisted_enabled=None, parent_window=None):
        times_text, invalid_times = self._parse_mesh_times_text(raw_times_text)
        if self.ignore_freshness_var.get():
            lookback_minutes = self._safe_positive_int(
                self.mesh_network_window.get_lookback_minutes_text() if getattr(self, "mesh_network_window", None) else settings.get("mesh_lookback_minutes", 20),
                settings.get("mesh_lookback_minutes", 20),
                minimum=1
            )
        else:
            lookback_minutes = self._mesh_derived_lookback_minutes(
                raw_times_text=times_text,
                broadcast_interval=broadcast_interval,
            )

        settings["mesh_station_count"] = station_count
        settings["mesh_lookback_minutes"] = lookback_minutes
        settings["mesh_broadcast_interval_minutes"] = broadcast_interval
        settings["mesh_broadcast_times_24h"] = times_text
        settings["mesh_tx_time_limit_minutes"] = tx_time_limit_minutes
        if tx_mode is not None:
            normalized_mode = str(tx_mode or "NORMAL").strip().upper() or "NORMAL"
            if normalized_mode not in ("TURBO", "FAST", "NORMAL", "SLOW"):
                normalized_mode = "NORMAL"
            settings["mesh_tx_mode"] = normalized_mode
        if assisted_enabled is not None:
            settings["mesh_assisted_generation_enabled"] = bool(assisted_enabled)
        save_settings(settings)

        if getattr(self, "mesh_network_window", None) is not None:
            self.mesh_network_window.set_station_count_text(station_count)
            self.mesh_network_window.set_lookback_minutes_text(lookback_minutes)
            self.mesh_network_window.set_broadcast_interval_text(broadcast_interval)
            self.mesh_network_window.set_broadcast_times_24h_text(times_text)
            self.mesh_network_window.set_tx_time_limit_minutes_text(tx_time_limit_minutes)
            if tx_mode is not None:
                self.mesh_network_window.set_tx_mode_text(settings.get("mesh_tx_mode", "NORMAL"))
        self._sync_mesh_lookback_controls()
        self._update_mesh_status_text()
        self._on_mesh_window_inputs_changed()

        if invalid_times:
            self._dark_info_dialog(
                "Invalid Time Entries Removed",
                "These time entries were invalid and were removed:\n\n"
                + "\n".join(invalid_times)
                + "\n\nThe valid time entries were saved.",
                parent=parent_window,
                refocus_widget=parent_window,
            )
        else:
            self._dark_info_dialog(
                "Saved",
                "Mesh report reminder settings saved!",
                parent=parent_window,
                refocus_widget=parent_window,
            )

    def show_tx_mesh_reports_settings_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("TX Mesh Reports Settings")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        frame = tk.LabelFrame(
            outer,
            text="Mesh Report Reminder",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=10,
            pady=10
        )
        frame.pack(fill="x", expand=True)

        station_count_var = tk.StringVar(value=str(settings.get("mesh_station_count", 5)))
        lookback_var = tk.StringVar(value=str(settings.get("mesh_lookback_minutes", 20)))
        interval_var = tk.StringVar(value=str(settings.get("mesh_broadcast_interval_minutes", 20)))
        times_var = tk.StringVar(value=str(settings.get("mesh_broadcast_times_24h", "") or ""))
        tx_time_limit_var = tk.StringVar(value=str(settings.get("mesh_tx_time_limit_minutes", 0)))
        tx_mode_var = tk.StringVar(value=str(settings.get("mesh_tx_mode", "NORMAL") or "NORMAL").strip().upper())
        assisted_var = tk.BooleanVar(value=bool(settings.get("mesh_assisted_generation_enabled", False)))

        tk.Label(frame, text="Stations to broadcast:", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=0, sticky="w", pady=4)
        tk.Entry(frame, textvariable=station_count_var, width=8).grid(row=0, column=1, sticky="w", padx=(8, 18), pady=4)
        tk.Label(frame, text="Look back period (minutes):", bg=self.bg_color, fg=self.fg_color).grid(row=0, column=2, sticky="w", pady=4)
        lookback_entry = tk.Entry(frame, textvariable=lookback_var, width=8)
        lookback_entry.grid(row=0, column=3, sticky="w", padx=(8, 18), pady=4)
        tk.Label(frame, text="Broadcast interval (minutes):", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=0, sticky="w", pady=4)
        tk.Entry(frame, textvariable=interval_var, width=8).grid(row=1, column=1, sticky="w", padx=(8, 18), pady=4)
        tk.Label(frame, text="Report TX time limit (minutes):", bg=self.bg_color, fg=self.fg_color).grid(row=1, column=2, sticky="w", pady=4)

        limit_row = tk.Frame(frame, bg=self.bg_color)
        limit_row.grid(row=1, column=3, sticky="w", padx=(8, 18), pady=4)
        tk.Entry(limit_row, textvariable=tx_time_limit_var, width=8).pack(side="left")
        tk.Label(limit_row, text="0 = No limit", bg=self.bg_color, fg=self.fg_color).pack(side="left", padx=(8, 0))

        mode_row = tk.Frame(frame, bg=self.bg_color)
        mode_row.grid(row=2, column=2, columnspan=2, sticky="w", pady=4)
        tk.Label(mode_row, text="Mode:", bg=self.bg_color, fg=self.fg_color, anchor="w", justify="left", font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=(0, 8))
        for mode_name in ("TURBO", "FAST", "NORMAL"):
            tk.Radiobutton(
                mode_row,
                text=mode_name.title(),
                variable=tx_mode_var,
                value=mode_name,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
            ).pack(side="left", padx=(0, 8))

        tk.Label(frame, text="Specific broadcast times (24h, comma-separated):", bg=self.bg_color, fg=self.fg_color).grid(row=3, column=0, sticky="w", pady=4)
        tk.Entry(frame, textvariable=times_var, width=40).grid(row=3, column=1, columnspan=3, sticky="we", padx=(8, 18), pady=4)
        tk.Label(frame, text="Example: 08:00, 12:30, 18:45, 23:00", bg=self.bg_color, fg=self.fg_color, anchor="w", justify="left").grid(row=4, column=0, columnspan=4, sticky="w", pady=(0, 8))

        tk.Checkbutton(
            outer,
            text="Enable Assisted one-click mesh report generation based on your settings.",
            variable=assisted_var,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color,
            activebackground=self.bg_color,
            activeforeground=self.fg_color,
            highlightthickness=0,
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(12, 0))

        editable = bool(self.ignore_freshness_var.get())
        try:
            lookback_entry.configure(state="normal" if editable else "readonly")
        except Exception:
            pass
        if not editable:
            lookback_var.set(str(self._mesh_derived_lookback_minutes(raw_times_text=times_var.get(), broadcast_interval=self._safe_positive_int(interval_var.get(), settings.get("mesh_broadcast_interval_minutes", 20), minimum=1))))

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def do_save():
            station_count = self._safe_positive_int(station_count_var.get(), settings.get("mesh_station_count", 5), minimum=1)
            interval_minutes = self._safe_positive_int(interval_var.get(), settings.get("mesh_broadcast_interval_minutes", 20), minimum=1)
            tx_limit_minutes = self._safe_positive_int(tx_time_limit_var.get(), settings.get("mesh_tx_time_limit_minutes", 0), minimum=0)
            self._save_mesh_settings_values(
                station_count=station_count,
                broadcast_interval=interval_minutes,
                raw_times_text=times_var.get(),
                tx_time_limit_minutes=tx_limit_minutes,
                tx_mode=tx_mode_var.get(),
                assisted_enabled=assisted_var.get(),
                parent_window=dialog,
            )
            if dialog.winfo_exists():
                dialog.destroy()

        tk.Button(button_row, text="Save Settings", command=do_save, bg=self.highlight_color, fg=self.fg_color, width=14).pack(side="left")
        tk.Button(button_row, text="Cancel", command=dialog.destroy, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left", padx=(8, 0))

        self._center_dialog_on_screen(dialog, self.root)
        dialog.deiconify()
        dialog.lift()
        dialog.focus_force()
        self.root.wait_window(dialog)

    def _current_mesh_tx_time_limit_minutes(self):
        raw_value = (
            self.mesh_network_window.get_tx_time_limit_minutes_text()
            if getattr(self, "mesh_network_window", None)
            else settings.get("mesh_tx_time_limit_minutes", 0)
        )
        return self._safe_positive_int(
            raw_value,
            settings.get("mesh_tx_time_limit_minutes", 0),
            minimum=0
        )

    def _apply_mesh_tx_time_limit(self, lines, mode_name, tx_limit_minutes):
        limited_lines = list(lines or [])
        try:
            limit_minutes = max(0, int(tx_limit_minutes or 0))
        except Exception:
            limit_minutes = 0

        if limit_minutes <= 0 or not limited_lines:
            return limited_lines

        limit_seconds = limit_minutes * 60
        original_lines = list(limited_lines)
        while limited_lines:
            preview_text = "@JS8MESH " + ";".join(limited_lines)
            estimated_seconds = estimate_mesh_report_seconds(preview_text, mode_name)
            if estimated_seconds <= limit_seconds:
                break
            limited_lines.pop()

        if len(limited_lines) != len(original_lines):
            self._append_auto_responder_debug(
                f"Mesh TX limit trimmed lines: mode={mode_name} limit_minutes={limit_minutes} "
                f"before={original_lines} after={limited_lines}"
            )

        return limited_lines

    def _update_mesh_tx_estimate(self, preview_text=None):
        text = str(
            preview_text
            if preview_text is not None
            else self.mesh_network_window.get_preview_text()
        ).strip()

        if not text or text.startswith("No mesh report lines could be generated."):
            self.mesh_network_window.set_estimated_tx_time_text("Estimated TX Time: n/a")
            self.mesh_network_window.set_warning_text("")
            return

        seconds = estimate_mesh_report_seconds(text, self._mesh_preview_mode_name())
        if seconds <= 0:
            self.mesh_network_window.set_estimated_tx_time_text("Estimated TX Time: n/a")
            self.mesh_network_window.set_warning_text("")
            return

        mode = self._mesh_preview_mode_name()
        self.mesh_network_window.set_estimated_tx_time_text(
            f"Estimated TX Time: {format_duration(seconds)}"
        )
        self.mesh_network_window.set_warning_text(
            (
                f"Mode {mode.title()} using measured JS8Call compact-report timing.\n"
                f"{self._js8call_send_effects_text(mode)}"
            )
        )

    def _on_mesh_window_inputs_changed(self):
        station_count = self._safe_positive_int(
            self.mesh_network_window.get_station_count_text(),
            settings.get("mesh_station_count", 5),
            minimum=1
        )
        self._sync_mesh_lookback_controls()
        lookback_minutes = self._current_mesh_lookback_minutes()

        lines = self.generate_mesh_broadcast_lines(
            station_count=station_count,
            lookback_minutes=lookback_minutes
        )
        lines = self._apply_mesh_tx_time_limit(
            lines,
            self._mesh_preview_mode_name(),
            self._current_mesh_tx_time_limit_minutes(),
        )

        if lines:
            preview_text = "@JS8MESH " + ";".join(lines)
        else:
            preview_text = (
                "No mesh report lines could be generated.\n\n"
                "Check that:\n"
                "- your User Callsign is set\n"
                "- recent records exist\n"
                "- recent records contain numeric SNR values"
            )

        self.mesh_network_window.set_preview_text(preview_text)
        self._update_mesh_tx_estimate(preview_text)

    def _js8call_send_status_dialog(self, title, message_text):
        self._dark_info_dialog(title, message_text)

    def _new_js8call_bridge(self):
        host = str(settings.get("js8call_host", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(settings.get("js8call_port", 2442))
        except Exception:
            port = 2442
        return JS8CallBridge(host=host, port=port)

    def _estimated_send_timeout_seconds(self, message_text, mode_name):
        text = str(message_text or "").strip()
        mode = str(mode_name or "NORMAL").strip().upper() or "NORMAL"
        base = estimate_mesh_report_seconds(text, mode)
        if base <= 0:
            base = 120
        return max(120, int(base + 90))

    def _estimated_relay_tx_seconds(self, message_text, mode_name):
        text = str(message_text or "").strip()
        mode = normalize_mesh_mode(mode_name)
        base = estimate_mesh_report_seconds(text, mode)
        if base > 0:
            return int(base)
        chars = max(1, len(text))
        frames = max(2, 1 + ((chars + 12) // 13))
        return int(frames * MODE_SECONDS.get(mode, 15))

    def _send_text_to_js8call_async(self, text, target_mode, settings_key=None, parent_window=None, success_callback=None, error_callback=None, silent_failure=False, skip_speed_warning_dialog=False, tolerate_unconfirmed_manual_stage=False, early_success_callback=None):
        text = str(text or "").strip()
        target_mode = str(target_mode or "NORMAL").strip().upper() or "NORMAL"
        if not text:
            self._dark_info_dialog(
                "Nothing to Send",
                "There is no prepared text to send to JS8Call.",
                parent=parent_window,
                refocus_widget=parent_window,
            )
            return

        if settings_key:
            settings[settings_key] = target_mode
            save_settings(settings)

        allow_auto_send = bool(settings.get("js8call_allow_auto_send", False))

        if allow_auto_send and not skip_speed_warning_dialog and not self._js8call_speed_warning_dialog(parent=parent_window, refocus_widget=parent_window):
            return

        def worker():
            timeout_seconds = self._estimated_send_timeout_seconds(text, target_mode)
            try:
                with self._new_js8call_bridge() as bridge:
                    if allow_auto_send:
                        selected_call = bridge.get_selected_call()
                        if selected_call:
                            raise JS8CallBridgeError(
                                f"JS8Call currently has {selected_call} selected.\n\n"
                                "Auto-send was blocked to avoid turning this into a directed message.\n\n"
                                "Please deselect the callsign in JS8Call and try again."
                            )
                        result = bridge.send_text_with_temporary_speed(
                            text=text,
                            target_speed=target_mode,
                            wait_timeout=timeout_seconds,
                            on_tx_started=(
                                (lambda partial_result: self.root.after(
                                    0,
                                    lambda partial_result=dict(partial_result): early_success_callback(partial_result),
                                ))
                                if callable(early_success_callback)
                                else None
                            ),
                        )
                    else:
                        queued_ok = bridge.set_tx_text(text)
                        if not queued_ok and not bool(tolerate_unconfirmed_manual_stage):
                            raise JS8CallBridgeError(
                                "JS8Mesh could not confirm that the text was loaded into JS8Call."
                            )
                        result = {
                            "original_speed": bridge.get_speed(),
                            "requested_speed": target_mode,
                            "speed_switch_attempted": False,
                            "speed_switched": False,
                            "manual_send_only": True,
                            "stage_confirmed": bool(queued_ok),
                        }
                        if callable(early_success_callback):
                            try:
                                self.root.after(0, lambda result=dict(result): early_success_callback(result))
                            except Exception:
                                pass
            except JS8CallBridgeError as exc:
                error_text = str(exc)
                if callable(error_callback):
                    try:
                        self.root.after(0, lambda error_text=error_text: error_callback(error_text))
                    except Exception:
                        pass
                if not silent_failure:
                    self.root.after(
                        0,
                        lambda: self._dark_info_dialog(
                            "JS8Call Send Failed",
                            error_text,
                            parent=parent_window,
                            refocus_widget=parent_window,
                        ),
                    )
                return
            except Exception as exc:
                error_text = f"Unexpected error while sending to JS8Call:\n\n{exc}"
                if callable(error_callback):
                    try:
                        self.root.after(0, lambda error_text=error_text: error_callback(error_text))
                    except Exception:
                        pass
                if not silent_failure:
                    self.root.after(
                        0,
                        lambda: self._dark_info_dialog(
                            "JS8Call Send Failed",
                            error_text,
                            parent=parent_window,
                            refocus_widget=parent_window,
                        ),
                    )
                return

            manual_send_only = bool(result.get("manual_send_only"))
            original_speed = str(result.get("original_speed", "") or "").strip()

            if manual_send_only:
                sent_message = (
                    "Text was loaded into JS8Call only.\n\n"
                    "JS8Mesh did not press Send because JS8Call Control is set to NO.\n\n"
                    "If needed, change JS8Call to the correct TX mode yourself and press Send manually."
                )
            else:
                sent_message = "Message was sent to JS8Call."
                if original_speed and original_speed == target_mode:
                    sent_message += f"\n\nJS8Call was already in {target_mode.title()} mode."
                elif result.get("speed_switched"):
                    sent_message += (
                        f"\n\nJS8Mesh switched JS8Call to {target_mode.title()} for this transmission and restored "
                        f"{original_speed.title()} after TX completed."
                        if original_speed and result.get("speed_restored")
                        else (
                            f"\n\nJS8Mesh switched JS8Call to {target_mode.title()} for this transmission, "
                            f"but could not confirm restoring {original_speed.title()} afterward."
                            if original_speed
                            else f"\n\nJS8Mesh switched JS8Call to {target_mode.title()} for this transmission."
                        )
                    )
                else:
                    sent_message += (
                        f"\n\nJS8Mesh queued the message, but the JS8Call speed-change request did not succeed."
                    )

            _ = sent_message

            if callable(success_callback):
                try:
                    self.root.after(0, lambda result=dict(result): success_callback(result))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def send_prepared_relay_to_js8call(self):
        text = str(self.relay_builder.prepared_message_var.get() or "").strip()
        if not text:
            self._dark_info_dialog("Nothing to Send", "Prepare relay text first.")
            return

        selected_pathway = self._get_selected_pathway()
        operational_pathway = self._operational_path_from_inbound_display(selected_pathway)
        path_parts = [part.strip().upper() for part in operational_pathway.split(">") if part.strip()]
        final_target = path_parts[-1] if len(path_parts) >= 2 else ""
        reverse_chain = ">".join(list(reversed(path_parts[1:-1])) + [path_parts[0]]) if len(path_parts) >= 2 else ""
        pending_metadata = {
            "pathway": selected_pathway,
            "operational_path": operational_pathway,
            "target": final_target,
            "expected_ack_chain": reverse_chain,
            "tx_mode": self.relay_builder.get_tx_mode_text(),
            "message_mode": str(self.relay_builder.message_mode_var.get() or "").strip() or "-",
            "message_text": str(self.relay_builder._get_message_body()).strip(),
            "prepared_message": text,
            "estimated_tx_seconds": self._estimated_relay_tx_seconds(text, self.relay_builder.get_tx_mode_text()),
        }

        settings["relay_tx_mode"] = str(self.relay_builder.tx_mode_var.get() or "DEFAULT").strip().upper() or "DEFAULT"
        save_settings(settings)
        target_mode = self.relay_builder.get_tx_mode_text()
        self._send_text_to_js8call_async(
            text=text,
            target_mode=target_mode,
            settings_key=None,
            parent_window=None,
            tolerate_unconfirmed_manual_stage=True,
            success_callback=lambda result, meta=dict(pending_metadata): (
                self._register_pending_relay_ack(meta)
                if normalize_callsign(meta.get("target", "")) and str(meta.get("expected_ack_chain", "")).strip()
                else None
            ),
        )

    def send_mesh_preview_to_js8call(self):
        text = str(self.mesh_network_window.get_preview_text() or "").strip()
        if not text or text.startswith("No mesh report lines could be generated."):
            self._dark_info_dialog(
                "No Mesh Report Data",
                "No mesh report lines could be generated.",
                parent=self.mesh_network_window.window,
                refocus_widget=self.mesh_network_window.window,
            )
            return
        target_mode = self.mesh_network_window.get_tx_mode_text()
        event_id = f"TXMESH-MANUAL-{int(time.time() * 1000)}"
        frequency_text = self._normalize_frequency_text(self.selected_frequency_var.get()) or self.selected_frequency_var.get().strip()
        settings["mesh_tx_mode"] = target_mode
        save_settings(settings)
        self._append_tx_mesh_reports_log({
            "event_id": event_id,
            "timestamp": self._now().isoformat(timespec="seconds"),
            "requester": "",
            "request_type": "MESH",
            "frequency": frequency_text,
            "reply_text": text,
            "speed": target_mode,
            "status": "QUEUED",
            "reason": "Manual TX Mesh Report queued for send to JS8Call.",
        })
        self._send_text_to_js8call_async(
            text=text,
            target_mode=target_mode,
            settings_key="mesh_tx_mode",
            parent_window=self.mesh_network_window.window,
            success_callback=lambda result, eid=event_id, preview_text=text: (
                self._update_tx_mesh_reports_log_status(
                    eid,
                    "STAGED",
                    "Manual TX Mesh Report was loaded into JS8Call only. JS8Call Control is OFF.",
                    reply_text=preview_text,
                )
                if bool(result.get("manual_send_only"))
                else (
                    self._update_tx_mesh_reports_log_status(
                        eid,
                        "SENT",
                        "Manual TX Mesh Report was sent to JS8Call and transmission completed cleanly.",
                        reply_text=preview_text,
                    )
                    if bool(result.get("tx_completed"))
                    else self._update_tx_mesh_reports_log_status(
                        eid,
                        "SENT",
                        "Manual TX Mesh Report was handed off to JS8Call, but JS8Mesh could not confirm that the transmission finished cleanly.",
                        reply_text=preview_text,
                    )
                )
                or (
                    self.mesh_network_window._close_window()
                    if not bool(result.get("manual_send_only"))
                    else None
                )
            ),
            error_callback=lambda error_text, eid=event_id, preview_text=text: self._update_tx_mesh_reports_log_status(
                eid,
                "SKIPPED",
                f"Send failed: {error_text}",
                reply_text=preview_text,
            ),
        )

    def _prepare_mesh_broadcast(self, station_count, lookback_minutes, copy_to_clipboard=False):
        lines, preview_text = self._build_mesh_preview_data(
            station_count=station_count,
            lookback_minutes=lookback_minutes,
            mode_name=self._mesh_preview_mode_name(),
            tx_limit_minutes=self._current_mesh_tx_time_limit_minutes(),
            copy_to_clipboard=copy_to_clipboard,
            update_window=True,
            excluded_callsigns=None,
        )
        return lines, preview_text

    def save_mesh_settings(self):
        station_count = self._safe_positive_int(
            self.mesh_network_window.get_station_count_text(),
            settings.get("mesh_station_count", 5),
            minimum=1
        )
        broadcast_interval = self._safe_positive_int(
            self.mesh_network_window.get_broadcast_interval_text(),
            settings.get("mesh_broadcast_interval_minutes", 20),
            minimum=1
        )

        raw_times_text = self.mesh_network_window.get_broadcast_times_24h_text()
        times_text, invalid_times = self._parse_mesh_times_text(raw_times_text)
        if self.ignore_freshness_var.get():
            lookback_minutes = self._safe_positive_int(
                self.mesh_network_window.get_lookback_minutes_text(),
                settings.get("mesh_lookback_minutes", 20),
                minimum=1
            )
        else:
            lookback_minutes = self._mesh_derived_lookback_minutes(
                raw_times_text=times_text,
                broadcast_interval=broadcast_interval,
            )
        tx_time_limit_minutes = self._safe_positive_int(
            self.mesh_network_window.get_tx_time_limit_minutes_text(),
            settings.get("mesh_tx_time_limit_minutes", 0),
            minimum=0
        )
        self._save_mesh_settings_values(
            station_count=station_count,
            broadcast_interval=broadcast_interval,
            raw_times_text=raw_times_text,
            tx_time_limit_minutes=tx_time_limit_minutes,
            tx_mode=self.mesh_network_window.get_tx_mode_text(),
            parent_window=self.mesh_network_window.window,
        )

    def broadcast_mesh_report_now(self):
        station_count = self._safe_positive_int(
            self.mesh_network_window.get_station_count_text(),
            settings.get("mesh_station_count", 5),
            minimum=1
        )
        broadcast_interval = self._safe_positive_int(
            self.mesh_network_window.get_broadcast_interval_text(),
            settings.get("mesh_broadcast_interval_minutes", 20),
            minimum=1
        )

        raw_times_text = self.mesh_network_window.get_broadcast_times_24h_text()
        times_text, invalid_times = self._parse_mesh_times_text(raw_times_text)
        if self.ignore_freshness_var.get():
            lookback_minutes = self._safe_positive_int(
                self.mesh_network_window.get_lookback_minutes_text(),
                settings.get("mesh_lookback_minutes", 20),
                minimum=1
            )
        else:
            lookback_minutes = self._mesh_derived_lookback_minutes(
                raw_times_text=times_text,
                broadcast_interval=broadcast_interval,
            )
        tx_time_limit_minutes = self._safe_positive_int(
            self.mesh_network_window.get_tx_time_limit_minutes_text(),
            settings.get("mesh_tx_time_limit_minutes", 0),
            minimum=0
        )

        settings["mesh_station_count"] = station_count
        settings["mesh_lookback_minutes"] = lookback_minutes
        settings["mesh_broadcast_interval_minutes"] = broadcast_interval
        settings["mesh_broadcast_times_24h"] = times_text
        settings["mesh_tx_time_limit_minutes"] = tx_time_limit_minutes
        save_settings(settings)

        self.mesh_network_window.set_station_count_text(station_count)
        self.mesh_network_window.set_lookback_minutes_text(lookback_minutes)
        self.mesh_network_window.set_broadcast_interval_text(broadcast_interval)
        self.mesh_network_window.set_broadcast_times_24h_text(times_text)
        self.mesh_network_window.set_tx_time_limit_minutes_text(tx_time_limit_minutes)
        self._sync_mesh_lookback_controls()

        lines, _preview_text = self._prepare_mesh_broadcast(
            station_count=station_count,
            lookback_minutes=lookback_minutes,
            copy_to_clipboard=True
        )

        if invalid_times:
            self._dark_info_dialog(
                "Invalid Time Entries Removed",
                "These time entries were invalid and were removed:\n\n"
                + "\n".join(invalid_times)
                + "\n\nThe valid time entries were saved.",
                parent=self.mesh_network_window.window,
                refocus_widget=self.mesh_network_window.window,
            )
            return

        if not lines:
            self._dark_info_dialog(
                "No Mesh Report Data",
                "No mesh report lines could be generated.",
                parent=self.mesh_network_window.window,
                refocus_widget=self.mesh_network_window.window,
            )
            return

        self._dark_info_dialog(
            "Mesh Report Prepared",
            "Mesh report lines were generated and copied to clipboard.",
            parent=self.mesh_network_window.window,
            refocus_widget=self.mesh_network_window.window,
        )

    def copy_mesh_preview(self):
        text = self.mesh_network_window.get_preview_text()
        if not text:
            self._dark_info_dialog(
                "Nothing to Copy",
                "There is no mesh report preview text to copy.",
                parent=self.mesh_network_window.window,
                refocus_widget=self.mesh_network_window.window,
            )
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

        self._dark_info_dialog(
            "Copied",
            "Mesh report preview copied to clipboard.",
            parent=self.mesh_network_window.window,
            refocus_widget=self.mesh_network_window.window,
        )

    def _show_scheduled_mesh_report_dialog(self, preview_text, mode_name):
        parent_widget = self.mesh_network_window.window if getattr(self, "mesh_network_window", None) and self.mesh_network_window.has_window() else self.root
        return self._show_send_confirmation_dialog(
            dialog_title="Scheduled Mesh Report",
            title_text="JS8Mesh has generated this scheduled mesh report based on your settings.",
            body_text=(
                f"Mode: {str(mode_name or 'NORMAL').strip().upper()}\n\n"
                f"Generated message ready to send to JS8Call:\n{str(preview_text or '').strip()}"
            ),
            footer_text=(
                f"{self._js8call_send_effects_text(mode_name)}\n\n"
                "Press Send to JS8Call to continue or Cancel to abort."
            ),
            parent_widget=parent_widget,
            confirm_label="Send to JS8Call",
            cancel_label="Cancel",
        )

    def _mesh_scheduler_tick(self):
        try:
            station_count = self._safe_positive_int(
                settings.get("mesh_station_count", 5),
                5,
                minimum=1
            )
            interval_minutes = self._safe_positive_int(
                settings.get("mesh_broadcast_interval_minutes", 20),
                10,
                minimum=1
            )

            now = datetime.now()
            slots = self._mesh_broadcast_slots()

            due = False

            if slots:
                current_hhmm = now.strftime("%H:%M")
                slot_key = f"{now.strftime('%Y-%m-%d')} {current_hhmm}"

                if current_hhmm in slots and self.last_mesh_scheduled_slot_key != slot_key:
                    due = True
                    self.last_mesh_scheduled_slot_key = slot_key
            else:
                if self.last_mesh_broadcast_time is None:
                    due = True
                else:
                    elapsed_seconds = (now - self.last_mesh_broadcast_time).total_seconds()
                    if elapsed_seconds >= interval_minutes * 60:
                        due = True

            if due:
                event_id = f"TXMESH-{int(time.time() * 1000)}"
                frequency_text = self._normalize_frequency_text(self.selected_frequency_var.get()) or self.selected_frequency_var.get().strip()
                if self.ignore_freshness_var.get():
                    lookback_minutes = self._current_mesh_lookback_minutes(
                        for_slot_hhmm=now.strftime("%H:%M") if slots else None,
                    )
                else:
                    lookback_minutes = self._mesh_derived_lookback_minutes(
                        raw_times_text=settings.get("mesh_broadcast_times_24h", ""),
                        broadcast_interval=interval_minutes,
                        for_slot_hhmm=now.strftime("%H:%M") if slots else None,
                    )
                lines, preview_text = self._prepare_mesh_broadcast(
                    station_count=station_count,
                    lookback_minutes=lookback_minutes,
                    copy_to_clipboard=False
                )
                target_mode = str(settings.get("mesh_tx_mode", "NORMAL") or "NORMAL").strip().upper() or "NORMAL"
                preview_text = str(preview_text or "").strip()
                self._append_tx_mesh_reports_log({
                    "event_id": event_id,
                    "timestamp": now.isoformat(timespec="seconds"),
                    "requester": "",
                    "request_type": "MESH",
                    "frequency": frequency_text,
                    "reply_text": preview_text if lines else "",
                    "speed": target_mode,
                    "status": "GENERATED" if lines and preview_text and not preview_text.startswith("No mesh report lines could be generated.") else "SKIPPED",
                    "reason": "Scheduled mesh report prepared." if lines and preview_text and not preview_text.startswith("No mesh report lines could be generated.") else "No scheduled mesh report could be generated at this time.",
                })
                if lines and preview_text and not preview_text.startswith("No mesh report lines could be generated."):
                    if self._show_scheduled_mesh_report_dialog(preview_text, target_mode):
                        self._send_text_to_js8call_async(
                            text=preview_text,
                            target_mode=target_mode,
                            settings_key="mesh_tx_mode",
                            parent_window=self.mesh_network_window.window if getattr(self, "mesh_network_window", None) else None,
                            success_callback=lambda _result, eid=event_id: self._update_tx_mesh_reports_log_status(
                                eid,
                                "SENT",
                                "Sent to JS8Call from scheduled mesh report."
                                if bool(_result.get("tx_completed"))
                                else "JS8Call began transmitting the scheduled mesh report, but JS8Mesh could not confirm that the transmission finished cleanly.",
                                reply_text=preview_text,
                            ),
                            error_callback=lambda error_text, eid=event_id: self._update_tx_mesh_reports_log_status(eid, "SKIPPED", f"Send failed: {error_text}", reply_text=preview_text),
                        )
                    else:
                        self._update_tx_mesh_reports_log_status(event_id, "SKIPPED", "Skipped: user canceled scheduled mesh report send.", reply_text=preview_text)
            else:
                self._update_mesh_status_text()

        finally:
            self.root.after(1000, self._mesh_scheduler_tick)

    # ------------------------------------------------
    # File loading / history
    # ------------------------------------------------

    def select_file(self):
        path = filedialog.askopenfilename(
            title="Select DIRECTED.TXT",
            filetypes=[("TXT files", "*.TXT")]
        )

        if path:
            self.directed_file = path
            settings["directed_file"] = path
            save_settings(settings)
            self.force_read_directed_txt()


    def _reset_activity_maintenance_prompt_if_below_threshold(self):
        if len(self.records) < self.activity_warn_threshold:
            if settings.get("activity_db_prompt_active"):
                settings["activity_db_prompt_active"] = False
                save_settings(settings)

    def _trim_oldest_activity_records(self, trim_count=None, save_immediately=True):
        trim_count = self._safe_positive_int(
            trim_count if trim_count is not None else self.activity_trim_amount,
            fallback=self.activity_trim_amount,
            minimum=1
        )

        if trim_count <= 0 or not self.records:
            return 0

        trim_count = min(trim_count, len(self.records))
        trimmed_records = self.records[:trim_count]
        self.records = self.records[trim_count:]

        self.record_keys = {self._record_key(r) for r in self.records}

        trimmed_raw_lines = {
            str(record.get("raw", "")).strip()
            for record in trimmed_records
            if str(record.get("raw", "")).strip()
        }

        if trimmed_raw_lines:
            self.processed.difference_update(trimmed_raw_lines)

        del snr_reports_db[:trim_count]

        if save_immediately:
            save_snr_reports(snr_reports_db)

        self.rebuild_activity_window_from_records()
        self.refresh_topology_window()
        self._reset_activity_maintenance_prompt_if_below_threshold()

        return trim_count

    def _export_records_to_csv_path(self, path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "DATE", "TIME", "FROM", "TO", "FROM_NORM", "TO_NORM",
                "MSG", "FREQUENCY", "SNR", "RAW", "DATETIME_ISO"
            ])

            for record in self.records:
                dt = record.get("datetime")
                writer.writerow([
                    record.get("date", ""),
                    record.get("time", ""),
                    record.get("from", ""),
                    record.get("to", ""),
                    record.get("from_norm", ""),
                    record.get("to_norm", ""),
                    record.get("msg", ""),
                    record.get("freq", ""),
                    record.get("snr", ""),
                    record.get("raw", ""),
                    dt.isoformat() if dt else "",
                ])

    def _export_and_trim_activity_records(self):
        path = filedialog.asksaveasfilename(
            title="Export SNR Reports Database",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )

        if not path:
            return False

        self._export_records_to_csv_path(path)
        trimmed = self._trim_oldest_activity_records(self.activity_trim_amount, save_immediately=True)

        messagebox.showinfo(
            "Export and Trim Complete",
            f"SNR Reports Database exported successfully.\n\n"
            f"Trimmed {trimmed} oldest record(s) from activity storage."
        )
        return True

    def _trim_activity_records_only(self):
        trimmed = self._trim_oldest_activity_records(self.activity_trim_amount, save_immediately=True)

        messagebox.showinfo(
            "Trim Complete",
            f"Trimmed {trimmed} oldest record(s) from activity storage."
        )
        return True

    def _close_activity_maintenance_dialog(self):
        if self.activity_maintenance_dialog is not None:
            try:
                self.activity_maintenance_dialog.destroy()
            except Exception:
                pass
            self.activity_maintenance_dialog = None

    def _show_activity_maintenance_dialog(self):
        if self.activity_maintenance_dialog is not None and self.activity_maintenance_dialog.winfo_exists():
            self.activity_maintenance_dialog.lift()
            self.activity_maintenance_dialog.focus_force()
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Activity Database Maintenance")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self.activity_maintenance_dialog = dialog

        outer = tk.Frame(dialog, bg=self.bg_color, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        message_text = (
            f"Activity database has reached {len(self.records)} lines.\n\n"
            f"To keep JS8Mesh fast and stable, export to CSV and trim about "
            f"{self.activity_trim_amount} oldest lines from activity storage."
        )

        tk.Label(
            outer,
            text=message_text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            wraplength=460
        ).pack(fill="x")

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        def do_export_and_trim():
            if self._export_and_trim_activity_records():
                settings["activity_db_prompt_active"] = False
                save_settings(settings)
                self._close_activity_maintenance_dialog()

        def do_trim_only():
            if self._trim_activity_records_only():
                settings["activity_db_prompt_active"] = False
                save_settings(settings)
                self._close_activity_maintenance_dialog()

        def do_later():
            settings["activity_db_prompt_active"] = True
            save_settings(settings)
            self._close_activity_maintenance_dialog()

        tk.Button(
            button_row,
            text="Export + Trim",
            command=do_export_and_trim,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=16
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Trim Only",
            command=do_trim_only,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            button_row,
            text="Later",
            command=do_later,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", do_later)
        dialog.update_idletasks()

        x = self.root.winfo_rootx() + 100
        y = self.root.winfo_rooty() + 100
        dialog.geometry(f"+{x}+{y}")

    def _maybe_prompt_activity_maintenance(self):
        if len(self.records) < self.activity_warn_threshold:
            self._reset_activity_maintenance_prompt_if_below_threshold()
            return

        if settings.get("activity_db_prompt_active"):
            return

        settings["activity_db_prompt_active"] = True
        save_settings(settings)
        self.root.after(0, self._show_activity_maintenance_dialog)

    def force_read_directed_txt(self):
        if not os.path.exists(self.directed_file):
            messagebox.showwarning(
                "File not found",
                f"{self.directed_file} does not exist!"
            )
            return

        with open(self.directed_file, encoding="utf-8") as f:
            lines = f.readlines()

        added_count = 0

        for line in lines:
            if line in self.processed:
                continue

            parsed = parse_directed_line(line)

            if not parsed:
                continue

            classify_callsign(parsed["from"])
            classify_callsign(parsed["to"])

            if self._append_record(parsed, source_line=line, save_immediately=False):
                added_count += 1

        if added_count > 0:
            self._sort_records_and_storage(save_immediately=False)
            save_snr_reports(snr_reports_db)
            self.rebuild_activity_window_from_records()
            self._refresh_current_pathway_view()
            self._maybe_prompt_activity_maintenance()

        self.refresh_topology_window()

        messagebox.showinfo(
            "Force Read DIRECTED.TXT",
            f"Added {added_count} new line(s) from DIRECTED.TXT."
        )

        try:
            stat_result = os.stat(self.directed_file)
            self._directed_last_mtime_ns = getattr(stat_result, "st_mtime_ns", None)
            self._directed_last_size = int(getattr(stat_result, "st_size", 0) or 0)
            with open(self.directed_file, encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                self._directed_tail_position = f.tell()
            self._directed_partial_line = ""
        except Exception:
            pass

    def _read_appended_directed_records(self):
        if not self.directed_file or not os.path.exists(self.directed_file):
            return []

        new_records = []
        try:
            stat_result = os.stat(self.directed_file)
            current_mtime_ns = getattr(stat_result, "st_mtime_ns", None)
            current_size = int(getattr(stat_result, "st_size", 0) or 0)
            rewrite_detected = (
                self._directed_last_mtime_ns is not None
                and current_mtime_ns != self._directed_last_mtime_ns
                and current_size <= self._directed_tail_position
            )

            if rewrite_detected:
                with open(self.directed_file, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                self._directed_tail_position = current_size
                self._directed_partial_line = ""
                self._directed_last_mtime_ns = current_mtime_ns
                self._directed_last_size = current_size

                for line in lines:
                    if line in self.processed:
                        continue
                    parsed = parse_directed_line(line)
                    if not parsed:
                        continue
                    new_records.append((parsed, line))

                return new_records

            with open(self.directed_file, encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                file_end = f.tell()

                if self._directed_tail_position > file_end:
                    self._directed_tail_position = 0
                    self._directed_partial_line = ""

                f.seek(self._directed_tail_position)
                chunk = f.read()
                self._directed_tail_position = f.tell()
                self._directed_last_mtime_ns = current_mtime_ns
                self._directed_last_size = current_size
        except Exception:
            return []

        if not chunk:
            return []

        text = f"{self._directed_partial_line}{chunk}"
        self._directed_partial_line = ""
        lines = text.splitlines(keepends=True)

        if lines and not lines[-1].endswith(("\n", "\r")):
            self._directed_partial_line = lines.pop()

        for line in lines:
            if line in self.processed:
                continue
            parsed = parse_directed_line(line)
            if not parsed:
                continue
            new_records.append((parsed, line))

        return new_records

    def add_to_activity(self, record):
        if not self.activity_window.has_window():
            return

        self.activity_window.add_row_top(
            values=(
                record["date"],
                record["time"],
                record["snr"],
                record["from"],
                record["to"],
                self._activity_message_text(record),
                record["freq"],
            )
        )

    def export_snr_database_csv(self):
        path = filedialog.asksaveasfilename(
            title="Export SNR Reports Database",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )

        if not path:
            return

        self._export_records_to_csv_path(path)

        messagebox.showinfo(
            "Export Complete",
            "SNR Reports Database exported successfully."
        )


    def _safe_parse_iso_datetime(self, value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _now(self):
        return datetime.now()

    def _record_now(self):
        return utc_now_naive()

    def _minutes_ago_from_iso(self, iso_text):
        dt = self._safe_parse_iso_datetime(iso_text)
        if dt is None:
            return None
        minutes = int((self._now() - dt).total_seconds() / 60.0)
        if minutes < 0:
            minutes = 0
        return minutes

    def _last_success_display(self, rec):
        minutes = self._minutes_ago_from_iso(rec.get("last_success_time", ""))
        if minutes is None:
            return ""
        return f"{minutes}m"

    def _confidence_display(self, rec):
        try:
            value = float(rec.get("reliability_points", 0.0) or 0.0)
        except Exception:
            value = 0.0
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _latest_relay_result_time_for_pathway(self, pathway_text, result_code):
        target_path = self._operational_path_from_inbound_display(pathway_text).strip().upper()
        if not target_path:
            return ""

        latest_dt = None
        latest_iso = ""

        for item in relay_history_db:
            if str(item.get("result", "")).strip().upper() != str(result_code).strip().upper():
                continue
            item_path = self._operational_path_from_inbound_display(item.get("pathway", "")).strip().upper()
            if item_path != target_path:
                continue
            timestamp_text = str(item.get("timestamp", "")).strip()
            dt = self._safe_parse_iso_datetime(timestamp_text)
            if dt is None:
                continue
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
                latest_iso = timestamp_text

        return latest_iso

    def _enrich_pathway_history_fields(self, rec):
        rec = dict(rec or {})
        if not str(rec.get("last_success_time", "")).strip():
            rec["last_success_time"] = self._latest_relay_result_time_for_pathway(rec.get("pathway", ""), "S")
        if not str(rec.get("last_failure_time", "")).strip():
            rec["last_failure_time"] = self._latest_relay_result_time_for_pathway(rec.get("pathway", ""), "F")
        return rec

    def _relay_nodes_from_pathway_text(self, pathway_text):
        operational = self._operational_path_from_inbound_display(pathway_text)
        parts = [part.strip().upper() for part in str(operational or "").split(">") if part.strip()]
        if len(parts) < 3:
            return []
        return parts[1:-1]

    def _pathway_reliability_text_from_db(self, pathway_text):
        return pathway_reliability_text(pathway_text, relay_history_db=relay_history_db)

    def _pathway_reliability_points_from_db(self, pathway_text):
        return pathway_reliability_points(pathway_text, relay_history_db=relay_history_db)

    def _operational_path_from_inbound_display(self, pathway_text):
        return str(pathway_text or "").replace("<", ">").replace("  ", " ").strip()

    def _inbound_display_path_from_operational(self, pathway_text):
        parts = [part.strip() for part in str(pathway_text or "").split(">") if part.strip()]
        if len(parts) < 2:
            return str(pathway_text or "").strip()
        if len(parts) == 2:
            return f"{parts[0]}<{parts[1]}"
        return ">".join(parts[:-1]) + f"<{parts[-1]}"


    def _inbound_state_key(self, pathway_text):
        text = str(pathway_text or "").strip().upper()
        return re.sub(r"\s+", "", text)

    def _retry_warning_text_for_state(self, state):
        state = dict(state or {})
        until_dt = self._safe_parse_iso_datetime(state.get("failure_cooldown_until", ""))
        failed_dt = self._safe_parse_iso_datetime(state.get("last_failure_time", ""))
        if until_dt is None:
            return ""
        now = self._now()
        if now >= until_dt:
            return ""
        mins_left = max(1, int((until_dt - now).total_seconds() / 60.0))
        if failed_dt is None:
            return f"Cooling Down ({mins_left}m)"
        minutes_ago = max(0, int((now - failed_dt).total_seconds() / 60.0))
        return f"Cooling Down ({mins_left}m) — This route was tried and failed {minutes_ago} min ago. Retry anyway?"


    def _find_inbound_state_for_pathway(self, pathway_text):
        target_key = self._inbound_state_key(pathway_text)
        if not target_key:
            return {}

        for raw_key, raw_state in inbound_routes_db.items():
            state = dict(raw_state or {})
            candidate_keys = {
                self._inbound_state_key(raw_key),
                self._inbound_state_key(state.get("display_path", "")),
                self._inbound_state_key(state.get("operational_path", "")),
                self._inbound_state_key(
                    self._operational_path_from_inbound_display(state.get("display_path", ""))
                ),
                self._inbound_state_key(
                    self._inbound_display_path_from_operational(state.get("operational_path", ""))
                ),
            }
            if target_key in candidate_keys:
                return state

        latest_failed_dt = None
        for item in relay_history_db:
            if not isinstance(item, dict):
                continue
            result = str(item.get("result", "")).strip().upper()
            if result not in ("F", "FAILED", "FAIL"):
                continue
            hist_path = str(item.get("pathway", "")).strip()
            if self._inbound_state_key(hist_path) != target_key:
                continue
            dt = self._safe_parse_iso_datetime(item.get("timestamp", ""))
            if dt is None:
                continue
            if latest_failed_dt is None or dt > latest_failed_dt:
                latest_failed_dt = dt

        if latest_failed_dt is None:
            return {}

        return {
            "display_path": str(pathway_text or "").strip(),
            "last_failure_time": latest_failed_dt.isoformat(timespec="seconds"),
            "failure_cooldown_until": (latest_failed_dt + timedelta(minutes=FAILURE_COOLDOWN_MINUTES)).isoformat(timespec="seconds"),
            "origin": "inbound",
        }

    def _build_inbound_promotion_evidence_row(self, rec):
        details = []
        origin = str(rec.get("origin", "")).strip().lower()
        if origin in ("manual_linear", "native_linear_manual"):
            details.append("Linear route confirmed from promoted inbound")
        elif origin in ("inbound_promoted", "promoted_inbound"):
            details.append("Successful inbound reachability promotion")
        else:
            details.append("Inbound reachability candidate")
        convergence = str(rec.get("convergence", "")).strip() or str(rec.get("convergence_node", "")).strip()
        target = str(rec.get("target", "")).strip()
        op_path = str(rec.get("operational_path", "")).strip()
        if convergence:
            details.append(f"Convergence node: {convergence}")
        if target:
            details.append(f"Target: {target}")
        if op_path:
            details.append(f"Operational path: {op_path}")
        last_success = str(rec.get("last_success_time", "")).strip()
        last_failure = str(rec.get("last_failure_time", "")).strip()
        if last_success:
            details.append(f"Last success: {last_success}")
        if last_failure:
            details.append(f"Last failure: {last_failure}")
        return {
            "hop_index": 0,
            "from_display": convergence or "-",
            "to_display": target or "-",
            "evidence_type": "LINEAR CONFIRMED" if origin in ("manual_linear", "native_linear_manual") else ("PROMOTED INBOUND" if origin in ("inbound_promoted", "promoted_inbound") else "INBOUND"),
            "used_snr": "",
            "used_snr_text": "",
            "age_minutes": None,
            "age_text": "",
            "freq": "",
            "local_monitor_snr": None,
            "local_monitor_snr_text": "",
            "payload_reported_snr": None,
            "payload_reported_snr_text": "",
            "source_from": "",
            "source_to": "",
            "msg": " | ".join(details),
            "raw": " | ".join(details),
            "datetime": None,
        }

    def _merge_inbound_state_into_recommendation(self, rec):
        rec = dict(rec or {})
        rec.setdefault("origin", "inbound")
        rec.setdefault("operational_path", self._operational_path_from_inbound_display(rec.get("pathway", "")))
        rec.setdefault("convergence_node", rec.get("convergence", ""))
        state = self._find_inbound_state_for_pathway(rec.get("pathway", ""))
        if state:
            if state.get("origin"):
                rec["origin"] = state.get("origin")
            rec["last_success_time"] = state.get("last_success_time", "")
            rec["last_failure_time"] = state.get("last_failure_time", "")
            rec["failure_cooldown_until"] = state.get("failure_cooldown_until", "")
            rec["promotion_started_at"] = state.get("promotion_started_at", "")
            rec["operational_path"] = state.get("operational_path", rec.get("operational_path", ""))
            rec["convergence_node"] = state.get("convergence_node", rec.get("convergence_node", ""))
            rec["target"] = state.get("target", rec.get("target", ""))
            if "retry_warning" in state:
                rec["retry_warning"] = state.get("retry_warning", "")

        operational_path = str(rec.get("operational_path", "")).strip() or self._operational_path_from_inbound_display(rec.get("pathway", ""))
        if operational_path:
            rec["operational_path"] = operational_path
            rec["reliability"] = self._pathway_reliability_text_from_db(operational_path)
            rec["reliability_points"] = self._pathway_reliability_points_from_db(operational_path)
            history = pathway_reliability_components(operational_path, relay_history_db=relay_history_db)
            rec["exact_success_points"] = history.get("exact_success_points", 0.0)
            rec["exact_failure_points"] = history.get("exact_failure_points", 0.0)
            rec["exact_reliability_points"] = history.get("exact_reliability_points", 0.0)
            rec["inherited_reliability_points"] = history.get("inherited_reliability_points", 0.0)

        retry_text = self._retry_warning_text_for_state(rec)
        rec["retry_warning"] = retry_text

        if rec.get("origin") in ("manual_linear", "native_linear_manual"):
            rec["warning"] = "Linear Pathway"
            rec["retry_warning"] = ""
            rec["failure_cooldown_until"] = ""
            rec["last_failure_time"] = ""
        elif rec.get("origin") in ("inbound_promoted", "promoted_inbound"):
            rec["warning"] = "Promoted Inbound"
            rec["retry_warning"] = ""
            rec["failure_cooldown_until"] = ""
            rec["last_failure_time"] = ""
        else:
            if retry_text:
                rec["warning"] = retry_text.split("—", 1)[0].strip() or "Cooling Down"
            else:
                rec["warning"] = "Inbound Reachability"

        evidence = list(rec.get("evidence", []) or [])
        evidence.insert(0, self._build_inbound_promotion_evidence_row(rec))
        rec["evidence"] = evidence
        return rec

    def _apply_retry_warning_if_needed(self, rec):
        if not rec:
            return True
        retry_text = str(rec.get("retry_warning", "")).strip()
        if not retry_text:
            return True
        pathway = str(rec.get("pathway", "")).strip()
        now = self._now()
        if self._last_retry_warning_pathway == pathway and self._last_retry_warning_at is not None:
            if (now - self._last_retry_warning_at).total_seconds() < 5:
                return True
        ok = self._dark_confirm_dialog("Retry Warning", retry_text)
        if ok:
            self._last_retry_warning_pathway = pathway
            self._last_retry_warning_at = now
        return ok

    def _update_inbound_route_state_from_result(self, rec, result):
        if not rec:
            return
        origin = str(rec.get("origin", "")).strip().lower()
        if origin not in ("inbound", "inbound_promoted", "promoted_inbound", "manual_linear", "native_linear_manual"):
            return

        now = self._now()
        key = self._inbound_state_key(rec.get("pathway", ""))
        state = dict(self._find_inbound_state_for_pathway(rec.get("pathway", "")))
        state["display_path"] = rec.get("pathway", "")
        state["operational_path"] = rec.get("operational_path", self._operational_path_from_inbound_display(rec.get("pathway", "")))
        state["convergence_node"] = rec.get("convergence_node", rec.get("convergence", ""))
        state["target"] = rec.get("target", self.target_call_var.get().strip().upper())
        state["category"] = str(rec.get("category", state.get("category", "FAST"))).strip().upper() or "FAST"
        state["score"] = int(rec.get("score", state.get("score", 0)) or 0)
        state["relays"] = int(rec.get("relays", state.get("relays", 0)) or 0)
        state["reliability"] = self._pathway_reliability_text_from_db(state.get("operational_path", rec.get("pathway", "")))
        state["reliability_points"] = self._pathway_reliability_points_from_db(state.get("operational_path", rec.get("pathway", "")))
        state["freshness"] = str(rec.get("freshness", state.get("freshness", ""))).strip()
        if origin in ("inbound_promoted", "promoted_inbound"):
            state["origin"] = "inbound_promoted"
        elif origin in ("manual_linear", "native_linear_manual"):
            state["origin"] = "manual_linear"
        else:
            state["origin"] = "inbound"

        if result == "S":
            if origin in ("inbound_promoted", "promoted_inbound") and str(self.current_pathway_view).strip().lower() == "linear":
                state["origin"] = "manual_linear"
            elif origin in ("manual_linear", "native_linear_manual"):
                state["origin"] = "manual_linear"
            else:
                state["origin"] = "inbound_promoted"
            state["last_success_time"] = now.isoformat(timespec="seconds")
            if not str(state.get("promotion_started_at", "")).strip():
                state["promotion_started_at"] = now.isoformat(timespec="seconds")
            state["last_failure_time"] = ""
            state["failure_cooldown_until"] = ""
        else:
            if state.get("origin") in ("inbound_promoted", "manual_linear"):
                state["last_failure_time"] = now.isoformat(timespec="seconds")
                state["failure_cooldown_until"] = ""
            else:
                state["origin"] = "inbound"
                state["last_failure_time"] = now.isoformat(timespec="seconds")
                state["failure_cooldown_until"] = (now + timedelta(minutes=FAILURE_COOLDOWN_MINUTES)).isoformat(timespec="seconds")

        inbound_routes_db[key] = state
        save_inbound_routes(inbound_routes_db)

    def _build_promoted_inbound_linear_recommendations(self, records, user_cs, target_cs, max_age_minutes, max_relays=None):
        target_norm = str(target_cs or "").strip().upper()
        promoted = []
        manual_linear_paths = set()

        for raw_key, raw_state in inbound_routes_db.items():
            if not isinstance(raw_state, dict):
                continue

            state = dict(raw_state)
            origin = str(state.get("origin", "")).strip().lower()
            if origin not in ("inbound_promoted", "promoted_inbound", "manual_linear", "native_linear_manual"):
                continue

            state_target = str(state.get("target", "")).strip().upper()
            if target_norm and state_target and state_target != target_norm:
                continue

            display_path = str(state.get("display_path", raw_key)).strip()
            operational_path = str(
                state.get("operational_path", self._operational_path_from_inbound_display(display_path))
            ).strip()
            if not operational_path:
                continue

            relay_count = max(0, len([p for p in operational_path.split(">") if p.strip()]) - 2)
            if max_relays is not None and relay_count > int(max_relays):
                continue
            is_manual_linear = origin in ("manual_linear", "native_linear_manual")
            if is_manual_linear:
                manual_linear_paths.add(operational_path.upper())

            candidate = {
                "pathway": operational_path,
                "operational_path": operational_path,
                "category": str(state.get("category", "FAST")).strip().upper() or "FAST",
                "warning": "" if is_manual_linear else "Promoted Inbound",
                "relays": int(state.get("relays", relay_count) or relay_count),
                "score": int(state.get("score", 0) or 0),
                "reliability": self._pathway_reliability_text_from_db(operational_path),
                "reliability_points": self._pathway_reliability_points_from_db(operational_path),
                "freshness": str(state.get("freshness", ">65 min")).strip() or ">65 min",
                "origin": "manual_linear" if is_manual_linear else "inbound_promoted",
                "convergence_node": state.get("convergence_node", ""),
                "target": state.get("target", ""),
                "last_success_time": state.get("last_success_time", ""),
                "last_failure_time": state.get("last_failure_time", ""),
                "failure_cooldown_until": state.get("failure_cooldown_until", ""),
                "promotion_started_at": state.get("promotion_started_at", ""),
                "evidence": [self._build_inbound_promotion_evidence_row(state)],
                "is_direct": False,
            }
            promoted.append(candidate)

        return promoted, manual_linear_paths

    # ------------------------------------------------
    # Pathway generation
    # ------------------------------------------------

    def show_inbound_reachability_pathways(self):
        if not self._apply_frequency_value(show_error=True):
            return

        target = self.target_call_var.get().strip().upper()
        user_cs = self.user_call_var.get().strip().upper()

        if not user_cs or not target:
            messagebox.showwarning(
                "Missing Callsign",
                "Enter both User and Target callsigns."
            )
            return

        max_age_minutes = self._current_max_age_minutes()
        frequency_records = self._records_for_selected_frequency()
        selected_frequency = self.selected_frequency_var.get().strip()

        self.pathways_panel.clear_rows()
        self.last_pathway_recommendations = {}
        self._current_pathway_rows = []
        self.current_pathway_view = "inbound"
        self.current_view_title_var.set("VIEWING: INBOUND REACHABILITY")

        recommendations = recommend_inbound_reachability_paths(
            records=frequency_records,
            user_cs=user_cs,
            target_cs=target,
            max_hops=self.max_hops_var.get(),
            min_snr=self.min_snr_var.get(),
            max_age_minutes=max_age_minutes,
            reliability_db=reliability_db,
            ignore_freshness=bool(self.ignore_freshness_var.get()),
            relay_history_db=relay_history_db,
        )

        merged_recommendations = [
            self._enrich_pathway_history_fields(self._merge_inbound_state_into_recommendation(rec))
            for rec in recommendations
        ]
        promoted_inbound_routes, manual_linear_paths = self._build_promoted_inbound_linear_recommendations(
            frequency_records,
            user_cs,
            target,
            max_age_minutes=max_age_minutes,
            max_relays=self.max_hops_var.get(),
        )

        native_linear_paths = {
            str(rec.get("pathway", "")).strip().upper()
            for rec in recommend_paths(
                records=frequency_records,
                user_cs=user_cs,
                target_cs=target,
                max_hops=self.max_hops_var.get(),
                min_snr=self.min_snr_var.get(),
                max_age_minutes=max_age_minutes,
                reliability_db=reliability_db,
                classify_callsign=classify_callsign,
                promoted_inbound_routes=None,
                relay_history_db=relay_history_db,
            )
            if str(rec.get("pathway", "")).strip()
        }
        merged_recommendations = [
            rec for rec in merged_recommendations
            if str(
                rec.get(
                    "operational_path",
                    self._operational_path_from_inbound_display(rec.get("pathway", "")),
                )
            ).strip().upper() not in native_linear_paths
            and str(
                rec.get(
                    "operational_path",
                    self._operational_path_from_inbound_display(rec.get("pathway", "")),
                )
            ).strip().upper() not in manual_linear_paths
        ]
        seen_paths = {str(rec.get("pathway", "")).strip().upper() for rec in merged_recommendations if str(rec.get("pathway", "")).strip()}

        for promoted in promoted_inbound_routes:
            display_path = self._inbound_display_path_from_operational(promoted.get("operational_path", promoted.get("pathway", "")))
            if not display_path:
                continue
            key = str(display_path).strip().upper()
            if key in seen_paths:
                continue
            if str(promoted.get("operational_path", "")).strip().upper() in native_linear_paths:
                continue
            synthetic = dict(promoted)
            synthetic["pathway"] = display_path
            if str(synthetic.get("origin", "")).strip().lower() in ("manual_linear", "native_linear_manual"):
                synthetic["warning"] = "Linear Pathway"
                synthetic["origin"] = "manual_linear"
            else:
                synthetic["warning"] = "Promoted Inbound"
                synthetic["origin"] = "inbound_promoted"
            synthetic["freshness"] = promoted.get("freshness", "") or ">65 min"
            synthetic = self._enrich_pathway_history_fields(synthetic)
            merged_recommendations.append(synthetic)
            seen_paths.add(key)

        row_defs = []
        for rec in merged_recommendations:
            self.last_pathway_recommendations[rec.get("pathway", "")] = rec
            status_text = str(rec.get("warning", "")).strip() or "Inbound Reachability"
            row_tag = self.pathways_panel.pathway_row_tag(
                rec.get("category", ""),
                status_text,
                rec.get("relays", 0)
            )
            row_defs.append({
                "values": (
                    rec.get("pathway", ""),
                    self.pathways_panel.decorated_category(rec.get("category", "")),
                    status_text,
                    rec.get("relays", 0),
                    rec.get("score", 0),
                    rec.get("reliability", "0/0"),
                    self._confidence_display(rec),
                    self._last_success_display(rec),
                    rec.get("freshness", ""),
                ),
                "tag": row_tag,
            })

        if not row_defs:
            self.relay_builder.update_message_preview()
            self.refresh_topology_window()
            self._dark_info_dialog(
                "No Inbound Reachability Found",
                "No convergence stations were found on the selected frequency.\n\n"
                f"Selected frequency: {selected_frequency}\n\n"
                "Inbound reachability requires at least one station that:\n"
                "- you can reach, and\n"
                f"- directly hears {target}\n\n"
                "Check that relevant records exist for this frequency and are fresh enough, or enable Test Mode."
            )
            return

        self._set_current_pathway_rows(row_defs)
        self.relay_builder.update_message_preview()
        self.refresh_topology_window()

    def show_pathways(self):
        if not self._apply_frequency_value(show_error=True):
            return

        target = self.target_call_var.get().strip().upper()
        user_cs = self.user_call_var.get().strip().upper()

        if not user_cs or not target:
            messagebox.showwarning(
                "Missing Callsign",
                "Enter both User and Target callsigns."
            )
            return

        max_age_minutes = self._current_max_age_minutes()
        frequency_records = self._records_for_selected_frequency()
        selected_frequency = self.selected_frequency_var.get().strip()

        self.pathways_panel.clear_rows()
        self.last_pathway_recommendations = {}
        self._current_pathway_rows = []
        self.current_pathway_view = "linear"
        self.current_view_title_var.set("VIEWING: LINEAR PATHWAYS")

        promoted_inbound_routes, _manual_linear_paths = self._build_promoted_inbound_linear_recommendations(
            frequency_records,
            user_cs,
            target,
            max_age_minutes=max_age_minutes,
            max_relays=self.max_hops_var.get(),
        )

        recommendations = recommend_paths(
            records=frequency_records,
            user_cs=user_cs,
            target_cs=target,
            max_hops=self.max_hops_var.get(),
            min_snr=self.min_snr_var.get(),
            max_age_minutes=max_age_minutes,
            reliability_db=reliability_db,
            classify_callsign=classify_callsign,
            promoted_inbound_routes=promoted_inbound_routes,
            relay_history_db=relay_history_db,
        )
        recommendations = [self._enrich_pathway_history_fields(rec) for rec in recommendations]

        for rec in recommendations:
            if not str(rec.get("warning", "")).strip():
                rec["warning"] = "Direct" if rec.get("is_direct") else "Linear Pathway"
            self.last_pathway_recommendations[rec.get("pathway", "")] = rec

        direct_path_text = f"{user_cs}>{target}"
        direct_already_present = any(
            str(rec.get("pathway", "")).strip().upper() == direct_path_text
            for rec in recommendations
        )

        direct_user_heard_target, direct_target_heard_user = latest_direct_reports(
            frequency_records,
            user_cs,
            target,
            max_age_minutes=max_age_minutes,
        )

        has_direct = bool(direct_user_heard_target)

        if has_direct and not direct_already_present:
            category = direct_path_category(
                frequency_records,
                user_cs,
                target,
                max_age_minutes=max_age_minutes
            )
            manual_direct_rec = {
                "pathway": direct_path_text,
                "category": category,
                "relays": 0,
                "score": direct_path_score(
                    frequency_records,
                    user_cs,
                    target,
                    max_age_minutes=max_age_minutes
                ),
                "reliability": self._pathway_reliability_text_from_db(direct_path_text),
                "freshness": direct_path_freshness(
                    frequency_records,
                    user_cs,
                    target,
                    max_age_minutes=max_age_minutes
                ),
                "reliability_points": self._pathway_reliability_points_from_db(direct_path_text),
                "is_direct": True,
                "evidence": direct_path_evidence(
                    frequency_records,
                    user_cs,
                    target,
                    max_age_minutes=max_age_minutes,
                ),
            }
            manual_direct_rec = self._enrich_pathway_history_fields(manual_direct_rec)
            self.last_pathway_recommendations[direct_path_text] = manual_direct_rec
            direct_status = "Direct"
            row_tag = self.pathways_panel.pathway_row_tag(category, direct_status, 0)

            self.pathways_panel.insert_row(
                values=(
                    direct_path_text,
                    self.pathways_panel.decorated_category(category),
                    direct_status,
                    0,
                    manual_direct_rec["score"],
                    manual_direct_rec["reliability"],
                    self._confidence_display(manual_direct_rec),
                    self._last_success_display(manual_direct_rec),
                    manual_direct_rec["freshness"],
                ),
                tag=row_tag
            )

        row_defs = []
        for rec in recommendations:
            status_text = str(rec.get("warning", "")).strip() or ("Direct" if rec.get("is_direct") else "Linear Pathway")
            row_tag = self.pathways_panel.pathway_row_tag(
                rec["category"],
                status_text,
                rec.get("relays", rec.get("hops", 0))
            )

            row_defs.append({
                "values": (
                    rec["pathway"],
                    self.pathways_panel.decorated_category(rec["category"]),
                    status_text,
                    rec.get("relays", rec.get("hops", 0)),
                    rec["score"],
                    rec["reliability"],
                    self._confidence_display(rec),
                    self._last_success_display(rec),
                    rec["freshness"],
                ),
                "tag": row_tag,
            })

        if not row_defs:
            self.relay_builder.update_message_preview()
            self.refresh_topology_window()
            self._dark_info_dialog(
                "No Pathways Found",
                "No relay pathways could be generated on the selected frequency.\n\n"
                f"Selected frequency: {selected_frequency}\n\n"
                "Check that:\n"
                "- User Callsign is correct\n"
                "- Target Callsign is correct\n"
                "- relevant records exist for this frequency in DIRECTED.TXT\n"
                "- records are fresh enough, or Test Mode is enabled\n"
                "- SNR / relay settings are not too restrictive"
            )
            return

        self._set_current_pathway_rows(row_defs)
        self.relay_builder.update_message_preview()
        self.refresh_topology_window()

    def _refresh_current_pathway_view(self):
        selected_path = self._get_selected_pathway()
        if self.user_call_var.get().strip() and self.target_call_var.get().strip():
            if str(self.current_pathway_view).strip().lower() == "inbound":
                self.show_inbound_reachability_pathways()
            else:
                self.show_pathways()
        else:
            self.refresh_topology_window()
        self._reselect_pathway_if_visible(selected_path)

    def _reselect_pathway_if_visible(self, pathway_text):
        pathway = str(pathway_text or "").strip()
        if not pathway:
            return
        tree = getattr(self, "pathways_tree", None)
        if tree is None:
            return
        for item_id in tree.get_children(""):
            item = tree.item(item_id)
            values = item.get("values", ())
            if values and str(values[0]).strip() == pathway:
                tree.selection_set(item_id)
                tree.focus(item_id)
                tree.see(item_id)
                break

    def _refresh_last_success_display(self):
        changed = False
        updated_rows = []
        for row in list(self._current_pathway_rows or []):
            values = list(row.get("values", ()))
            if len(values) >= 8:
                pathway = str(values[0]).strip()
                rec = self.last_pathway_recommendations.get(pathway, {})
                new_last_success = self._last_success_display(rec)
                if values[7] != new_last_success:
                    values[7] = new_last_success
                    changed = True
            updated_rows.append({
                "values": tuple(values),
                "tag": row.get("tag"),
            })
        if changed:
            self._current_pathway_rows = updated_rows

        tree = getattr(self, "pathways_tree", None)
        if tree is not None:
            for item_id in tree.get_children(""):
                item = tree.item(item_id)
                values = list(item.get("values", ()))
                if len(values) < 8:
                    continue
                pathway = str(values[0]).strip()
                rec = self.last_pathway_recommendations.get(pathway, {})
                new_last_success = self._last_success_display(rec)
                if values[7] != new_last_success:
                    values[7] = new_last_success
                    tree.item(item_id, values=tuple(values))

    def _last_success_tick(self):
        try:
            self._refresh_last_success_display()
        finally:
            self.root.after(30000, self._last_success_tick)

    def _topology_tick(self):
        try:
            if getattr(self, "topology_window", None) is not None and self.topology_window.has_window():
                self.refresh_topology_window()
        finally:
            self.root.after(30000, self._topology_tick)

    def _sync_frequency_from_js8call_tick(self):
        try:
            if bool(self.sync_frequency_from_js8call_var.get()):
                self._sync_frequency_from_js8call_once()
        finally:
            self.root.after(5000, self._sync_frequency_from_js8call_tick)

    def _ack_tokens(self, msg_text):
        return re.findall(r"[A-Z0-9#?]+", str(msg_text or "").strip().upper())

    def _is_ack_like_message(self, msg_text):
        upper_text = str(msg_text or "").strip().upper()
        if not upper_text or upper_text.startswith("@"):
            return False

        tokens = self._ack_tokens(upper_text)
        if not tokens:
            return False
        if any(token in ACK_NEGATIVE_TOKENS for token in tokens):
            return False

        first = tokens[0]
        if first in ACK_POSITIVE_TOKENS:
            return True
        if first.startswith("RR") and len(first) <= 4:
            return True
        return "QSL" in tokens or "ACK" in tokens

    def _prune_pending_ack_relays(self):
        now = self._now()
        kept = []
        for item in list(self._pending_ack_relays or []):
            expires_dt = self._safe_parse_iso_datetime(item.get("expires_at", ""))
            if expires_dt is not None and expires_dt < now:
                continue
            kept.append(dict(item))
        self._pending_ack_relays = kept

    def _relay_event_id(self, pathway_text, created_at_text):
        pathway = self._operational_path_from_inbound_display(pathway_text).strip().upper()
        return f"{created_at_text}|{pathway}"

    def _find_relay_history_index_by_event_id(self, event_id):
        target = str(event_id or "").strip()
        if not target:
            return None
        for index, item in enumerate(list(relay_history_db or [])):
            if str(item.get("event_id", "")).strip() == target:
                return index
        return None

    def _restore_pending_relay_ack_state(self):
        now = self._now()
        restored = []
        changed = False
        for item in list(relay_history_db or []):
            if str(item.get("result", "")).strip().upper() != "P":
                continue

            timestamp_text = str(item.get("timestamp", "")).strip()
            pathway = self._operational_path_from_inbound_display(item.get("pathway", "")).strip().upper()
            if not timestamp_text or not pathway:
                continue

            event_id = str(item.get("event_id", "")).strip() or self._relay_event_id(pathway, timestamp_text)
            item["event_id"] = event_id
            created_dt = self._safe_parse_iso_datetime(timestamp_text)
            if created_dt is None:
                item["result"] = "F"
                item["resolved_at"] = now.isoformat(timespec="seconds")
                changed = True
                continue

            expires_dt = self._safe_parse_iso_datetime(item.get("pending_until", ""))
            if expires_dt is None:
                expires_dt = created_dt + timedelta(minutes=ACK_RECOGNITION_WINDOW_MINUTES)
            if now >= expires_dt:
                item["result"] = "F"
                item["resolved_at"] = now.isoformat(timespec="seconds")
                changed = True
                continue

            restored.append({
                "event_id": event_id,
                "pathway": str(item.get("pathway", "")).strip() or pathway,
                "operational_path": str(item.get("operational_path", "")).strip() or pathway,
                "target": normalize_callsign(item.get("ack_from_expected", "")),
                "expected_ack_chain": str(item.get("expected_ack_chain", "")).strip().upper(),
                "tx_mode": str(item.get("tx_mode", "")).strip(),
                "message_mode": str(item.get("message_mode", "")).strip(),
                "message_text": str(item.get("message_text", "")).strip(),
                "prepared_message": str(item.get("prepared_message", "")).strip(),
                "created_at": created_dt.isoformat(timespec="seconds"),
                "expires_at": expires_dt.isoformat(timespec="seconds"),
            })

        self._pending_ack_relays = restored
        if changed:
            self._rebuild_reliability_from_history()
            save_reliability(reliability_db)
            save_relay_history(relay_history_db)

    def _ack_chain_parts(self, to_text):
        return [part.strip().upper() for part in str(to_text or "").split(">") if part.strip()]

    def _record_pending_relay_history(self, pending_item):
        event_id = str(pending_item.get("event_id", "")).strip()
        if not event_id:
            return
        if self._find_relay_history_index_by_event_id(event_id) is not None:
            return

        history_entry = {
            "timestamp": str(pending_item.get("created_at", "")).strip() or self._now().isoformat(timespec="seconds"),
            "result": "P",
            "pathway": str(pending_item.get("pathway", "")).strip(),
            "operational_path": str(pending_item.get("operational_path", "")).strip() or self._operational_path_from_inbound_display(pending_item.get("pathway", "")),
            "tx_mode": str(pending_item.get("tx_mode", "")).strip() or "-",
            "message_mode": str(pending_item.get("message_mode", "")).strip() or "-",
            "message_text": str(pending_item.get("message_text", "")).strip(),
            "prepared_message": str(pending_item.get("prepared_message", "")).strip(),
            "display_category": classify_display_category(self, pending_item.get("pathway", "")),
            "event_id": event_id,
            "expected_ack_chain": str(pending_item.get("expected_ack_chain", "")).strip().upper(),
            "ack_from_expected": str(pending_item.get("target", "")).strip().upper(),
            "pending_until": str(pending_item.get("expires_at", "")).strip(),
        }
        relay_history_db.append(history_entry)
        save_relay_history(relay_history_db)
        if self.past_relays_window is not None and self.past_relays_window.winfo_exists():
            self.update_past_relays_table()

    def _register_pending_relay_ack(self, metadata):
        item = dict(metadata or {})
        display_path = str(item.get("pathway", "")).strip()
        operational_path = str(item.get("operational_path", "")).strip() or self._operational_path_from_inbound_display(display_path)
        pathway = operational_path.strip().upper()
        target = normalize_callsign(item.get("target", ""))
        expected_ack_chain = str(item.get("expected_ack_chain", "")).strip().upper()
        prepared_message = str(item.get("prepared_message", "")).strip()
        if not pathway or not target or not prepared_message or not expected_ack_chain:
            return

        now_dt = self._now()
        item["pathway"] = display_path or pathway
        item["operational_path"] = operational_path or pathway
        item["target"] = target
        item["expected_ack_chain"] = expected_ack_chain
        item["created_at"] = now_dt.isoformat(timespec="seconds")
        try:
            estimated_tx_seconds = max(0, int(item.get("estimated_tx_seconds", 0) or 0))
        except Exception:
            estimated_tx_seconds = 0
        item["pending_until"] = (now_dt + timedelta(seconds=estimated_tx_seconds) + timedelta(minutes=ACK_RECOGNITION_WINDOW_MINUTES)).isoformat(timespec="seconds")
        item["expires_at"] = item["pending_until"]
        item["event_id"] = str(item.get("event_id", "")).strip() or self._relay_event_id(pathway, item["created_at"])
        self._prune_pending_ack_relays()
        self._pending_ack_relays.append(item)
        self._record_pending_relay_history(item)

    def _relay_ack_tick(self):
        try:
            self._expire_pending_relay_ack_items()
        finally:
            self.root.after(30000, self._relay_ack_tick)

    def _expire_pending_relay_ack_items(self):
        now_text = self._now().isoformat(timespec="seconds")
        remaining = []
        for item in list(self._pending_ack_relays or []):
            expires_dt = self._safe_parse_iso_datetime(item.get("expires_at", ""))
            if expires_dt is not None and self._now() >= expires_dt:
                self._resolve_pending_relay_item(item, "F", ack_record=None, timestamp_text=now_text)
                continue
            remaining.append(item)
        self._pending_ack_relays = remaining

    def _build_auto_ack_history_entry(self, pending_item, ack_record, timestamp_text):
        pathway = str(pending_item.get("pathway", "")).strip()
        return {
            "timestamp": str(pending_item.get("created_at", "")).strip() or timestamp_text,
            "result": "S",
            "pathway": pathway,
            "operational_path": str(pending_item.get("operational_path", "")).strip() or self._operational_path_from_inbound_display(pathway),
            "tx_mode": str(pending_item.get("tx_mode", "")).strip() or "-",
            "message_mode": str(pending_item.get("message_mode", "")).strip() or "-",
            "message_text": str(pending_item.get("message_text", "")).strip(),
            "prepared_message": str(pending_item.get("prepared_message", "")).strip(),
            "display_category": classify_display_category(self, pathway),
            "result_source": "AUTO ACK",
            "event_id": str(pending_item.get("event_id", "")).strip(),
            "expected_ack_chain": str(pending_item.get("expected_ack_chain", "")).strip().upper(),
            "ack_from_expected": str(pending_item.get("target", "")).strip().upper(),
            "ack_from": str(ack_record.get("from", "")).strip().upper(),
            "ack_message": str(ack_record.get("msg", "")).strip(),
            "ack_datetime": timestamp_text,
        }

    def _apply_relay_result(self, pathway, result, rec=None, timestamp_text=None, history_entry=None, replace_event_id=None):
        pathway = str(pathway or "").strip()
        if not pathway:
            return False

        rec = dict(rec or self.last_pathway_recommendations.get(pathway, {}) or {})
        relays = self._relay_nodes_from_pathway_text(pathway)
        now_text = str(timestamp_text or datetime.now().isoformat(timespec="seconds")).strip()

        for relay in relays:
            stats = dict(reliability_db.get(relay, {"S": 0, "F": 0}) or {})
            stats["S"] = int(stats.get("S", 0) or 0)
            stats["F"] = int(stats.get("F", 0) or 0)
            stats[result] += 1
            stats["success_count"] = stats["S"]
            stats["failure_count"] = stats["F"]
            stats["participation_count"] = stats["S"] + stats["F"]
            stats["last_result"] = result
            stats["last_updated"] = now_text
            if result == "S":
                stats["last_success"] = now_text
            elif result == "F":
                stats["last_failure"] = now_text
            reliability_db[relay] = stats

        final_history_entry = history_entry if history_entry is not None else build_relay_history_entry(self, pathway, result, now_text)
        replace_index = self._find_relay_history_index_by_event_id(replace_event_id)
        if replace_index is not None:
            relay_history_db[replace_index] = final_history_entry
        else:
            relay_history_db.append(final_history_entry)

        self._update_inbound_route_state_from_result(rec, result)

        if rec:
            if result == "S":
                rec["last_success_time"] = now_text
                rec["last_failure_time"] = ""
            else:
                rec["last_failure_time"] = now_text
            self.last_pathway_recommendations[pathway] = rec

        save_reliability(reliability_db)
        save_relay_history(relay_history_db)
        self._refresh_current_pathway_view()

        if self.past_relays_window is not None and self.past_relays_window.winfo_exists():
            self.update_past_relays_table()

        if getattr(self, "relay_profiles_window", None) is not None and self.relay_profiles_window.has_window():
            self.refresh_relay_profiles_window()

        return True

    def _resolve_pending_relay_item(self, pending_item, result, ack_record=None, timestamp_text=None):
        pathway = str(pending_item.get("pathway", "")).strip()
        if not pathway:
            return False

        timestamp_text = str(timestamp_text or self._now().isoformat(timespec="seconds")).strip()
        if result == "S":
            history_entry = self._build_auto_ack_history_entry(pending_item, ack_record or {}, timestamp_text)
        else:
            history_entry = {
                "timestamp": str(pending_item.get("created_at", "")).strip() or timestamp_text,
                "result": "F",
                "pathway": pathway,
                "operational_path": str(pending_item.get("operational_path", "")).strip() or self._operational_path_from_inbound_display(pathway),
                "tx_mode": str(pending_item.get("tx_mode", "")).strip() or "-",
                "message_mode": str(pending_item.get("message_mode", "")).strip() or "-",
                "message_text": str(pending_item.get("message_text", "")).strip(),
                "prepared_message": str(pending_item.get("prepared_message", "")).strip(),
                "display_category": classify_display_category(self, pathway),
                "result_source": "TIMEOUT",
                "event_id": str(pending_item.get("event_id", "")).strip(),
                "expected_ack_chain": str(pending_item.get("expected_ack_chain", "")).strip().upper(),
                "ack_from_expected": str(pending_item.get("target", "")).strip().upper(),
                "resolved_at": timestamp_text,
            }

        return self._apply_relay_result(
            pathway=pathway,
            result=result,
            rec=self.last_pathway_recommendations.get(pathway, {}),
            timestamp_text=timestamp_text,
            history_entry=history_entry,
            replace_event_id=str(pending_item.get("event_id", "")).strip(),
        )

    def _maybe_process_ack_record(self, record):
        self._prune_pending_ack_relays()
        if not self._pending_ack_relays:
            return

        sender = normalize_callsign(record.get("from", ""))
        recipient_chain_text = str(record.get("to", "")).strip().upper()
        recipient_chain_parts = self._ack_chain_parts(recipient_chain_text)
        recipient_final = normalize_callsign(recipient_chain_parts[-1]) if recipient_chain_parts else ""
        if not sender or not recipient_chain_text or not recipient_final:
            return
        if recipient_final not in self._own_known_callsigns():
            return
        if not self._is_ack_like_message(record.get("msg", "")):
            return

        ack_dt = record.get("datetime") or self._now()
        matched_index = None
        matched_item = None

        for index in range(len(self._pending_ack_relays) - 1, -1, -1):
            item = self._pending_ack_relays[index]
            if sender != normalize_callsign(item.get("target", "")):
                continue
            if recipient_chain_text != str(item.get("expected_ack_chain", "")).strip().upper():
                continue
            created_dt = self._safe_parse_iso_datetime(item.get("created_at", ""))
            if created_dt is not None and ack_dt < created_dt:
                continue
            matched_index = index
            matched_item = item
            break

        if matched_item is None:
            return

        timestamp_text = ack_dt.isoformat(timespec="seconds")
        self._resolve_pending_relay_item(matched_item, "S", ack_record=record, timestamp_text=timestamp_text)
        del self._pending_ack_relays[matched_index]

    # ------------------------------------------------
    # Relay result logging
    # ------------------------------------------------

    def mark_relay(self, result):
        selection = self.pathways_tree.selection()

        if not selection:
            messagebox.showwarning(
                "No selection",
                "Select a pathway first."
            )
            return

        prompt_text = "Mark Success. Sure?"
        title = "Mark Success"

        if result == "F":
            prompt_text = "Mark Failure. Sure?"
            title = "Mark Failure"

        if not self._dark_confirm_dialog(title, prompt_text):
            return

        item = self.pathways_tree.item(selection[0])
        selected_item_id = selection[0]
        pathway = str(item["values"][0]).strip()
        rec = self.last_pathway_recommendations.get(pathway, {})
        now_text = datetime.now().isoformat(timespec="seconds")
        values = list(item.get("values", ()))
        if result == "S" and len(values) >= 8:
            values[7] = "0m"
            try:
                self.pathways_tree.item(selected_item_id, values=tuple(values))
            except Exception:
                pass
        if rec:
            rec = dict(rec)
            if result == "S":
                rec["last_success_time"] = now_text
                rec["last_failure_time"] = ""
            else:
                rec["last_failure_time"] = now_text
            self.last_pathway_recommendations[pathway] = rec
            values = list(item.get("values", ()))
            if len(values) >= 8:
                values[7] = self._last_success_display(rec)
                try:
                    self.pathways_tree.item(selected_item_id, values=tuple(values))
                except Exception:
                    pass

        self._apply_relay_result(
            pathway=pathway,
            result=result,
            rec=rec,
            timestamp_text=now_text,
        )

    # ------------------------------------------------
    # Past relays window
    # ------------------------------------------------

    def show_past_relays_window(self):
        return _show_past_relays_window_impl(self, relay_history_db)

    def update_past_relays_table(self):
        return _update_past_relays_table_impl(self, relay_history_db)

    def _clear_past_relays_search(self):
        return _clear_past_relays_search_impl(self, relay_history_db)

    def show_relay_profiles_window(self):
        if getattr(self, "relay_profiles_window", None) is None:
            return
        self.relay_profiles_window.show()
        self.refresh_relay_profiles_window()

    def refresh_relay_profiles_window(self):
        window = getattr(self, "relay_profiles_window", None)
        if window is None:
            return

        selected_frequency = window.get_selected_frequency()
        records = list(self.records)
        if selected_frequency and selected_frequency.upper() != "ALL":
            records = [
                record for record in records
                if self._frequency_matches(record.get("freq", ""), selected_frequency)
            ]

        max_age_minutes = self._current_max_age_minutes()
        profiles = compute_station_operational_profiles(
            records=records,
            reliability_db=reliability_db,
            selected_frequency=selected_frequency,
            user_callsign=self.user_call_var.get().strip().upper(),
            max_age_minutes=max_age_minutes,
        )

        search_text = window.get_search_text()
        rows = []
        for callsign in sorted(profiles.keys()):
            profile = dict(profiles.get(callsign, {}) or {})
            if search_text and search_text not in callsign:
                continue
            rows.append(profile)

        summary_text = (
            f"Relay profiles: {len(rows)} station(s) shown | "
            f"Frequency: {selected_frequency or 'ALL'} | "
            "External relay observations only help a station if a normal Linear or Inbound pathway already exists."
        )
        window.set_rows(rows, summary_text=summary_text)

    # ------------------------------------------------
    # Background monitoring
    # ------------------------------------------------

    def _apply_new_records_on_main_thread(self, new_records):
        if not new_records:
            return

        added_count = 0
        appended_records = []

        for parsed, source_line in new_records:
            classify_callsign(parsed["from"])
            classify_callsign(parsed["to"])

            if self._append_record(parsed, source_line=source_line, save_immediately=False):
                appended_records.append(parsed)
                added_count += 1

        for parsed in appended_records:
            self.add_to_activity(parsed)
            matched_watched_callsigns = self._watched_callsigns_matching_record(parsed)
            if matched_watched_callsigns:
                self._show_watched_callsign_alert(parsed, matched_watched_callsigns)
            self._handle_js8mesh_command_record(parsed)
            self._maybe_process_ack_record(parsed)

        if added_count > 0:
            self._sort_records_and_storage(save_immediately=False)
            save_snr_reports(snr_reports_db)
            self.rebuild_activity_window_from_records()
            self._refresh_current_pathway_view()
            self.refresh_topology_window()
            if getattr(self, "relay_profiles_window", None) is not None and self.relay_profiles_window.has_window():
                self.refresh_relay_profiles_window()
            self._maybe_prompt_activity_maintenance()

    def monitor_directed(self):
        while True:
            try:
                new_records = self._read_appended_directed_records()
                if new_records:
                    self.root.after(0, self._apply_new_records_on_main_thread, new_records)

            except Exception as exc:
                print("[MONITOR ERROR]", exc)

            time.sleep(settings.get("refresh", 1))
