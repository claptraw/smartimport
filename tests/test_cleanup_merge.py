from types import SimpleNamespace

from beetsplug.smartimport import PUBLIC_ROUTE_NAMES, SmartImportPlugin


def configured_plugin(tmp_path):
    plugin = SmartImportPlugin()
    for key in ("incoming", "staging", "manual", "duplicates", "failed"):
        plugin.config[key].set(str(tmp_path / key))
    return plugin


def test_smartcleanup_merges_same_pending_release_without_timestamp_folder(tmp_path):
    plugin = configured_plugin(tmp_path)
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


def test_smartcleanup_preserves_both_files_on_filename_collision(tmp_path):
    plugin = configured_plugin(tmp_path)
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
