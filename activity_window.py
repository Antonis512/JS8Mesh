import tkinter as tk
from tkinter import ttk


class ActivityWindow:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        select_file_callback,
        force_read_directed_txt_callback,
        update_display_callback,
        initial_display_limit,
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.select_file_callback = select_file_callback
        self.force_read_directed_txt_callback = force_read_directed_txt_callback
        self.update_display_callback = update_display_callback

        self.window = None
        self.tree = None
        self._drag_start_item = None
        self.display_limit_var = tk.StringVar(value=str(initial_display_limit))
        self.search_var = tk.StringVar(value="")
        self._all_rows = []

    def show(self):
        if self.window is not None:
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Activity Table")
        self.window.configure(bg=self.bg_color)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        controls_frame = tk.Frame(self.window, bg=self.bg_color)
        controls_frame.pack(fill="x", padx=5, pady=5)

        tk.Button(
            controls_frame,
            text="Force Read DIRECTED.TXT",
            command=self._force_read_directed_txt_and_focus,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="left", padx=5)

        tk.Label(
            controls_frame,
            text="Lines to show:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(12, 4))

        tk.Entry(
            controls_frame,
            textvariable=self.display_limit_var,
            width=8
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            controls_frame,
            text="Update",
            command=self._update_display_and_focus,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="left", padx=5)

        tk.Label(
            controls_frame,
            text="Find callsign:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(12, 4))

        search_entry = tk.Entry(
            controls_frame,
            textvariable=self.search_var,
            width=18
        )
        search_entry.pack(side="left", padx=(0, 6))
        search_entry.bind("<KeyRelease>", self._on_search_changed)
        search_entry.bind("<Return>", self._on_search_changed)

        tk.Button(
            controls_frame,
            text="Clear Search",
            command=self._clear_search_and_focus,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="left", padx=5)

        tk.Button(
            controls_frame,
            text="Close",
            command=self.close,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="right", padx=5)

        table_frame = tk.Frame(self.window, bg=self.bg_color)
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.tree = ttk.Treeview(
            table_frame,
            columns=("date", "time", "snr", "from", "to", "msg", "freq"),
            show="headings",
            selectmode="extended"
        )

        for col in ("date", "time", "snr", "from", "to", "msg", "freq"):
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=120, anchor="center")

        y_scroll = tk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=y_scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")

        self.tree.tag_configure("low_snr", foreground="red")
        self.tree.tag_configure("ok", foreground="white", background=self.bg_color)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-1>", self._drag_select_start)
        self.tree.bind("<B1-Motion>", self._drag_select_motion)
        self.tree.bind("<Control-c>", lambda event: self._copy_selection())
        self.tree.bind("<Control-C>", lambda event: self._copy_selection())

        self.window.focus_force()

    def close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.tree = None
            self._all_rows = []

    def has_window(self):
        return self.window is not None and self.tree is not None

    def get_display_limit(self):
        text = self.display_limit_var.get().strip()

        if not text:
            return 500

        if text.upper() == "ALL":
            return None

        try:
            value = int(text)
        except ValueError:
            return 500

        if value < 1:
            return 1

        return value

    def get_display_limit_text(self):
        text = self.display_limit_var.get().strip()
        if not text:
            return "500"
        return text

    def clear_rows(self):
        self._all_rows = []
        if self.tree is not None:
            self.tree.delete(*self.tree.get_children())

    def add_row_top(self, values, tag="ok"):
        row = {"values": tuple(values), "tag": tag}
        self._all_rows.insert(0, row)
        self._refresh_tree_from_cache()

    def trim_to_limit(self):
        self._refresh_tree_from_cache()

    def _refresh_tree_from_cache(self):
        if self.tree is None:
            return

        self.tree.delete(*self.tree.get_children())

        query = self.search_var.get().strip().upper()
        limit = self.get_display_limit()

        shown = 0
        for row in self._all_rows:
            if query and not self._row_matches_query(row["values"], query):
                continue
            self.tree.insert("", "end", values=row["values"], tags=(row["tag"],))
            shown += 1
            if limit is not None and shown >= limit:
                break

    def _row_matches_query(self, values, query):
        if not query:
            return True
        haystacks = []
        if len(values) > 3:
            haystacks.append(str(values[3]).upper())
        if len(values) > 4:
            haystacks.append(str(values[4]).upper())
        if len(values) > 5:
            haystacks.append(str(values[5]).upper())
        return any(query in text for text in haystacks)

    def _select_file_and_focus(self):
        self.select_file_callback()
        if self.window is not None:
            self.window.lift()
            self.window.focus_force()

    def _force_read_directed_txt_and_focus(self):
        self.force_read_directed_txt_callback()
        if self.window is not None:
            self.window.lift()
            self.window.focus_force()

    def _update_display_and_focus(self):
        self.update_display_callback()
        if self.window is not None:
            self.window.lift()
            self.window.focus_force()

    def _on_search_changed(self, event=None):
        self._refresh_tree_from_cache()
        if self.window is not None:
            self.window.lift()

    def _clear_search_and_focus(self):
        self.search_var.set("")
        self._refresh_tree_from_cache()
        if self.window is not None:
            self.window.lift()
            self.window.focus_force()

    def _copy_selection(self):
        if self.tree is None:
            return

        selection = self.tree.selection()
        if not selection:
            return

        lines = []

        for item_id in selection:
            item = self.tree.item(item_id)
            values = item.get("values", [])
            lines.append("\t".join(str(v) for v in values))

        text = "\n".join(lines).strip()
        if not text:
            return

        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()

    def _drag_select_start(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        self._drag_start_item = row_id if row_id else None

    def _drag_select_motion(self, event):
        if self.tree is None or not self._drag_start_item:
            return

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        children = list(self.tree.get_children(""))
        if not children:
            return

        try:
            start_index = children.index(self._drag_start_item)
            end_index = children.index(row_id)
        except ValueError:
            return

        low = min(start_index, end_index)
        high = max(start_index, end_index)
        self.tree.selection_set(children[low:high + 1])
        self.tree.focus(row_id)

    def _show_context_menu(self, event):
        if self.tree is None:
            return

        row_id = self.tree.identify_row(event.y)
        if row_id:
            if row_id not in self.tree.selection():
                self.tree.selection_set(row_id)
            self.tree.focus(row_id)

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
            command=self._copy_selection
        )
        menu.add_command(
            label="Select All",
            command=lambda: self.tree.selection_set(self.tree.get_children(""))
        )
        menu.tk_popup(event.x_root, event.y_root)
