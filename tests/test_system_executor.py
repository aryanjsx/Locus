# tests/test_system_executor.py
# Run with: pytest tests/test_system_executor.py -v

import pytest
from unittest.mock import patch, MagicMock

from aura.schemas.command import ExecutorType
from aura.executors.system_executor import SystemExecutor
from aura.executors.system_monitor import SystemMonitor
from aura.executors.shell_executor import ShellExecutor, COMMAND_ALLOWLIST
from aura.utils.app_registry import get_command, is_url
from aura.core.safety_gate import SafetyGate

CONFIG = {
    "safety": {"confirmation_timeout": 1, "audit_log": "/tmp/test_audit.log"},
    "executors": {"shell_timeout": 5},
}


# ── App Registry ──────────────────────────────────────────────────────

def test_registry_chrome():
    assert get_command("chrome") is not None

def test_registry_youtube_returns_url():
    result = get_command("youtube")
    assert isinstance(result, str)
    assert "youtube" in result
    assert result.startswith("https://")

def test_registry_unknown_returns_none():
    assert get_command("xyznotarealapp") is None

def test_registry_case_insensitive():
    assert get_command("CHROME") == get_command("chrome")


# ── SystemExecutor ────────────────────────────────────────────────────

class TestSystemExecutor:
    def setup_method(self):
        self.ex = SystemExecutor(CONFIG)

    def test_open_app_unknown_app(self):
        result = self.ex.run("open_app", {"app_name": "x!"})
        assert result.success is False
        assert "don't know" in result.output.lower()

    def test_open_url_valid(self):
        with patch("webbrowser.open_new_tab", return_value=True) as mock_open:
            result = self.ex.run("open_url", {"url": "https://youtube.com"})
            assert result.success is True
            mock_open.assert_called_once()

    def test_open_url_unsafe_scheme(self):
        result = self.ex.run("open_url", {"url": "javascript:alert(1)"})
        assert result.success is False

    def test_open_url_missing(self):
        result = self.ex.run("open_url", {"url": ""})
        assert result.success is False

    def test_unknown_action(self):
        result = self.ex.run("fly_to_moon", {})
        assert result.success is False
        assert result.executor == ExecutorType.SYSTEM

    @pytest.mark.skipif(
        not __import__("os").environ.get("DISPLAY") and __import__("sys").platform == "linux",
        reason="pyautogui requires a display server on Linux",
    )
    def test_screenshot_saves_file(self):
        mock_screenshot = MagicMock()
        with patch("pyautogui.screenshot", return_value=mock_screenshot):
            result = self.ex.run("screenshot", {})
        assert result.success is True
        assert "screenshot" in result.output.lower()
        mock_screenshot.save.assert_called_once()

    def test_open_youtube_uses_default_browser(self):
        """'open youtube' must go through webbrowser, never subprocess with chrome."""
        with patch("webbrowser.open_new_tab") as mock_browser:
            mock_browser.return_value = True
            result = self.ex.run("open_app", {"app_name": "youtube"})
        assert result.success is True
        mock_browser.assert_called_once()
        called_url = mock_browser.call_args[0][0]
        assert "youtube.com" in called_url

    def test_open_url_direct_https(self):
        """Direct https:// URL always opens in default browser."""
        with patch("webbrowser.open_new_tab", return_value=True) as mock_browser:
            result = self.ex.run("open_url", {"url": "https://github.com"})
        assert result.success is True
        mock_browser.assert_called_once_with("https://github.com")

    def test_open_url_bare_domain_gets_https(self):
        """A bare domain like 'google.com' gets https:// prepended."""
        with patch("webbrowser.open_new_tab", return_value=True):
            result = self.ex.run("open_url", {"url": "google.com"})
        assert result.success is True

    def test_open_url_javascript_scheme_blocked(self):
        """javascript: URLs must be rejected — security rule."""
        result = self.ex.run("open_url", {"url": "javascript:alert(1)"})
        assert result.success is False

    def test_open_url_file_scheme_blocked(self):
        """file:// URLs must be rejected."""
        result = self.ex.run("open_url", {"url": "file:///etc/passwd"})
        assert result.success is False

    def test_open_unlisted_site_guesses_url(self):
        """An unknown site name triggers guess_url() and opens https://www.name.com."""
        with patch("webbrowser.open_new_tab", return_value=True) as mock_browser:
            result = self.ex.run("open_app", {"app_name": "somerandombrand"})
        assert result.success is True
        called_url = mock_browser.call_args[0][0]
        assert "somerandombrand" in called_url

    def test_open_app_registry_url_does_not_spawn_subprocess(self):
        """Verifies no subprocess.Popen is called when opening a website."""
        with patch("webbrowser.open_new_tab", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                self.ex.run("open_app", {"app_name": "netflix"})
        mock_popen.assert_not_called()


def test_app_registry_has_no_browser_url_list_entries():
    """Verify no registry entry is still in the old ['chrome', 'url'] format."""
    from aura.utils.app_registry import _FULL_REGISTRY, is_url
    for name, entry in _FULL_REGISTRY.items():
        if isinstance(entry, list):
            first = entry[0]
            assert not is_url(first), (
                f"Registry entry '{name}' starts with a URL in a list: {entry}. "
                "Move it to _WEBSITES as a plain string."
            )


# ── ShellExecutor ─────────────────────────────────────────────────────

class TestShellExecutor:
    def setup_method(self):
        self.ex = ShellExecutor(CONFIG)

    def test_allowlisted_command_runs(self):
        result = self.ex.run("run_command", {"command": ["python", "--version"]})
        assert result.success is True
        assert "python" in result.data["stdout"].lower()

    def test_blocked_command_rejected(self):
        result = self.ex.run("run_command", {"command": ["rm", "-rf", "/"]})
        assert result.success is False
        assert "not allowed" in result.output.lower()

    def test_shell_metachar_rejected(self):
        result = self.ex.run("run_command", {"command": ["echo", "hello; rm -rf /"]})
        assert result.success is False
        assert "unsafe" in result.output.lower()

    def test_raw_string_rejected(self):
        result = self.ex.run("run_command", {"command": "echo hello"})
        assert result.success is False

    def test_all_allowlist_entries_are_strings(self):
        for entry in COMMAND_ALLOWLIST:
            assert isinstance(entry, str), f"Allowlist entry is not a string: {entry!r}"


# ── SafetyGate ────────────────────────────────────────────────────────

class TestSafetyGate:
    def setup_method(self):
        self.gate = SafetyGate(CONFIG)

    def test_non_destructive_passes_immediately(self):
        from aura.schemas.command import CommandPlan, ExecutorType
        plan = CommandPlan(
            executor=ExecutorType.SYSTEM, action="screenshot",
            is_destructive=False, requires_confirm=False,
        )
        assert self.gate.check(plan) is True

    def test_confirmation_yes_accepted(self):
        from aura.schemas.command import CommandPlan, ExecutorType
        import threading
        plan = CommandPlan(
            executor=ExecutorType.SYSTEM, action="shutdown",
            is_destructive=True, requires_confirm=True,
        )
        # Send "yes" after 0.1 seconds
        threading.Timer(0.1, lambda: self.gate.receive_confirmation("yes")).start()
        result = self.gate.check(plan)
        assert result is True

    def test_confirmation_timeout_denies(self):
        from aura.schemas.command import CommandPlan, ExecutorType
        plan = CommandPlan(
            executor=ExecutorType.SYSTEM, action="shutdown",
            is_destructive=True, requires_confirm=True,
        )
        # Send nothing — should timeout in 1s (test config) and deny
        result = self.gate.check(plan)
        assert result is False

    def test_invalid_word_denied(self):
        from aura.schemas.command import CommandPlan, ExecutorType
        import threading
        plan = CommandPlan(
            executor=ExecutorType.SYSTEM, action="restart",
            is_destructive=True, requires_confirm=True,
        )
        threading.Timer(0.1, lambda: self.gate.receive_confirmation("maybe")).start()
        result = self.gate.check(plan)
        assert result is False
