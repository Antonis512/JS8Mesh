import tkinter as tk


class MeshNetworkWindow:

    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        initial_station_count="5",
        initial_lookback_minutes="20",
        initial_broadcast_interval="20",
        initial_broadcast_times_24h="",
        initial_tx_mode="NORMAL",
        initial_tx_time_limit_minutes="0",
        save_callback=None,
        broadcast_now_callback=None,
        copy_preview_callback=None,
        send_to_js8call_callback=None,
        show_activity_callback=None,
        input_changed_callback=None,
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color

        self.save_callback = save_callback
        self.broadcast_now_callback = broadcast_now_callback
        self.copy_preview_callback = copy_preview_callback
        self.send_to_js8call_callback = send_to_js8call_callback
        self.show_activity_callback = show_activity_callback
        self.input_changed_callback = input_changed_callback

        self.window = None
        self.preview_text = None
        self.lookback_entry = None
        self._pending_input_after_id = None

        self.schedule_mode_var = tk.StringVar(value="Schedule mode: interval")
        self.status_details_var = tk.StringVar(value="Mesh report scheduler idle.")
        self.tx_mode_var = tk.StringVar(value=str(initial_tx_mode or "NORMAL").strip().upper())
        self.estimated_tx_time_var = tk.StringVar(value="Estimated TX Time: n/a")
        self.warning_var = tk.StringVar(value="")

        self.station_count_var = tk.StringVar(value=str(initial_station_count))
        self.lookback_minutes_var = tk.StringVar(value=str(initial_lookback_minutes))
        self.broadcast_interval_var = tk.StringVar(value=str(initial_broadcast_interval))
        self.broadcast_times_24h_var = tk.StringVar(value=str(initial_broadcast_times_24h or ""))
        self.tx_time_limit_minutes_var = tk.StringVar(value=str(initial_tx_time_limit_minutes))

        self.tx_mode_var.trace_add("write", self._on_any_input_changed)

    def has_window(self):
        return self.window is not None and self.window.winfo_exists()

    def _close_window(self):
        try:
            if self.window is not None and self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass
        self.window = None
        self.preview_text = None
        self.lookback_entry = None
        self._pending_input_after_id = None

    def show(self):
        if self.has_window():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self.window = tk.Toplevel(self.master)
        self.window.title("Mesh Reports")
        self.window.configure(bg=self.bg_color)
        try:
            self.window.attributes("-topmost", False)
        except Exception:
            pass

        outer = tk.Frame(self.window, bg=self.bg_color, padx=12, pady=12)
        outer.pack(fill="both", expand=True)

        controls = tk.LabelFrame(
            outer,
            text="Mesh Report Reminder",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=10,
            pady=10
        )
        controls.pack(fill="x", expand=False)

        tk.Label(
            controls,
            text="Stations to broadcast:",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=0, column=0, sticky="w", pady=4)

        tk.Entry(
            controls,
            textvariable=self.station_count_var,
            width=8
        ).grid(row=0, column=1, sticky="w", padx=(8, 18), pady=4)

        tk.Label(
            controls,
            text="Look back period (minutes):",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=0, column=2, sticky="w", pady=4)

        self.lookback_entry = tk.Entry(
            controls,
            textvariable=self.lookback_minutes_var,
            width=8
        )
        self.lookback_entry.grid(row=0, column=3, sticky="w", padx=(8, 18), pady=4)

        tk.Label(
            controls,
            text="Broadcast interval (minutes):",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=1, column=0, sticky="w", pady=4)

        tk.Entry(
            controls,
            textvariable=self.broadcast_interval_var,
            width=8
        ).grid(row=1, column=1, sticky="w", padx=(8, 18), pady=4)

        tk.Label(
            controls,
            text="JR/HR/HRC TX time limit (minutes):",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=1, column=2, sticky="w", pady=4)

        limit_row = tk.Frame(controls, bg=self.bg_color)
        limit_row.grid(row=1, column=3, sticky="w", padx=(8, 18), pady=4)

        tk.Entry(
            limit_row,
            textvariable=self.tx_time_limit_minutes_var,
            width=8
        ).pack(side="left")

        tk.Label(
            limit_row,
            text="0 = No limit",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(8, 0))

        mode_row = tk.Frame(controls, bg=self.bg_color)
        mode_row.grid(row=2, column=2, columnspan=2, sticky="w", pady=4)

        tk.Label(
            mode_row,
            text="Mode:",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 10, "bold")
        ).pack(side="left", padx=(0, 8))

        for mode_name in ("TURBO", "FAST", "NORMAL"):
            tk.Radiobutton(
                mode_row,
                text=mode_name.title(),
                variable=self.tx_mode_var,
                value=mode_name,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0,
                anchor="w",
                justify="left"
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            controls,
            text="Specific broadcast times (24h, comma-separated):",
            bg=self.bg_color,
            fg=self.fg_color
        ).grid(row=3, column=0, sticky="w", pady=4)

        tk.Entry(
            controls,
            textvariable=self.broadcast_times_24h_var,
            width=40
        ).grid(row=3, column=1, columnspan=3, sticky="we", padx=(8, 18), pady=4)

        example_row = tk.Frame(controls, bg=self.bg_color)
        example_row.grid(row=4, column=0, columnspan=4, sticky="we", pady=(0, 6))

        tk.Label(
            example_row,
            text="Example: 08:00, 12:30, 18:45, 23:00",
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left"
        ).pack(side="left")

        tk.Button(
            example_row,
            text="Save New Settings",
            command=self._save_clicked,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=16
        ).pack(side="right")

        tk.Button(
            example_row,
            text="Show Today's Decoded Mesh",
            command=self._show_activity_clicked,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="right", padx=(0, 8))

        status_frame = tk.LabelFrame(
            outer,
            text="Mesh Report Scheduler Status",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=10,
            pady=10
        )
        status_frame.pack(fill="x", expand=False, pady=(10, 0))
        status_frame.grid_columnconfigure(0, weight=3)
        status_frame.grid_columnconfigure(1, weight=1)
        status_frame.grid_columnconfigure(2, weight=2)

        left_frame = tk.Frame(status_frame, bg=self.bg_color)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 16))

        tk.Label(
            left_frame,
            textvariable=self.schedule_mode_var,
            bg=self.bg_color,
            fg="#ff4444",
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 12, "bold")
        ).pack(fill="x", anchor="w", pady=(0, 6))

        tk.Label(
            left_frame,
            textvariable=self.status_details_var,
            bg=self.bg_color,
            fg=self.fg_color,
            justify="left",
            anchor="w",
            wraplength=520
        ).pack(fill="x", anchor="w")

        right_frame = tk.Frame(status_frame, bg=self.bg_color)
        right_frame.grid(row=0, column=1, columnspan=2, sticky="ne")

        tk.Label(
            right_frame,
            textvariable=self.estimated_tx_time_var,
            bg=self.bg_color,
            fg=self.fg_color,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        tk.Label(
            right_frame,
            textvariable=self.warning_var,
            bg=self.bg_color,
            fg="#ff4444",
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 10, "bold"),
            wraplength=260
        ).pack(anchor="w", pady=(6, 0))

        preview_frame = tk.LabelFrame(
            outer,
            text="Mesh Report Preview",
            bg=self.bg_color,
            fg=self.fg_color,
            padx=10,
            pady=10
        )
        preview_frame.pack(fill="both", expand=True, pady=(10, 0))

        preview_actions = tk.Frame(preview_frame, bg=self.bg_color)
        preview_actions.pack(fill="x", pady=(0, 8))

        tk.Button(
            preview_actions,
            text="Copy Preview",
            command=self._copy_preview_clicked,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=14
        ).pack(side="left")

        if self.send_to_js8call_callback is not None:
            tk.Button(
                preview_actions,
                text="Send to JS8Call",
                command=self._send_to_js8call_clicked,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=16
            ).pack(side="left", padx=(8, 0))

        self.preview_text = tk.Text(
            preview_frame,
            height=12,
            wrap="word",
            undo=False
        )
        self.preview_text.pack(side="left", fill="both", expand=True)

        scroll_y = tk.Scrollbar(
            preview_frame,
            orient="vertical",
            command=self.preview_text.yview
        )
        scroll_y.pack(side="right", fill="y")
        self.preview_text.configure(yscrollcommand=scroll_y.set)

        bottom = tk.Frame(outer, bg=self.bg_color)
        bottom.pack(fill="x", pady=(10, 0))

        tk.Button(
            bottom,
            text="Close",
            command=self._close_window,
            bg=self.highlight_color,
            fg=self.fg_color,
            width=12
        ).pack(side="right")

        self.window.protocol("WM_DELETE_WINDOW", self._close_window)

        self.set_preview_text(
            "Mesh report lines will appear here.\n\n"
            "Preview wraps to window width. The transmitted report remains a single-line message."
        )

    def _on_any_input_changed(self, *_args):
        if self.input_changed_callback is None:
            return
        try:
            if self._pending_input_after_id is not None and self.master is not None:
                self.master.after_cancel(self._pending_input_after_id)
        except Exception:
            pass
        try:
            self._pending_input_after_id = self.master.after(300, self._run_input_changed_callback)
        except Exception:
            self._pending_input_after_id = None
            self.input_changed_callback()

    def _run_input_changed_callback(self):
        self._pending_input_after_id = None
        if self.input_changed_callback is not None:
            self.input_changed_callback()

    def _save_clicked(self):
        if self.save_callback is not None:
            self.save_callback()

    def _broadcast_now_clicked(self):
        if self.broadcast_now_callback is not None:
            self.broadcast_now_callback()

    def _copy_preview_clicked(self):
        if self.copy_preview_callback is not None:
            self.copy_preview_callback()

    def _send_to_js8call_clicked(self):
        if self.send_to_js8call_callback is not None:
            self.send_to_js8call_callback()

    def _show_activity_clicked(self):
        if self.show_activity_callback is not None:
            self.show_activity_callback()

    def get_station_count_text(self):
        return self.station_count_var.get().strip()

    def get_lookback_minutes_text(self):
        return self.lookback_minutes_var.get().strip()

    def get_broadcast_interval_text(self):
        return self.broadcast_interval_var.get().strip()

    def get_broadcast_times_24h_text(self):
        return self.broadcast_times_24h_var.get().strip()

    def get_tx_mode_text(self):
        return self.tx_mode_var.get().strip().upper()

    def get_tx_time_limit_minutes_text(self):
        return self.tx_time_limit_minutes_var.get().strip()

    def set_station_count_text(self, value):
        self.station_count_var.set(str(value))

    def set_lookback_minutes_text(self, value):
        self.lookback_minutes_var.set(str(value))

    def set_lookback_editable(self, editable):
        if self.lookback_entry is None:
            return
        try:
            self.lookback_entry.configure(state="normal" if editable else "readonly")
        except Exception:
            pass

    def set_broadcast_interval_text(self, value):
        self.broadcast_interval_var.set(str(value))

    def set_broadcast_times_24h_text(self, value):
        self.broadcast_times_24h_var.set(str(value))

    def set_tx_mode_text(self, value):
        self.tx_mode_var.set(str(value).strip().upper())

    def set_tx_time_limit_minutes_text(self, value):
        self.tx_time_limit_minutes_var.set(str(value))

    def get_preview_text(self):
        if self.preview_text is None:
            return ""
        try:
            if not self.preview_text.winfo_exists():
                self.preview_text = None
                return ""
            return self.preview_text.get("1.0", "end-1c").strip()
        except Exception:
            self.preview_text = None
            return ""

    def set_preview_text(self, text):
        if self.preview_text is None:
            return
        try:
            if not self.preview_text.winfo_exists():
                self.preview_text = None
                return
            self.preview_text.delete("1.0", "end")
            self.preview_text.insert("1.0", text)
        except Exception:
            self.preview_text = None

    def set_status_text(self, schedule_mode_text, details_text):
        self.schedule_mode_var.set(str(schedule_mode_text))
        self.status_details_var.set(str(details_text))

    def set_estimated_tx_time_text(self, text):
        self.estimated_tx_time_var.set(str(text))

    def set_warning_text(self, text):
        self.warning_var.set(str(text))
