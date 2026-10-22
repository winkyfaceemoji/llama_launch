# Llama Launcher: the entry point. The code is in launcher_app/ (ADR 0020); README.md says what it does.
import ctypes
import sys
import tkinter as tk
from launcher_app.processes import claim_instance, watch_show_requests
from launcher_app.window import CommandLauncherGUI


if __name__ == "__main__":
    show_requests = claim_instance()
    if show_requests is None:
        sys.exit(0)  # another launcher is running and now shows its window (ADR 0017)
    # Our own taskbar button and icon; without this, Windows groups the window under
    # pythonw.exe's icon. It must be set before the first window exists.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LlamaLauncher")
    root = tk.Tk()
    app  = CommandLauncherGUI(root)

    def bring_to_front():
        root.deiconify()
        root.lift()
        root.focus_force()
    watch_show_requests(show_requests, lambda: root.after(0, bring_to_front))
    root.mainloop()
