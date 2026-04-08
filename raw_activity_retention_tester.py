import os
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta

from storage import (
    APP_STORAGE_DIR,
    settings,
    snr_reports_db,
    save_settings,
    save_snr_reports,
)


class RawActivityRetentionTester:
    def __init__(self, root):
        self.root = root
        self.root.title("Raw Activity Retention Tester")
        self.root.configure(bg="#222222")
        self.root.resizable(False, False)

        self.bg = "#222222"
        self.fg = "#ffffff"
        self.btn = "#444444"

        self.status_var = tk.StringVar(value="")
        self._build()
        self._refresh_status()

    def _build(self):
        outer = tk.Frame(self.root, bg=self.bg, padx=14, pady=14)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="Prepare shared storage so the next JS8Mesh launch triggers raw-activity pruning.",
            bg=self.bg,
            fg=self.fg,
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        tk.Label(
            outer,
            text=f"Storage folder: {APP_STORAGE_DIR}",
            bg=self.bg,
            fg=self.fg,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(10, 0))

        button_row = tk.Frame(outer, bg=self.bg)
        button_row.pack(fill="x", pady=(14, 0))

        tk.Button(
            button_row,
            text="Prepare Prune Demo",
            command=self._prepare_prune_demo,
            bg=self.btn,
            fg=self.fg,
            width=20,
        ).pack(side="left")

        tk.Button(
            button_row,
            text="Reset Warning Month",
            command=self._reset_warning_month,
            bg=self.btn,
            fg=self.fg,
            width=20,
        ).pack(side="left", padx=(8, 0))

        tk.Button(
            button_row,
            text="Clear Demo Records",
            command=self._clear_demo_records,
            bg=self.btn,
            fg=self.fg,
            width=20,
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            outer,
            textvariable=self.status_var,
            bg=self.bg,
            fg=self.fg,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(14, 0))

    def _demo_records(self):
        base_dt = datetime.now() - timedelta(days=31, minutes=5)
        samples = [
            ("18SV1231", "@JS8MESH", "JR.1.*19AT872.+2.15"),
            ("13DW615", "@JS8MESH", "JR.1.*18SV1231.+6.12;1.13OT264.-6.18"),
            ("91RDX919", "18SV8110", "JC JR"),
        ]
        demo_items = []
        for index, (src, dst, msg) in enumerate(samples):
            item_dt = base_dt + timedelta(minutes=index)
            demo_items.append(
                {
                    "date": item_dt.strftime("%Y-%m-%d"),
                    "time": item_dt.strftime("%H:%M:%S"),
                    "from": src,
                    "to": dst,
                    "from_norm": src,
                    "to_norm": "" if dst.startswith("@") else dst,
                    "msg": msg,
                    "freq": "27.245 MHz",
                    "snr": "+10",
                    "raw": f"{src}: {dst} {msg}".strip(),
                    "jr_sender_speed": "",
                    "datetime_iso": item_dt.isoformat(),
                    "source_line": f"RETENTION_TEST_DEMO_{index + 1}",
                }
            )
        return demo_items

    def _refresh_status(self):
        demo_count = 0
        for item in list(snr_reports_db):
            if isinstance(item, dict) and str(item.get("source_line", "")).startswith("RETENTION_TEST_DEMO_"):
                demo_count += 1
        warned_month = str(settings.get("raw_activity_retention_warning_month", "") or "").strip() or "(blank)"
        self.status_var.set(
            "Current demo state:\n"
            f"- Demo records in shared snr_reports.json: {demo_count}\n"
            f"- raw_activity_retention_warning_month: {warned_month}\n\n"
            "Use 'Prepare Prune Demo', then launch JS8Mesh to verify the startup prune prompt."
        )

    def _reset_warning_month(self):
        settings["raw_activity_retention_warning_month"] = ""
        save_settings(settings)
        self._refresh_status()
        messagebox.showinfo(
            "Warning Month Reset",
            "The monthly raw-activity warning marker was cleared.\n\n"
            "The next JS8Mesh launch can show the prune prompt again if prune candidates exist.",
        )

    def _prepare_prune_demo(self):
        existing = [
            item
            for item in list(snr_reports_db)
            if isinstance(item, dict) and str(item.get("source_line", "")).startswith("RETENTION_TEST_DEMO_")
        ]
        if not existing:
            snr_reports_db.extend(self._demo_records())
        settings["raw_activity_retention_warning_month"] = ""
        save_snr_reports(snr_reports_db)
        save_settings(settings)
        self._refresh_status()
        messagebox.showinfo(
            "Prune Demo Prepared",
            "Added old demo raw-activity records and cleared the monthly warning marker.\n\n"
            "Launch JS8Mesh to test the startup prune warning.",
        )

    def _clear_demo_records(self):
        kept = []
        removed = 0
        for item in list(snr_reports_db):
            if isinstance(item, dict) and str(item.get("source_line", "")).startswith("RETENTION_TEST_DEMO_"):
                removed += 1
                continue
            kept.append(item)
        snr_reports_db[:] = kept
        save_snr_reports(snr_reports_db)
        self._refresh_status()
        messagebox.showinfo(
            "Demo Records Cleared",
            f"Removed {removed} retention-test demo record(s) from shared storage.",
        )


def main():
    os.makedirs(APP_STORAGE_DIR, exist_ok=True)
    root = tk.Tk()
    RawActivityRetentionTester(root)
    root.mainloop()


if __name__ == "__main__":
    main()
