"""Show a notification in the terminal and, best effort, on the desktop."""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

from kronos.tools.registry import ToolContext, ToolError


def desktop_notify(title: str, message: str) -> bool:
    """Try the platform's notifier. Returns True if a notifier was invoked."""
    system = platform.system()
    try:
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], check=False, timeout=5)
            return True
        if system == "Darwin" and shutil.which("osascript"):
            script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            return True
        if system == "Windows" and shutil.which("powershell"):
            script = (
                "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                f"$n.Visible=$true;$n.ShowBalloonTip(5000,'{_escape(title)}','{_escape(message)}',"
                "[System.Windows.Forms.ToolTipIcon]::Info)"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, timeout=10)
            return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "''")


def notify_user(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    title = str(tool_input.get("title", "")).strip() or "Jarvis"
    message = str(tool_input.get("message", "")).strip()
    if not message:
        raise ToolError("notify_user needs a message.")
    ctx.notify(title, message)
    delivered = desktop_notify(title, message)
    where = "terminal and desktop" if delivered else "terminal"
    return f"Notification shown to the user ({where})."
