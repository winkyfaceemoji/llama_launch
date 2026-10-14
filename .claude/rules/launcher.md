---
paths: launcher.pyw
---

# launcher.pyw

- **Child output must keep draining.** Each `Popen` uses `encoding="utf-8", errors="replace"` and `_stream_output` reads until EOF. If the reader thread dies (a decode error under Windows' cp1252 default can do this), the pipe fills and llama-server blocks on its next log line, which looks like the model hanging mid-chat.
- **tkinter is not thread-safe.** Background threads (readers, health poll, tray) must hand UI work to the main thread with `widget.after(0, ...)`.
- **Every launch command goes through `build_launch_command`**, which appends `--api-key-file` (ADR 0005). Don't start llama-server from anywhere else, and never ship a preset with `--tools all` or `--agent`: they enable shell and file-write tools that any web page can call.
- **The key is never displayed** (only copied) and never written to `config.json`, whose contents the UVX proxy also reads.
- **Auto preset:** `AUTO_COMMAND` must not set `-c` or `-ngl`; setting either turns `--fit` off for that value (ADR 0001). `load_presets` re-adds Auto if the config lacks it, and `_delete_preset` refuses to delete it.
- **Preset selection resets to Auto** on startup and on every model change; other presets are manual overrides and are not remembered (ADR 0002).
- **Preset `models` are case-insensitive substring keywords**, not exact filenames, even though the dialog stores full names. A short keyword like `"qwen"` matches every Qwen file.
- **Vision models:** an `mmproj*.gguf` is only paired with models in the same *subfolder*; one sitting in the root models folder is ignored. Display names come from the file name, and the first one found wins (root before subfolders, then alphabetical).
- **`_free_port` force-kills any process listening on 8080/8081/8001**, not just ones the launcher started, and runs `netstat` on the UI thread (the window stalls briefly at launch).
- `_kill_proc` uses `taskkill /T` because with `shell=True` the Popen PID is `cmd.exe`; `proc.kill()` would kill only the shell and leave llama-server running and holding VRAM.
- Log panes are never trimmed; a very long session makes the UI slower (not the model).
