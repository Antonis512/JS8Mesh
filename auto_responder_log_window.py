import tkinter as tk
from tkinter import ttk


class AutoResponderLogWindow:
    def __init__(self, master, bg_color, fg_color, highlight_color, clear_callback=None, export_callback=None,
                 export_csv_callback=None,
                 title_text="Requested JR Responds Log", empty_summary_text="No Requested JR response activity loaded.",
                 summary_prefix="Requested JR response log entries",
                 heading_help=None):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.clear_callback = clear_callback
        self.export_callback = export_callback
        self.export_csv_callback = export_csv_callback
        self.title_text = title_text
        self.empty_summary_text = empty_summary_text
        self.summary_prefix = summary_prefix

        self.window = None
        self.tree = None
        self.summary_var = tk.StringVar(value=self.empty_summary_text)
        self._rows_by_item = {}
        self._tooltip_window = None
        self._tooltip_label = None
        self._active_heading_key = None
        self._sort_column = "timestamp"
        self._sort_descending = True
        default_help = {
            "timestamp": "When the Requested JR response event was recorded.",
            "requester": "The station that requested the report.",
            "request_type": "Requested report type: JR, JRN, or JRS.",
            "frequency": "Frequency used for this event.",
            "reply_text": "Generated reply text, if any.",
            "speed": "Selected or calculated send speed used for the reply.",
            "status": "QUEUED = JS8Mesh prepared the send. STAGED = text was loaded into JS8Call only. SENT = transmission started, with the reason column telling whether clean finish was confirmed. SKIPPED = send was canceled or failed.",
            "reason": "Reason for the result, especially if the event was skipped.",
        }
        self._heading_help = dict(heading_help or default_help)

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title(self.title_text)
        self.window.configure(bg=self.bg_color)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        outer = tk.Frame(self.window, bg=self.bg_color, padx=8, pady=8)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=self.bg_color)
        top.pack(fill="x", pady=(0, 6))

        tk.Label(
            top,
            textvariable=self.summary_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        if callable(self.export_callback):
            tk.Button(
                top,
                text="Export .txt",
                command=self.export_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=12,
            ).pack(side="right", padx=(8, 0))

        if callable(self.export_csv_callback):
            tk.Button(
                top,
                text="Export .csv",
                command=self.export_csv_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=12,
            ).pack(side="right", padx=(8, 0))

        if callable(self.clear_callback):
            tk.Button(
                top,
                text="Clear Log",
                command=self.clear_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=12,
            ).pack(side="right")

        tk.Button(
            top,
            text="Close",
            command=self.close,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12,
        ).pack(side="right", padx=(8, 0))

        table_frame = tk.Frame(outer, bg=self.bg_color)
        table_frame.pack(fill="both", expand=True)

        columns = ("timestamp", "requester", "request_type", "frequency", "reply_text", "speed", "status", "reason")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )

        headings = {
            "timestamp": "TIME",
            "requester": "REQUESTER",
            "request_type": "TYPE",
            "frequency": "FREQ",
            "reply_text": "GENERATED REPLY",
            "speed": "SPEED",
            "status": "STATUS",
            "reason": "REASON",
        }

        widths = {
            "timestamp": 145,
            "requester": 110,
            "request_type": 70,
            "frequency": 90,
            "reply_text": 360,
            "speed": 70,
            "status": 80,
            "reason": 260,
        }

        for col in columns:
            anchor = "w" if col in ("reply_text", "reason") else "center"
            self.tree.heading(col, text=headings[col], command=lambda c=col: self._sort_rows(c))
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=(col in ("reply_text", "reason")))

        scroll_y = tk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll_x = tk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Control-c>", self._copy_selected_rows)
        self.tree.bind("<Control-C>", self._copy_selected_rows)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda _event: self._hide_tooltip())

        self.window.geometry("1320x520")
        self.window.focus_force()

    def close(self):
        self._hide_tooltip()
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.tree = None
            self._rows_by_item = {}
        try:
            if self.master is not None and self.master.winfo_exists():
                self.master.deiconify()
                self.master.lift()
                self.master.focus_force()
        except Exception:
            pass

    def set_rows(self, rows):
        self.show()
        self._rows_by_item = {}
        self._source_rows = list(rows or [])
        self._sort_rows(refresh=False)
        self.summary_var.set(f"{self.summary_prefix}: {len(self._source_rows)} | Newest first")

    def _sort_rows(self, column=None, refresh=True):
        if self.tree is None:
            return
        if column:
            if self._sort_column == column:
                self._sort_descending = not self._sort_descending
            else:
                self._sort_column = column
                self._sort_descending = True if column == "timestamp" else False

        rows = list(getattr(self, "_source_rows", []))

        def sort_key(row):
            mapping = {
                "timestamp": str(row.get("timestamp", "") or ""),
                "requester": str(row.get("requester", "") or ""),
                "request_type": str(row.get("request_type", "") or ""),
                "frequency": str(row.get("frequency", "") or ""),
                "reply_text": str(row.get("reply_text", "") or ""),
                "speed": str(row.get("speed", "") or ""),
                "status": str(row.get("status", "") or ""),
                "reason": str(row.get("reason", "") or ""),
            }
            return mapping.get(self._sort_column, mapping["timestamp"])

        rows.sort(key=sort_key, reverse=bool(self._sort_descending))
        self.tree.delete(*self.tree.get_children(""))
        for row in rows:
            values = (
                row.get("timestamp", ""),
                row.get("requester", ""),
                row.get("request_type", ""),
                row.get("frequency", ""),
                row.get("reply_text", ""),
                row.get("speed", ""),
                row.get("status", ""),
                row.get("reason", ""),
            )
            item_id = self.tree.insert("", "end", values=values)
            self._rows_by_item[item_id] = values

    def _copy_selected_rows(self, _event=None):
        if self.tree is None:
            return "break"
        selection = self.tree.selection()
        if not selection:
            return "break"
        lines = []
        for item_id in selection:
            values = self._rows_by_item.get(item_id) or self.tree.item(item_id).get("values", [])
            lines.append("\t".join(str(value) for value in values))
        text = "\n".join(lines).strip()
        if not text:
            return "break"
        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()
        return "break"

    def _show_context_menu(self, event=None):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        current_selection = set(self.tree.selection())
        if row_id and row_id not in current_selection:
            self.tree.selection_set((row_id,))
            self.tree.focus(row_id)
        menu = tk.Menu(self.tree, tearoff=0)
        menu.add_command(label="Copy Selected Row(s)", command=lambda: self._copy_selected_rows())
        menu.add_command(label="Select All", command=lambda: self.tree.selection_set(self.tree.get_children("")))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _show_tooltip(self, heading_key, x_root, y_root):
        help_text = str(self._heading_help.get(heading_key, "") or "").strip()
        if not help_text:
            self._hide_tooltip()
            return
        if self._tooltip_window is None or not self._tooltip_window.winfo_exists():
            self._tooltip_window = tk.Toplevel(self.window if self.window is not None else self.master)
            self._tooltip_window.withdraw()
            self._tooltip_window.overrideredirect(True)
            self._tooltip_window.configure(bg="#ffffdd")
            self._tooltip_label = tk.Label(
                self._tooltip_window,
                bg="#ffffdd",
                fg="#000000",
                justify="left",
                anchor="w",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=4,
                wraplength=320,
            )
            self._tooltip_label.pack(fill="both", expand=True)
        self._tooltip_label.configure(text=help_text)
        self._tooltip_window.geometry(f"+{x_root + 12}+{y_root + 12}")
        self._tooltip_window.deiconify()
        self._tooltip_window.lift()
        self._active_heading_key = heading_key

    def _hide_tooltip(self):
        if self._tooltip_window is None:
            return
        try:
            if self._tooltip_window.winfo_exists():
                self._tooltip_window.withdraw()
        except Exception:
            pass
        self._active_heading_key = None

    def _on_tree_motion(self, event):
        if self.tree is None:
            return
        region = self.tree.identify_region(event.x, event.y)
        if region != "heading":
            self._hide_tooltip()
            return
        column_id = self.tree.identify_column(event.x)
        mapping = {
            "#1": "timestamp",
            "#2": "requester",
            "#3": "request_type",
            "#4": "frequency",
            "#5": "reply_text",
            "#6": "speed",
            "#7": "status",
            "#8": "reason",
        }
        heading_key = mapping.get(column_id)
        if not heading_key:
            self._hide_tooltip()
            return
        if heading_key == self._active_heading_key:
            return
        self._show_tooltip(heading_key, event.x_root, event.y_root)
