"""Discover and register user-provided hook scripts.

Users place executables at .minicode/hooks/<event_name>.{sh,py}. The runner
treats them as shell-out callbacks: runs the script, ignores stdout (for
now — JSON sink is Phase E1), treats exit 0 as success and exit != 0 as
logged warning. Permission to run is the user's choice to put the script
there.
"""

import subprocess
from pathlib import Path

HOOK_EVENTS = (
    "session_start",
    "session_end",
    "pre_compact",
    "user_prompt_submit",
)

HOOK_EXTENSIONS = (".sh", ".py")


def discover_user_hooks(workdir: Path) -> dict[str, list[Path]]:
    """Map event_name -> list of executable scripts found in .minicode/hooks/."""
    hooks_dir = Path(workdir) / ".minicode" / "hooks"
    if not hooks_dir.is_dir():
        return {event: [] for event in HOOK_EVENTS}
    found: dict[str, list[Path]] = {event: [] for event in HOOK_EVENTS}
    for event in HOOK_EVENTS:
        for ext in HOOK_EXTENSIONS:
            p = hooks_dir / f"{event}{ext}"
            if p.is_file() and p.stat().st_mode & 0o111:
                found[event].append(p)
    return found


def make_shell_callback(script: Path):
    """Return a Hooks-compatible callback that runs `script` as a subprocess."""
    def callback(event: str, *args):
        try:
            proc = subprocess.run(
                [str(script)],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                from loguru import logger
                logger.warning(
                    "user hook {} exited {}: {}",
                    script, proc.returncode, proc.stderr[:200],
                )
        except subprocess.TimeoutExpired:
            from loguru import logger
            logger.warning("user hook {} timed out", script)
    return callback
