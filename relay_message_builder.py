import tkinter as tk
from tkinter import messagebox


class RelayMessageBuilder:
    def __init__(
        self,
        master,
        bg_color,
        fg_color,
        highlight_color,
        get_selected_pathway_callback,
        send_to_js8call_callback=None,
        show_past_relays_callback=None,
        mark_success_callback=None,
        mark_failure_callback=None,
        initial_tx_mode="DEFAULT",
    ):
        self.master = master
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.highlight_color = highlight_color
        self.get_selected_pathway_callback = get_selected_pathway_callback
        self.send_to_js8call_callback = send_to_js8call_callback
        self.show_past_relays_callback = show_past_relays_callback
        self.mark_success_callback = mark_success_callback
        self.mark_failure_callback = mark_failure_callback

        self.message_mode_var = tk.StringVar(value="MSG")
        self.tx_mode_var = tk.StringVar(value=str(initial_tx_mode or "NORMAL").strip().upper())
        self.default_tx_mode = "NORMAL"
        self.default_tx_mode_label_var = tk.StringVar(value="Default = Normal")
        self.prepared_message_var = tk.StringVar(value="")

        self.prepared_entry = None
        self.message_text = None

    def build_ui(self, parent):
        mode_row = tk.Frame(parent, bg=self.bg_color)
        mode_row.pack(fill="x", padx=5, pady=(5, 2))

        tk.Label(
            mode_row,
            text="Send Mode:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(0, 10))

        for mode in ("MSG", "MSG TO:"):
            tk.Radiobutton(
                mode_row,
                text=mode,
                variable=self.message_mode_var,
                value=mode,
                command=self._on_message_mode_changed,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0
            ).pack(side="left", padx=(0, 12))

        tk.Button(
            mode_row,
            text="Copy Prepared Text",
            command=self.copy_prepared_message,
            bg=self.highlight_color,
            fg=self.fg_color
        ).pack(side="right", padx=5)

        if self.show_past_relays_callback is not None:
            tk.Button(
                mode_row,
                text="Show Past Relays",
                command=self.show_past_relays_callback,
                bg=self.highlight_color,
                fg=self.fg_color
            ).pack(side="right", padx=5)

        if self.send_to_js8call_callback is not None:
            tk.Button(
                mode_row,
                text="Send to JS8Call",
                command=self.send_to_js8call,
                bg=self.highlight_color,
                fg=self.fg_color
            ).pack(side="right", padx=5)

        tx_mode_row = tk.Frame(parent, bg=self.bg_color)
        tx_mode_row.pack(fill="x", padx=5, pady=(2, 2))

        tk.Label(
            tx_mode_row,
            text="TX MODE:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(0, 10))

        for mode_name in ("DEFAULT", "NORMAL", "FAST", "TURBO", "SLOW"):
            tk.Radiobutton(
                tx_mode_row,
                text=mode_name.title(),
                variable=self.tx_mode_var,
                value=mode_name,
                bg=self.bg_color,
                fg=self.fg_color,
                selectcolor=self.bg_color,
                activebackground=self.bg_color,
                activeforeground=self.fg_color,
                highlightthickness=0
            ).pack(side="left", padx=(0, 12))

        tk.Label(
            tx_mode_row,
            textvariable=self.default_tx_mode_label_var,
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(side="left", padx=(10, 0))

        if self.mark_failure_callback is not None:
            tk.Button(
                tx_mode_row,
                text="Mark Failure",
                command=self.mark_failure_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=18
            ).pack(side="right")

        if self.mark_success_callback is not None:
            tk.Button(
                tx_mode_row,
                text="Mark Success",
                command=self.mark_success_callback,
                bg=self.highlight_color,
                fg=self.fg_color,
                width=18
            ).pack(side="right", padx=(0, 8))

        prepared_row = tk.Frame(parent, bg=self.bg_color)
        prepared_row.pack(fill="x", padx=5, pady=(6, 2))

        tk.Label(
            prepared_row,
            text="Prepared JS8 Text:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(anchor="w")

        self.prepared_entry = tk.Entry(
            prepared_row,
            textvariable=self.prepared_message_var
        )
        self.prepared_entry.pack(fill="x", pady=(2, 0))

        text_row = tk.Frame(parent, bg=self.bg_color)
        text_row.pack(fill="both", expand=True, padx=5, pady=(6, 5))

        tk.Label(
            text_row,
            text="Message Text:",
            bg=self.bg_color,
            fg=self.fg_color
        ).pack(anchor="w")

        text_inner = tk.Frame(text_row, bg=self.bg_color)
        text_inner.pack(fill="both", expand=True, pady=(2, 0))

        self.message_text = tk.Text(
            text_inner,
            height=5,
            wrap="word",
            undo=True
        )
        self.message_text.pack(side="left", fill="both", expand=True)
        self.message_text.bind("<KeyRelease>", self._on_message_text_changed)
        self.message_text.bind("<Button-3>", self._show_message_text_context_menu)

        text_scroll = tk.Scrollbar(text_inner, command=self.message_text.yview)
        text_scroll.pack(side="right", fill="y")
        self.message_text.configure(yscrollcommand=text_scroll.set)

    def _get_message_body(self):
        if self.message_text is None:
            return ""

        raw = self.message_text.get("1.0", "end-1c")
        lines = [line.strip() for line in raw.splitlines()]
        body = " ".join(part for part in lines if part)
        return body.strip()

    def _get_selected_pathway(self):
        if self.get_selected_pathway_callback is None:
            return ""
        return self.get_selected_pathway_callback()

    def _build_message_structure(self, pathway, mode):
        if not pathway:
            return ""

        normalized_pathway = str(pathway or "").replace("<", ">")
        nodes = [node.strip() for node in normalized_pathway.split(">") if node.strip()]
        if len(nodes) < 2:
            return ""

        chain = nodes[1:]
        if not chain:
            return ""

        recipient = chain[-1]
        relay_chain = chain[:-1]

        if mode == "MSG":
            return f'{">".join(chain)} MSG'

        if mode == "MSG TO:":
            if relay_chain:
                return f'{">".join(relay_chain)} MSG TO: {recipient} ['
            return f"MSG TO: {recipient} ["

        return f'{">".join(chain)} MSG'

    def _build_prepared_message(self, pathway, mode, body):
        structure = self._build_message_structure(pathway, mode)
        if not structure:
            return ""

        body = body.strip()

        if mode == "MSG TO:":
            return f"{structure}{body}]"

        if body:
            return f"{structure} {body}"

        return structure

    def _on_message_mode_changed(self, event=None):
        self.update_message_preview()

    def _on_message_text_changed(self, event=None):
        self.update_message_preview()

    def on_pathway_selected(self, event=None):
        self.update_message_preview()

    def _show_message_text_context_menu(self, event):
        menu = tk.Menu(
            self.master,
            tearoff=0,
            bg=self.bg_color,
            fg=self.fg_color,
            activebackground=self.highlight_color,
            activeforeground=self.fg_color
        )

        menu.add_command(
            label="Cut",
            command=lambda: self.message_text.event_generate("<<Cut>>")
        )
        menu.add_command(
            label="Copy",
            command=lambda: self.message_text.event_generate("<<Copy>>")
        )
        menu.add_command(
            label="Paste",
            command=lambda: self.message_text.event_generate("<<Paste>>")
        )
        menu.add_separator()
        menu.add_command(
            label="Select All",
            command=self._select_all_message_text
        )

        self.message_text.focus_force()
        menu.tk_popup(event.x_root, event.y_root)

    def _select_all_message_text(self):
        if self.message_text is None:
            return
        self.message_text.tag_add("sel", "1.0", "end-1c")
        self.message_text.mark_set("insert", "1.0")
        self.message_text.see("insert")

    def update_message_preview(self):
        pathway = self._get_selected_pathway()
        mode = self.message_mode_var.get()
        body = self._get_message_body()

        if not pathway:
            self.prepared_message_var.set("")
            return

        self.prepared_message_var.set(
            self._build_prepared_message(pathway, mode, body)
        )

    def copy_prepared_message(self):
        text = self.prepared_message_var.get().strip()
        if not text:
            messagebox.showwarning(
                "Nothing to Copy",
                "Select a pathway and enter message text first."
            )
            return

        self.master.clipboard_clear()
        self.master.clipboard_append(text)
        self.master.update()

        messagebox.showinfo(
            "Copied",
            "Prepared JS8 text copied to clipboard."
        )

    def get_tx_mode_text(self):
        selected = self.tx_mode_var.get().strip().upper()
        if selected == "DEFAULT":
            return str(self.default_tx_mode or "NORMAL").strip().upper() or "NORMAL"
        return selected

    def set_default_tx_mode(self, mode_name):
        normalized = str(mode_name or "").strip().upper()
        if not normalized:
            normalized = "NORMAL"
        self.default_tx_mode = normalized
        self.default_tx_mode_label_var.set(f"Default = {normalized.title()}")

    def send_to_js8call(self):
        if self.send_to_js8call_callback is not None:
            self.send_to_js8call_callback()
