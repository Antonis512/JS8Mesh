import tkinter as tk
from tkinter import ttk


class FindSearchLogWindow:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        clear_callback=None,
        export_callback=None,
        export_csv_callback=None,
        send_selected_callback=None,
        title_text="Find Searches",
        empty_summary_text="No active find searches loaded.",
        summary_prefix="Find search entries",
        heading_help=None,
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.clear_callback = clear_callback
        self.export_callback = export_callback
        self.export_csv_callback = export_csv_callback
        self.send_selected_callback = send_selected_callback
        self.title_text = title_text
        self.empty_summary_text = empty_summary_text
        self.summary_prefix = summary_prefix

        self.window = None
        self.tree = None
        self.summary_var = tk.StringVar(value=self.empty_summary_text)
        self._rows_by_item = {}
        self._source_rows = []
        self._tooltip_window = None
        self._tooltip_label = None
        self._active_heading_key = None
        self._sort_column = "created_at"
        self._sort_descending = True
        self._selected_find_ids = set()
        self._heading_help = dict(
            heading_help
            or {
                "created_at": "When the search request was created or refreshed.",
                "target_callsign": "The callsign being searched for.",
                "requester": "Who is searching for this callsign.",
                "frequency": "Frequency this search applies to.",
                "return_path": "Preferred current path to return a FINDR result.",
                "status": "ACTIVE, FOUND, SENT, EXPIRED, or SKIPPED.",
                "expires_in": "Time remaining until the 24-hour search expires.",
                "details": "Extra details such as who found the callsign or why the search was skipped.",
            }
        )

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

        if callable(self.send_selected_callback):
            tk.Button(
                top,
                text="Send Selected Now",
                command=self.send_selected_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=16,
            ).pack(side="right", padx=(8, 0))

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

        columns = ("created_at", "target_callsign", "requester", "frequency", "return_path", "status", "expires_in", "details")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )

        headings = {
            "created_at": "TIME",
            "target_callsign": "TARGET",
            "requester": "REQUESTER",
            "frequency": "FREQ",
            "return_path": "RETURN PATH",
            "status": "STATUS",
            "expires_in": "EXPIRES IN",
            "details": "DETAILS",
        }

        widths = {
            "created_at": 145,
            "target_callsign": 110,
            "requester": 110,
            "frequency": 90,
            "return_path": 230,
            "status": 90,
            "expires_in": 90,
            "details": 350,
        }

        for col in columns:
            anchor = "w" if col in ("return_path", "details") else "center"
            self.tree.heading(col, text=headings[col], command=lambda c=col: self._sort_rows(c))
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=(col in ("return_path", "details")))

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
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda _event: self._hide_tooltip())

        self.window.geometry("1280x520")
        self.window.focus_force()

    def close(self):
        self._hide_tooltip()
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.tree = None
            self._rows_by_item = {}
            self._selected_find_ids = set()
        try:
            if self.master is not None and self.master.winfo_exists():
                self.master.deiconify()
                self.master.lift()
                self.master.focus_force()
        except Exception:
            pass

    def set_rows(self, rows):
        if not self.has_window():
            self._source_rows = list(rows or [])
            self.summary_var.set(f"{self.summary_prefix}: {len(self._source_rows)} | Newest first")
            return
        selected_ids = {
            str((self._rows_by_item.get(item_id) or {}).get("find_id", "")).strip()
            for item_id in self.tree.selection()
            if isinstance(self._rows_by_item.get(item_id), dict)
        }
        selected_ids = {item_id for item_id in selected_ids if item_id}
        if not selected_ids and self._selected_find_ids:
            selected_ids = set(self._selected_find_ids)
        self._rows_by_item = {}
        self._source_rows = list(rows or [])
        self._sort_rows(refresh=False, selected_ids=selected_ids)
        self.summary_var.set(f"{self.summary_prefix}: {len(self._source_rows)} | Newest first")

    def _sort_rows(self, column=None, refresh=True, selected_ids=None):
        if self.tree is None:
            return
        if column:
            if self._sort_column == column:
                self._sort_descending = not self._sort_descending
            else:
                self._sort_column = column
                self._sort_descending = True if column == "created_at" else False

        rows = list(getattr(self, "_source_rows", []))

        def sort_key(row):
            mapping = {
                "created_at": str(row.get("created_at", "") or ""),
                "target_callsign": str(row.get("target_callsign", "") or ""),
                "requester": str(row.get("requester", "") or ""),
                "frequency": str(row.get("frequency", "") or ""),
                "return_path": str(row.get("return_path", "") or ""),
                "status": str(row.get("status", "") or ""),
                "expires_in": str(row.get("expires_in", "") or ""),
                "details": str(row.get("details", "") or ""),
            }
            return mapping.get(self._sort_column, mapping["created_at"])

        rows.sort(key=sort_key, reverse=bool(self._sort_descending))
        self.tree.delete(*self.tree.get_children(""))
        selection_to_restore = []
        for row in rows:
            values = (
                row.get("created_at", ""),
                row.get("target_callsign", ""),
                row.get("requester", ""),
                row.get("frequency", ""),
                row.get("return_path", ""),
                row.get("status", ""),
                row.get("expires_in", ""),
                row.get("details", ""),
            )
            item_id = self.tree.insert("", "end", values=values)
            self._rows_by_item[item_id] = dict(row)
            row_find_id = str(row.get("find_id", "") or "").strip()
            if selected_ids and row_find_id and row_find_id in selected_ids:
                selection_to_restore.append(item_id)
        if selection_to_restore:
            try:
                self.tree.selection_set(selection_to_restore)
                self.tree.focus(selection_to_restore[0])
                self._selected_find_ids = {
                    str((self._rows_by_item.get(item_id) or {}).get("find_id", "")).strip()
                    for item_id in selection_to_restore
                    if isinstance(self._rows_by_item.get(item_id), dict)
                }
            except Exception:
                pass
        elif selected_ids is not None:
            self._selected_find_ids = set(
                item_id for item_id in selected_ids if str(item_id or "").strip()
            )

    def _copy_selected_rows(self, _event=None):
        if self.tree is None:
            return "break"
        selection = self.tree.selection()
        if not selection:
            return "break"
        lines = []
        for item_id in selection:
            row = self._rows_by_item.get(item_id)
            values = self.tree.item(item_id).get("values", [])
            if isinstance(row, dict):
                lines.append("\t".join(str(value) for value in values))
            else:
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
        if callable(self.send_selected_callback):
            menu.add_command(label="Send Selected Now", command=self.send_selected_callback)
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
            "#1": "created_at",
            "#2": "target_callsign",
            "#3": "requester",
            "#4": "frequency",
            "#5": "return_path",
            "#6": "status",
            "#7": "expires_in",
            "#8": "details",
        }
        heading_key = mapping.get(column_id)
        if not heading_key:
            self._hide_tooltip()
            return
        if heading_key == self._active_heading_key:
            return
        self._show_tooltip(heading_key, event.x_root, event.y_root)

    def _on_tree_select(self, _event=None):
        if self.tree is None:
            self._selected_find_ids = set()
            return
        self._selected_find_ids = {
            str((self._rows_by_item.get(item_id) or {}).get("find_id", "")).strip()
            for item_id in self.tree.selection()
            if isinstance(self._rows_by_item.get(item_id), dict)
            and str((self._rows_by_item.get(item_id) or {}).get("find_id", "")).strip()
        }

    def selected_row_dicts(self):
        if self.tree is None:
            return []
        rows = []
        for item_id in self.tree.selection():
            row = self._rows_by_item.get(item_id)
            if isinstance(row, dict):
                rows.append(dict(row))
        return rows
