"""Identity generation — every crab is unique."""

import hashlib
import json
import os
import sys
import time

from hermitclaw.config import config


def identity_path() -> str:
    """Path to identity.json — reads from config each time so it stays current."""
    return os.path.join(config["environment_path"], "identity.json")


# --- Trait dimensions (curated lists) ---

DOMAINS = [
    "mycology",
    "orbital mechanics",
    "fermentation",
    "cartography",
    "origami",
    "tidal patterns",
    "cryptography",
    "bioluminescence",
    "typography",
    "sonar",
    "geologic strata",
    "knot theory",
    "permaculture",
    "glassblowing",
    "semaphore",
    "circadian rhythms",
    "folk etymology",
    "tessellation",
    "foraging",
    "acoustics",
    "celestial navigation",
    "pigment chemistry",
    "murmuration",
    "bookbinding",
    "erosion",
    "signal processing",
    "mycelial networks",
    "letterpress",
    "thermodynamics",
    "tidepool ecology",
    "radio astronomy",
    "psychoacoustics",
    "weaving patterns",
    "volcanic geology",
    "ciphers",
    "birdsong",
    "fractal geometry",
    "archival science",
    "hydrology",
    "clockwork",
    "seed dispersal",
    "morse code",
    "cloud formation",
    "metalwork",
    "braille systems",
    "stellar nucleosynthesis",
    "composting",
    "map projection",
    "wind patterns",
    "amber preservation",
]

THINKING_STYLES = [
    "connecting disparate ideas",
    "following chains of cause and effect",
    "finding patterns in noise",
    "deconstructing systems into parts",
    "building mental models",
    "tracing things back to first principles",
    "mapping relationships between concepts",
    "looking for what's missing",
    "inverting assumptions",
    "layering details into bigger pictures",
    "noticing what others overlook",
    "asking why something works at all",
    "translating between domains",
    "following the smallest thread",
    "collecting and comparing examples",
    "sketching out taxonomies",
]

TEMPERAMENTS = [
    "patient and methodical",
    "restless and wide-ranging",
    "meticulous and detail-oriented",
    "playful and associative",
    "intense and focused",
    "wandering and serendipitous",
    "quiet and observational",
    "energetic and prolific",
]


def _derive_traits(seed_bytes: bytes) -> dict:
    """Deterministically derive personality traits from raw seed bytes."""
    h = hashlib.sha512(seed_bytes).digest()

    def pick(lst, offset):
        chunk = int.from_bytes(h[offset : offset + 4], "big")
        return lst[chunk % len(lst)]

    domains = []
    for i in range(3):
        d = pick(DOMAINS, i * 4)
        while d in domains:
            h_extra = hashlib.sha256(h + bytes([i + 10])).digest()
            d = DOMAINS[int.from_bytes(h_extra[:4], "big") % len(DOMAINS)]
        domains.append(d)

    styles = []
    for i in range(2):
        s = pick(THINKING_STYLES, 12 + i * 4)
        while s in styles:
            h_extra = hashlib.sha256(h + bytes([i + 20])).digest()
            s = THINKING_STYLES[
                int.from_bytes(h_extra[:4], "big") % len(THINKING_STYLES)
            ]
        styles.append(s)

    temperament = pick(TEMPERAMENTS, 20)

    return {
        "domains": domains,
        "thinking_styles": styles,
        "temperament": temperament,
    }


def _collect_entropy() -> bytes:
    """Collect entropy from the user mashing their keyboard."""
    print("  Now seed its genome. What you type becomes its DNA —")
    print("  every character and its timing shapes who it becomes.")
    print("  Mash keys, type nonsense, slam the keyboard. Be random.")
    print("  Press Enter when done.\n")

    # Collect keystrokes with timing
    entropy_pool = bytearray()
    start = time.perf_counter_ns()

    try:
        # Try raw terminal mode for character-by-character reading
        import tty
        import termios

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)

        sys.stdout.write("  > ")
        sys.stdout.flush()
        chars_collected = 0

        while True:
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"):
                break

            # Mix in the character and nanosecond timing
            t = time.perf_counter_ns() - start
            entropy_pool.extend(ch.encode())
            entropy_pool.extend(t.to_bytes(8, "big"))
            chars_collected += 1
            sys.stdout.write(ch)
            sys.stdout.flush()

        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print()

    except (ImportError, termios.error, AttributeError):
        # Fallback for systems without termios (Windows, etc.)
        raw = input("  > ")
        for i, ch in enumerate(raw):
            t = time.perf_counter_ns() - start
            entropy_pool.extend(ch.encode())
            entropy_pool.extend(t.to_bytes(8, "big"))
        chars_collected = len(raw)

    print(f"\n  Collected {chars_collected} keystrokes of entropy.")

    # Hash the entropy pool down to 32 bytes
    return hashlib.sha256(bytes(entropy_pool)).digest()


def _display_birth(name: str, genome_hex: str, traits: dict):
    """Print the birth sequence to the terminal."""
    print()
    print("  Initializing...")
    time.sleep(0.5)

    print()
    print("  genome:")
    for i in range(0, len(genome_hex), 32):
        row = genome_hex[i : i + 32]
        spaced = " ".join(row[j : j + 4] for j in range(0, len(row), 4))
        print(f"    {spaced}")
        time.sleep(0.15)

    print()
    time.sleep(0.3)

    print("  decoded:")
    print(f"    temperament : {traits['temperament']}")
    time.sleep(0.2)
    print(f"    style       : {traits['thinking_styles'][0]}")
    time.sleep(0.15)
    print(f"    style       : {traits['thinking_styles'][1]}")
    time.sleep(0.15)
    for d in traits["domains"]:
        print(f"    curiosity   : {d}")
        time.sleep(0.15)

    print()
    time.sleep(0.3)
    print(f"  {name} is awake.")
    print()


def load_identity() -> dict | None:
    """Load existing identity, or return None if it doesn't exist."""
    path = identity_path()
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def load_identity_from(box_path: str) -> dict | None:
    """Load identity.json from a given box path directly (no global config)."""
    path = os.path.join(box_path, "identity.json")
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def create_identity() -> dict:
    """Run the onboarding flow: name it, mash keyboard, generate genome."""
    print()
    print("  ==============================")
    print("  A new HermitClaw is being born.")
    print("  ==============================")
    print()
    name = input("  What will you name it? > ").strip()
    if not name:
        name = "Crab"

    # Set environment path to {name}_box/
    project_root = os.path.dirname(os.path.dirname(__file__))
    box_path = os.path.join(project_root, f"{name.lower()}_box")
    config["environment_path"] = box_path

    print()
    seed_bytes = _collect_entropy()
    genome_hex = seed_bytes.hex()
    traits = _derive_traits(seed_bytes)

    identity = {
        "name": name,
        "genome": genome_hex,
        "traits": traits,
        "born": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    path = identity_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(identity, f, indent=2)

    _display_birth(name, genome_hex, traits)
    return identity
