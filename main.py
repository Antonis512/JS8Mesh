import tkinter as tk
from tkinter import messagebox
import traceback

from gui_main import JS8MeshGUI

try:
    root = tk.Tk()
    app = JS8MeshGUI(root)
    root.mainloop()
except Exception as exc:
    error_text = traceback.format_exc()
    print(error_text)
    try:
        temp_root = tk.Tk()
        temp_root.withdraw()
        messagebox.showerror("JS8Mesh Startup Error", error_text)
        temp_root.destroy()
    except Exception:
        pass
    raise