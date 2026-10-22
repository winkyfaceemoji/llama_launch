"""Models found in the llama.cpp folder, presets, and the launch command."""
import os
import re
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
