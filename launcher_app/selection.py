"""Models found in the llama.cpp folder, presets, and the launch command."""
import os
import re
from dataclasses import dataclass
from .paths import MODELS_DIR


# Leaves -c and -ngl unset so llama-server's --fit picks the largest context
# that fits in free VRAM. --context-shift and --swa-full stay off (both are
# off by default): a full chat stops instead of silently dropping messages.
# No --tools: newer builds only offer file, shell and OS tools, and refuse to
# start with an unknown one. The time comes from the time MCP server (ADR 0011).
AUTO_PRESET = "Auto (fit to VRAM)"


AUTO_COMMAND = (
    'llama-server -m "{model}" --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 '
    '--webui-mcp-proxy --port 8081'
)


def load_presets(config: dict) -> dict:
    """Load and validate presets with enhanced migration logic."""
    presets = config.get("presets", {})
    
    # Validate and migrate presets
    migrated = {}
    for name, value in presets.items():
        if not isinstance(value, dict):
            # Handle legacy string-based presets
            migrated[name] = {
                "command": value,
                "models": []
            }
        else:
            # Validate modern dict-based presets
            if not isinstance(value.get("command"), str):
                raise ValueError(f"Invalid preset '{name}': 'command' must be a string")
                
            models = value.get("models", [])
            if not isinstance(models, list):
                raise ValueError(f"Invalid preset '{name}': 'models' must be a list")
                
            # Ensure all model entries are strings
            if not all(isinstance(model, str) for model in models):
                raise ValueError(f"Invalid preset '{name}': all models must be strings")
                
            migrated[name] = {
                "command": value["command"],
                "models": models
            }

    # The Auto preset always exists, so every model has a working default. Its
    # command always comes from code: a copy saved in config.json would
    # otherwise keep an outdated command forever.
    migrated.pop(AUTO_PRESET, None)
    return {AUTO_PRESET: {"command": AUTO_COMMAND, "models": []}, **migrated}


def build_launch_command(template, model_path, mmproj, api_key_file):
    # Substitute the model path, attach the vision projector when there is one,
    # and require the API key unless the preset already sets its own.
    cmd = template.replace("{model}", model_path)
    if mmproj and "--mmproj" not in cmd and not re.search(r"(^|\s)-mm\s", cmd):
        cmd += f' --mmproj "{mmproj}"'
    if "--api-key" not in cmd:
        cmd += f' --api-key-file "{api_key_file}"'
    return cmd


def _is_extra_shard(filename):
    # Split models load from their first shard; hide -00002-of-0000N and later.
    m = re.search(r"-(\d{5})-of-\d{5}\.gguf$", filename, re.IGNORECASE)
    return bool(m) and m.group(1) != "00001"


def detect_models(llama_dir=""):
    # Returns {display name: (model path, mmproj path or None)}.
    # Models live directly in the folder, or in a subfolder next to an
    # mmproj*.gguf projector (llama-server's router layout), which enables
    # image input. Projector files are never listed as models.
    models_dir = llama_dir if llama_dir else MODELS_DIR
    found = {}

    def scan(folder, with_mmproj):
        try:
            files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".gguf"))
        except OSError:
            return
        mmproj = None
        if with_mmproj:
            mmproj = next((os.path.join(folder, f) for f in files
                           if f.lower().startswith("mmproj")), None)
        for f in files:
            if f.lower().startswith("mmproj") or _is_extra_shard(f):
                continue
            found.setdefault(f[:-5], (os.path.join(folder, f), mmproj))

    if not os.path.isdir(models_dir):
        return found
    scan(models_dir, with_mmproj=False)
    for d in sorted(os.listdir(models_dir)):
        sub = os.path.join(models_dir, d)
        if os.path.isdir(sub):
            scan(sub, with_mmproj=True)
    return found


def preset_matches_model(preset, model_filename):
    # A preset matches if any of its keywords appear in the model filename.
    # A preset with no keywords matches all models.
    keywords = preset.get("models", [])
    if not keywords:
        return True
    lower = model_filename.lower()
    return any(kw.lower() in lower for kw in keywords)


NO_MODELS = "(no .gguf models found)"
NO_PRESETS = "(no matching presets)"


@dataclass(frozen=True)
class View:
    # What the Model and Preset dropdowns show and what Launch runs. command is "" when nothing can launch.
    models: tuple
    model: str
    presets: tuple
    preset: str
    command: str
    model_files: tuple  # (path, mmproj) of the shown model, or (None, None): what updates check against


class PresetError(Exception):
    # A preset change refused before anything changed; the window shows it as a messagebox of that kind.
    def __init__(self, kind, title, message):
        super().__init__(message)
        self.kind, self.title, self.message = kind, title, message


