from types import SimpleNamespace

import pytest

from beetsplug.smartimport import PUBLIC_ROUTE_NAMES, SmartImportPlugin


PATH_KEYS = ("incoming", "staging", "manual", "duplicates", "failed")


@pytest.fixture
def configured_plugin(tmp_path):
    """Configure temporary paths without leaking Beets' global plugin config.

    Beets plugin configuration is shared through the global config tree. These
    regression tests therefore restore the previous path values after each test
    so later tests can still verify that the public defaults are intentionally
    empty/required.
    """
    plugin = SmartImportPlugin()
    previous = {key: plugin.config[key].as_str() for key in PATH_KEYS}

    for key in PATH_KEYS:
        plugin.config[key].set(str(tmp_path / key))

    try:
        yield plugin
    finally:
        for key, value in previous.items():
            plugin.config[key].set(value)


def test_smartcleanup_merges_same_pending_release_without_timestamp_folder(
    tmp_path, configured_plugin
):
    plugin = configured_plugin
    staging = tmp_path / "staging"
    manual = tmp_path / "manual"
    group_name = "96d4c283 - Future - The Real Me"

    staged_group = staging / group_name
    staged_group.mkdir(parents=True)
    (staged_group / "02 - Second.flac").write_bytes(b"second")

    target = manual / PUBLIC_ROUTE_NAMES["cleanup_manual"] / group_name
    target.mkdir(parents=True)
    (target / "01 - First.flac").write_bytes(b"first")

    plugin.smartcleanup(None, SimpleNamespace(dry_run=False), [])

    assert not staged_group.exists()
    assert (target / "01 - First.flac").read_bytes() == b"first"
    assert (target / "02 - Second.flac").read_bytes() == b"second"
    assert sorted(path.name for path in target.parent.iterdir()) == [group_name]


def test_smartcleanup_preserves_both_files_on_filename_collision(
    tmp_path, configured_plugin
):
    plugin = configured_plugin
    staging = tmp_path / "staging"
    manual = tmp_path / "manual"
    group_name = "613c8999 - Future - High Off Life"

    staged_group = staging / group_name
    staged_group.mkdir(parents=True)
    (staged_group / "Track.flac").write_bytes(b"new")

    target = manual / PUBLIC_ROUTE_NAMES["cleanup_manual"] / group_name
    target.mkdir(parents=True)
    (target / "Track.flac").write_bytes(b"old")

    plugin.smartcleanup(None, SimpleNamespace(dry_run=False), [])

    assert (target / "Track.flac").read_bytes() == b"old"
    assert (target / "Track_1.flac").read_bytes() == b"new"
    assert not staged_group.exists()
