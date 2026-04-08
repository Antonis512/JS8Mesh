import os
import csv
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta

FAILURE_COOLDOWN_MINUTES = 30

from storage import (
    settings,
    reliability_db,
    relay_history_db,
    inbound_routes_db,
    snr_reports_db,
    save_settings,
    save_reliability,
    save_relay_history,
    save_inbound_routes,
    save_snr_reports,
)

from callsign_utils import classify_callsign
from activity_window import ActivityWindow
from mesh_network_window import MeshNetworkWindow
from mesh_report_activity_window import MeshReportActivityWindow
from topology_window import TopologyWindow
from relay_message_builder import RelayMessageBuilder
from past_relays_window import (
    build_relay_history_entry,
    clear_past_relays_search as _clear_past_relays_search_impl,
    show_past_relays_window as _show_past_relays_window_impl,
    update_past_relays_table as _update_past_relays_table_impl,
)
from pathways_panel import PathwaysPanel
from parser_directed import parse_directed_line
from pathway_evidence_window import PathwayEvidenceWindow

from pathway_engine import (
    recommend_paths,
    recommend_inbound_reachability_paths,
    latest_direct_reports,
    direct_path_score,
    direct_path_freshness,
    direct_path_category,
    direct_path_evidence,
    sort_recommendations,
)

from topology_engine import (
    build_hearing_graph,
    rank_best_relays_for_target,
    build_topology_debug_snapshot,
    export_dual_topology_snapshot,
    extract_mother_node_discovery,
    mother_node_display_text,
    MOTHER_NODE_GRID,
)


