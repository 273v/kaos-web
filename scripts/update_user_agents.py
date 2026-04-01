#!/usr/bin/env python3
"""Update user-agent strings from microlinkhq/top-user-agents.

Fetches the latest desktop and mobile user-agent lists from GitHub and
saves them as a JSON file in kaos_web/data/user_agents.json.

Run periodically to keep UAs current:
    uv run python scripts/update_user_agents.py

Source: https://github.com/microlinkhq/top-user-agents
Updated weekly from 300M+ real requests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

DESKTOP_URL = "https://raw.githubusercontent.com/microlinkhq/top-user-agents/master/src/desktop.json"
MOBILE_URL = "https://raw.githubusercontent.com/microlinkhq/top-user-agents/master/src/mobile.json"
OUTPUT_PATH = Path(__file__).parent.parent / "kaos_web" / "data" / "user_agents.json"


def main() -> None:
    print("Fetching desktop user agents...")
    desktop_resp = httpx.get(DESKTOP_URL, timeout=30.0)
    desktop_resp.raise_for_status()
    desktop: list[str] = desktop_resp.json()
    print(f"  {len(desktop)} desktop UAs")

    print("Fetching mobile user agents...")
    mobile_resp = httpx.get(MOBILE_URL, timeout=30.0)
    mobile_resp.raise_for_status()
    mobile: list[str] = mobile_resp.json()
    print(f"  {len(mobile)} mobile UAs")

    data = {
        "source": "https://github.com/microlinkhq/top-user-agents",
        "desktop": desktop,
        "mobile": mobile,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Written to {OUTPUT_PATH}")
    print(f"Total: {len(desktop)} desktop + {len(mobile)} mobile = {len(desktop) + len(mobile)} UAs")


if __name__ == "__main__":
    main()
