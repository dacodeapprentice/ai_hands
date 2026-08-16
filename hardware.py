"""
hardware.py — Looks at the computer the app is running on and picks
the biggest local AI model that will realistically run well on it.

Bigger models are smarter but need more RAM (or GPU memory) and run
slower. This module's job is to avoid two bad outcomes: picking a
model so big it barely crawls (or crashes), or picking one so small
it's needlessly weak when the machine could handle better.

The recommendations below are deliberately conservative — leaving
headroom for the OS and other running apps — since a model that's
merely a bit slower is a much better outcome than one that grinds the
whole computer to a halt.
"""

import subprocess
import psutil


def get_total_ram_gb() -> float:
    return psutil.virtual_memory().total / (1024 ** 3)


def get_cpu_cores() -> int:
    return psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1


def get_gpu_vram_gb():
    """
    Tries to detect a dedicated NVIDIA GPU's VRAM. Returns None if no
    NVIDIA GPU is found (e.g. no dedicated GPU, or an AMD/Apple one —
    those need different detection tools we don't rely on here, so we
    fall back to treating the machine as CPU/RAM-only, which is always
    a safe assumption).
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            mb = float(result.stdout.strip().splitlines()[0])
            return mb / 1024
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


# Ordered from smallest/safest to largest/best. Each entry: the Ollama
# model tag to pull, and the minimum usable memory (GPU VRAM if present,
# otherwise system RAM) it realistically needs with headroom left over
# for the OS and this app.
MODEL_TIERS = [
    {"tag": "qwen2.5-coder:1.5b", "min_gb": 0,    "label": "Very small — runs on almost anything, but limited ability"},
    {"tag": "qwen2.5-coder:3b",   "min_gb": 6,    "label": "Small — light and fast, modest coding ability"},
    {"tag": "qwen2.5-coder:7b",   "min_gb": 10,   "label": "Medium — solid general coding ability, the sweet spot for most laptops"},
    {"tag": "qwen2.5-coder:14b",  "min_gb": 18,   "label": "Large — noticeably better multi-step reasoning, needs a strong machine"},
    {"tag": "qwen2.5-coder:32b",  "min_gb": 28,   "label": "Very large — best local quality, needs a high-end GPU or a lot of RAM"},
]


def recommend_model():
    """
    Returns a dict describing the recommended model tag, why it was
    picked, and the detected hardware, so the UI can show its reasoning
    instead of just handing you a name.
    """
    ram_gb = get_total_ram_gb()
    vram_gb = get_gpu_vram_gb()

    # Prefer GPU VRAM as the limiting factor if there's a dedicated GPU,
    # since that's what actually constrains model size when present.
    # Otherwise, system RAM is the constraint.
    effective_gb = vram_gb if vram_gb is not None else ram_gb

    chosen = MODEL_TIERS[0]
    for tier in MODEL_TIERS:
        if effective_gb >= tier["min_gb"]:
            chosen = tier

    return {
        "model_tag": chosen["tag"],
        "reason": chosen["label"],
        "ram_gb": round(ram_gb, 1),
        "vram_gb": round(vram_gb, 1) if vram_gb is not None else None,
        "cpu_cores": get_cpu_cores(),
    }
