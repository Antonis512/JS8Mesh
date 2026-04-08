
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

# PAST RELAYS WINDOW CONTRACT
# Columns:
#   Time / Date | Result | Mode | Pathway | Message
# Required features:
#   - search box
#   - clear search button
#   - filter: All / Successful Only / Failed Only / Pending Only
#   - newest first
#   - scrollbars inside frame
# Pathway prefixes supported:
#   [LINEAR], [INBOUND], [LINEAR OF INBOUND], [NATIVE CONFIRMED]

PAST_RELAYS_HEADING_HELP = {
    "timestamp": "When the relay was sent. This window shows the past 48 hours only.",
    "result": "S = success, F = failure, P = pending ACK within the 5-minute window.",
    "mode": "TX mode used for that relay attempt.",
    "pathway": "The pathway category and exact route used for the relay.",
    "message": "The typed relay message body for that attempt.",
}


def classify_tx_mode(app, pathway: str) -> str:
    tx_mode = "-"
    try:
        rec = getattr(app, "last_pathway_recommendations", {}).get(pathway, {})
        category_text = str(rec.get("category", "")).strip().upper()

        if "TURBO" in category_text:
            tx_mode = "TURBO"
        elif "FAST" in category_text:
            tx_mode = "FAST"
        elif "SLOW" in category_text:
            tx_mode = "SLOW"
        elif category_text:
            tx_mode = "NORMAL"
    except Exception:
        pass
    return tx_mode


def classify_display_category(app, pathway: str) -> str:
    display_category = "[LINEAR]"
    try:
        rec = getattr(app, "last_pathway_recommendations", {}).get(pathway, {})
        origin = str(rec.get("origin", "")).strip().lower()
        native_confirmed = bool(rec.get("native_confirmed", False))

        if origin == "inbound":
            display_category = "[INBOUND]"
        elif origin in ("inbound_promoted", "promoted_inbound"):
            display_category = "[LINEAR OF INBOUND]"
            if native_confirmed:
                display_category += " [NATIVE CONFIRMED]"
        elif native_confirmed:
            display_category = "[LINEAR] [NATIVE CONFIRMED]"
    except Exception:
        pass
    return display_category


def build_relay_history_entry(app, pathway: str, result: str, timestamp: str) -> dict:
    message_mode = "-"
    message_text = ""
    prepared_message = ""
    try:
        if getattr(app, "relay_builder", None) is not None:
            if hasattr(app.relay_builder, "message_mode_var") and app.relay_builder.message_mode_var is not None:
                message_mode = str(app.relay_builder.message_mode_var.get()).strip() or "-"
            if hasattr(app.relay_builder, "_get_message_body"):
                message_text = str(app.relay_builder._get_message_body()).strip()
            if hasattr(app.relay_builder, "_build_prepared_message"):
                prepared_message = str(
                    app.relay_builder._build_prepared_message(pathway, message_mode, message_text)
                ).strip()
    except Exception:
        pass

    return {
        "timestamp": timestamp,
        "result": result,
        "pathway": pathway,
        "tx_mode": classify_tx_mode(app, pathway),
        "message_mode": message_mode,
        "message_text": message_text,
        "prepared_message": prepared_message,
        "display_category": classify_display_category(app, pathway),
    }


def _message_display(item: dict) -> str:
    message_text = str(item.get("message_text", "")).strip()
    message_mode = str(item.get("message_mode", "")).strip()
    message_display = message_text
    if not message_display and message_mode and message_mode != "-":
        message_display = message_mode
    return message_display


def _pathway_display(item: dict) -> str:
    pathway = str(item.get("pathway", "")).strip()
    display_category = str(item.get("display_category", "")).strip()
    return f"{display_category} {pathway}".strip()