class JS8MeshGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("JS8 Mesh by SV8TTL, 18SV8110")

        self.bg_color = "#222222"
        self.fg_color = "#ffffff"
        self.highlight_color = "#444444"

        self.root.configure(bg=self.bg_color)

        self.directed_file = settings.get("directed_file", "")
        self._directed_monitor_position = 0
        self._directed_monitor_file = self.directed_file
        self._initialize_directed_monitor_position()

        self.records = self._load_records_from_storage()
        self.processed = self._load_processed_lines_from_storage()
        self.record_keys = {self._record_key(r) for r in self.records}

        self.activity_maintenance_dialog = None
        self.activity_warn_threshold = 3000
        self.activity_trim_amount = 1500

        self.past_relays_window = None
        self.pathway_evidence_window = None
        self.last_pathway_recommendations = {}
        self._last_retry_warning_pathway = ""
        self._last_retry_warning_at = None
        self.past_relays_count_var = tk.StringVar(value="20")
        self.past_relays_search_var = tk.StringVar(value="")
        self.past_relays_result_filter_var = tk.StringVar(value="All")
        self.past_relays_tree = None
        self._past_relays_drag_start = None

        self.last_mesh_broadcast_time = None
        self.last_mesh_broadcast_lines = []
        self.last_mesh_scheduled_slot_key = None

        self.mother_notice_disabled = bool(settings.get("mother_notice_never_show_again", False))
        self.mother_discovery_seen = set()

        self.frequency_options = [
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

        self.selected_frequency_var = tk.StringVar(
            value=settings.get("selected_frequency", "7.078 MHz")
        )

        self.topology_mode_var = tk.StringVar(
            value=settings.get("topology_mode", "traffic")
        )

        settings["refresh"] = 1
        save_settings(settings)

        self.create_treeview_style()
        self.create_widgets()

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
            initial_lookback_minutes=settings.get("mesh_lookback_minutes", 15),
            initial_broadcast_interval=settings.get("mesh_broadcast_interval_minutes", 10),
            initial_broadcast_times_24h=settings.get("mesh_broadcast_times_24h", ""),
            save_callback=self.save_mesh_settings,
            broadcast_now_callback=self.broadcast_mesh_report_now,
            copy_preview_callback=self.copy_mesh_preview,
            show_activity_callback=self.show_mesh_report_activity_today,
        )

        self.mesh_report_activity_window = MeshReportActivityWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
        )

        self.topology_window = TopologyWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            topology_mode_changed_callback=self._on_topology_mode_changed_from_window,
            initial_topology_mode=settings.get("topology_mode", "traffic"),
        )

        self.pathway_evidence_window = PathwayEvidenceWindow(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
        )

        threading.Thread(target=self.monitor_directed, daemon=True).start()
        self.root.after(1000, self._mesh_scheduler_tick)

    # ------------------------------------------------
    # Storage helpers
    # ------------------------------------------------

    def _load_records_from_storage(self):
        loaded = []

        for stored in snr_reports_db:
            record = self._record_from_storage(stored)
            if record:
                loaded.append(record)

        return self._dedup_records(loaded)

    def _load_processed_lines_from_storage(self):
        processed = set()

        for stored in snr_reports_db:
            source_line = str(stored.get("source_line", "")).strip()
            if source_line:
                processed.add(source_line)

        return processed

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

    def _append_record(self, record, source_line="", save_immediately=True):
        normalized_record = dict(record)

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

    def _current_max_age_minutes(self):
        if self.ignore_freshness_var.get():
            return 999999
        return 65

    def _current_mesh_activity_minutes(self):
        return 60

    def _current_mesh_core_threshold(self):
        return 2

    def _minutes_ago(self, record):
        dt = record.get("datetime")

        if dt is None:
            return 0

        minutes = int((datetime.now() - dt).total_seconds() / 60.0)

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

    # ------------------------------------------------
    # Mother Node helpers
    # ------------------------------------------------

    def _process_mother_node_discovery_for_record(self, record):
        if self.mother_notice_disabled:
            return

        discovery = extract_mother_node_discovery(record)
        if not discovery:
            return

        discovery_key = discovery.get("message_key")
        if discovery_key in self.mother_discovery_seen:
            return

        self.mother_discovery_seen.add(discovery_key)
        self._show_mother_node_notice_dialog(record.get("freq", ""))

    def _show_mother_node_notice_dialog(self, frequency_text=""):
        dialog = tk.Toplevel(self.root)
        dialog.title("Important Notice")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        never_show_var = tk.BooleanVar(value=self.mother_notice_disabled)

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        mother_name = mother_node_display_text(frequency_text)

        message_text = (
            "Important Notice:\n"
            f"Just heard Mother Node: {mother_name}.\n"
            f"Grid square {MOTHER_NODE_GRID}.\n"
            "Say HI to Mother Node. Much appreciated."
        )

        tk.Label(
            outer,
            text=message_text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10)
        ).pack(anchor="w")

        tk.Checkbutton(
            outer,
            text="Never show this message again",
            variable=never_show_var,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color
        ).pack(anchor="w", pady=(14, 0))

        def close_dialog():
            self.mother_notice_disabled = bool(never_show_var.get())
            settings["mother_notice_never_show_again"] = self.mother_notice_disabled
            save_settings(settings)
            dialog.destroy()

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(16, 0))

        tk.Button(
            button_row,
            text="OK",
            command=close_dialog,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=10
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.update_idletasks()

        x = self.root.winfo_rootx() + 90
        y = self.root.winfo_rooty() + 90
        dialog.geometry(f"+{x}+{y}")

        self.root.wait_window(dialog)

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

        self.user_call_var = tk.StringVar(value=settings.get("user_callsign", "18SV8110"))

        self.user_entry = tk.Entry(top, textvariable=self.user_call_var, width=12)
        self.user_entry.grid(row=0, column=1)
        self.user_entry.bind("<Return>", self._enter_pressed)

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
            text="Topology",
            command=self.show_topology_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=6, padx=5)

        tk.Button(
            top,
            text="Activity",
            command=self.show_activity_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=7, padx=5)

        tk.Button(
            top,
            text="Mesh Reports",
            command=self.show_mesh_network_window,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=8, padx=5)

        tk.Button(
            top,
            text="About",
            command=self.show_about,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=0, column=9, padx=5)

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

        tk.Button(
            top,
            text="Export SNR DB",
            command=self.export_snr_database_csv,
            bg=self.highlight_color,
            fg=self.fg_color
        ).grid(row=1, column=5, padx=5)

        self.ignore_freshness_var = tk.BooleanVar(value=False)

        tk.Checkbutton(
            top,
            text="Ignore Freshness (Test Mode)",
            variable=self.ignore_freshness_var,
            command=self._on_ignore_freshness_toggled,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color
        ).grid(row=1, column=6, sticky="w")

        self.test_mode_recent_records_var = tk.StringVar(
            value=str(settings.get("test_mode_recent_records_limit", "1000"))
        )

        tk.Label(
            top,
            text="Test Mode recent records:",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=2, column=6, sticky="e", padx=(0, 6), pady=(2, 0))

        self.test_mode_recent_records_entry = tk.Entry(
            top,
            textvariable=self.test_mode_recent_records_var,
            width=10
        )
        self.test_mode_recent_records_entry.grid(row=2, column=7, sticky="w", pady=(2, 0))

        tk.Label(
            top,
            text="0 = all",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=2, column=8, sticky="w", pady=(2, 0))

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
            copy_selection_callback=self._copy_treeview_selection,
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

        relay_builder_host = tk.Frame(message_inner, bg=self.bg_color)
        relay_builder_host.pack(side="left", fill="both", expand=True)

        actions_frame = tk.LabelFrame(
            message_inner,
            text="Actions",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=8,
            pady=8
        )
        actions_frame.pack(side="right", fill="y", padx=(10, 0))

        self.relay_builder = RelayMessageBuilder(
            master=self.root,
            bg_color=self.bg_color,
            fg_color=self.fg_color,
            highlight_color=self.highlight_color,
            get_selected_pathway_callback=self._get_selected_pathway,
        )
        self.relay_builder.build_ui(relay_builder_host)

        tk.Button(
            actions_frame,
            text="Mark Success",
            command=lambda: self.mark_relay("S"),
            bg=self.highlight_color,
            fg=self.fg_color,
            width=22
        ).pack(fill="x", pady=(0, 8))

        tk.Button(
            actions_frame,
            text="Mark Failure",
            command=lambda: self.mark_relay("F"),
            bg=self.highlight_color,
            fg=self.fg_color,
            width=22
        ).pack(fill="x", pady=(0, 8))

        tk.Button(
            actions_frame,
            text="Show Past Relays",
            command=self.show_past_relays_window,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=22
        ).pack(fill="x", pady=(0, 8))

        tk.Button(
            actions_frame,
            text="Explain Selected Pathway",
            command=self.show_selected_pathway_evidence,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=22
        ).pack(fill="x")

        self.main_pane.add(message_frame, minsize=180)

    # ------------------------------------------------
    # Frequency helpers
    # ------------------------------------------------

    def _normalize_frequency_text(self, text):
        mhz_value = self._frequency_to_mhz(text)
        if mhz_value is None:
            return None

        normalized = f"{mhz_value:.6f}".rstrip("0").rstrip(".")
        return f"{normalized} MHz"

    def _apply_frequency_value(self, show_error=False):
        normalized = self._normalize_frequency_text(self.selected_frequency_var.get())
        if normalized is None:
            if show_error:
                self._dark_info_dialog(
                    "Invalid Frequency",
                    "Enter a valid frequency.\n\nExamples:\n7.078\n14.078\n27.245\n27.245000\n7078000\n27245\n27245000"
                )
            return False

        self.selected_frequency_var.set(normalized)
        settings["selected_frequency"] = normalized
        save_settings(settings)
        return True

    def _on_frequency_changed(self, event=None):
        self._apply_frequency_value(show_error=False)
        self.refresh_topology_window()

    def _on_frequency_focus_out(self, event=None):
        self._apply_frequency_value(show_error=False)
        self.refresh_topology_window()

    def _on_frequency_enter(self, event=None):
        self._apply_frequency_value(show_error=True)
        self.refresh_topology_window()

    def _on_ignore_freshness_toggled(self):
        self.refresh_topology_window()

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

    # ------------------------------------------------
    # Selection helpers
    # ------------------------------------------------

    def _enter_pressed(self, event):
        self.show_pathways()

    def _on_pathway_selected(self, event=None):
        rec = self._selected_pathway_recommendation()
        if not self._apply_retry_warning_if_needed(rec):
            return
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
            f"Reliability: {rec.get('reliability', '')}"
        )

        if rec.get("origin"):
            summary += f"   |   Origin: {rec.get('origin', '')}"
        if rec.get("native_confirmed"):
            summary += "   |   Native Confirmed: YES"
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
        menu.tk_popup(event.x_root, event.y_root)

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

    def _dark_info_dialog(self, title, prompt_text):
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

        tk.Button(
            button_row,
            text="OK",
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

    # ------------------------------------------------
    # About / window openers
    # ------------------------------------------------

    def show_about(self):
        about = tk.Toplevel(self.root)
        about.title("About JS8 Mesh")
        about.configure(bg=self.bg_color)

        outer = tk.Frame(about, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="JS8 Mesh by SV8TTL, 18SV8110",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w", pady=(0, 12))

        tk.Label(
            outer,
            text='Made to connect the Greek "Sierra Victor DX Group" JS8 stations of distant islands through the use of relays, building a weak-signal mesh network.',
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 14))

        tk.Label(
            outer,
            text="● Thanks to my family for waiting for me all the hours it took to build this.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text="● Thanks to SV1SJJ, 18SV1231 for his patience, help, and heartful guidance in everything I need about radio.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tk.Label(
            outer,
            text="● Thanks to the SV DX Group for letting me stubbornly interrupt their DX again and again to try and make contact with them.",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=620,
            font=("TkDefaultFont", 10)
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

        compact_segments = msg_text.replace("\r", "\n").replace(";", "\n").splitlines()
        entries = []
        sender = str(record.get("from", "")).strip().upper()
        date_text = str(record.get("date", "")).strip()
        time_text = str(record.get("time", "")).strip()
        freq_text = str(record.get("freq", "")).strip()

        compact_re = re.compile(
            r"(?:@JS8MESH\s+)?J\|(?P<heard>[A-Z0-9/\\-]+)\|(?P<snr>[+-]?\d+(?:\.\d+)?)\|(?P<minutes>\d{1,6})",
            re.IGNORECASE,
        )
        full_re = re.compile(
            r"(?:@JS8MESH\s+)?JM\|(?P<source>[^|;]+)\|(?P<heard>[^|;]+)\|A?(?P<snr>[+-]?\d+(?:\.\d+)?)"
            r"(?:\|L(?P<minutes>\d+)m)?(?:\|N(?P<count>\d+))?",
            re.IGNORECASE,
        )

        for segment in compact_segments:
            segment = " ".join(segment.strip().split())
            if not segment:
                continue

            match = compact_re.search(segment)
            if match:
                try:
                    snr_val = float(match.group("snr"))
                except (ValueError, TypeError):
                    continue
                heard = str(match.group("heard") or "").strip().upper()
                minutes_text = str(match.group("minutes") or "").strip()
                entries.append({
                    "date": date_text,
                    "time": time_text,
                    "sender": sender,
                    "heard": heard,
                    "snr": snr_val,
                    "snr_text": f"{int(snr_val):+d}" if float(snr_val).is_integer() else f"{snr_val:+.1f}",
                    "minutes_text": minutes_text,
                    "minutes": int(minutes_text) if minutes_text.isdigit() else None,
                    "format": "J",
                    "decoded": f"J|{heard}|{match.group('snr')}|{minutes_text}",
                    "freq": freq_text,
                })
                continue

            match = full_re.search(segment)
            if match:
                try:
                    snr_val = float(match.group("snr"))
                except (ValueError, TypeError):
                    continue
                source = str(match.group("source") or sender).strip().upper()
                heard = str(match.group("heard") or "").strip().upper()
                minutes_text = str(match.group("minutes") or "").strip()
                count_text = str(match.group("count") or "1").strip()
                decoded = f"JM|{source}|{heard}|A{match.group('snr')}"
                if minutes_text:
                    decoded += f"|L{minutes_text}m"
                if count_text:
                    decoded += f"|N{count_text}"
                entries.append({
                    "date": date_text,
                    "time": time_text,
                    "sender": source or sender,
                    "heard": heard,
                    "snr": snr_val,
                    "snr_text": f"{int(snr_val):+d}" if float(snr_val).is_integer() else f"{snr_val:+.1f}",
                    "minutes_text": minutes_text,
                    "minutes": int(minutes_text) if minutes_text.isdigit() else None,
                    "format": "JM",
                    "decoded": decoded,
                    "freq": freq_text,
                })

        return entries

    def show_mesh_report_activity_today(self):
        if not self._apply_frequency_value(show_error=False):
            return

        today = datetime.now().date()
        rows = []

        for record in self._records_for_selected_frequency():
            dt_value = record.get("datetime")
            if dt_value is None or dt_value.date() != today:
                continue

            rows.extend(self._decode_mesh_entries_from_record(record))

        rows.sort(
            key=lambda row: (
                row.get("sender", ""),
                row.get("date", ""),
                row.get("time", ""),
                row.get("heard", ""),
            )
        )

        self.mesh_report_activity_window.set_rows(
            rows,
            frequency_text=self.selected_frequency_var.get().strip(),
        )

    def show_activity_window(self):
        self.activity_window.show()
        self.rebuild_activity_window_from_records()

    def show_mesh_network_window(self):
        self._update_mesh_status_text()
        self.mesh_network_window.show()

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

        self.activity_window.tree.delete(
            *self.activity_window.tree.get_children()
        )

        limit = self.activity_window.get_display_limit()

        if limit is None:
            records_to_show = self.records
        else:
            records_to_show = self.records[-limit:]

        for record in records_to_show:
            self.add_to_activity(record)

    def save_settings(self):
        if not self._apply_frequency_value(show_error=True):
            return

        settings["user_callsign"] = self.user_call_var.get()
        settings["directed_file"] = self.directed_file
        settings["min_snr"] = self.min_snr_var.get()
        settings["max_hops"] = self.max_hops_var.get()
        settings["refresh"] = 1
        settings["selected_frequency"] = self.selected_frequency_var.get().strip()
        settings["topology_mode"] = self._current_topology_mode()
        settings["mother_notice_never_show_again"] = bool(self.mother_notice_disabled)
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

        debug_snapshot = build_topology_debug_snapshot(
            records=records_for_view,
            max_age_minutes=max_age_minutes,
            frequency=selected_frequency,
            now=datetime.now(),
        )

        dual_snapshot = export_dual_topology_snapshot(
            records=records_for_view,
            traffic_max_age_minutes=max_age_minutes,
            mesh_activity_minutes=self._current_mesh_activity_minutes(),
            mesh_core_threshold=self._current_mesh_core_threshold(),
            frequency=selected_frequency,
            now=datetime.now(),
        )

        traffic_export = dual_snapshot["traffic"]
        mesh_export = dual_snapshot["mesh"]
        mesh_stats = dual_snapshot["mesh_stats"]

        if topology_mode == "mesh":
            nodes_to_show = mesh_export["nodes"]
            edges_to_show = mesh_export["edges"]
            visible_role_text = (
                f"Known Nodes: {mesh_stats.get('mesh_known', 0)}   |   "
                f"Active Nodes: {mesh_stats.get('mesh_active', 0)}   |   "
                f"Core Nodes: {mesh_stats.get('mesh_core', 0)}"
            )
        else:
            nodes_to_show = traffic_export["nodes"]
            edges_to_show = traffic_export["edges"]
            visible_role_text = f"Traffic stations: {len(traffic_export['nodes'])}"

        graph = build_hearing_graph(
            records=records_for_view,
            max_age_minutes=max_age_minutes,
            frequency=selected_frequency,
        )

        relay_candidates = []
        if user_cs and target_cs:
            relay_candidates = rank_best_relays_for_target(
                graph=graph,
                user_cs=user_cs,
                target_cs=target_cs,
                reliability_db=reliability_db,
                max_candidates=20,
            )

        self.topology_window.populate(
            nodes=nodes_to_show,
            relay_candidates=relay_candidates,
            topology_mode=topology_mode,
        )

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

        print("[TOPOLOGY DEBUG]", debug_snapshot)
        print("[DUAL TOPOLOGY DEBUG]", {
            "topology_mode": topology_mode,
            "traffic_nodes": len(traffic_export["nodes"]),
            "traffic_edges": len(traffic_export["edges"]),
            "mesh_nodes": len(mesh_export["nodes"]),
            "mesh_edges": len(mesh_export["edges"]),
            "mesh_stats": mesh_stats,
        })

    # ------------------------------------------------
    # Mesh report logic
    # ------------------------------------------------

    def _mesh_station_summaries(self, lookback_minutes):
        now = datetime.now()
        own_callsign = self.user_call_var.get().strip().upper()
        summaries = {}

        for record in self.records:
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
                -item["snr"],
                item["minutes_ago"],
                item["heard_station"],
            )
        )

        return final_items

    def generate_mesh_broadcast_lines(self, station_count, lookback_minutes):
        source_station = self.user_call_var.get().strip().upper()
        if not source_station:
            return []

        summaries = self._mesh_station_summaries(lookback_minutes)
        selected = summaries[:station_count]

        lines = []

        for item in selected:
            snr_text = f"{int(round(item['snr'])):+d}"
            minutes_text = str(int(item["minutes_ago"]))
            lines.append(
                f"J|{item['heard_station']}|{snr_text}|{minutes_text}"
            )

        return lines

    def _format_mesh_timestamp(self, dt_value):
        if dt_value is None:
            return "never"
        return dt_value.strftime("%Y-%m-%d %H:%M:%S")

    def _update_mesh_status_text(self):
        slots = self._mesh_broadcast_slots()
        interval_minutes = self._safe_positive_int(
            settings.get("mesh_broadcast_interval_minutes", 10),
            10,
            minimum=1
        )

        if slots:
            schedule_mode_text = "SCHEDULE MODE: SPECIFIC TIMES"
            details_text = f"Broadcast times: {', '.join(slots)}.\n"
        else:
            schedule_mode_text = "SCHEDULE MODE: INTERVAL"
            details_text = f"Broadcast interval: every {interval_minutes} minute(s).\n"

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

    def _prepare_mesh_broadcast(self, station_count, lookback_minutes, copy_to_clipboard=False):
        lines = self.generate_mesh_broadcast_lines(
            station_count=station_count,
            lookback_minutes=lookback_minutes
        )

        self.last_mesh_broadcast_time = datetime.now()
        self.last_mesh_broadcast_lines = list(lines)

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
        self._update_mesh_status_text()

        if lines and copy_to_clipboard:
            self.root.clipboard_clear()
            self.root.clipboard_append(preview_text)
            self.root.update()

        return lines

    def save_mesh_settings(self):
        station_count = self._safe_positive_int(
            self.mesh_network_window.get_station_count_text(),
            settings.get("mesh_station_count", 5),
            minimum=1
        )
        lookback_minutes = self._safe_positive_int(
            self.mesh_network_window.get_lookback_minutes_text(),
            settings.get("mesh_lookback_minutes", 15),
            minimum=1
        )
        broadcast_interval = self._safe_positive_int(
            self.mesh_network_window.get_broadcast_interval_text(),
            settings.get("mesh_broadcast_interval_minutes", 10),
            minimum=1
        )

        raw_times_text = self.mesh_network_window.get_broadcast_times_24h_text()
        times_text, invalid_times = self._parse_mesh_times_text(raw_times_text)

        settings["mesh_station_count"] = station_count
        settings["mesh_lookback_minutes"] = lookback_minutes
        settings["mesh_broadcast_interval_minutes"] = broadcast_interval
        settings["mesh_broadcast_times_24h"] = times_text
        save_settings(settings)

        self.mesh_network_window.set_station_count_text(station_count)
        self.mesh_network_window.set_lookback_minutes_text(lookback_minutes)
        self.mesh_network_window.set_broadcast_interval_text(broadcast_interval)
        self.mesh_network_window.set_broadcast_times_24h_text(times_text)

        self._update_mesh_status_text()

        if invalid_times:
            self._dark_info_dialog(
                "Invalid Time Entries Removed",
                "These time entries were invalid and were removed:\n\n"
                + "\n".join(invalid_times)
                + "\n\nThe valid time entries were saved."
            )
        else:
            self._dark_info_dialog("Saved", "Mesh report settings saved!")

    def broadcast_mesh_report_now(self):
        station_count = self._safe_positive_int(
            self.mesh_network_window.get_station_count_text(),
            settings.get("mesh_station_count", 5),
            minimum=1
        )
        lookback_minutes = self._safe_positive_int(
            self.mesh_network_window.get_lookback_minutes_text(),
            settings.get("mesh_lookback_minutes", 15),
            minimum=1
        )
        broadcast_interval = self._safe_positive_int(
            self.mesh_network_window.get_broadcast_interval_text(),
            settings.get("mesh_broadcast_interval_minutes", 10),
            minimum=1
        )

        raw_times_text = self.mesh_network_window.get_broadcast_times_24h_text()
        times_text, invalid_times = self._parse_mesh_times_text(raw_times_text)

        settings["mesh_station_count"] = station_count
        settings["mesh_lookback_minutes"] = lookback_minutes
        settings["mesh_broadcast_interval_minutes"] = broadcast_interval
        settings["mesh_broadcast_times_24h"] = times_text
        save_settings(settings)

        self.mesh_network_window.set_station_count_text(station_count)
        self.mesh_network_window.set_lookback_minutes_text(lookback_minutes)
        self.mesh_network_window.set_broadcast_interval_text(broadcast_interval)
        self.mesh_network_window.set_broadcast_times_24h_text(times_text)

        lines = self._prepare_mesh_broadcast(
            station_count=station_count,
            lookback_minutes=lookback_minutes,
            copy_to_clipboard=True
        )

        if invalid_times:
            self._dark_info_dialog(
                "Invalid Time Entries Removed",
                "These time entries were invalid and were removed:\n\n"
                + "\n".join(invalid_times)
                + "\n\nThe valid time entries were saved."
            )
            return

        if not lines:
            self._dark_info_dialog("No Mesh Report Data", "No mesh report lines could be generated.")
            return

        self._dark_info_dialog(
            "Mesh Report Prepared",
            "Mesh report lines were generated and copied to clipboard."
        )

    def copy_mesh_preview(self):
        text = self.mesh_network_window.get_preview_text()
        if not text:
            self._dark_info_dialog("Nothing to Copy", "There is no mesh report preview text to copy.")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

        self._dark_info_dialog("Copied", "Mesh report preview copied to clipboard.")

    def _mesh_scheduler_tick(self):
        try:
            station_count = self._safe_positive_int(
                settings.get("mesh_station_count", 5),
                5,
                minimum=1
            )
            lookback_minutes = self._safe_positive_int(
                settings.get("mesh_lookback_minutes", 15),
                15,
                minimum=1
            )
            interval_minutes = self._safe_positive_int(
                settings.get("mesh_broadcast_interval_minutes", 10),
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
                self._prepare_mesh_broadcast(
                    station_count=station_count,
                    lookback_minutes=lookback_minutes,
                    copy_to_clipboard=False
                )
            else:
                self._update_mesh_status_text()

        finally:
            self.root.after(1000, self._mesh_scheduler_tick)

    # ------------------------------------------------
    # File loading / history
    # ------------------------------------------------

    def _initialize_directed_monitor_position(self):
        self._directed_monitor_file = self.directed_file
        self._directed_monitor_position = 0

        if self.directed_file and os.path.exists(self.directed_file):
            try:
                self._directed_monitor_position = os.path.getsize(self.directed_file)
            except OSError:
                self._directed_monitor_position = 0

    def _read_new_directed_lines(self):
        if not self.directed_file or not os.path.exists(self.directed_file):
            self._directed_monitor_file = self.directed_file
            self._directed_monitor_position = 0
            return []

        if self._directed_monitor_file != self.directed_file:
            self._initialize_directed_monitor_position()
            return []

        try:
            file_size = os.path.getsize(self.directed_file)
        except OSError:
            return []

        if file_size < self._directed_monitor_position:
            self._directed_monitor_position = 0

        with open(self.directed_file, encoding="utf-8", errors="replace") as f:
            f.seek(self._directed_monitor_position)
            lines = f.readlines()
            self._directed_monitor_position = f.tell()

        return lines

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
            self._initialize_directed_monitor_position()


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

        with open(self.directed_file, encoding="utf-8", errors="replace") as f:
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
                self._process_mother_node_discovery_for_record(parsed)
                added_count += 1

        if added_count > 0:
            save_snr_reports(snr_reports_db)
            self.rebuild_activity_window_from_records()
            self._maybe_prompt_activity_maintenance()

        self.refresh_topology_window()
        self._initialize_directed_monitor_position()

        messagebox.showinfo(
            "Force Read DIRECTED.TXT",
            f"Added {added_count} new line(s) from DIRECTED.TXT."
        )

    def add_to_activity(self, record):
        if not self.activity_window.has_window():
            return

        self.activity_window.add_row_top(
            values=(
                record["date"],
                record["time"],
                record["from"],
                record["to"],
                record["msg"],
                record["freq"],
                record["snr"],
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

    def _operational_path_from_inbound_display(self, pathway_text):
        return str(pathway_text or "").replace("<", ">").replace("  ", " ").strip()

    def _inbound_state_key(self, pathway_text):
        return str(pathway_text or "").strip()

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

    def _build_inbound_promotion_evidence_row(self, rec):
        details = []
        origin = str(rec.get("origin", "")).strip().lower()
        if origin in ("inbound_promoted", "promoted_inbound"):
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
        if rec.get("native_confirmed"):
            details.append("Native linear confirmation: YES")
        return {
            "hop_index": 0,
            "from_display": convergence or "-",
            "to_display": target or "-",
            "evidence_type": "PROMOTED INBOUND" if origin in ("inbound_promoted", "promoted_inbound") else "INBOUND",
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
        key = self._inbound_state_key(rec.get("pathway", ""))
        state = inbound_routes_db.get(key, {})
        if state:
            if state.get("origin"):
                rec["origin"] = state.get("origin")
            rec["last_success_time"] = state.get("last_success_time", "")
            rec["last_failure_time"] = state.get("last_failure_time", "")
            rec["failure_cooldown_until"] = state.get("failure_cooldown_until", "")
            rec["promotion_started_at"] = state.get("promotion_started_at", "")
            rec["native_confirmed"] = bool(state.get("native_confirmed", False))
            rec["operational_path"] = state.get("operational_path", rec.get("operational_path", ""))
            rec["convergence_node"] = state.get("convergence_node", rec.get("convergence_node", ""))
            rec["target"] = state.get("target", rec.get("target", ""))
            if "retry_warning" in state:
                rec["retry_warning"] = state.get("retry_warning", "")

        retry_text = self._retry_warning_text_for_state(rec)
        rec["retry_warning"] = retry_text

        if rec.get("origin") in ("inbound_promoted", "promoted_inbound"):
            rec["warning"] = "Promoted Inbound"
            rec["retry_warning"] = ""
            rec["failure_cooldown_until"] = ""
            rec["last_failure_time"] = ""
            if rec.get("native_confirmed"):
                rec["warning"] = "Promoted Inbound / Native Confirmed"
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
        if origin not in ("inbound", "inbound_promoted", "promoted_inbound"):
            return

        now = self._now()
        key = self._inbound_state_key(rec.get("pathway", ""))
        state = dict(inbound_routes_db.get(key, {}))
        state["display_path"] = rec.get("pathway", "")
        state["operational_path"] = rec.get("operational_path", self._operational_path_from_inbound_display(rec.get("pathway", "")))
        state["convergence_node"] = rec.get("convergence_node", rec.get("convergence", ""))
        state["target"] = rec.get("target", self.target_call_var.get().strip().upper())
        state["origin"] = "inbound" if origin == "inbound" else "inbound_promoted"
        state["native_confirmed"] = bool(rec.get("native_confirmed", False))

        if result == "S":
            state["origin"] = "inbound_promoted"
            state["last_success_time"] = now.isoformat(timespec="seconds")
            state["promotion_started_at"] = now.isoformat(timespec="seconds")
            state["last_failure_time"] = state.get("last_failure_time", "")
            state["failure_cooldown_until"] = ""
        else:
            state["last_failure_time"] = now.isoformat(timespec="seconds")
            state["failure_cooldown_until"] = (now + timedelta(minutes=FAILURE_COOLDOWN_MINUTES)).isoformat(timespec="seconds")

        inbound_routes_db[key] = state
        save_inbound_routes(inbound_routes_db)

    def _build_promoted_inbound_linear_recommendations(self, records, user_cs, target_cs, max_age_minutes):
        live_inbound = recommend_inbound_reachability_paths(
            records=records,
            user_cs=user_cs,
            target_cs=target_cs,
            max_hops=self.max_hops_var.get(),
            min_snr=self.min_snr_var.get(),
            max_age_minutes=max_age_minutes,
            reliability_db=reliability_db,
            ignore_freshness=bool(self.ignore_freshness_var.get()),
        )

        promoted = []
        for rec in live_inbound:
            merged = self._merge_inbound_state_into_recommendation(rec)
            if merged.get("origin") not in ("inbound_promoted", "promoted_inbound"):
                continue
            merged["pathway"] = merged.get("operational_path", merged.get("pathway", ""))
            merged["is_direct"] = False
            merged["warning"] = "Promoted Inbound"
            if merged.get("native_confirmed"):
                merged["warning"] = "Promoted Inbound / Native Confirmed"
            promoted.append(merged)
        return promoted

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

        recommendations = recommend_inbound_reachability_paths(
            records=frequency_records,
            user_cs=user_cs,
            target_cs=target,
            max_hops=self.max_hops_var.get(),
            min_snr=self.min_snr_var.get(),
            max_age_minutes=max_age_minutes,
            reliability_db=reliability_db,
            classify_callsign=classify_callsign,
        )

        for rec in recommendations:
            self.last_pathway_recommendations[rec.get("pathway", "")] = rec
            row_tag = self.pathways_panel.pathway_row_tag(
                rec.get("category", "SLOW"),
                "",
                rec.get("relays", rec.get("hops", 0))
            )
            self.pathways_panel.insert_row(
                values=(
                    rec.get("pathway", ""),
                    self.pathways_panel.decorated_category(rec.get("category", "SLOW")),
                    rec.get("relays", rec.get("hops", 0)),
                    rec.get("score", 0),
                    rec.get("reliability", "0/0"),
                    rec.get("freshness", ""),
                ),
                tag=row_tag
            )

        if not self.pathways_panel.focus_first_row():
            self.relay_builder.update_message_preview()
            self.refresh_topology_window()
            self._dark_info_dialog(
                "No Inbound Reachability Found",
                "No inbound reachability pathways could be generated on the selected frequency.\n\n"
                f"Selected frequency: {selected_frequency}\n\n"
                "Check that:\n"
                "- User Callsign is correct\n"
                "- Target Callsign is correct\n"
                "- relevant records exist for this frequency in DIRECTED.TXT\n"
                "- records are fresh enough, or Test Mode is enabled\n"
                "- SNR / relay settings are not too restrictive"
            )
            return

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

        recommendations = recommend_paths(
            records=frequency_records,
            user_cs=user_cs,
            target_cs=target,
            max_hops=self.max_hops_var.get(),
            min_snr=self.min_snr_var.get(),
            max_age_minutes=max_age_minutes,
            reliability_db=reliability_db,
            classify_callsign=classify_callsign,
        )

        for rec in recommendations:
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

        has_direct = bool(direct_user_heard_target or direct_target_heard_user)

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
                "reliability": "0/0",
                "freshness": direct_path_freshness(
                    frequency_records,
                    user_cs,
                    target,
                    max_age_minutes=max_age_minutes
                ),
                "is_direct": True,
                "evidence": direct_path_evidence(
                    frequency_records,
                    user_cs,
                    target,
                    max_age_minutes=max_age_minutes,
                ),
            }
            self.last_pathway_recommendations[direct_path_text] = manual_direct_rec
            row_tag = self.pathways_panel.pathway_row_tag(category, "", 0)

            self.pathways_panel.insert_row(
                values=(
                    direct_path_text,
                    self.pathways_panel.decorated_category(category),
                    0,
                    manual_direct_rec["score"],
                    manual_direct_rec["reliability"],
                    manual_direct_rec["freshness"],
                ),
                tag=row_tag
            )

        for rec in recommendations:
            row_tag = self.pathways_panel.pathway_row_tag(
                rec["category"],
                "",
                rec.get("relays", rec.get("hops", 0))
            )

            self.pathways_panel.insert_row(
                values=(
                    rec["pathway"],
                    self.pathways_panel.decorated_category(rec["category"]),
                    rec.get("relays", rec.get("hops", 0)),
                    rec["score"],
                    rec["reliability"],
                    rec["freshness"],
                ),
                tag=row_tag
            )

        if not self.pathways_panel.focus_first_row():
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

        self.relay_builder.update_message_preview()
        self.refresh_topology_window()

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
        pathway = str(item["values"][0]).strip()
        rec = self.last_pathway_recommendations.get(pathway, {})
        relays = pathway.split(">")[1:-1]

        for relay in relays:
            stats = reliability_db.get(relay, {"S": 0, "F": 0})
            stats[result] += 1
            reliability_db[relay] = stats

        now_text = datetime.now().isoformat(timespec="seconds")
        relay_history_db.append(
            build_relay_history_entry(
                self,
                pathway,
                result,
                now_text,
            )
        )

        self._update_inbound_route_state_from_result(rec, result)

        save_reliability(reliability_db)
        save_relay_history(relay_history_db)

        if self.user_call_var.get().strip() and self.target_call_var.get().strip():
            self.show_pathways()
        else:
            self.refresh_topology_window()

        if self.past_relays_window is not None and self.past_relays_window.winfo_exists():
            self.update_past_relays_table()

    # ------------------------------------------------
    # Past relays window
    # ------------------------------------------------

    def show_past_relays_window(self):
        return _show_past_relays_window_impl(self, relay_history_db)

    def update_past_relays_table(self):
        return _update_past_relays_table_impl(self, relay_history_db)

    def _clear_past_relays_search(self):
        return _clear_past_relays_search_impl(self, relay_history_db)

    # ------------------------------------------------
    # Background monitoring
    # ------------------------------------------------

    def _apply_new_records_on_main_thread(self, new_records):
        if not new_records:
            return

        added_count = 0

        for parsed, source_line in new_records:
            classify_callsign(parsed["from"])
            classify_callsign(parsed["to"])

            if self._append_record(parsed, source_line=source_line, save_immediately=False):
                self.add_to_activity(parsed)
                self._process_mother_node_discovery_for_record(parsed)
                added_count += 1

        if added_count > 0:
            save_snr_reports(snr_reports_db)
            self.refresh_topology_window()
            self._maybe_prompt_activity_maintenance()

    def monitor_directed(self):
        while True:
            try:
                new_records = []

                for line in self._read_new_directed_lines():
                    if line in self.processed:
                        continue

                    parsed = parse_directed_line(line)
                    if not parsed:
                        continue

                    new_records.append((parsed, line))

                if new_records:
                    self.root.after(0, self._apply_new_records_on_main_thread, new_records)

            except Exception as exc:
                print("[MONITOR ERROR]", exc)

            time.sleep(settings.get("refresh", 1))
