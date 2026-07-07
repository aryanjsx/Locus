# aura/executors/system_executor.py
# AURA System Executor — handles all OS-level commands:
#   open_app, open_url, close_app, screenshot, volume, shutdown,
#   restart, log_off, sleep, lock, kill_process, set_volume
#
# SAFETY RULES (Engineering Spec §5.2) — NEVER violate:
#   1. subprocess ALWAYS uses list form — NEVER shell=True
#   2. Voice input NEVER passed raw to subprocess
#   3. App names are resolved through app_registry ONLY
#   4. Destructive commands (shutdown/restart/kill) MUST be flagged
#      is_destructive=True in CommandPlan — SafetyGate handles confirmation

from __future__ import annotations
import logging
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from aura.schemas.command import ExecutionResult, ExecutorType
from aura.utils.app_registry import get_command, guess_url, is_url

logger = logging.getLogger("aura.system_executor")

_OS = platform.system().lower()


class SystemExecutor:
    """
    Executes system-level commands.
    All public methods return ExecutionResult — they never raise to the caller.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public dispatcher — CommandEngine calls this
    # ------------------------------------------------------------------

    def run(self, action: str, params: dict[str, Any]) -> ExecutionResult:
        """
        Dispatch to the correct method based on action name.
        action must be one of the ALLOWED_ACTIONS below.
        """
        ALLOWED_ACTIONS = {
            "open_app":       self.open_app,
            "open_url":       self.open_url,
            "close_app":      self.close_app,
            "screenshot":     self.screenshot,
            "set_volume":     self.set_volume,
            "mute":           self.mute,
            "shutdown":       self.shutdown,
            "restart":        self.restart,
            "log_off":        self.log_off,
            "sleep":          self.sleep_pc,
            "lock":           self.lock_pc,
            "kill_process":   self.kill_process,
            "minimize_all":   self.minimize_all,
        }
        if action not in ALLOWED_ACTIONS:
            return ExecutionResult(
                success=False,
                output=f"Unknown system action: {action}. I don't know how to do that.",
                error=f"Action '{action}' not in allowlist",
                executor=ExecutorType.SYSTEM,
            )
        start = time.monotonic()
        try:
            result = ALLOWED_ACTIONS[action](params)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        except Exception as exc:
            logger.error(f"SystemExecutor.{action} raised: {exc}", exc_info=True)
            return ExecutionResult(
                success=False,
                output=f"Something went wrong running {action}. Check the logs.",
                error=str(exc),
                executor=ExecutorType.SYSTEM,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    # ------------------------------------------------------------------
    # App launching
    # ------------------------------------------------------------------

    def open_app(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Open an application or website.

        Resolution order:
          1. Exact match in app_registry -> native app OR URL
          2. URL given directly in app_name (user said "open https://...")
          3. guess_url() fallback -> construct https://www.name.com
          4. Fail with clear error

        All URL opening goes through open_url() which uses webbrowser (default browser).
        No subprocess call ever hardcodes a browser name.

        params:
            app_name: str  — e.g. "youtube", "chrome", "vscode", "https://github.com"
        """
        app_name: str = params.get("app_name", "").strip()
        if not app_name:
            return ExecutionResult(
                success=False,
                output="I need an application name to open.",
                error="Missing app_name param",
                executor=ExecutorType.SYSTEM,
            )

        # ── Case 1: User passed a direct URL ─────────────────────────────
        if is_url(app_name):
            return self.open_url({"url": app_name})

        # ── Case 2: Registry lookup ───────────────────────────────────────
        command = get_command(app_name)

        if command is not None:
            if isinstance(command, str) and is_url(command):
                return self.open_url({"url": command})

            cmd_list: list[str] = [command] if isinstance(command, str) else command

            if is_url(cmd_list[0]):
                logger.warning(
                    f"Registry entry for '{app_name}' still uses browser+URL format. "
                    f"Routing to open_url instead. Fix the registry entry."
                )
                return self.open_url({"url": cmd_list[-1]})

            logger.info(f"Launching native app: {cmd_list}")
            try:
                if _OS == "darwin":
                    subprocess.Popen(
                        ["open", "-a"] + cmd_list,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        cmd_list,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return ExecutionResult(
                    success=True,
                    output=f"Opening {app_name}.",
                    executor=ExecutorType.SYSTEM,
                )
            except FileNotFoundError:
                guessed = guess_url(app_name)
                if guessed:
                    logger.info(
                        f"Native app '{cmd_list[0]}' not found on PATH. "
                        f"Falling back to URL: {guessed}"
                    )
                    return self.open_url({"url": guessed})
                return ExecutionResult(
                    success=False,
                    output=(
                        f"I couldn't find {app_name} on your system. "
                        "Make sure it's installed and on your PATH."
                    ),
                    error=f"Executable not found: {cmd_list[0]}",
                    executor=ExecutorType.SYSTEM,
                )

        # ── Case 3: Not in registry — try URL guess ───────────────────────
        guessed_url = guess_url(app_name)
        if guessed_url:
            logger.info(f"'{app_name}' not in registry — guessing URL: {guessed_url}")
            return self.open_url({"url": guessed_url})

        # ── Case 4: Cannot resolve ────────────────────────────────────────
        return ExecutionResult(
            success=False,
            output=(
                f"I don't know how to open {app_name}. "
                f"You can add it to the app registry in aura/utils/app_registry.py."
            ),
            error=f"'{app_name}' not in registry and guess_url returned None",
            executor=ExecutorType.SYSTEM,
        )

    def open_url(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Open a URL in the user's default browser.

        Uses Python's built-in webbrowser module which respects the OS
        default browser setting on Windows, macOS, and Linux.
        NEVER calls subprocess with a browser name — that would ignore user preference.

        Supported schemes: http://, https://
        Windows ms- protocol links are opened with os.startfile().

        params:
            url: str
        """
        import webbrowser

        url: str = params.get("url", "").strip()
        if not url:
            return ExecutionResult(
                success=False,
                output="I need a URL to open.",
                error="Missing url param",
                executor=ExecutorType.SYSTEM,
            )

        ALLOWED_SCHEMES = ("http://", "https://", "ms-")
        if not any(url.startswith(scheme) for scheme in ALLOWED_SCHEMES):
            if re.match(r"^[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}", url):
                url = "https://" + url
                logger.info(f"Prepended https:// → {url}")
            else:
                return ExecutionResult(
                    success=False,
                    output=(
                        "I can only open http or https URLs. "
                        f"That URL scheme isn't safe to open."
                    ),
                    error=f"Unsafe URL scheme: {url}",
                    executor=ExecutorType.SYSTEM,
                )

        if url.startswith("ms-"):
            try:
                os.startfile(url)  # type: ignore[attr-defined]
                return ExecutionResult(
                    success=True,
                    output=f"Opening {url.split(':')[0].replace('ms-', '')} settings.",
                    executor=ExecutorType.SYSTEM,
                )
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    output="Couldn't open that Windows settings page.",
                    error=str(exc),
                    executor=ExecutorType.SYSTEM,
                )

        logger.info(f"Opening URL in default browser: {url}")
        try:
            opened = webbrowser.open_new_tab(url)
            if opened:
                domain = url.replace("https://", "").replace("http://", "").split("/")[0]
                display = domain.replace("www.", "")
                return ExecutionResult(
                    success=True,
                    output=f"Opening {display} in your default browser.",
                    data={"url": url},
                    executor=ExecutorType.SYSTEM,
                )
            else:
                return ExecutionResult(
                    success=False,
                    output=(
                        "I couldn't find a default browser to open the URL. "
                        "Please set a default browser in your system settings."
                    ),
                    error="webbrowser.open_new_tab returned False",
                    executor=ExecutorType.SYSTEM,
                )
        except Exception as exc:
            logger.error(f"open_url failed: {exc}", exc_info=True)
            return ExecutionResult(
                success=False,
                output="Something went wrong opening the browser.",
                error=str(exc),
                executor=ExecutorType.SYSTEM,
            )

    # ------------------------------------------------------------------
    # App closing
    # ------------------------------------------------------------------

    def close_app(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Close an application by process name.

        params:
            process_name: str   — e.g. "chrome", "code", "spotify"
        """
        process_name: str = params.get("process_name", "").strip()
        if not process_name:
            return ExecutionResult(
                success=False,
                output="I need the application name to close it.",
                error="Missing process_name",
                executor=ExecutorType.SYSTEM,
            )

        # Sanitise — only alphanumeric, dots, dashes
        safe_name = "".join(c for c in process_name if c.isalnum() or c in ".-_")
        if not safe_name:
            return ExecutionResult(
                success=False,
                output=f"That process name doesn't look safe to me.",
                error=f"Sanitised name is empty from input: {process_name}",
                executor=ExecutorType.SYSTEM,
            )

        logger.info(f"Closing process: {safe_name}")
        if _OS == "windows":
            result = subprocess.run(
                ["taskkill", "/IM", f"{safe_name}.exe", "/F"],
                capture_output=True, text=True,
            )
        else:
            result = subprocess.run(
                ["pkill", "-f", safe_name],
                capture_output=True, text=True,
            )

        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output=f"Closed {safe_name}.",
                executor=ExecutorType.SYSTEM,
            )
        return ExecutionResult(
            success=False,
            output=f"I couldn't find a running process called {safe_name}.",
            error=result.stderr.strip(),
            executor=ExecutorType.SYSTEM,
        )

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Take a screenshot to a temporary file for downstream consumption
        (e.g., LLaVA vision description in Phase 4). The file is deleted
        after the data is returned — screenshots are NOT persisted per
        spec Section 5.3.

        params:
            filename: str (optional) — used in output message only
        """
        try:
            import pyautogui
            import tempfile
            from datetime import datetime

            filename: str = params.get("filename", "")
            if not filename:
                filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = Path(tmp.name)
            tmp.close()

            try:
                screenshot = pyautogui.screenshot()
                screenshot.save(str(tmp_path))

                logger.info(f"Screenshot captured (transient): {tmp_path.name}")
                return ExecutionResult(
                    success=True,
                    output=f"Screenshot captured: {filename}",
                    data={"path": str(tmp_path), "transient": True},
                    executor=ExecutorType.SYSTEM,
                )
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        except ImportError:
            return ExecutionResult(
                success=False,
                output="PyAutoGUI is not installed. Run pip install pyautogui.",
                error="pyautogui not found",
                executor=ExecutorType.SYSTEM,
            )

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def set_volume(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Set system volume to a percentage (0–100).

        params:
            level: int   — 0 to 100
        """
        try:
            level = int(params.get("level", 50))
            level = max(0, min(100, level))   # clamp to valid range
        except (ValueError, TypeError):
            return ExecutionResult(
                success=False,
                output="Please give me a volume level between 0 and 100.",
                error="Invalid level param",
                executor=ExecutorType.SYSTEM,
            )

        if _OS == "windows":
            try:
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                # pycaw uses scalar 0.0 to 1.0
                volume.SetMasterVolumeLevelScalar(level / 100.0, None)
                logger.info(f"Volume set to {level}%")
                return ExecutionResult(
                    success=True,
                    output=f"Volume set to {level} percent.",
                    executor=ExecutorType.SYSTEM,
                )
            except ImportError:
                # Fallback: use nircmd if available
                result = subprocess.run(
                    ["nircmd", "setsysvolume", str(int(level / 100 * 65535))],
                    capture_output=True,
                )
                if result.returncode == 0:
                    return ExecutionResult(
                        success=True,
                        output=f"Volume set to {level} percent.",
                        executor=ExecutorType.SYSTEM,
                    )
                return ExecutionResult(
                    success=False,
                    output="I need pycaw or nircmd to control volume on Windows.",
                    error="pycaw and nircmd both unavailable",
                    executor=ExecutorType.SYSTEM,
                )
        else:
            result = subprocess.run(
                ["amixer", "-q", "sset", "Master", f"{level}%"],
                capture_output=True,
            )
            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    output=f"Volume set to {level} percent.",
                    executor=ExecutorType.SYSTEM,
                )
            return ExecutionResult(
                success=False,
                output="Couldn't change volume on this system.",
                error=result.stderr.decode(),
                executor=ExecutorType.SYSTEM,
            )

    def mute(self, params: dict[str, Any]) -> ExecutionResult:
        """Toggle or set mute state."""
        return self.set_volume({"level": 0})

    # ------------------------------------------------------------------
    # Power management — ALL destructive — SafetyGate must have
    # already confirmed before these are ever called
    # ------------------------------------------------------------------

    def shutdown(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Shut down the computer.
        ONLY called after SafetyGate has confirmed — CommandEngine enforces this.
        """
        logger.warning("Executing shutdown — safety gate previously confirmed.")
        delay: int = int(params.get("delay_seconds", 0))

        if _OS == "windows":
            cmd = ["shutdown", "/s", "/t", str(delay)]
        else:
            cmd = ["shutdown", "-h", f"+{delay // 60}" if delay else "now"]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output="Shutting down. Goodbye.",
                executor=ExecutorType.SYSTEM,
                was_confirmed=True,
            )
        return ExecutionResult(
            success=False,
            output="Shutdown command failed. Check logs.",
            error=result.stderr,
            executor=ExecutorType.SYSTEM,
        )

    def restart(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Restart the computer.
        ONLY called after SafetyGate has confirmed.
        """
        logger.warning("Executing restart — safety gate previously confirmed.")
        delay: int = int(params.get("delay_seconds", 0))

        if _OS == "windows":
            cmd = ["shutdown", "/r", "/t", str(delay)]
        else:
            cmd = ["shutdown", "-r", f"+{delay // 60}" if delay else "now"]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output="Restarting now. See you on the other side.",
                executor=ExecutorType.SYSTEM,
                was_confirmed=True,
            )
        return ExecutionResult(
            success=False,
            output="Restart command failed. Check logs.",
            error=result.stderr,
            executor=ExecutorType.SYSTEM,
        )

    def log_off(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Log off the current user.
        ONLY called after SafetyGate has confirmed.
        """
        logger.warning("Executing log off — safety gate previously confirmed.")
        if _OS == "windows":
            result = subprocess.run(["shutdown", "/l"], capture_output=True, text=True)
        else:
            result = subprocess.run(
                ["gnome-session-quit", "--logout", "--no-prompt"],
                capture_output=True, text=True,
            )
        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output="Logging off. See you next time.",
                executor=ExecutorType.SYSTEM,
                was_confirmed=True,
            )
        return ExecutionResult(
            success=False,
            output="Log off failed.",
            error=result.stderr,
            executor=ExecutorType.SYSTEM,
        )

    def sleep_pc(self, params: dict[str, Any]) -> ExecutionResult:
        """Put the computer to sleep."""
        logger.info("Putting computer to sleep.")
        if _OS == "windows":
            result = subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"],
                capture_output=True,
            )
        else:
            result = subprocess.run(["systemctl", "suspend"], capture_output=True)

        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output="Going to sleep. Wake me when you need me.",
                executor=ExecutorType.SYSTEM,
            )
        return ExecutionResult(
            success=False,
            output="Sleep command failed.",
            error=result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr,
            executor=ExecutorType.SYSTEM,
        )

    def lock_pc(self, params: dict[str, Any]) -> ExecutionResult:
        """Lock the workstation."""
        logger.info("Locking workstation.")
        if _OS == "windows":
            result = subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                capture_output=True,
            )
        else:
            result = subprocess.run(
                ["loginctl", "lock-session"],
                capture_output=True,
            )
        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                output="Locking your screen.",
                executor=ExecutorType.SYSTEM,
            )
        return ExecutionResult(
            success=False,
            output="Lock command failed.",
            error=str(result.stderr),
            executor=ExecutorType.SYSTEM,
        )

    # ------------------------------------------------------------------
    # Process management
    # ------------------------------------------------------------------

    def kill_process(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Kill a process by name or PID.
        ONLY called after SafetyGate has confirmed.

        params:
            process_name: str  (either this or pid required)
            pid: int           (either this or process_name required)
        """
        process_name: str = params.get("process_name", "").strip()
        pid: int | None = params.get("pid")

        logger.warning(
            f"Killing process name='{process_name}' pid={pid} "
            f"— safety gate previously confirmed."
        )

        if pid:
            try:
                import psutil
                proc = psutil.Process(int(pid))
                proc.terminate()
                return ExecutionResult(
                    success=True,
                    output=f"Process {pid} terminated.",
                    executor=ExecutorType.SYSTEM,
                    was_confirmed=True,
                )
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    output=f"Could not kill PID {pid}: {exc}",
                    error=str(exc),
                    executor=ExecutorType.SYSTEM,
                )

        if process_name:
            safe_name = "".join(c for c in process_name if c.isalnum() or c in ".-_")
            if _OS == "windows":
                result = subprocess.run(
                    ["taskkill", "/IM", f"{safe_name}.exe", "/F"],
                    capture_output=True, text=True,
                )
            else:
                result = subprocess.run(
                    ["pkill", "-9", "-f", safe_name],
                    capture_output=True, text=True,
                )
            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    output=f"Killed {safe_name}.",
                    executor=ExecutorType.SYSTEM,
                    was_confirmed=True,
                )
            return ExecutionResult(
                success=False,
                output=f"No process named {safe_name} was found.",
                error=result.stderr,
                executor=ExecutorType.SYSTEM,
            )

        return ExecutionResult(
            success=False,
            output="I need either a process name or a PID to kill.",
            error="Missing process_name and pid",
            executor=ExecutorType.SYSTEM,
        )

    def minimize_all(self, params: dict[str, Any]) -> ExecutionResult:
        """Minimize all windows — show the desktop."""
        if _OS == "windows":
            try:
                import pyautogui
                pyautogui.hotkey("win", "d")
                return ExecutionResult(
                    success=True,
                    output="All windows minimized.",
                    executor=ExecutorType.SYSTEM,
                )
            except ImportError:
                result = subprocess.run(
                    ["nircmd", "win", "sendmsg", "class", "Shell_TrayWnd",
                     "0x5B4", "0", "0"],
                    capture_output=True,
                )
        else:
            result = subprocess.run(
                ["wmctrl", "-k", "on"],
                capture_output=True,
            )
            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    output="All windows minimized.",
                    executor=ExecutorType.SYSTEM,
                )

        return ExecutionResult(
            success=False,
            output="Couldn't minimize all windows.",
            executor=ExecutorType.SYSTEM,
        )
