"""Sandboxed shell — the agent can run commands, but only inside environment/."""

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys

logger = logging.getLogger("hermitclaw.tools")

# Commands that should never be run (checked as prefixes after stripping)
BLOCKED_PREFIXES = [
    "sudo", "su ", "rm -rf /", "chmod", "chown", "kill", "pkill",
    "curl", "wget", "nc ", "ncat", "ssh", "scp", "sftp",
    "node", "ruby", "perl", "bash", "sh ", "zsh",
    "export", "source", "eval", "exec",
    "mount", "umount", "dd ", "mkfs", "fdisk",
    "apt", "brew", "npm", "yarn",
    "open ", "xdg-open",
]

# Path to the Python sandbox wrapper
_SANDBOX = os.path.join(os.path.dirname(__file__), "pysandbox.py")


def _venv_dir(env_root: str) -> str:
    """Path to the crab's virtual environment."""
    return os.path.join(os.path.realpath(env_root), ".venv")


def _venv_python(env_root: str) -> str:
    """Path to the venv's Python interpreter."""
    return os.path.join(_venv_dir(env_root), "bin", "python")


def _venv_bin(env_root: str) -> str:
    """Path to the venv's bin directory."""
    return os.path.join(_venv_dir(env_root), "bin")


def ensure_venv(env_root: str):
    """Create the crab's venv if it doesn't exist. Called once on startup."""
    venv = _venv_dir(env_root)
    if os.path.isfile(_venv_python(env_root)):
        return
    logger.info(f"Creating crab venv at {venv}...")
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "venv", venv, "--python", sys.executable],
                       capture_output=True, timeout=30)
    else:
        subprocess.run([sys.executable, "-m", "venv", venv],
                       capture_output=True, timeout=30)
    logger.info("Crab venv created.")


def _is_safe_command(command: str) -> str | None:
    """Return an error message if the command is unsafe, else None."""
    stripped = command.strip()

    if not stripped:
        return "Blocked: empty command."

    # Block dangerous command prefixes
    for prefix in BLOCKED_PREFIXES:
        if stripped.startswith(prefix):
            return f"Blocked: '{prefix}' commands are not allowed."

    # Block parent directory traversal — check actual path tokens, not content strings.
    # Split on whitespace and strip shell operators to find path-like tokens.
    for token in stripped.split():
        clean = token.lstrip("><=|;&(")
        if clean == ".." or clean.startswith("../") or "/.." in clean:
            return "Blocked: '..' path traversal is not allowed in commands."

    # Block shell escape tricks
    if "`" in stripped:
        return "Blocked: backtick command substitution is not allowed."
    if "$(" in stripped:
        return "Blocked: command substitution $() is not allowed."
    if "${" in stripped:
        return "Blocked: variable expansion ${} is not allowed."
    if "~" in stripped:
        return "Blocked: '~' (home expansion) is not allowed."

    # Block absolute paths — only relative paths from environment/ are allowed.
    # Check each whitespace-separated token; strip leading shell operators.
    # Only flag tokens where / is followed by a word char (actual paths like /usr/bin),
    # not markup like /> or /' or /" which appear in XML/HTML/SVG content.
    import re
    for token in stripped.split():
        clean = token.lstrip("><=|;&(")
        if re.match(r"/[A-Za-z0-9_]", clean) and not clean.startswith("/dev/null"):
            return "Blocked: absolute paths are not allowed. Use relative paths only."

    return None


def _rewrite_python_cmd(command: str, env_root: str) -> str | None:
    """If command is a python invocation, rewrite to run through the sandbox.

    Uses the crab's venv python so installed packages are available.
    Returns the rewritten command, or None if it's not a python command.
    """
    stripped = command.strip()
    if not stripped:
        return None

    # Detect first command token, including absolute/relative paths like:
    #   python, python3, python3.12, .venv/bin/python, /usr/bin/python3
    try:
        first = shlex.split(stripped, posix=True)[0]
    except (ValueError, IndexError):
        return None
    exe = os.path.basename(first)
    if not re.fullmatch(r"python(?:3(?:\.\d+)?)?", exe):
        return None

    rest = stripped[len(first):]
    real_root = os.path.realpath(env_root)
    python = _venv_python(env_root) if os.path.isfile(_venv_python(env_root)) else sys.executable
    return f"{shlex.quote(python)} {shlex.quote(_SANDBOX)} {shlex.quote(real_root)}{rest}"


def _rewrite_pip_cmd(command: str, env_root: str) -> str | None:
    """If command is pip/uv pip, rewrite to use the venv. Returns rewritten cmd or None."""
    stripped = command.strip()
    if stripped.startswith("uv pip "):
        # Route through venv python
        rest = stripped[7:]  # after "uv pip "
        uv = shutil.which("uv") or "uv"
        return f"{shlex.quote(uv)} pip {rest} --python {shlex.quote(_venv_python(env_root))}"
    if stripped.startswith("pip install") or stripped.startswith("pip3 install"):
        # Use venv pip
        return f"{shlex.quote(_venv_python(env_root))} -m pip {stripped[stripped.index('install'):]}"
    return None


def run_command(command: str, env_root: str) -> str:
    """Run a shell command sandboxed to the environment/ folder."""
    real_root = os.path.realpath(env_root)

    # Safety check (runs on original command before any rewriting)
    err = _is_safe_command(command)
    if err:
        return err

    # Route python commands through the sandbox wrapper
    rewritten = _rewrite_python_cmd(command, env_root)
    if rewritten is not None:
        command = rewritten

    # Route pip/uv pip through the venv
    pip_rewritten = _rewrite_pip_cmd(command, env_root)
    if pip_rewritten is not None:
        command = pip_rewritten

    # Include venv bin in PATH so installed tools are available
    vbin = _venv_bin(env_root)
    venv_path = f"{vbin}:/usr/bin:/bin" if os.path.isdir(vbin) else "/usr/bin:/bin"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=real_root,
            capture_output=True,
            text=True,
            timeout=60,  # longer timeout for pip installs
            env={
                "HOME": real_root,
                "PATH": venv_path,
                "TMPDIR": real_root,
                "LANG": "en_US.UTF-8",
                "VIRTUAL_ENV": _venv_dir(env_root),
            },
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        if not output.strip():
            output = "(no output)"

        # Truncate very long output
        if len(output) > 3000:
            output = output[:3000] + "\n...(truncated)"

        return output

    except subprocess.TimeoutExpired:
        return "Error: command timed out (15s limit)"
    except Exception as e:
        return f"Error: {e}"


def execute_tool(name: str, arguments: dict, env_root: str) -> str:
    """Run a tool by name."""
    if name == "shell":
        return run_command(arguments["command"], env_root)
    else:
        return f"Unknown tool: {name}"
