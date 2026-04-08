
import tkinter as tk
from tkinter import ttk


class MeshReportActivityWindow:
    def __init__(self, master, bg_color, fg_color, highlight_color):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color

        self.window = None
        self.tree = None
        self.summary_var = tk.StringVar(value="No decoded mesh activity loaded.")
        self._rows_by_item = {}

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Decoded Mesh Activity (Today)")
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
            justify="left"
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            top,
            text="Close",
            command=self.close,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="right")

        table_frame = tk.Frame(outer, bg=self.bg_color)
        table_frame.pack(fill="both", expand=True)

        columns = ("date", "time", "sender", "heard", "snr", "heard_age", "format", "decoded", "freq")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended"
        )

        headings = {
            "date": "DATE",
            "time": "TIME",
            "sender": "SENDER",
            "heard": "HEARD",
            "snr": "SNR",
            "heard_age": "HEARD AGO (m)",
            "format": "FORMAT",
            "decoded": "DECODED MESSAGE",
            "freq": "FREQ",
        }

        widths = {
            "date": 95,
            "time": 75,
            "sender": 110,
            "heard": 110,
            "snr": 60,
            "heard_age": 110,
            "format": 80,
            "decoded": 360,
            "freq": 90,
        }

        for col in columns:
            anchor = "w" if col in ("decoded",) else "center"
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=(col == "decoded"))

        scroll_y = tk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll_x = tk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.window.geometry("1200x520")
        self.window.focus_force()
        self.tree.bind("<Control-c>", self._copy_selected_rows)
        self.tree.bind("<Control-C>", self._copy_selected_rows)
        self.tree.bind("<Button-3>", self._show_context_menu)

    def close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self.tree = None
            self._rows_by_item = {}

    def set_rows(self, rows, frequency_text=""):
        self.show()

        self.tree.delete(*self.tree.get_children())
        self._rows_by_item = {}

        for row in rows:
            values = (
                row.get("date", ""),
                row.get("time", ""),
                row.get("sender", ""),
                row.get("heard", ""),
                row.get("snr_text", ""),
                row.get("minutes_text", ""),
                row.get("format", ""),
                row.get("decoded", ""),
                row.get("freq", ""),
            )
            item_id = self.tree.insert("", "end", values=values)
            self._rows_by_item[item_id] = values

        freq_part = f" | Frequency: {frequency_text}" if frequency_text else ""
        self.summary_var.set(
            f"Decoded mesh entries today: {len(rows)} | Latest first{freq_part}"
        )

    def _copy_selected_rows(self, event=None):
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
        if row_id and row_id not in self.tree.selection():
            self.tree.selection_set((row_id,))
            self.tree.focus(row_id)
        menu = tk.Menu(self.tree, tearoff=0)
        menu.add_command(label="Copy Selected Row(s)", command=lambda: self._copy_selected_rows())
        menu.add_command(label="Select All", command=lambda: self.tree.selection_set(self.tree.get_children("")))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