def show_past_relays_window(app, relay_history_db):
    if app.past_relays_window is not None and app.past_relays_window.winfo_exists():
        app.past_relays_window.deiconify()
        app.past_relays_window.lift()
        update_past_relays_table(app, relay_history_db)
        return

    app.past_relays_window = tk.Toplevel(app.root)
    app.past_relays_window.title("Past Relays Log")
    app.past_relays_window.configure(bg=app.bg_color)
    app.past_relays_window.geometry("1180x560")
    app.past_relays_window.protocol("WM_DELETE_WINDOW", lambda: _close_past_relays_window(app))

    outer = tk.Frame(app.past_relays_window, bg=app.bg_color, padx=12, pady=12)
    outer.pack(fill="both", expand=True)

    controls = tk.Frame(outer, bg=app.bg_color)
    controls.pack(fill="x", pady=(0, 8))

    tk.Label(
        controls,
        text="Showing all relay attempts from the last 48 hours.",
        bg=app.bg_color,
        fg=app.fg_color
    ).pack(side="left", padx=(0, 14))

    tk.Label(controls, text="Search:", bg=app.bg_color, fg=app.fg_color).pack(side="left")

    search_entry = tk.Entry(
        controls,
        textvariable=app.past_relays_search_var,
        width=28
    )
    search_entry.pack(side="left", padx=(6, 6))
    search_entry.bind("<KeyRelease>", lambda event: update_past_relays_table(app, relay_history_db))

    tk.Button(
        controls,
        text="Clear Search",
        command=lambda: clear_past_relays_search(app, relay_history_db),
        bg=app.highlight_color,
        fg=app.fg_color
    ).pack(side="left", padx=(0, 10))

    tk.Label(controls, text="Show:", bg=app.bg_color, fg=app.fg_color).pack(side="left")

    result_combo = ttk.Combobox(
        controls,
        textvariable=app.past_relays_result_filter_var,
        values=("All", "Successful Only", "Failed Only", "Pending Only"),
        state="readonly",
        width=16
    )
    result_combo.pack(side="left", padx=(6, 10))
    result_combo.bind("<<ComboboxSelected>>", lambda event: update_past_relays_table(app, relay_history_db))

    tk.Button(
        controls,
        text="Update",
        command=lambda: update_past_relays_table(app, relay_history_db),
        bg=app.highlight_color,
        fg=app.fg_color
    ).pack(side="left")

    tk.Button(
        controls,
        text="Clear Log",
        command=app.clear_past_relays_log,
        bg=app.highlight_color,
        fg=app.fg_color
    ).pack(side="right")

    tk.Button(
        controls,
        text="Export .csv",
        command=app.export_past_relays_log_csv,
        bg=app.highlight_color,
        fg=app.fg_color
    ).pack(side="right", padx=(8, 0))

    tk.Button(
        controls,
        text="Export .txt",
        command=app.export_past_relays_log_txt,
        bg=app.highlight_color,
        fg=app.fg_color
    ).pack(side="right", padx=(8, 0))

    table_outer = tk.Frame(outer, bg=app.bg_color)
    table_outer.pack(fill="both", expand=True)

    table_frame = tk.Frame(table_outer, bg=app.bg_color, bd=1, relief="sunken")
    table_frame.pack(fill="both", expand=True)

    columns = ("timestamp", "result", "mode", "pathway", "message")
    app.past_relays_tree = ttk.Treeview(
        table_frame,
        columns=columns,
        show="headings",
        selectmode="extended"
    )

    app.past_relays_tree.heading("timestamp", text="Time / Date")
    app.past_relays_tree.heading("result", text="Result")
    app.past_relays_tree.heading("mode", text="Mode")
    app.past_relays_tree.heading("pathway", text="Pathway")
    app.past_relays_tree.heading("message", text="Message")

    app.past_relays_tree.column("timestamp", width=150, anchor="w")
    app.past_relays_tree.column("result", width=70, anchor="center")
    app.past_relays_tree.column("mode", width=90, anchor="center")
    app.past_relays_tree.column("pathway", width=320, anchor="w")
    app.past_relays_tree.column("message", width=520, anchor="w")

    app.past_relays_tree.bind("<Button-3>", app._show_past_relays_context_menu)
    app.past_relays_tree.bind("<Button-1>", app._past_relays_drag_select_start)
    app.past_relays_tree.bind("<B1-Motion>", app._past_relays_drag_select_motion)
    app.past_relays_tree.bind("<Control-c>", lambda event: app._copy_treeview_selection(app.past_relays_tree))
    app.past_relays_tree.bind("<Control-C>", lambda event: app._copy_treeview_selection(app.past_relays_tree))
    app.past_relays_tree.bind("<Motion>", lambda event: _on_past_relays_tree_motion(app, event))
    app.past_relays_tree.bind("<Leave>", lambda event: _hide_past_relays_heading_tooltip(app))

    y_scrollbar = tk.Scrollbar(table_frame, orient="vertical", command=app.past_relays_tree.yview)
    x_scrollbar = tk.Scrollbar(table_frame, orient="horizontal", command=app.past_relays_tree.xview)
    app.past_relays_tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

    app.past_relays_tree.grid(row=0, column=0, sticky="nsew")
    y_scrollbar.grid(row=0, column=1, sticky="ns")
    x_scrollbar.grid(row=1, column=0, sticky="ew")

    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    bottom = tk.Frame(outer, bg=app.bg_color)
    bottom.pack(fill="x", pady=(8, 0))

    tk.Button(
        bottom,
        text="Close",
        command=lambda: _close_past_relays_window(app),
        bg=app.highlight_color,
        fg=app.fg_color,
        width=12
    ).pack(side="right")

    update_past_relays_table(app, relay_history_db)


