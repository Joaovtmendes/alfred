#!/usr/bin/env python3
"""Submit the M5 message templates (m5-templates/*.json) to the WhatsApp Business Account.

Run on your own machine (the token never leaves it):

    cd alfred
    python scripts/submit_templates.py            # dry run: shows what would be sent
    python scripts/submit_templates.py --apply    # submits the missing ones

Needs in .env (or the environment): WHATSAPP_TOKEN, WHATSAPP_WABA_ID.
Templates that already exist (same name + language) are skipped, so re-running is safe.
Meta reviews each one (usually minutes to 48 h); check the status in
WhatsApp Manager → Message templates, or re-run this script (it prints the status).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "m5-templates"
GRAPH = f"https://graph.facebook.com/{os.environ.get('GRAPH_API_VERSION', 'v25.0')}"


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.split(" #")[0].strip().strip("\"'"))


def translations(spec: dict) -> list[tuple[str, list[dict]]]:
    """(language, components) pairs from our JSON files (dict or list layout)."""
    t = spec.get("translations", {})
    items = t.items() if isinstance(t, dict) else ((x["language"], x) for x in t)
    out = []
    for lang, x in items:
        comps = x["components"] if isinstance(x, dict) else x
        out.append((lang, comps))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="really submit (default: dry run)")
    args = parser.parse_args()

    load_env()
    token = os.environ.get("WHATSAPP_TOKEN")
    waba = os.environ.get("WHATSAPP_WABA_ID")
    if not token or not waba:
        print("Missing WHATSAPP_TOKEN or WHATSAPP_WABA_ID in .env", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}"}

    existing = {}
    url = f"{GRAPH}/{waba}/message_templates?fields=name,language,status&limit=200"
    while url:
        r = httpx.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        for t in data.get("data", []):
            existing[(t["name"], t["language"])] = t["status"]
        url = data.get("paging", {}).get("next")

    created = failed = 0
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        spec = json.loads(path.read_text())
        for lang, components in translations(spec):
            key = (spec["name"], lang)
            if key in existing:
                print(f"  = {spec['name']:<28} {lang:<6} already exists ({existing[key]})")
                continue
            body = {
                "name": spec["name"],
                "language": lang,
                "category": spec["category"],
                "components": components,
            }
            if not args.apply:
                print(f"  + {spec['name']:<28} {lang:<6} would be submitted")
                continue
            r = httpx.post(
                f"{GRAPH}/{waba}/message_templates", headers=headers, json=body, timeout=30
            )
            if r.is_success:
                created += 1
                print(f"  ✓ {spec['name']:<28} {lang:<6} submitted → {r.json().get('status')}")
            else:
                failed += 1
                err = r.json().get("error", {})
                print(f"  ✗ {spec['name']:<28} {lang:<6} {err.get('error_user_msg') or err}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to submit.")
    else:
        print(f"\nSubmitted {created}, failed {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
