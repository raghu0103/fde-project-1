"""Write the project-only .env file for app.py.

Asks for the app API key with hidden input and for the model name, then
writes APP_ANTHROPIC_API_KEY and APP_ANTHROPIC_MODEL to .env next to this
script. The key is never printed.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def write_env(path: Path, api_key: str, model: str) -> None:
    content = f"APP_ANTHROPIC_API_KEY={api_key}\nAPP_ANTHROPIC_MODEL={model}\n"
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Best effort; Windows ignores most mode bits.


def main(
    getpass_fn: Callable[[str], str] = getpass.getpass,
    input_fn: Callable[[str], str] = input,
    env_path: Path = ENV_PATH,
) -> int:
    if env_path.exists():
        answer = input_fn(f"{env_path.name} already exists. Overwrite? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Left the existing .env unchanged.")
            return 1

    api_key = getpass_fn("APP_ANTHROPIC_API_KEY (input hidden): ").strip()
    if not api_key:
        print("No key entered. Nothing was written.")
        return 1
    if any(ch.isspace() for ch in api_key):
        print("The key contains whitespace. Nothing was written.")
        return 1

    model = input_fn(f"APP_ANTHROPIC_MODEL [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
    if any(ch.isspace() for ch in model):
        print("The model name contains whitespace. Nothing was written.")
        return 1

    write_env(env_path, api_key, model)
    print(f"Wrote {env_path.name} with APP_ANTHROPIC_API_KEY (hidden) and APP_ANTHROPIC_MODEL={model}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