def update_past_relays_table(app, relay_history_db):
    if app.past_relays_tree is None:
        return

    search_text = str(app.past_relays_search_var.get()).strip().lower()
    result_filter = str(app.past_relays_result_filter_var.get()).strip()

    app.past_relays_tree.delete(*app.past_relays_tree.get_children())
    app._past_relays_row_data = {}

    cutoff = datetime.now() - timedelta(hours=48)
    recent_items = []
    for item in list(relay_history_db or []):
        timestamp = str(item.get("timestamp", "")).strip()
        try:
            dt = datetime.fromisoformat(timestamp)
        except Exception:
            continue
        if dt >= cutoff:
            recent_items.append(item)
    recent_items.reverse()

    for item in recent_items:
        result = str(item.get("result", "")).strip().upper()
        if result_filter == "Successful Only" and result != "S":
            continue
        if result_filter == "Failed Only" and result != "F":
            continue
        if result_filter == "Pending Only" and result != "P":
            continue

        timestamp = str(item.get("timestamp", "")).strip()
        tx_mode = str(item.get("tx_mode", "")).strip() or "-"
        pathway_display = _pathway_display(item)
        message_mode = str(item.get("message_mode", "")).strip()
        message_text = str(item.get("message_text", "")).strip()
        prepared_message = str(item.get("prepared_message", "")).strip()
        message_display = _message_display(item)

        haystack = " | ".join([
            timestamp,
            result,
            tx_mode,
            pathway_display,
            message_mode,
            message_text,
            prepared_message,
        ]).lower()

        if search_text and search_text not in haystack:
            continue

        row_id = app.past_relays_tree.insert(
            "",
            tk.END,
            values=(
                timestamp,
                result,
                tx_mode,
                pathway_display,
                message_display,
            )
        )
        app._past_relays_row_data[row_id] = dict(item)


def clear_past_relays_search(app, relay_history_db):
    app.past_relays_search_var.set("")
    update_past_relays_table(app, relay_history_db)


def _close_past_relays_window(app):
    window = getattr(app, "past_relays_window", None)
    if window is not None and window.winfo_exists():
        window.destroy()
    app.past_relays_window = None
    app.past_relays_tree = None
    try:
        if app.root is not None and app.root.winfo_exists():
            app.root.deiconify()
            app.root.lift()
            app.root.focus_force()
    except Exception:
        pass


def _hide_past_relays_heading_tooltip(app):
    tooltip = getattr(app, "_past_relays_heading_tooltip", None)
    if tooltip is not None:
        try:
            tooltip.destroy()
        except Exception:
            pass
    app._past_relays_heading_tooltip = None
    app._past_relays_heading_key = None


def _show_past_relays_heading_tooltip(app, key, x_root, y_root):
    text = PAST_RELAYS_HEADING_HELP.get(key, "")
    if not text:
        _hide_past_relays_heading_tooltip(app)
        return

    if getattr(app, "_past_relays_heading_key", None) == key and getattr(app, "_past_relays_heading_tooltip", None) is not None:
        return

    _hide_past_relays_heading_tooltip(app)
    tooltip = tk.Toplevel(app.past_relays_window)
    tooltip.wm_overrideredirect(True)
    tooltip.configure(bg="#111111")
    tooltip.geometry(f"+{x_root + 14}+{y_root + 14}")

    label = tk.Label(
        tooltip,
        text=text,
        bg="#111111",
        fg=app.fg_color,
        justify="left",
        anchor="w",
        padx=8,
        pady=6,
        wraplength=320,
    )
    label.pack()
    app._past_relays_heading_tooltip = tooltip
    app._past_relays_heading_key = key


def _on_past_relays_tree_motion(app, event):
    tree = getattr(app, "past_relays_tree", None)
    if tree is None:
        return

    if tree.identify_region(event.x, event.y) != "heading":
        _hide_past_relays_heading_tooltip(app)
        return

    column_id = tree.identify_column(event.x)
    if not column_id:
        _hide_past_relays_heading_tooltip(app)
        return

    try:
        index = int(str(column_id).replace("#", "")) - 1
    except Exception:
        _hide_past_relays_heading_tooltip(app)
        return

    columns = ("timestamp", "result", "mode", "pathway", "message")
    if index < 0 or index >= len(columns):
        _hide_past_relays_heading_tooltip(app)
        return

    _show_past_relays_heading_tooltip(app, columns[index], event.x_root, event.y_root)
