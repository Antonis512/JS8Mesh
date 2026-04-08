import tkinter as tk
from tkinter import ttk


class _SimpleTooltip:
    def __init__(self, master, bg_color, fg_color):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.tip = None

    def show(self, x_root, y_root, text):
        self.hide()
        if not text:
            return
        self.tip = tk.Toplevel(self.master)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(bg=self.bg_color)
        self.tip.geometry(f"+{x_root + 12}+{y_root + 12}")
        label = tk.Label(
            self.tip,
            text=text,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()

    def hide(self):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class RelayProfilesWindow:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        refresh_callback=None,
        frequency_options=None,
        initial_frequency="",
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.refresh_callback = refresh_callback
        self.frequency_options = list(frequency_options or [])
        self.selected_frequency_var = tk.StringVar(value=str(initial_frequency or "ALL").strip() or "ALL")

        self.window = None
        self.tree = None
        self.frequency_combo = None
        self.summary_var = tk.StringVar(value="No relay profiles loaded.")
        self.search_var = tk.StringVar(value="")
        self._drag_start = None
        self._rows = []
        self._suspend_refresh = False
        self._tooltip = _SimpleTooltip(master, bg_color, fg_color)
        self._heading_hover_map = {}
        self._row_tooltips = {}

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Relay Profiles")
        self.window.configure(bg=self.bg_color)
        self.window.geometry("1120x680")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        outer = tk.Frame(self.window, bg=self.bg_color, padx=8, pady=8)
        outer.pack(fill="both", expand=True)

        controls = tk.Frame(outer, bg=self.bg_color)
        controls.pack(fill="x", pady=(0, 6))

        tk.Label(controls, text="Search station:", bg=self.bg_color, fg=self.fg_color).pack(side="left")
        search_entry = tk.Entry(controls, textvariable=self.search_var, width=20)
        search_entry.pack(side="left", padx=(6, 8))
        search_entry.bind("<KeyRelease>", self._on_filter_changed)
        search_entry.bind("<Return>", self._on_filter_changed)

        tk.Button(
            controls,
            text="Clear Search",
            command=self._clear_search,
            bg=self.highlight_color,
            fg=self.fg_color,
        ).pack(side="left", padx=(0, 12))

        tk.Label(controls, text="Frequency:", bg=self.bg_color, fg=self.fg_color).pack(side="left")
        self.frequency_combo = ttk.Combobox(
            controls,
            textvariable=self.selected_frequency_var,
            values=self.frequency_options,
            state="normal",
            width=16,
        )
        self.frequency_combo.pack(side="left", padx=(6, 8))
        self.frequency_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)
        self.frequency_combo.bind("<FocusOut>", self._on_filter_changed)
        self.frequency_combo.bind("<Return>", self._on_filter_changed)

        tk.Label(controls, text="(Choose a frequency or ALL)", bg=self.bg_color, fg=self.fg_color).pack(side="left", padx=(4, 0))

        tk.Button(controls, text="Refresh", command=self.request_refresh, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="right", padx=(8, 0))
        tk.Button(controls, text="Close", command=self.close, bg=self.highlight_color, fg=self.fg_color, width=10).pack(side="right")

        tk.Label(outer, textvariable=self.summary_var, bg=self.bg_color, fg=self.fg_color, anchor="w", justify="left").pack(fill="x", pady=(0, 6))

        table_frame = tk.Frame(outer, bg=self.bg_color)
        table_frame.pack(fill="both", expand=True)

        columns = (
            "callsign", "freq", "rf_visibility", "relay_state", "my_success", "my_failure", "seen_count", "last_seen"
        )
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")

        headings = {
            "callsign": "CALLSIGN",
            "freq": "FREQ",
            "rf_visibility": "RF VISIBILITY",
            "relay_state": "RELAY STATE",
            "my_success": "MY S",
            "my_failure": "MY F",
            "seen_count": "SEEN COUNT",
            "last_seen": "LAST SEEN",
        }
        widths = {
            "callsign": 150,
            "freq": 110,
            "rf_visibility": 150,
            "relay_state": 180,
            "my_success": 70,
            "my_failure": 70,
            "seen_count": 95,
            "last_seen": 170,
        }
        for col in columns:
            anchor = "w" if col in ("callsign", "freq", "rf_visibility", "relay_state", "last_seen") else "center"
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=anchor)

        y_scroll = tk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        x_scroll = tk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Button-1>", self._drag_select_start)
        self.tree.bind("<B1-Motion>", self._drag_select_motion)
        self.tree.bind("<Control-c>", self._copy_selected_rows)
        self.tree.bind("<Control-C>", self._copy_selected_rows)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._on_tree_leave)

        self._heading_hover_map = {
            "#1": "Callsign: station callsign.",
            "#2": "Freq: operating frequency for the profile.",
            "#3": "RF Visibility: score from stored activity, recency, and average SNR.",
            "#4": "Relay State:\nDirect = heard directly\nRelay = acts as relay node\nEndpoint = destination only\nUnknown = not enough data",
            "#5": "My S: times you marked success for pathways using this station.",
            "#6": "My F: times you marked failure for pathways using this station.",
            "#7": "Seen Count: how many stored records include this station.",
            "#8": "Last Seen: newest stored activity record for this station.",
        }

        self.request_refresh()

    def close(self):
        self._tooltip.hide()
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def get_search_text(self):
        return str(self.search_var.get() or "").strip().upper()

    def get_selected_frequency(self):
        value = str(self.selected_frequency_var.get() or "ALL").strip()
        return value or "ALL"

    def set_selected_frequency(self, frequency_text):
        self.selected_frequency_var.set(str(frequency_text or "ALL").strip() or "ALL")

    def set_frequency_options(self, frequency_options):
        self.frequency_options = list(frequency_options or [])
        current = self.get_selected_frequency()
        if current not in self.frequency_options:
            current = "ALL" if "ALL" in self.frequency_options else (self.frequency_options[0] if self.frequency_options else "ALL")
            self.selected_frequency_var.set(current)
        if self.frequency_combo is not None:
            self.frequency_combo["values"] = self.frequency_options

    def request_refresh(self):
        if self._suspend_refresh:
            return
        if self.refresh_callback is not None:
            self.refresh_callback()

    def set_rows(self, rows, summary_text=""):
        self._rows = list(rows or [])
        if self.tree is None:
            self.summary_var.set(summary_text)
            return
        self.tree.delete(*self.tree.get_children())
        self._row_tooltips = {}
        for row in self._rows:
            values = (
                row.get("callsign", ""),
                row.get("freq", ""),
                row.get("rf_visibility_text", row.get("rf_visibility", "")),
                row.get("relay_state", ""),
                row.get("my_s", 0),
                row.get("my_f", 0),
                row.get("seen_count", 0),
                row.get("last_seen", ""),
            )
            item_id = self.tree.insert("", tk.END, values=values)
            self._row_tooltips[item_id] = (
                f"Callsign: {row.get('callsign', '')}\n"
                f"Freq: {row.get('freq', '')}\n"
                f"RF Visibility: {row.get('rf_visibility_text', row.get('rf_visibility', ''))}\n"
                f"Relay State: {row.get('relay_state', '')}\n"
                f"My S/My F: {row.get('my_s', 0)}/{row.get('my_f', 0)}\n"
                f"Seen Count: {row.get('seen_count', 0)}\n"
                f"Last Seen: {row.get('last_seen', '')}"
            )
        self.summary_var.set(summary_text)

    def _on_filter_changed(self, event=None):
        self.request_refresh()

    def _clear_search(self):
        self.search_var.set("")
        self.request_refresh()

    def _drag_select_start(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        self._drag_start = row_id if row_id else None

    def _drag_select_motion(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        if not self._drag_start or not row_id:
            return
        children = list(self.tree.get_children(""))
        try:
            a = children.index(self._drag_start)
            b = children.index(row_id)
        except ValueError:
            return
        low, high = min(a, b), max(a, b)
        selected = children[low:high + 1]
        self.tree.selection_set(selected)
        self.tree.focus(row_id)

    def _copy_selected_rows(self, event=None):
        if self.tree is None:
            return "break"
        selection = self.tree.selection()
        if not selection:
            return "break"
        lines = []
        for item_id in selection:
            values = self.tree.item(item_id).get("values", [])
            lines.append("\t".join(str(v) for v in values))
        text = "\n".join(lines).strip()
        if text:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self.master.update()
        return "break"

    def _on_tree_motion(self, event):
        if self.tree is None:
            return
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            column = self.tree.identify_column(event.x)
            self._tooltip.show(event.x_root, event.y_root, self._heading_hover_map.get(column, ""))
            return
        if region == "cell":
            row_id = self.tree.identify_row(event.y)
            self._tooltip.show(event.x_root, event.y_root, self._row_tooltips.get(row_id, ""))
            return
        self._tooltip.hide()

    def _on_tree_leave(self, event=None):
        self._tooltip.hide()

    def _show_context_menu(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        if row_id:
            if row_id not in self.tree.selection():
                self.tree.selection_set(row_id)
            self.tree.focus(row_id)
        menu = tk.Menu(self.master, tearoff=0, bg=self.bg_color, fg=self.fg_color, activebackground=self.highlight_color, activeforeground=self.fg_color)
        menu.add_command(label="Copy Selected Row(s)", command=self._copy_selected_rows)
        menu.add_command(label="Select All", command=lambda: self.tree.selection_set(self.tree.get_children("")))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
