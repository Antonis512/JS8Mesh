import tkinter as tk
from tkinter import ttk
import re

try:
    from storage import settings, save_settings
except Exception:
    settings = {}

    def save_settings(_settings):
        return None


class PathwayEvidenceWindow:
    def __init__(self, master, bg_color, fg_color, highlight_color):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color

        self.window = None
        self.tree = None
        self.details_text = None
        self.path_var = tk.StringVar(value="No pathway selected.")
        self.summary_var = tk.StringVar(value="")
        self._evidence_rows = []
        self._drag_start = None

        self._geometry_setting_key = "pathway_evidence_window_geometry"
        self._default_geometry = self._initial_geometry()
        self._geometry_save_after_id = None
        self._last_saved_geometry = str(
            settings.get(self._geometry_setting_key, self._default_geometry)
        ).strip() or self._default_geometry

    def _initial_geometry(self):
        try:
            screen_w = max(800, int(self.master.winfo_screenwidth() or 0))
            screen_h = max(600, int(self.master.winfo_screenheight() or 0))
        except Exception:
            screen_w = 1600
            screen_h = 900

        width = max(900, screen_w // 2)
        height = max(520, screen_h // 2)
        x = max(20, (screen_w - width) // 2)
        y = max(20, (screen_h - height) // 2)
        return f"{width}x{height}+{x}+{y}"

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Pathway Evidence")
        self.window.configure(bg=self.bg_color)
        self.window.geometry(self._last_saved_geometry or self._default_geometry)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Configure>", self._on_window_configure)

        outer = tk.Frame(self.window, bg=self.bg_color, padx=10, pady=10)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=self.bg_color)
        top.pack(fill="x", pady=(0, 8))

        tk.Label(top, text="Selected Pathway:", bg=self.bg_color, fg=self.fg_color).pack(side="left")
        tk.Label(
            top,
            textvariable=self.path_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        tk.Button(
            top,
            text="Close",
            command=self.close,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12,
        ).pack(side="right")

        tk.Label(
            outer,
            textvariable=self.summary_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            wraplength=1450,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
        ).pack(fill="x", pady=(0, 8))

        table_frame = tk.LabelFrame(outer, text="Evidence Used", bg=self.bg_color, fg=self.fg_color)
        table_frame.pack(fill="both", expand=True, pady=(0, 8))

        inner = tk.Frame(table_frame, bg=self.bg_color)
        inner.pack(fill="both", expand=True, padx=6, pady=6)
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            inner,
            columns=("hop", "from", "to", "type", "used_snr", "age", "freq", "local_snr", "payload_snr"),
            show="headings",
            selectmode="extended",
        )

        headings = {
            "hop": "HOP",
            "from": "FROM",
            "to": "TO",
            "type": "TYPE",
            "used_snr": "USED SNR",
            "age": "AGE",
            "freq": "FREQ",
            "local_snr": "LOCAL RX SNR",
            "payload_snr": "PAYLOAD SNR",
        }
        widths = {
            "hop": 60,
            "from": 160,
            "to": 160,
            "type": 120,
            "used_snr": 90,
            "age": 80,
            "freq": 110,
            "local_snr": 110,
            "payload_snr": 110,
        }
        for col in ("hop", "from", "to", "type", "used_snr", "age", "freq", "local_snr", "payload_snr"):
            anchor = "center" if col not in ("from", "to", "freq") else "w"
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=anchor)

        tree_scroll = tk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_row_selected)
        self.tree.bind("<Control-c>", lambda event: self._copy_selection())
        self.tree.bind("<Control-C>", lambda event: self._copy_selection())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-1>", self._drag_select_start)
        self.tree.bind("<B1-Motion>", self._drag_select_motion)

        details_frame = tk.LabelFrame(outer, text="Selected Evidence Details", bg=self.bg_color, fg=self.fg_color)
        details_frame.pack(fill="both", expand=True)

        details_inner = tk.Frame(details_frame, bg=self.bg_color)
        details_inner.pack(fill="both", expand=True, padx=6, pady=6)
        details_inner.grid_rowconfigure(0, weight=1)
        details_inner.grid_columnconfigure(0, weight=1)

        self.details_text = tk.Text(
            details_inner,
            wrap="word",
            height=10,
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.fg_color,
            state="disabled",
        )
        details_scroll = tk.Scrollbar(details_inner, orient="vertical", command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=details_scroll.set)
        self.details_text.grid(row=0, column=0, sticky="nsew")
        details_scroll.grid(row=0, column=1, sticky="ns")

        self.details_text.bind("<Control-c>", self._copy_details_event)
        self.details_text.bind("<Control-C>", self._copy_details_event)

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

    def close(self):
        self._save_window_geometry()

        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.tree = None
            self.details_text = None
            self._evidence_rows = []

    def update_pathway(self, pathway_text, evidence_rows, summary_text=""):
        self.show()
        self.path_var.set(str(pathway_text or ""))
        self.summary_var.set(str(summary_text or ""))
        self._evidence_rows = list(evidence_rows or [])

        if self.tree is None:
            return

        self.tree.delete(*self.tree.get_children())

        for idx, row in enumerate(self._evidence_rows, start=1):
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row.get("hop_index", idx),
                    row.get("from_display", ""),
                    row.get("to_display", ""),
                    row.get("evidence_type", ""),
                    row.get("used_snr_text", ""),
                    row.get("age_text", ""),
                    row.get("freq", ""),
                    row.get("local_monitor_snr_text", ""),
                    row.get("payload_reported_snr_text", ""),
                ),
            )

        if self._evidence_rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.tree.see(first)
            self._show_row_details(0)
        else:
            self._set_details_text(
                "No evidence is available for this pathway.\n\n"
                "Run Show Pathways first, then select a pathway and open this window again."
            )

    def _display_message_text(self, row):
        msg_text = str(row.get("msg", "") or "").strip()
        if msg_text:
            return msg_text

        raw = str(row.get("raw", "") or "").strip()
        if not raw:
            return ""

        tokens = raw.split()
        sender_idx = None
        for i, token in enumerate(tokens):
            if token.endswith(":"):
                sender_idx = i
                break

        if sender_idx is not None and sender_idx + 2 <= len(tokens):
            return " ".join(tokens[sender_idx + 2:]).strip()

        match = re.search(r"\b\S+:\s+\S+\s+(.*)$", raw)
        if match:
            return match.group(1).strip()

        return raw

    def _set_details_text(self, text):
        if self.details_text is None:
            return
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert("1.0", str(text or ""))
        self.details_text.configure(state="disabled")

    def _show_row_details(self, index):
        if index < 0 or index >= len(self._evidence_rows):
            self._set_details_text("")
            return

        row = self._evidence_rows[index]
        lines = [
            f"Hop: {row.get('hop_index', '')}",
            f"Link: {row.get('from_display', '')} -> {row.get('to_display', '')}",
            f"Evidence Type: {row.get('evidence_type', '')}",
            f"Used SNR: {row.get('used_snr_text', '')}",
            f"Age: {row.get('age_text', '')}",
            f"Frequency: {row.get('freq', '')}",
            f"Local RX SNR: {row.get('local_monitor_snr_text', '')}",
            f"Payload SNR: {row.get('payload_reported_snr_text', '')}",
            f"Observed sender: {row.get('source_from', '')}",
            f"Observed recipient: {row.get('source_to', '')}",
            f"Message: {self._display_message_text(row)}",
            "",
            "RAW LINE / RECORD:",
            row.get('raw', ''),
        ]
        self._set_details_text("\n".join(lines).strip())

    def _on_row_selected(self, event=None):
        if self.tree is None:
            return
        selection = self.tree.selection()
        if not selection:
            return
        item_id = selection[0]
        children = list(self.tree.get_children(""))
        try:
            index = children.index(item_id)
        except ValueError:
            index = -1
        self._show_row_details(index)

    def _copy_selection(self):
        if self.tree is None:
            return "break"
        selection = self.tree.selection()
        if not selection:
            return "break"
        lines = []
        for item_id in selection:
            item = self.tree.item(item_id)
            values = item.get("values", [])
            lines.append("\t".join(str(v) for v in values))
        text = "\n".join(lines).strip()
        if not text:
            return "break"
        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()
        return "break"

    def _copy_details(self):
        if self.details_text is None:
            return
        text = self.details_text.get("1.0", "end-1c").strip()
        if not text:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()

    def _copy_details_event(self, event=None):
        self._copy_details()
        return "break"

    def _show_context_menu(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        if row_id:
            if row_id not in self.tree.selection():
                self.tree.selection_set(row_id)
            self.tree.focus(row_id)
            self._on_row_selected()
        menu = tk.Menu(
            self.window,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color,
        )
        menu.add_command(label="Copy Selected Row(s)", command=self._copy_selection)
        menu.add_command(label="Copy Details", command=self._copy_details)
        menu.tk_popup(event.x_root, event.y_root)

    def _drag_select_start(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        self._drag_start = row_id if row_id else None

    def _drag_select_motion(self, event):
        if self.tree is None or not self._drag_start:
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        children = list(self.tree.get_children(""))
        if not children:
            return
        try:
            start_index = children.index(self._drag_start)
            end_index = children.index(row_id)
        except ValueError:
            return
        low = min(start_index, end_index)
        high = max(start_index, end_index)
        self.tree.selection_set(children[low:high + 1])
        self.tree.focus(row_id)
