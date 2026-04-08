import tkinter as tk
from tkinter import ttk

try:
    from storage import settings, save_settings
except Exception:
    settings = {}

    def save_settings(_settings):
        return None


class TopologyWindow:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        topology_mode_changed_callback=None,
        wave_filter_changed_callback=None,
        explain_node_callback=None,
        initial_topology_mode="traffic",
        initial_wave_filter="ALL",
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color

        self.window = None
        self.nodes_tree = None
        self.current_topology_mode = str(initial_topology_mode or "traffic").strip().lower()
        if self.current_topology_mode not in ("traffic", "mesh"):
            self.current_topology_mode = "traffic"

        self.topology_mode_var = tk.StringVar(value=self.current_topology_mode)
        self.topology_mode_changed_callback = topology_mode_changed_callback
        self.wave_filter_changed_callback = wave_filter_changed_callback
        self.explain_node_callback = explain_node_callback
        initial_wave = str(initial_wave_filter or "ALL").strip().upper()
        if initial_wave.isdigit():
            initial_wave = f"<= {initial_wave}"
        self.wave_filter_var = tk.StringVar(value=initial_wave)
        self.wave_label = None
        self.wave_combo = None

        self.status_text = None
        self.legend_var = tk.StringVar(
            value="Legend: Green = Core Nodes   |   Yellow = Active Nodes   |   Gray = Known Mesh Nodes   |   Plain = Stations"
        )
        self.legend_label = None
        self.empty_state_var = tk.StringVar(value="")
        self.empty_state_label = None

        self._nodes_drag_start = None

        self._sort_state = {
            "column": "node",
            "descending": False,
        }

        self._geometry_setting_key = "topology_window_geometry"
        self._default_geometry = ""
        self._geometry_save_after_id = None
        self._last_saved_geometry = str(settings.get(self._geometry_setting_key, "")).strip()
        self._tooltip = None
        self._tooltip_label = None
        self._tooltip_heading_key = None
        self._heading_help = {
            "node": "Callsign of the station.",
            "type": (
                "Traffic classification. TX DIRECT = sent directly to another station. "
                "TX GROUP = sent to a group like @JS8MESH. "
                "RX DIRECT = only observed as the direct recipient of a station-to-station message. "
                "Combined labels mean the station played multiple roles in the current view."
            ),
            "role": (
                "Mesh role inferred from decoded JR and HR activity. "
                "CORE NODE = at least 3 mesh reports in the last hour. "
                "ACTIVE NODE = at least 1 mesh report in the last hour. "
                "KNOWN NODE = sent a mesh report before, but not in the last hour. "
                "OBSERVED NODE = seen as a node in mesh structure, but has not been seen sending mesh reports itself. "
                "STATION = reported in mesh traffic, but not marked as a node."
            ),
            "wave": "Smallest wave depth seen for this node in decoded JR or HR reports.",
            "parent": "Most recent parent node through which this node was reported in decoded mesh structure.",
            "path": "Derived mesh chain from the root node to this node, based on latest known parent links.",
            "activity": "How many observations involving this node are currently in the view.",
            "avg_snr": "Average SNR across current topology observations for this node.",
            "latest": "Most recent active hearing age for this node.",
            "neighbors": "How many downstream mesh neighbors are associated with this node.",
            "mesh_total": "Total decoded mesh reports sent by this node.",
            "mesh_recent": "Decoded mesh reports from this node within the recent mesh activity window.",
            "last_mesh": "Minutes ago this node most recently sent a mesh report.",
        }
        self._nodes_columns = (
            "node",
            "type",
            "role",
            "wave",
            "parent",
            "path",
            "activity",
            "avg_snr",
            "latest",
            "neighbors",
            "mesh_total",
            "mesh_recent",
            "last_mesh",
        )

    def _initial_geometry(self):
        if self._last_saved_geometry:
            return self._last_saved_geometry

        try:
            screen_w = int(self.master.winfo_screenwidth())
            screen_h = int(self.master.winfo_screenheight())
        except Exception:
            return "960x540"

        width = max(900, int(screen_w / 3))
        height = max(520, int(screen_h / 3))
        return f"{width}x{height}"

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Mesh Topology")
        self.window.configure(bg=self.bg_color)
        self.window.geometry(self._initial_geometry())
        self.window.attributes("-topmost", False)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Configure>", self._on_window_configure)

        outer = tk.Frame(self.window, bg=self.bg_color, padx=10, pady=10)
        outer.pack(fill="both", expand=True)

        controls = tk.Frame(outer, bg=self.bg_color)
        controls.pack(fill="x", pady=(0, 8), anchor="w")

        tk.Label(
            controls,
            text="Topology View:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left")

        mode_combo = ttk.Combobox(
            controls,
            textvariable=self.topology_mode_var,
            values=("traffic", "mesh"),
            state="readonly",
            width=10
        )
        mode_combo.pack(side="left", padx=(8, 0))
        mode_combo.bind("<<ComboboxSelected>>", self._on_mode_changed)

        self.wave_label = tk.Label(
            controls,
            text="Mesh Waves:",
            bg=self.bg_color,
            fg=self.fg_color
        )
        self.wave_label.pack(side="left", padx=(16, 0))

        self.wave_combo = ttk.Combobox(
            controls,
            textvariable=self.wave_filter_var,
            values=(
                "ALL",
                "<= 1", "<= 2", "<= 3", "<= 4", "<= 5", "<= 6", "<= 7", "<= 8", "<= 9",
                "ONLY 1", "ONLY 2", "ONLY 3", "ONLY 4", "ONLY 5", "ONLY 6", "ONLY 7", "ONLY 8", "ONLY 9", "ONLY 9+",
            ),
            state="readonly",
            width=9
        )
        self.wave_combo.pack(side="left", padx=(8, 0))
        self.wave_combo.bind("<<ComboboxSelected>>", self._on_wave_filter_changed)

        tk.Button(
            controls,
            text="Explain Selected Node",
            command=self._explain_selected_node,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=20
        ).pack(side="right", padx=(0, 8))

        tk.Button(
            controls,
            text="Close",
            command=self._on_close,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="right")

        status_frame = tk.Frame(outer, bg=self.bg_color)
        status_frame.pack(fill="x", pady=(0, 6))

        self.status_text = tk.Text(
            status_frame,
            height=2,
            wrap="word",
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.fg_color,
            relief="solid",
            borderwidth=1
        )
        self.status_text.pack(fill="x")
        self.status_text.insert("1.0", "No topology data loaded yet.")
        self.status_text.configure(state="disabled")

        self.status_text.bind("<Control-c>", self._copy_status_text_event)
        self.status_text.bind("<Control-C>", self._copy_status_text_event)
        self.status_text.bind("<Button-3>", self._show_status_context_menu)

        legend_frame = tk.Frame(outer, bg=self.bg_color)
        legend_frame.pack(fill="x", pady=(0, 8))

        self.legend_label = tk.Label(
            legend_frame,
            textvariable=self.legend_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        )
        self.legend_label.pack(fill="x")

        self.empty_state_label = tk.Label(
            outer,
            textvariable=self.empty_state_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=1200,
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=10
        )
        self.empty_state_label.pack(fill="x", pady=(0, 10))
        self.empty_state_label.pack_forget()

        nodes_frame = tk.LabelFrame(
            outer,
            text="Nodes",
            bg=self.bg_color,
            fg=self.fg_color
        )
        nodes_frame.pack(fill="both", expand=True, pady=(0, 10))

        nodes_table_frame = tk.Frame(nodes_frame, bg=self.bg_color)
        nodes_table_frame.pack(fill="both", expand=True, padx=6, pady=6)
        nodes_table_frame.grid_rowconfigure(0, weight=1)
        nodes_table_frame.grid_columnconfigure(0, weight=1)

        self.nodes_tree = ttk.Treeview(
            nodes_table_frame,
            columns=(
                *self._nodes_columns,
            ),
            show="headings",
            selectmode="extended"
        )

        self.nodes_tree.heading("node", text="STATION", command=lambda: self._sort_nodes_by("node"))
        self.nodes_tree.heading("type", text="TYPE", command=lambda: self._sort_nodes_by("type"))
        self.nodes_tree.heading("role", text="ROLE", command=lambda: self._sort_nodes_by("role"))
        self.nodes_tree.heading("wave", text="WAVE", command=lambda: self._sort_nodes_by("wave"))
        self.nodes_tree.heading("parent", text="PARENT", command=lambda: self._sort_nodes_by("parent"))
        self.nodes_tree.heading("path", text="PATH", command=lambda: self._sort_nodes_by("path"))
        self.nodes_tree.heading("activity", text="SEEN", command=lambda: self._sort_nodes_by("activity"))
        self.nodes_tree.heading("avg_snr", text="AVG SNR", command=lambda: self._sort_nodes_by("avg_snr"))
        self.nodes_tree.heading("latest", text="LAST HEARD ACTIVE", command=lambda: self._sort_nodes_by("latest"))
        self.nodes_tree.heading("neighbors", text="NEIGHBORS", command=lambda: self._sort_nodes_by("neighbors"))
        self.nodes_tree.heading("mesh_total", text="MESH TOTAL", command=lambda: self._sort_nodes_by("mesh_total"))
        self.nodes_tree.heading("mesh_recent", text="MESH RECENT", command=lambda: self._sort_nodes_by("mesh_recent"))
        self.nodes_tree.heading("last_mesh", text="LAST MESH TX", command=lambda: self._sort_nodes_by("last_mesh"))

        self.nodes_tree.column("node", width=180, anchor="w", stretch=False)
        self.nodes_tree.column("type", width=120, anchor="center", stretch=False)
        self.nodes_tree.column("role", width=120, anchor="center", stretch=False)
        self.nodes_tree.column("wave", width=70, anchor="center", stretch=False)
        self.nodes_tree.column("parent", width=140, anchor="w", stretch=False)
        self.nodes_tree.column("path", width=260, anchor="w", stretch=False)
        self.nodes_tree.column("activity", width=90, anchor="center", stretch=False)
        self.nodes_tree.column("avg_snr", width=90, anchor="center", stretch=False)
        self.nodes_tree.column("latest", width=140, anchor="center", stretch=False)
        self.nodes_tree.column("neighbors", width=90, anchor="center", stretch=False)
        self.nodes_tree.column("mesh_total", width=100, anchor="center", stretch=False)
        self.nodes_tree.column("mesh_recent", width=100, anchor="center", stretch=False)
        self.nodes_tree.column("last_mesh", width=110, anchor="center", stretch=False)

        self.nodes_tree.tag_configure("mesh_core", background="#1f6f3f", foreground="#ffffff")
        self.nodes_tree.tag_configure("mesh_active", background="#8a7a1f", foreground="#ffffff")
        self.nodes_tree.tag_configure("mesh_known", background="#555555", foreground="#ffffff")
        self.nodes_tree.tag_configure("observed_only", background="", foreground=self.fg_color)
        self.nodes_tree.tag_configure("station", background="", foreground=self.fg_color)
        self.nodes_tree.tag_configure("wave_1", background="#214f7a", foreground="#ffffff")
        self.nodes_tree.tag_configure("wave_2", background="#5a4f1c", foreground="#ffffff")
        self.nodes_tree.tag_configure("wave_3", background="#5b2d65", foreground="#ffffff")

        self.nodes_tree.bind("<Control-c>", lambda event: self._copy_treeview_selection(self.nodes_tree))
        self.nodes_tree.bind("<Control-C>", lambda event: self._copy_treeview_selection(self.nodes_tree))
        self.nodes_tree.bind("<Button-3>", lambda event: self._show_tree_context_menu(event, self.nodes_tree))
        self.nodes_tree.bind("<Button-1>", self._nodes_drag_select_start)
        self.nodes_tree.bind("<B1-Motion>", self._nodes_drag_select_motion)
        self.nodes_tree.bind("<Motion>", self._on_nodes_tree_motion)
        self.nodes_tree.bind("<Leave>", lambda event: self._hide_heading_tooltip())

        nodes_scrollbar = tk.Scrollbar(
            nodes_table_frame,
            orient="vertical",
            command=self.nodes_tree.yview
        )
        self.nodes_tree.configure(yscrollcommand=nodes_scrollbar.set)

        self.nodes_tree.grid(row=0, column=0, sticky="nsew")
        nodes_scrollbar.grid(row=0, column=1, sticky="ns")

    def _on_window_configure(self, event=None):
        if self.window is None or event is None or event.widget is not self.window:
            return

        if self.window.state() != "normal":
            return

        if self._geometry_save_after_id is not None:
            try:
                self.window.after_cancel(self._geometry_save_after_id)
            except Exception:
                pass

        self._geometry_save_after_id = self.window.after(250, self._save_window_geometry)

    def _save_window_geometry(self):
        self._geometry_save_after_id = None

        if self.window is None or not self.window.winfo_exists():
            return

        if self.window.state() != "normal":
            return

        geometry = str(self.window.geometry()).strip()
        if not geometry:
            return

        self._last_saved_geometry = geometry
        settings[self._geometry_setting_key] = geometry
        save_settings(settings)

    def _on_close(self):
        self._save_window_geometry()
        self._hide_heading_tooltip()
        self._tooltip = None
        self._tooltip_label = None

        if self.window is not None:
            try:
                self.window.destroy()
            finally:
                self.window = None
                self.nodes_tree = None

    def _on_mode_changed(self, event=None):
        self.current_topology_mode = str(self.topology_mode_var.get() or "traffic").strip().lower()
        if self.current_topology_mode not in ("traffic", "mesh"):
            self.current_topology_mode = "traffic"

        if callable(self.topology_mode_changed_callback):
            self.topology_mode_changed_callback(self.current_topology_mode)

    def _on_wave_filter_changed(self, event=None):
        if callable(self.wave_filter_changed_callback):
            self.wave_filter_changed_callback(self.get_mesh_wave_filter())

    def set_topology_mode(self, topology_mode):
        mode = str(topology_mode or "traffic").strip().lower()
        if mode not in ("traffic", "mesh"):
            mode = "traffic"
        self.current_topology_mode = mode
        self.topology_mode_var.set(mode)

    def get_mesh_wave_filter(self):
        raw = str(self.wave_filter_var.get() or "ALL").strip().upper()
        if raw == "ALL":
            return {
                "mode": "all",
                "value": None,
            }
        if raw == "ONLY 9+":
            return {
                "mode": "min",
                "value": 9,
            }
        if raw.startswith("ONLY"):
            raw_value = raw.replace("ONLY", "").strip()
            try:
                return {
                    "mode": "exact",
                    "value": max(1, int(raw_value)),
                }
            except Exception:
                return {
                    "mode": "all",
                    "value": None,
                }
        raw = raw.replace("<=", "").strip()
        try:
            return {
                "mode": "upto",
                "value": max(1, int(raw)),
            }
        except Exception:
            return {
                "mode": "all",
                "value": None,
            }

    def set_status_text(self, text):
        if self.status_text is None:
            return

        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert("1.0", str(text))
        self.status_text.configure(state="disabled")

    def set_empty_state_text(self, text):
        if self.empty_state_label is None:
            return

        text = str(text or "").strip()
        self.empty_state_var.set(text)

        if text:
            self.empty_state_label.pack(fill="x", pady=(0, 10))
        else:
            self.empty_state_label.pack_forget()

    def clear(self):
        if self.nodes_tree is not None:
            self.nodes_tree.delete(*self.nodes_tree.get_children())

    def _configure_nodes_for_mode(self, topology_mode):
        self.current_topology_mode = str(topology_mode or "traffic").strip().lower()

        if self.nodes_tree is None:
            return

        if self.legend_label is not None:
            self.legend_label.pack(fill="x")

        if self.current_topology_mode == "mesh":
            self.legend_var.set(
                "Role colors: Green = Core Node, Yellow = Active Node, Gray = Known Node. "
                "Wave colors: Blue = Wave 1, Olive = Wave 2, Purple = Wave 3. Higher waves keep plain rows."
            )
            if self.wave_label is not None:
                self.wave_label.configure(state="normal")
            if self.wave_combo is not None:
                self.wave_combo.configure(state="readonly")
            self.nodes_tree.column("type", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("role", width=120, stretch=True, minwidth=90)
            self.nodes_tree.column("wave", width=70, stretch=True, minwidth=60)
            self.nodes_tree.column("parent", width=140, stretch=True, minwidth=110)
            self.nodes_tree.column("path", width=260, stretch=True, minwidth=180)
            self.nodes_tree.column("mesh_total", width=100, stretch=True, minwidth=80)
            self.nodes_tree.column("mesh_recent", width=100, stretch=True, minwidth=80)
            self.nodes_tree.column("last_mesh", width=110, stretch=True, minwidth=90)
        else:
            self.legend_var.set(
                "Traffic type: TX DIRECT = sent directly, TX GROUP = sent to a group, RX DIRECT = received directly. Combined labels show multiple roles."
            )
            if self.wave_label is not None:
                self.wave_label.configure(state="disabled")
            if self.wave_combo is not None:
                self.wave_combo.configure(state="disabled")
            self.nodes_tree.column("type", width=120, stretch=True, minwidth=90)
            self.nodes_tree.column("role", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("wave", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("parent", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("path", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("mesh_total", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("mesh_recent", width=0, stretch=False, minwidth=0)
            self.nodes_tree.column("last_mesh", width=0, stretch=False, minwidth=0)

    def populate(self, nodes, topology_mode="traffic"):
        self.clear()
        self.set_topology_mode(topology_mode)
        self._configure_nodes_for_mode(topology_mode)

        sortable_nodes = []
        for node in nodes:
            raw_role = node.get("mesh_role", "observed_only")
            is_mesh_node = bool(node.get("is_mesh_node"))

            if not is_mesh_node and topology_mode == "mesh":
                role_text = "STATION"
                display_tag = "station"
            elif raw_role == "mesh_core":
                role_text = "CORE NODE"
                display_tag = raw_role
            elif raw_role == "mesh_active":
                role_text = "ACTIVE NODE"
                display_tag = raw_role
            elif raw_role == "mesh_known":
                role_text = "KNOWN NODE"
                display_tag = raw_role
            else:
                role_text = "OBSERVED NODE" if topology_mode == "mesh" else "OBSERVED ONLY"
                display_tag = raw_role

            avg_snr = node.get("avg_snr")
            latest = node.get("latest_minutes_ago")
            last_mesh = node.get("last_mesh_report_minutes_ago")
            seen_value = node.get("seen_count", node.get("activity", 0))

            sortable_nodes.append({
                "raw": node,
                "values": (
                    node.get("id", ""),
                    node.get("traffic_type", ""),
                    role_text,
                    "" if node.get("wave_depth") is None else node.get("wave_depth"),
                    node.get("parent_node", ""),
                    node.get("path_text", ""),
                    seen_value,
                    "" if avg_snr is None else f"{avg_snr:.1f}",
                    "" if latest is None else f"{latest}m",
                    node.get("neighbor_count", 0),
                    node.get("mesh_report_count_total", 0),
                    node.get("mesh_report_count_recent", 0),
                    "" if last_mesh is None else f"{last_mesh}m",
                ),
                "sort": {
                    "node": str(node.get("id", "")),
                    "type": str(node.get("traffic_type", "")),
                    "role": role_text,
                    "wave": int(node.get("wave_depth", 999999) or 999999),
                    "parent": str(node.get("parent_node", "")),
                    "path": str(node.get("path_text", "")),
                    "activity": int(seen_value or 0),
                    "avg_snr": float(avg_snr) if avg_snr is not None else -9999.0,
                    "latest": int(latest) if latest is not None else 999999,
                    "neighbors": int(node.get("neighbor_count", 0) or 0),
                    "mesh_total": int(node.get("mesh_report_count_total", 0) or 0),
                    "mesh_recent": int(node.get("mesh_report_count_recent", 0) or 0),
                    "last_mesh": int(last_mesh) if last_mesh is not None else 999999,
                },
                "tag": display_tag,
            })

        self._populate_sorted_nodes(sortable_nodes)

    def _populate_sorted_nodes(self, sortable_nodes):
        self._current_sortable_nodes = list(sortable_nodes)
        self._apply_node_sort()

    def _show_heading_tooltip(self, text, x_root, y_root, heading_key):
        if self.nodes_tree is None or not self.nodes_tree.winfo_exists():
            return

        tooltip_exists = False
        if self._tooltip is not None:
            try:
                tooltip_exists = bool(self._tooltip.winfo_exists())
            except Exception:
                tooltip_exists = False

        if not tooltip_exists:
            self._tooltip = tk.Toplevel(self.nodes_tree)
            self._tooltip.withdraw()
            self._tooltip.overrideredirect(True)
            self._tooltip.configure(bg="#111111")
            self._tooltip_label = tk.Label(
                self._tooltip,
                bg="#111111",
                fg="#ffffff",
                justify="left",
                anchor="w",
                wraplength=320,
                padx=8,
                pady=6,
                relief="solid",
                borderwidth=1,
            )
            self._tooltip_label.pack()

        label_exists = False
        if self._tooltip_label is not None:
            try:
                label_exists = bool(self._tooltip_label.winfo_exists())
            except Exception:
                label_exists = False
        if not label_exists:
            self._tooltip = None
            self._tooltip_label = None
            return

        self._tooltip_label.configure(text=text)
        self._tooltip_heading_key = heading_key
        self._tooltip.geometry(f"+{x_root + 12}+{y_root + 12}")
        self._tooltip.deiconify()
        self._tooltip.lift()

    def _hide_heading_tooltip(self):
        self._tooltip_heading_key = None
        if self._tooltip is not None:
            try:
                if self._tooltip.winfo_exists():
                    self._tooltip.withdraw()
                else:
                    self._tooltip = None
                    self._tooltip_label = None
            except Exception:
                self._tooltip = None
                self._tooltip_label = None

    def _on_nodes_tree_motion(self, event=None):
        if self.nodes_tree is None or event is None:
            return
        region = self.nodes_tree.identify_region(event.x, event.y)
        if region != "heading":
            self._hide_heading_tooltip()
            return
        column_id = self.nodes_tree.identify_column(event.x)
        heading_key = ""
        if column_id.startswith("#"):
            try:
                index = int(column_id[1:]) - 1
                if 0 <= index < len(self._nodes_columns):
                    heading_key = self._nodes_columns[index]
            except Exception:
                heading_key = ""
        if not heading_key:
            self._hide_heading_tooltip()
            return
        help_text = self._heading_help.get(heading_key)
        if not help_text:
            self._hide_heading_tooltip()
            return
        if self._tooltip_heading_key == heading_key and self._tooltip is not None:
            try:
                if self._tooltip.winfo_exists():
                    self._tooltip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
                    return
            except Exception:
                self._tooltip = None
                self._tooltip_label = None
        self._show_heading_tooltip(help_text, event.x_root, event.y_root, heading_key)

    def _apply_node_sort(self):
        if self.nodes_tree is None:
            return

        self.nodes_tree.delete(*self.nodes_tree.get_children())

        column = self._sort_state.get("column", "node")
        descending = self._sort_state.get("descending", False)

        nodes = list(getattr(self, "_current_sortable_nodes", []))
        nodes.sort(key=lambda item: item["sort"].get(column), reverse=descending)

        for item in nodes:
            tags = [item["tag"]]
            wave_value = item["sort"].get("wave")
            if isinstance(wave_value, int) and wave_value in (1, 2, 3):
                tags.append(f"wave_{wave_value}")
            self.nodes_tree.insert(
                "",
                tk.END,
                values=item["values"],
                tags=tuple(tags)
            )

    def _sort_nodes_by(self, column_name):
        if self._sort_state.get("column") == column_name:
            self._sort_state["descending"] = not self._sort_state.get("descending", False)
        else:
            self._sort_state["column"] = column_name
            self._sort_state["descending"] = False

        self._apply_node_sort()

    def _copy_treeview_selection(self, tree):
        if tree is None:
            return "break"

        selection = tree.selection()
        if not selection:
            return "break"

        lines = []
        for item_id in selection:
            item = tree.item(item_id)
            values = item.get("values", [])
            lines.append("\t".join(str(v) for v in values))

        text = "\n".join(lines).strip()
        if not text:
            return "break"

        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()
        return "break"

    def _copy_status_text(self):
        if self.status_text is None:
            return

        text = self.status_text.get("1.0", "end-1c").strip()
        if not text:
            return

        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()

    def _copy_status_text_event(self, event=None):
        self._copy_status_text()
        return "break"

    def _show_status_context_menu(self, event):
        menu = tk.Menu(
            self.window,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color
        )
        menu.add_command(label="Copy Status Text", command=self._copy_status_text)
        menu.tk_popup(event.x_root, event.y_root)

    def _show_tree_context_menu(self, event, tree):
        row_id = tree.identify_row(event.y)
        if row_id:
            if row_id not in tree.selection():
                tree.selection_set(row_id)
            tree.focus(row_id)

        menu = tk.Menu(
            self.window,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color
        )
        menu.add_command(
            label="Copy Selected Row(s)",
            command=lambda: self._copy_treeview_selection(tree)
        )
        menu.add_command(
            label="Select All",
            command=lambda: tree.selection_set(tree.get_children(""))
        )
        if tree is self.nodes_tree:
            menu.add_command(label="Explain Selected Node", command=self._explain_selected_node)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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

    def _nodes_drag_select_start(self, event):
        if self.nodes_tree is None:
            return
        row_id = self.nodes_tree.identify_row(event.y)
        self._nodes_drag_start = row_id if row_id else None

    def _nodes_drag_select_motion(self, event):
        if self.nodes_tree is None:
            return
        row_id = self.nodes_tree.identify_row(event.y)
        if self._nodes_drag_start and row_id:
            self._select_treeview_drag_range(self.nodes_tree, self._nodes_drag_start, row_id)

    def _explain_selected_node(self):
        if self.nodes_tree is None or not callable(self.explain_node_callback):
            return
        selection = self.nodes_tree.selection()
        if not selection:
            return
        item = self.nodes_tree.item(selection[0])
        values = item.get("values", [])
        if not values:
            return
        node_id = str(values[0]).strip()
        if node_id:
            self.explain_node_callback(node_id, self.current_topology_mode)
