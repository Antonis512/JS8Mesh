import tkinter as tk
from tkinter import ttk

try:
    from storage import settings, save_settings
except Exception:
    settings = {}

    def save_settings(_settings):
        return None


class RequestJRWindow:
    TYPE_KEYS = ("GENERAL",)

    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        known_nodes_provider=None,
        state_changed_callback=None,
        send_callback=None,
        node_selected_callback=None,
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.known_nodes_provider = known_nodes_provider
        self.state_changed_callback = state_changed_callback
        self.send_callback = send_callback
        self.node_selected_callback = node_selected_callback

        self.window = None
        self.picker_window = None
        self.picker_tree = None
        self.picker_type = None
        self.preview_widget = None
        self.frame_widgets = {}
        self._speed_mode_buttons = {}
        self.hrc_target_entry = None

        self.type_var = tk.StringVar(value="GENERAL")
        self.report_scope_var = tk.StringVar(value="GENERAL")
        self.target_mode_vars = {
            key: tk.StringVar(value="RECIPIENT")
            for key in self.TYPE_KEYS
        }
        self.recipient_vars = {
            key: tk.StringVar(value="")
            for key in self.TYPE_KEYS
        }
        self.preview_var = tk.StringVar(value="")
        self.hrc_target_var = tk.StringVar(value="")
        self.speed_mode_var = tk.StringVar(value="DEFAULT")
        self.send_effects_var = tk.StringVar(value="")
        self.picker_search_var = tk.StringVar(value="")
        self.picker_wave_filter_var = tk.StringVar(value="All")
        self.picker_test_mode_var = tk.BooleanVar(
            value=bool(settings.get("request_jr_picker_test_mode", False))
        )
        self._picker_rows = []
        self._picker_all_rows = []
        self._picker_tooltip = None
        self._picker_tooltip_label = None
        self._picker_heading_key = None
        self._picker_sort_column = "callsign"
        self._picker_sort_descending = False
        self._picker_heading_help = {
            "callsign": "The node callsign that can receive a JR request.",
            "known_as": "Directly Heard = wave 1 node. Reported = downstream node learned through another node's JR.",
            "wave": "Smallest wave depth currently known for this node in mesh topology.",
            "path": "Stored mesh path currently known to the node. For reported nodes this is the route used in TX Preview.",
            "mode": "Recommended default speed to the first receiving station: T = Turbo, F = Fast, N = Normal.",
            "freshness": "Current freshness of this known-node path in minutes. In Test Mode, old nodes are still shown.",
        }
        self._picker_geometry_setting_key = "request_jr_picker_geometry"
        self._picker_drag_start = None

    def _type_label(self, type_key):
        labels = {
            "GENERAL": "Details",
        }
        return labels.get(str(type_key or "").strip().upper(), "Details")

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def get_window(self):
        return self.window

    def get_type_key(self):
        return "GENERAL"

    def set_type(self, type_key):
        self.type_var.set("GENERAL")
        self._update_frame_states()

    def get_target_mode(self, type_key=None):
        active = str(type_key or self.get_type_key()).strip().upper()
        return str(self.target_mode_vars.get(active, tk.StringVar(value="RECIPIENT")).get() or "RECIPIENT").strip().upper()

    def get_report_scope(self):
        return str(self.report_scope_var.get() or "GENERAL").strip().upper()

    def get_recipient(self, type_key=None):
        active = str(type_key or self.get_type_key()).strip().upper()
        return str(self.recipient_vars.get(active, tk.StringVar(value="")).get() or "").strip().upper()

    def get_hrc_target_callsign(self):
        return str(self.hrc_target_var.get() or "").strip().upper()

    def set_recipient(self, type_key, callsign):
        key = str(type_key or "GENERAL").strip().upper()
        if key in self.recipient_vars:
            self.recipient_vars[key].set(str(callsign or "").strip().upper())
        if key in self.target_mode_vars:
            self.target_mode_vars[key].set("RECIPIENT")
        self._notify_state_changed()

    def _hide_picker_heading_tooltip(self):
        tooltip = self._picker_tooltip
        if tooltip is None:
            return
        try:
            if tooltip.winfo_exists():
                tooltip.withdraw()
        except Exception:
            pass
        self._picker_heading_key = None

    def _show_picker_heading_tooltip(self, heading_key, x_root, y_root):
        help_text = str(self._picker_heading_help.get(heading_key, "") or "").strip()
        if not help_text:
            self._hide_picker_heading_tooltip()
            return
        tooltip = self._picker_tooltip
        label = self._picker_tooltip_label
        try:
            needs_create = (
                tooltip is None
                or label is None
                or not tooltip.winfo_exists()
                or not label.winfo_exists()
            )
        except Exception:
            needs_create = True
        if needs_create:
            tooltip = tk.Toplevel(self.picker_window if self.picker_window is not None else self.master)
            tooltip.withdraw()
            tooltip.overrideredirect(True)
            tooltip.configure(bg="#ffffdd")
            label = tk.Label(
                tooltip,
                text=help_text,
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
            label.pack(fill="both", expand=True)
            self._picker_tooltip = tooltip
            self._picker_tooltip_label = label
        else:
            label.configure(text=help_text)
        tooltip.geometry(f"+{x_root + 12}+{y_root + 12}")
        tooltip.deiconify()
        tooltip.lift()
        self._picker_heading_key = heading_key

    def _on_picker_tree_motion(self, event):
        tree = self.picker_tree
        if tree is None:
            return
        region = tree.identify_region(event.x, event.y)
        if region != "heading":
            self._hide_picker_heading_tooltip()
            return
        column_id = tree.identify_column(event.x)
        if column_id == "#1":
            heading_key = "callsign"
        elif column_id == "#2":
            heading_key = "known_as"
        elif column_id == "#3":
            heading_key = "wave"
        elif column_id == "#4":
            heading_key = "path"
        elif column_id == "#5":
            heading_key = "mode"
        elif column_id == "#6":
            heading_key = "freshness"
        else:
            heading_key = None
        if not heading_key:
            self._hide_picker_heading_tooltip()
            return
        if heading_key == self._picker_heading_key:
            return
        self._show_picker_heading_tooltip(heading_key, event.x_root, event.y_root)

    def _copy_picker_selection(self):
        tree = self.picker_tree
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            return
        lines = []
        for item_id in selection:
            item = tree.item(item_id)
            values = [str(value) for value in item.get("values", [])]
            lines.append("\t".join(values))
        text = "\n".join(lines).strip()
        if not text:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()

    def _select_picker_drag_range(self, start_item, current_item):
        tree = self.picker_tree
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
        selected = children[low:high + 1]
        tree.selection_set(selected)
        tree.focus(current_item)

    def _on_picker_drag_start(self, event):
        tree = self.picker_tree
        if tree is None:
            return
        row_id = tree.identify_row(event.y)
        self._picker_drag_start = row_id if row_id else None

    def _on_picker_drag_motion(self, event):
        tree = self.picker_tree
        if tree is None:
            return
        row_id = tree.identify_row(event.y)
        if self._picker_drag_start and row_id:
            self._select_picker_drag_range(self._picker_drag_start, row_id)

    def _show_picker_context_menu(self, event):
        tree = self.picker_tree
        if tree is None:
            return
        row_id = tree.identify_row(event.y)
        current_selection = set(tree.selection())
        if row_id and row_id not in current_selection:
            tree.selection_set((row_id,))
            tree.focus(row_id)
        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(label="Copy Selected Row(s)", command=self._copy_picker_selection)
        menu.add_command(label="Select All", command=lambda: tree.selection_set(tree.get_children("")))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def set_preview_text(self, text):
        preview_text = str(text or "")
        self.preview_var.set(preview_text)
        if self.preview_widget is None:
            return
        try:
            if not self.preview_widget.winfo_exists():
                self.preview_widget = None
                return
            self.preview_widget.configure(state="normal")
            self.preview_widget.delete("1.0", "end")
            self.preview_widget.insert("1.0", preview_text)
            self.preview_widget.configure(state="disabled")
        except Exception:
            self.preview_widget = None

    def get_preview_text(self):
        return str(self.preview_var.get() or "").strip()

    def get_speed_mode(self):
        return str(self.speed_mode_var.get() or "DEFAULT").strip().upper()

    def set_send_effects_text(self, text):
        self.send_effects_var.set(str(text or ""))

    def _notify_state_changed(self):
        if self.get_report_scope() in ("HEARD_STATIONS", "HEARD_RELAY_CANDIDATE") and self.get_target_mode("GENERAL") == "GROUP":
            self.target_mode_vars["GENERAL"].set("RECIPIENT")
        self._update_speed_mode_states()
        if callable(self.state_changed_callback):
            self.state_changed_callback()

    def _update_speed_mode_states(self):
        target_mode = self.get_target_mode("GENERAL")
        report_scope = self.get_report_scope()
        default_button = self._speed_mode_buttons.get("DEFAULT")
        default_enabled = target_mode != "GROUP"
        if default_button is not None and not default_enabled and self.get_speed_mode() == "DEFAULT":
            self.speed_mode_var.set("NORMAL")
        if default_button is not None:
            try:
                default_button.configure(state="normal" if default_enabled else "disabled")
            except Exception:
                pass
            try:
                default_button.configure(disabledforeground="#888888")
            except Exception:
                pass
        frame_info = self.frame_widgets.get("GENERAL", {}) if isinstance(self.frame_widgets, dict) else {}
        group_radio = frame_info.get("group_radio")
        if group_radio is not None:
            try:
                group_radio.configure(
                    state="disabled" if report_scope in ("HEARD_STATIONS", "HEARD_RELAY_CANDIDATE") else "normal",
                    disabledforeground="#888888",
                )
            except Exception:
                pass
        target_label = frame_info.get("hrc_target_label")
        target_entry = frame_info.get("hrc_target_entry")
        target_enabled = report_scope in ("HEARD_RELAY_CANDIDATE", "FIND_CALLSIGN")
        if target_label is not None:
            try:
                target_label.configure(fg=self.fg_color if target_enabled else "#888888")
            except Exception:
                pass
        if target_entry is not None:
            try:
                target_entry.configure(
                    state="normal" if target_enabled else "disabled",
                    fg="#000000",
                    disabledforeground="#666666",
                    bg="#ffffff" if target_enabled else "#eeeeee",
                    insertbackground="#000000",
                )
            except Exception:
                pass

    def _copy_preview(self):
        widget = self.preview_widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                self.preview_widget = None
                return
        except Exception:
            self.preview_widget = None
            return
        try:
            selected = widget.get("sel.first", "sel.last")
        except Exception:
            selected = self.get_preview_text()
        if not selected:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(selected)
        self.master.update()

    def _show_preview_context_menu(self, event):
        widget = self.preview_widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                self.preview_widget = None
                return
        except Exception:
            self.preview_widget = None
            return
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_preview)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _set_frame_enabled(self, type_key, enabled):
        widgets = self.frame_widgets.get(type_key, {})
        frame = widgets.get("frame")
        if frame is not None:
            try:
                frame.configure(fg=self.fg_color if enabled else "#888888")
            except Exception:
                pass
        state = "normal" if enabled else "disabled"
        fg = self.fg_color if enabled else "#888888"
        for widget in widgets.get("widgets", []):
            is_entry = isinstance(widget, tk.Entry)
            try:
                widget.configure(state=state)
            except Exception:
                pass
            try:
                if is_entry:
                    widget.configure(
                        fg="#000000",
                        disabledforeground="#666666",
                        bg="#ffffff" if enabled else "#eeeeee",
                        insertbackground="#000000",
                    )
                else:
                    widget.configure(fg=fg)
            except Exception:
                pass
            try:
                if not is_entry:
                    widget.configure(disabledforeground="#888888")
            except Exception:
                pass

    def _update_frame_states(self):
        active = self.get_type_key()
        for type_key in self.TYPE_KEYS:
            self._set_frame_enabled(type_key, type_key == active)
        self._notify_state_changed()

    def _refresh_picker_rows(self):
        tree = self.picker_tree
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                self.picker_tree = None
                return
        except Exception:
            self.picker_tree = None
            return
        search_text = str(self.picker_search_var.get() or "").strip().upper()
        selected_wave = str(self.picker_wave_filter_var.get() or "All").strip().upper()
        tree.delete(*tree.get_children(""))
        if callable(self.known_nodes_provider):
            try:
                nodes = list(self.known_nodes_provider(ignore_freshness=bool(self.picker_test_mode_var.get())))
            except TypeError:
                nodes = list(self.known_nodes_provider())
        else:
            nodes = []
        self._picker_rows = []
        self._picker_all_rows = []
        for node in nodes:
            callsign = str(node.get("callsign", "") or "").strip().upper()
            known_as = str(node.get("known_as", "") or "").strip()
            wave_text = "" if node.get("wave_depth") in (None, "") else str(node.get("wave_depth"))
            path_text = str(node.get("path_text", "") or "").strip()
            mode_text = str(node.get("mode", "") or "").strip().upper()
            freshness_minutes = node.get("freshness_minutes")
            freshness_text = str(node.get("freshness_text", "") or "").strip()
            if not callsign:
                continue
            if search_text and search_text not in callsign.upper():
                continue
            if selected_wave == "DIRECTLY HEARD" and known_as.upper() != "DIRECTLY HEARD":
                continue
            if selected_wave.startswith("WAVE "):
                expected_wave = selected_wave.split(" ", 1)[1].strip()
                if wave_text != expected_wave:
                    continue
            row_data = dict(node)
            row_data["_display_values"] = (callsign, known_as, wave_text, path_text, mode_text, freshness_text)
            row_data["_sort_freshness"] = 999999 if freshness_minutes in (None, "") else float(freshness_minutes)
            self._picker_all_rows.append(row_data)
        self._sort_picker_rows(refresh=False)

    def _sort_picker_rows(self, column=None, refresh=True):
        tree = self.picker_tree
        if tree is None:
            return
        if column:
            if self._picker_sort_column == column:
                self._picker_sort_descending = not self._picker_sort_descending
            else:
                self._picker_sort_column = column
                self._picker_sort_descending = False
        if refresh:
            self._refresh_picker_rows()
            return
        tree.delete(*tree.get_children(""))
        rows = list(self._picker_all_rows)
        sort_column = self._picker_sort_column

        def sort_key(row):
            callsign = str(row.get("callsign", "") or "")
            known_as = str(row.get("known_as", "") or "")
            wave_depth = row.get("wave_depth")
            try:
                wave_value = int(wave_depth)
            except Exception:
                wave_value = 999999
            path_text = str(row.get("path_text", "") or "")
            mode_text = str(row.get("mode", "") or "")
            freshness_value = float(row.get("_sort_freshness", 999999))
            mapping = {
                "callsign": callsign,
                "known_as": known_as,
                "wave": wave_value,
                "path": path_text,
                "mode": mode_text,
                "freshness": freshness_value,
            }
            return mapping.get(sort_column, callsign)

        rows.sort(key=sort_key, reverse=bool(self._picker_sort_descending))
        self._picker_rows = []
        for row in rows:
            values = tuple(row.get("_display_values", ()))
            row_id = tree.insert("", "end", values=values)
            self._picker_rows.append((row_id, dict(row)))

    def _apply_picker_selection(self):
        tree = self.picker_tree
        if tree is None or not self.picker_type:
            return
        selection = tree.selection()
        if not selection:
            return
        item = tree.item(selection[0])
        values = item.get("values", [])
        if not values:
            return
        selected_info = {}
        for row_id, row_data in self._picker_rows:
            if row_id == selection[0]:
                selected_info = dict(row_data)
                break
        self.set_recipient(self.picker_type, values[0])
        if callable(self.node_selected_callback):
            self.node_selected_callback(self.picker_type, selected_info)
        self._close_picker_window()

    def _close_picker_window(self):
        request_window = self.window
        try:
            if self.picker_window is not None and self.picker_window.winfo_exists():
                settings[self._picker_geometry_setting_key] = self.picker_window.geometry()
                save_settings(settings)
        except Exception:
            pass
        try:
            if self.picker_window is not None and self.picker_window.winfo_exists():
                self.picker_window.destroy()
        except Exception:
            pass
        self.picker_window = None
        self.picker_tree = None
        self.picker_type = None
        self._picker_rows = []
        self._hide_picker_heading_tooltip()
        try:
            if request_window is not None and request_window.winfo_exists():
                request_window.lift()
                request_window.focus_force()
        except Exception:
            pass

    def _open_picker(self, type_key):
        self.picker_type = type_key
        if self.picker_window is not None:
            try:
                if self.picker_window.winfo_exists():
                    self.picker_window.deiconify()
                    self.picker_window.lift()
                    self.picker_window.focus_force()
                    self._refresh_picker_rows()
                    return
            except Exception:
                pass
            self.picker_window = None
            self.picker_tree = None

        dialog = tk.Toplevel(self.window if self.window is not None else self.master)
        dialog.title("Known Nodes")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(True, True)
        self.picker_window = dialog

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
        search_entry = tk.Entry(search_row, textvariable=self.picker_search_var, width=24, bg="#ffffff", fg="#000000", insertbackground="#000000")
        search_entry.pack(side="left", fill="x", expand=True)
        search_entry.bind("<KeyRelease>", lambda _event: self._refresh_picker_rows())
        tk.Label(search_row, text="Show:", bg=self.bg_color, fg=self.fg_color).pack(side="left", padx=(12, 8))
        wave_combo = ttk.Combobox(
            search_row,
            textvariable=self.picker_wave_filter_var,
            values=("All", "Directly Heard", "Wave 1", "Wave 2", "Wave 3"),
            state="readonly",
            width=14,
        )
        wave_combo.pack(side="left")
        wave_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_picker_rows())
        test_mode_check = tk.Checkbutton(
            search_row,
            text="Test Mode",
            variable=self.picker_test_mode_var,
            bg=self.bg_color,
            fg=self.fg_color,
            selectcolor=self.bg_color,
            activebackground=self.bg_color,
            activeforeground=self.fg_color,
            highlightthickness=0,
            command=self._on_picker_test_mode_changed,
        )
        test_mode_check.pack(side="left", padx=(12, 0))

        tree_frame = tk.Frame(outer, bg=self.bg_color)
        tree_frame.pack(fill="both", expand=True, pady=(12, 0))
        tree = ttk.Treeview(tree_frame, columns=("callsign", "known_as", "wave", "path", "mode", "freshness"), show="headings", height=10, selectmode="extended")
        tree.heading("callsign", text="Known Node", command=lambda: self._sort_picker_rows("callsign"))
        tree.heading("known_as", text="How Known", command=lambda: self._sort_picker_rows("known_as"))
        tree.heading("wave", text="Wave", command=lambda: self._sort_picker_rows("wave"))
        tree.heading("path", text="Path", command=lambda: self._sort_picker_rows("path"))
        tree.heading("mode", text="Mode", command=lambda: self._sort_picker_rows("mode"))
        tree.heading("freshness", text="Freshness", command=lambda: self._sort_picker_rows("freshness"))
        tree.column("callsign", width=220, anchor="w")
        tree.column("known_as", width=140, anchor="w")
        tree.column("wave", width=60, anchor="center")
        tree.column("path", width=280, anchor="w")
        tree.column("mode", width=60, anchor="center")
        tree.column("freshness", width=90, anchor="center")
        tree.pack(side="left", fill="both", expand=True)
        self.picker_tree = tree
        tree.bind("<Double-1>", lambda _event: self._apply_picker_selection())
        tree.bind("<Motion>", self._on_picker_tree_motion)
        tree.bind("<Leave>", lambda _event: self._hide_picker_heading_tooltip())
        tree.bind("<ButtonPress-1>", self._on_picker_drag_start)
        tree.bind("<B1-Motion>", self._on_picker_drag_motion)
        tree.bind("<Button-3>", self._show_picker_context_menu)
        tree.bind("<Control-c>", lambda _event: self._copy_picker_selection())

        scroll_y = tk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        scroll_y.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll_y.set)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(12, 0))
        tk.Button(button_row, text="Request Report", command=self._apply_picker_selection, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left")
        tk.Button(button_row, text="Close", command=self._close_picker_window, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left", padx=(8, 0))

        dialog.protocol("WM_DELETE_WINDOW", self._close_picker_window)
        dialog.update_idletasks()
        saved_geometry = str(settings.get(self._picker_geometry_setting_key, "") or "").strip()
        if saved_geometry:
            dialog.geometry(saved_geometry)
        else:
            try:
                screen_w = int(dialog.winfo_screenwidth())
                screen_h = int(dialog.winfo_screenheight())
                width = max(dialog.winfo_reqwidth(), 760)
                height = max(dialog.winfo_reqheight(), 360)
                x = max(0, int((screen_w - width) / 2))
                y = max(0, int((screen_h - height) / 2))
                dialog.geometry(f"{width}x{height}+{x}+{y}")
            except Exception:
                anchor = self.window if self.window is not None else self.master
                x = anchor.winfo_rootx() + 110
                y = anchor.winfo_rooty() + 110
                dialog.geometry(f"+{x}+{y}")
        self._refresh_picker_rows()
        dialog.lift()
        dialog.focus_force()
        search_entry.focus_set()

    def _on_picker_test_mode_changed(self):
        settings["request_jr_picker_test_mode"] = bool(self.picker_test_mode_var.get())
        save_settings(settings)
        self._refresh_picker_rows()

    def close(self):
        self._close_picker_window()
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass
        self.window = None
        self.preview_widget = None
        self.frame_widgets = {}
        self._hide_picker_heading_tooltip()

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self._update_frame_states()
            return

        dialog = tk.Toplevel(self.master)
        dialog.title("Request Report")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(True, True)
        self.window = dialog

        outer = tk.Frame(dialog, bg=self.bg_color, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        frames_container = tk.Frame(outer, bg=self.bg_color)
        frames_container.pack(fill="x")

        for type_key in self.TYPE_KEYS:
            frame = tk.LabelFrame(
                frames_container,
                text=self._type_label(type_key),
                bg=self.bg_color,
                fg=self.fg_color,
                padx=10,
                pady=10,
            )
            frame.pack(fill="x", pady=(0, 8))

            mode_var = self.target_mode_vars[type_key]
            recipient_var = self.recipient_vars[type_key]

            scope_row = tk.Frame(frame, bg=self.bg_color)
            scope_row.pack(fill="x", pady=(0, 8))
            for scope_key, scope_label in (
                ("GENERAL", "General"),
                ("NODES_ONLY", "Nodes Only"),
                ("STATIONS_ONLY", "Stations Only"),
                ("HEARD_STATIONS", "Heard 4 Stations"),
                ("HEARD_RELAY_CANDIDATE", "Can Relay to Callsign"),
                ("FIND_CALLSIGN", "Find Callsign"),
            ):
                tk.Radiobutton(
                    scope_row,
                    text=scope_label,
                    variable=self.report_scope_var,
                    value=scope_key,
                    bg=self.bg_color,
                    fg=self.fg_color,
                    selectcolor=self.bg_color,
                    activebackground=self.bg_color,
                    activeforeground=self.fg_color,
                    highlightthickness=0,
                    command=self._notify_state_changed,
                ).pack(side="left", padx=(0, 12))

            recipient_row = tk.Frame(frame, bg=self.bg_color)
            recipient_row.pack(fill="x")
            direct_radio = tk.Radiobutton(
                recipient_row,
                text="Choose Recipient:",
                variable=mode_var,
                value="RECIPIENT",
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
                command=self._notify_state_changed,
            )
            direct_radio.pack(side="left", padx=(0, 8))
            recipient_entry = tk.Entry(recipient_row, textvariable=recipient_var, width=14, bg="#ffffff", fg="#000000", insertbackground="#000000")
            recipient_entry.pack(side="left")
            recipient_entry.bind("<KeyRelease>", lambda _event: self._notify_state_changed())
            select_button = tk.Button(
                recipient_row,
                text="Select from known nodes",
                command=lambda t=type_key: self._open_picker(t),
                bg=self.highlight_color,
                fg=self.fg_color,
                width=22,
            )
            select_button.pack(side="left", padx=(8, 0))

            group_row = tk.Frame(frame, bg=self.bg_color)
            group_row.pack(fill="x", pady=(8, 0))
            group_radio = tk.Radiobutton(
                group_row,
                text="Send to @JS8MESH",
                variable=mode_var,
                value="GROUP",
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
                command=self._notify_state_changed,
            )
            group_radio.pack(side="left")

            hrc_row = tk.Frame(frame, bg=self.bg_color)
            hrc_row.pack(fill="x", pady=(8, 0))
            hrc_target_label = tk.Label(
                hrc_row,
                text="Target Callsign for HRC/FIND:",
                bg=self.bg_color,
                fg="#888888",
            )
            hrc_target_label.pack(side="left", padx=(0, 8))
            hrc_target_entry = tk.Entry(
                hrc_row,
                textvariable=self.hrc_target_var,
                width=14,
                state="disabled",
                bg="#eeeeee",
                fg="#000000",
                insertbackground="#000000",
            )
            hrc_target_entry.pack(side="left")
            hrc_target_entry.bind("<KeyRelease>", lambda _event: self._notify_state_changed())
            self.hrc_target_entry = hrc_target_entry

            self.frame_widgets[type_key] = {
                "frame": frame,
                "widgets": [direct_radio, recipient_entry, select_button, group_radio],
                "group_radio": group_radio,
                "hrc_target_label": hrc_target_label,
                "hrc_target_entry": hrc_target_entry,
            }

        mode_row = tk.Frame(outer, bg=self.bg_color)
        mode_row.pack(fill="x", pady=(12, 0))
        tk.Label(mode_row, text="Speed Mode:", bg=self.bg_color, fg=self.fg_color).pack(side="left", padx=(0, 8))
        for mode_name in ("DEFAULT", "TURBO", "FAST", "NORMAL"):
            button = tk.Radiobutton(
                mode_row,
                text=mode_name.title(),
                variable=self.speed_mode_var,
                value=mode_name,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
                command=self._notify_state_changed,
            )
            button.pack(side="left", padx=(0, 8))
            self._speed_mode_buttons[mode_name] = button

        tk.Label(
            outer,
            textvariable=self.send_effects_var,
            bg=self.bg_color,
            fg="#ffcc66",
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(fill="x", pady=(8, 0))

        preview_frame = tk.LabelFrame(outer, text="TX Preview", bg=self.bg_color, fg=self.fg_color, padx=10, pady=10)
        preview_frame.pack(fill="both", expand=True, pady=(12, 0))

        preview_text = tk.Text(
            preview_frame,
            height=5,
            width=72,
            wrap="word",
            state="disabled",
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.fg_color,
            selectbackground="#555555",
            selectforeground="#ffffff",
        )
        preview_text.pack(side="left", fill="both", expand=True)
        preview_text.bind("<Button-3>", self._show_preview_context_menu)
        preview_text.bind("<Control-c>", lambda _event: self._copy_preview())
        self.preview_widget = preview_text

        scroll_y = tk.Scrollbar(preview_frame, orient="vertical", command=preview_text.yview)
        scroll_y.pack(side="right", fill="y")
        preview_text.configure(yscrollcommand=scroll_y.set)

        button_row = tk.Frame(outer, bg=self.bg_color)
        button_row.pack(fill="x", pady=(12, 0))
        tk.Button(button_row, text="Send to JS8Call", command=lambda: self.send_callback() if callable(self.send_callback) else None, bg=self.highlight_color, fg=self.fg_color, width=16).pack(side="left")
        tk.Button(button_row, text="Copy to Clipboard", command=self._copy_preview, bg=self.highlight_color, fg=self.fg_color, width=16).pack(side="left", padx=(8, 0))
        tk.Button(button_row, text="Close", command=self.close, bg=self.highlight_color, fg=self.fg_color, width=12).pack(side="left", padx=(8, 0))

        dialog.protocol("WM_DELETE_WINDOW", self.close)
        dialog.update_idletasks()
        x = self.master.winfo_rootx() + 100
        y = self.master.winfo_rooty() + 100
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.focus_force()
        self._update_frame_states()
