"""Sandboxed Python execution — restricts all file I/O to the environment folder."""

import builtins
import os
import sys
import types


def _blocked_module(name: str):
    """Create a fake module that raises PermissionError on any attribute access."""
    mod = types.ModuleType(name)

    def __getattr__(attr):
        raise PermissionError(f"{name} is blocked in sandbox")

    mod.__getattr__ = __getattr__
    return mod


def _check_path(path, env_root):
    """Ensure a path resolves inside env_root. Raises PermissionError if not."""
    if isinstance(path, (bytes, os.PathLike)):
        path = os.fsdecode(path)
    if not os.path.isabs(path):
        path = os.path.join(env_root, path)
    resolved = os.path.realpath(path)
    if resolved != env_root and not resolved.startswith(env_root + os.sep):
        raise PermissionError(f"Access denied: {path} (outside environment folder)")


def setup(env_root):
    """Lock down this Python process to only access env_root."""
    env_root = os.path.realpath(env_root)
    os.chdir(env_root)

    # --- Patch builtins.open ---
    _orig_open = builtins.open

    def safe_open(file, *args, **kwargs):
        _check_path(file, env_root)
        return _orig_open(file, *args, **kwargs)

    builtins.open = safe_open

    # --- Patch os functions that take a single path arg ---
    def _wrap1(fn):
        def wrapper(path, *args, **kwargs):
            _check_path(path, env_root)
            return fn(path, *args, **kwargs)

        return wrapper

    for name in (
        "listdir",
        "scandir",
        "remove",
        "unlink",
        "rmdir",
        "mkdir",
        "makedirs",
    ):
        if hasattr(os, name):
            setattr(os, name, _wrap1(getattr(os, name)))

    # --- Patch os functions that take two path args ---
    def _wrap2(fn):
        def wrapper(src, dst, *args, **kwargs):
            _check_path(src, env_root)
            _check_path(dst, env_root)
            return fn(src, dst, *args, **kwargs)

        return wrapper

    for name in ("rename", "replace", "link", "symlink"):
        if hasattr(os, name):
            setattr(os, name, _wrap2(getattr(os, name)))

    # --- Block os execution functions ---
    def _blocked(name):
        def nope(*a, **k):
            raise PermissionError(f"os.{name}() is blocked in sandbox")

        return nope

    for name in (
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "forkpty",
        "kill",
        "killpg",
        "chroot",
    ):
        if hasattr(os, name):
            setattr(os, name, _blocked(name))

    # --- Neuter shutil: allow import but block dangerous operations ---
    # Many libraries (pymupdf -> tarfile -> shutil) import shutil transitively.
    # Setting it to None breaks those imports entirely. Instead, we import it
    # and then replace its dangerous functions with no-ops.
    import shutil as _shutil

    def _shutil_blocked(name):
        def nope(*a, **k):
            raise PermissionError(f"shutil.{name}() is blocked in sandbox")

        return nope

    for _fn in (
        "rmtree",
        "move",
        "copy",
        "copy2",
        "copytree",
        "chown",
        "make_archive",
        "unpack_archive",
    ):
        setattr(_shutil, _fn, _shutil_blocked(_fn))

    # --- Block dangerous module imports ---
    # Use fake modules (not None) so that 'import urllib' etc. don't fail with
    # "halted; None in sys.modules" — imports succeed but any use raises.
    for mod in (
        "subprocess",
        "socket",
        "http",
        "ftplib",
        "smtplib",
        "ctypes",
        "multiprocessing",
        "signal",
        "webbrowser",
    ):
        sys.modules[mod] = _blocked_module(mod)

    # urllib.request: fake module. Must inject into urllib so "urllib.request" works.
    _fake_request = _blocked_module("urllib.request")
    sys.modules["urllib.request"] = _fake_request
    import urllib

    urllib.request = _fake_request


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: pysandbox.py <env_root> [-c <code> | <script.py> [args...]]")
        sys.exit(1)

    env_root = sys.argv[1]
    setup(env_root)

    if sys.argv[2] == "-c":
        code = sys.argv[3] if len(sys.argv) > 3 else ""
        exec(compile(code, "<sandbox>", "exec"))
    else:
        script = sys.argv[2]
        if script == "-" or not script.strip():
            print(
                "Error: 'python -' (stdin) is not supported in sandbox. Use 'python -c \"code\"' or 'python script.py'",
                file=sys.stderr,
            )
            sys.exit(1)
        sys.argv = sys.argv[2:]  # normalize sys.argv for the script
        with open(script) as f:
            exec(
                compile(f.read(), script, "exec"),
                {"__name__": "__main__", "__file__": script},
            )
