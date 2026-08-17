from pathlib import Path

import pytest
from beets.ui import UserError

from beetsplug.smartimport import SmartImportPlugin


def test_notifications_are_disabled_by_default():
    plugin = SmartImportPlugin()
    assert plugin.notifier.enabled() is False
    assert plugin.notifier.send("title", "body") is False


def test_missing_apprise_config_is_strict_only_for_test_command(tmp_path):
    plugin = SmartImportPlugin()
    plugin.config["notifications"]["enabled"].set(True)
    plugin.config["notifications"]["apprise_config"].set(str(tmp_path / "missing.conf"))

    # Normal workflow: notification problems never raise into import semantics.
    assert plugin.notifier.send("title", "body") is False

    # Explicit notification test: configuration errors are surfaced.
    with pytest.raises(UserError):
        plugin.notifier.send("title", "body", strict=True)


def test_summary_notification_failure_is_nonfatal(monkeypatch):
    plugin = SmartImportPlugin()
    plugin.config["notifications"]["enabled"].set(True)
    plugin.config["notifications"]["notify_on_failure"].set(True)
    plugin.config["notifications"]["notify_on_dry_run"].set(True)

    calls = []

    def fake_send(title, body, kind="info", strict=False):
        calls.append((title, body, kind, strict))
        return False

    monkeypatch.setattr(plugin.notifier, "send", fake_send)
    plugin._notify_run_summary(
        dry_run=True,
        ready=1,
        attached=0,
        staged=0,
        manual=0,
        duplicates=0,
        failed=1,
        replacements=0,
    )
    assert calls and calls[0][2] == "failure"