class Selection:
    # The home view's model and preset choice. Every change returns a View whose command is built from the
    # latest scan, never kept from an earlier one, so a model whose files changed can't launch stale.
    # Rules (ADR 0001, 0002): picking a model, even the same one, resets the preset to Auto; a preset choice
    # survives Refresh while its model does; Auto can't be edited or deleted. Startup doesn't remember the
    # model; a pick or a fallback after Refresh does (remember_model). Preset changes are saved through
    # save_presets with the whole dict, Auto included. UI thread only; the scan reads the disk.
    def __init__(self, presets, last_model, llama_dir, api_key_file, *,  # presets: as in config.json
                 remember_model=lambda name: None, save_presets=lambda presets: None, scan=detect_models):
        self._presets = load_presets({"presets": presets})  # Auto always first, from the code
        self._api_key_file = api_key_file
        self._remember = remember_model
        self._save_presets = save_presets
        self._scan = scan
        self._folder = llama_dir
        self._found = scan(llama_dir)
        names = self._names()
        self._model = last_model if last_model in names else (names[0] if names else None)
        self._preset = self._first_preset()
        self.view = self._build()

    # ── choices ───────────────────────────────────────────────

    def pick_model(self, name):
        if name in self._found:
            self._model = name
            self._remember(name)
            self._preset = self._first_preset()
        return self._rebuild()

    def pick_preset(self, name):
        if name in self._matching():
            self._preset = name
        return self._rebuild()

    def refresh(self):
        self._found = self._scan(self._folder)
        if self._model not in self._found:
            names = self._names()
            self._model = names[0] if names else None
            if self._model:
                self._remember(self._model)
            self._preset = self._first_preset()
        elif self._preset not in self._matching():
            self._preset = self._first_preset()
        return self._rebuild()

    def set_folder(self, llama_dir):
        self._folder = llama_dir
        return self.refresh()

    def model_names(self):
        # A fresh scan for the preset dialog's model list; the choice itself doesn't change.
        return sorted(self._scan(self._folder), key=str.lower)

    # ── presets ───────────────────────────────────────────────

    def preset_for_edit(self):
        name = self._editable(self._preset, "edit")
        p = self._presets[name]
        return name, p["command"], p.get("models", [])

    def add_preset(self, name, command, keywords):
        if name in self._presets:
            raise self._duplicate(name)
        self._presets[name] = {"command": command, "models": keywords}
        return self._presets_changed(select=name)

    def edit_preset(self, old, name, command, keywords):
        self._editable(old, "edit")
        if name != old and name in self._presets:
            raise self._duplicate(name)
        del self._presets[old]  # the edited preset moves to the end, as it always has
        self._presets[name] = {"command": command, "models": keywords}
        if self._preset == old:
            self._preset = name
        return self._presets_changed(select=name)

    def delete_preset(self, name, confirm=lambda name: True):
        # confirm(name) runs after the checks; a False answer changes nothing and returns None.
        self._editable(name, "delete")
        if not confirm(name):
            return None
        del self._presets[name]
        return self._presets_changed()

    # ── inside ────────────────────────────────────────────────

    def _names(self):
        return sorted(self._found, key=str.lower)

    def _matching(self):
        if self._model is None:
            return []
        return [n for n, p in self._presets.items() if preset_matches_model(p, self._model)]

    def _first_preset(self):
        matching = self._matching()
        if not matching:
            return None
        return AUTO_PRESET if AUTO_PRESET in matching else matching[0]

    def _editable(self, name, action):
        if not name or name not in self._presets:
            raise PresetError("warning", "No Preset", f"Please select a preset to {action}.")
        if name == AUTO_PRESET:
            raise PresetError("info", "Auto Preset",
                              "The Auto preset is built in and can't be edited. Use + Add for a preset of your own."
                              if action == "edit" else
                              "The Auto preset is the default for every model and can't be deleted.")
        return name

    @staticmethod
    def _duplicate(name):
        return PresetError("error", "Duplicate", f'A preset named "{name}" already exists.')

    def _presets_changed(self, select=None):
        self._save_presets(dict(self._presets))
        matching = self._matching()
        if select in matching:
            self._preset = select
        elif self._preset not in matching:
            self._preset = self._first_preset()
        return self._rebuild()

    def _rebuild(self):
        self.view = self._build()
        return self.view

    def _build(self):
        files = self._found.get(self._model, (None, None))
        command = ""
        if self._model is not None and self._preset is not None:
            command = build_launch_command(self._presets[self._preset]["command"], files[0], files[1],
                                           self._api_key_file)
        matching = self._matching()
        return View(
            models=tuple(self._names()) or (NO_MODELS,),
            model=self._model or NO_MODELS,
            presets=tuple(matching) or (NO_PRESETS,),
            preset=self._preset or NO_PRESETS,
            command=command,
            model_files=files,
        )
