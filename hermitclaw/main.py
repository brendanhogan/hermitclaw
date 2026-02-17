"""Entry point — multi-crab discovery + onboarding + starts the server."""

import glob
import json
import logging
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uvicorn
from hermitclaw.brain import Brain
from hermitclaw.config import config
from hermitclaw.identity import load_identity_from, create_identity
from hermitclaw.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

# When BOX_ROOT is set (e.g. in Docker), all *_box/ dirs live there.
# Falls back to PROJECT_ROOT for local development.
BOX_ROOT = os.environ.get("BOX_ROOT") or PROJECT_ROOT

# Headless mode: skip interactive prompts (required in Docker).
HEADLESS = os.environ.get("HERMITCLAW_HEADLESS", "0") == "1"

PORT = int(os.environ.get("PORT", 8000))


def _crab_id_from_box(box_path: str) -> str:
    """Derive crab ID from box directory name: coral_box -> coral."""
    dirname = os.path.basename(box_path)
    if dirname.endswith("_box"):
        return dirname[:-4]
    return dirname


def _discover_crabs() -> dict[str, Brain]:
    """Discover all *_box/ dirs under BOX_ROOT, migrate legacy environment/."""
    brains: dict[str, Brain] = {}

    # Migrate legacy environment/ if found (check both roots)
    for root in {PROJECT_ROOT, BOX_ROOT}:
        legacy = os.path.join(root, "environment")
        legacy_identity = os.path.join(legacy, "identity.json")
        if os.path.isfile(legacy_identity):
            with open(legacy_identity, "r") as f:
                identity = json.load(f)
            name = identity.get("name", "crab").lower()
            new_path = os.path.join(BOX_ROOT, f"{name}_box")
            print(f"\n  Migrating environment/ -> {new_path}...")
            shutil.move(legacy, new_path)

    # Scan for *_box/ directories under BOX_ROOT
    os.makedirs(BOX_ROOT, exist_ok=True)
    pattern = os.path.join(BOX_ROOT, "*_box")
    boxes = sorted(p for p in glob.glob(pattern) if os.path.isdir(p))

    for box_path in boxes:
        identity = load_identity_from(box_path)
        if not identity:
            continue
        crab_id = _crab_id_from_box(box_path)
        brain = Brain(identity, box_path)
        brains[crab_id] = brain

    return brains


if __name__ == "__main__":
    # Discover existing crabs
    brains = _discover_crabs()

    if brains:
        names = [b.identity["name"] for b in brains.values()]
        print(f"\n  Found {len(brains)} crab(s): {', '.join(names)}")
        if not HEADLESS:
            answer = input("  Create a new one? (y/N) > ").strip().lower()
            if answer == "y":
                identity = create_identity()
                crab_id = identity["name"].lower()
                box_path = os.path.join(BOX_ROOT, f"{crab_id}_box")
                brain = Brain(identity, box_path)
                brains[crab_id] = brain
    else:
        if HEADLESS:
            print("\n  No crabs found. Server starting — create one via POST /api/crabs")
        else:
            print("\n  No crabs found. Let's create one!")
            identity = create_identity()
            crab_id = identity["name"].lower()
            box_path = os.path.join(BOX_ROOT, f"{crab_id}_box")
            brain = Brain(identity, box_path)
            brains[crab_id] = brain

    # Initialize the app with all brains
    app = create_app(brains)

    names = [b.identity["name"] for b in brains.values()] if brains else ["(none yet)"]
    print(f"\n  Starting {len(brains)} crab(s): {', '.join(names)}")
    print(f"  Open http://localhost:{PORT} to watch them think\n")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
