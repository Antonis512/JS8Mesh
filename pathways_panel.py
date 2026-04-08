import tkinter as tk
import tkinter as tk
from tkinter import ttk


class PathwaysPanel:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        selected_frequency_var,
        frequency_options,
        frequency_changed_callback,
        frequency_focus_out_callback,
        frequency_enter_callback,
        pathway_selected_callback,
        pathway_search_changed_callback=None,
        pathway_search_prev_callback=None,
        pathway_search_next_callback=None,
        copy_selection_callback=None,
        remove_frequency_callback=None,
        explain_selected_pathway_callback=None,
        mark_success_callback=None,
        mark_failure_callback=None,
        current_view_title_var=None,
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color

        self.selected_frequency_var = selected_frequency_var
        self.frequency_options = list(frequency_options)

        self.frequency_changed_callback = frequency_changed_callback
        self.frequency_focus_out_callback = frequency_focus_out_callback
        self.frequency_enter_callback = frequency_enter_callback
        self.pathway_selected_callback = pathway_selected_callback
        self.pathway_search_changed_callback = pathway_search_changed_callback
        self.pathway_search_prev_callback = pathway_search_prev_callback
        self.pathway_search_next_callback = pathway_search_next_callback
        self.copy_selection_callback = copy_selection_callback
        self.remove_frequency_callback = remove_frequency_callback
        self.explain_selected_pathway_callback = explain_selected_pathway_callback
        self.mark_success_callback = mark_success_callback
        self.mark_failure_callback = mark_failure_callback
        self.current_view_title_var = current_view_title_var

        self.frame = None
        self.tree = None
        self.frequency_combo = None
        self.pathway_search_var = tk.StringVar(value="")
        self._drag_start = None
        self._tooltip_window = None
        self._tooltip_label = None
        self._tooltip_heading_key = None
        self._column_order = (
            "pathway",
            "snr_speed",
            "status",
            "relays",
            "score",
            "reliability",
            "confidence",
            "last_success",
            "freshness",
        )
        self._heading_help = {
            "pathway": "The full relay route from your station to the target.",
            "snr_speed": "The speed category inferred from signal quality: TURBO, FAST, NORMAL, or SLOW.",
            "status": "How the app currently classifies the route, such as Direct, Linear Pathway, or Promoted Inbound.",
            "relays": "How many relay stations are used in the route. Fewer is usually safer.",
            "score": "Base RF score from native evidence quality before pathway test history is considered.",
            "reliability": "Exact pathway S/F history from your manual tests of this specific route.",
            "confidence": "Numeric ranking confidence combining exact tests, weak inherited confidence, and path structure penalty.",
            "last_success": "How many minutes ago this exact route was last marked successful.",
            "freshness": "Age bucket of the weakest evidence supporting this route.",
        }

    def build_ui(self, parent):
        self.frame = tk.LabelFrame(
            parent,
            text="Recommended Pathways",
            bg=self.bg_color,
            fg=self.fg_color
        )

        controls = tk.Frame(self.frame, bg=self.bg_color)
        controls.pack(fill="x", padx=5, pady=(5, 2))

        tk.Label(
            controls,
            text="Operating / Target Frequency:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(0, 8))

        self.frequency_combo = ttk.Combobox(
            controls,
            textvariable=self.selected_frequency_var,
            values=self.frequency_options,
            state="normal",
            width=16
        )
        self.frequency_combo.pack(side="left")
        self.frequency_combo.bind("<<ComboboxSelected>>", self.frequency_changed_callback)
        self.frequency_combo.bind("<FocusOut>", self.frequency_focus_out_callback)
        self.frequency_combo.bind("<Return>", self.frequency_enter_callback)

        tk.Label(
            controls,
            text="Search:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(8, 4))

        search_entry = tk.Entry(
            controls,
            textvariable=self.pathway_search_var,
            width=18
        )
        search_entry.pack(side="left")
        search_entry.bind("<KeyRelease>", self._on_pathway_search_changed)
        search_entry.bind("<Return>", self._on_pathway_search_changed)

        tk.Button(
            controls,
            text="↑",
            command=self._on_pathway_search_prev,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=3
        ).pack(side="left", padx=(4, 0))

        tk.Button(
            controls,
            text="↓",
            command=self._on_pathway_search_next,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=3
        ).pack(side="left", padx=(4, 0))

        tk.Button(
            controls,
            text="Explain Selected Pathway",
            command=self.explain_selected_pathway_callback,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="left", padx=(8, 0))

        if self.current_view_title_var is not None:
            tk.Label(
                controls,
                textvariable=self.current_view_title_var,
                bg=self.bg_color,
                fg="#ff4444",
                font=("TkDefaultFont", 10, "bold"),
                anchor="w",
                justify="left",
            ).pack(side="left", padx=(10, 0))

        table_frame = tk.Frame(self.frame, bg=self.bg_color)
        table_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            table_frame,
            columns=self._column_order,
            show="headings",
            selectmode="extended"
        )

        column_widths = {
            "pathway": 260,
            "snr_speed": 110,
            "status": 170,
            "relays": 70,
            "score": 70,
            "reliability": 90,
            "confidence": 80,
            "last_success": 75,
            "freshness": 100,
        }

        heading_text = {
            "pathway": "PATHWAY",
            "snr_speed": "SNR SPEED",
            "status": "STATUS",
            "relays": "RELAYS",
            "score": "SCORE",
            "reliability": "S/F",
            "confidence": "CONF",
            "last_success": "LAST S",
            "freshness": "FRESHNESS",
        }

        for col in self._column_order:
            self.tree.heading(col, text=heading_text[col])
            anchor = "w" if col in ("pathway", "status") else "center"
            self.tree.column(col, width=column_widths.get(col, 120), anchor=anchor)

        self._configure_tags()

        scrollbar = tk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.pathway_selected_callback)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-1>", self._drag_select_start)
        self.tree.bind("<B1-Motion>", self._drag_select_motion)
        self.tree.bind("<Control-c>", lambda event: self.copy_selected_rows())
        self.tree.bind("<Control-C>", lambda event: self.copy_selected_rows())
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda event: self._hide_heading_tooltip())

        return self.frame

    def _configure_tags(self):
        self.tree.tag_configure("direct", foreground="#4da6ff")
        self.tree.tag_configure("turbo", foreground="#2ecc71")
        self.tree.tag_configure("fast", foreground="#d4c44c")
        self.tree.tag_configure("normal", foreground="#ffae42")
        self.tree.tag_configure("slow", foreground="#ff6b6b")
        self.tree.tag_configure("inbound", foreground="#66ccff")
        self.tree.tag_configure("promoted_inbound", foreground="#d291ff")
        self.tree.tag_configure("native_confirmed", foreground="#9bff9b")

    def decorated_category(self, category_text):
        category = str(category_text).strip().upper()
        if not category:
            return ""
        return f"● {category}"

    def pathway_row_tag(self, category_text, warning_text, relays_value):
        category = str(category_text).strip().upper()
        warning = str(warning_text or "").strip().lower()

        if "native confirmed" in warning:
            return "native_confirmed"

        if "promoted inbound" in warning or "linear of inbound" in warning:
            return "promoted_inbound"

        if "inbound" in warning:
            return "inbound"

        try:
            relays = int(relays_value)
        except (ValueError, TypeError):
            relays = None

        if relays == 0:
            return "direct"

        if category == "TURBO":
            return "turbo"

        if category == "FAST":
            return "fast"

        if category == "NORMAL":
            return "normal"

        return "slow"

    def clear_rows(self):
        if self.tree is None:
            return
        self.tree.delete(*self.tree.get_children())

    def _show_heading_tooltip(self, text, x_root, y_root, heading_key):
        if self._tooltip_window is None:
            self._tooltip_window = tk.Toplevel(self.tree)
            self._tooltip_window.withdraw()
            self._tooltip_window.overrideredirect(True)
            self._tooltip_window.configure(bg="#111111")
            self._tooltip_label = tk.Label(
                self._tooltip_window,
                text=text,
                bg="#111111",
                fg=self.fg_color,
                justify="left",
                anchor="w",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=4,
                wraplength=320,
            )
            self._tooltip_label.pack()
        else:
            self._tooltip_label.configure(text=text)

        self._tooltip_heading_key = heading_key
        self._tooltip_window.geometry(f"+{x_root + 12}+{y_root + 12}")
        self._tooltip_window.deiconify()
        self._tooltip_window.lift()

    def _hide_heading_tooltip(self):
        self._tooltip_heading_key = None
        if self._tooltip_window is not None:
            self._tooltip_window.withdraw()

    def _on_tree_motion(self, event):
        if self.tree is None:
            return
        region = self.tree.identify_region(event.x, event.y)
        if region != "heading":
            self._hide_heading_tooltip()
            return

        column_id = self.tree.identify_column(event.x)
        if not column_id.startswith("#"):
            self._hide_heading_tooltip()
            return

        try:
            index = int(column_id[1:]) - 1
        except ValueError:
            self._hide_heading_tooltip()
            return

        if index < 0 or index >= len(self._column_order):
            self._hide_heading_tooltip()
            return

        heading_key = self._column_order[index]
        help_text = self._heading_help.get(heading_key, "")
        if not help_text:
            self._hide_heading_tooltip()
            return

        if self._tooltip_heading_key == heading_key and self._tooltip_window is not None:
            self._tooltip_window.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
            return

        self._show_heading_tooltip(help_text, event.x_root, event.y_root, heading_key)

    def insert_row(self, values, tag=None):
        if self.tree is None:
            return None

        tags = ()
        if tag:
            tags = (tag,)

        return self.tree.insert("", tk.END, values=values, tags=tags)

    def get_selected_pathway(self):
        if self.tree is None:
            return ""

        selection = self.tree.selection()
        if not selection:
            return ""

        item = self.tree.item(selection[0])
        values = item.get("values", [])
        if not values:
            return ""

        return str(values[0]).strip()

    def get_search_text(self):
        return str(self.pathway_search_var.get() or "").strip()

    def focus_first_row(self):
        if self.tree is None:
            return False

        children = self.tree.get_children()
        if not children:
            return False

        first = children[0]
        self.tree.selection_set(first)
        self.tree.focus(first)
        self.tree.see(first)
        return True

    def copy_selected_rows(self):
        if self.copy_selection_callback is not None:
            self.copy_selection_callback(self.tree)

    def _on_pathway_search_changed(self, event=None):
        if self.pathway_search_changed_callback is not None:
            self.pathway_search_changed_callback(event)

    def _on_pathway_search_prev(self):
        if self.pathway_search_prev_callback is not None:
            self.pathway_search_prev_callback()

    def _on_pathway_search_next(self):
        if self.pathway_search_next_callback is not None:
            self.pathway_search_next_callback()

    def _select_drag_range(self, start_item, current_item):
        if self.tree is None or not start_item or not current_item:
            return

        children = list(self.tree.get_children(""))
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
        self.tree.selection_set(selected_items)
        self.tree.focus(current_item)

    def _drag_select_start(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        self._drag_start = row_id if row_id else None

    def _drag_select_motion(self, event):
        if self.tree is None:
            return
        row_id = self.tree.identify_row(event.y)
        if self._drag_start and row_id:
            self._select_drag_range(self._drag_start, row_id)
            if self.pathway_selected_callback is not None:
                self.pathway_selected_callback()

    def _show_context_menu(self, event):
        if self.tree is None:
            return

        row_id = self.tree.identify_row(event.y)
        if row_id:
            if row_id not in self.tree.selection():
                self.tree.selection_set(row_id)
                if self.pathway_selected_callback is not None:
                    self.pathway_selected_callback()
            self.tree.focus(row_id)

        menu = tk.Menu(
            self.master,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color
        )
        menu.add_command(
            label="Copy Selected Row(s)",
            command=self.copy_selected_rows
        )
        menu.add_command(
            label="Select All",
            command=lambda: self.tree.selection_set(self.tree.get_children(""))
        )
        if self.mark_success_callback is not None:
            menu.add_command(label="Mark Success", command=self.mark_success_callback)
        if self.mark_failure_callback is not None:
            menu.add_command(label="Mark Failure", command=self.mark_failure_callback)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
