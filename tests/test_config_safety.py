from pathlib import Path

import pytest
from beets.ui import UserError

from beetsplug.smartimport import AUDIO_EXTENSIONS, DEFAULT_AUDIO_EXTENSIONS, SmartImportPlugin


REQUIRED = ("incoming", "staging", "manual", "duplicates", "failed")


def configure_paths(plugin, root: Path):
    for key in REQUIRED:
        plugin.config[key].set(str(root / key))


def test_default_extensions_match_public_defaults():
    assert AUDIO_EXTENSIONS == frozenset(DEFAULT_AUDIO_EXTENSIONS)
    assert AUDIO_EXTENSIONS == {
        ".flac",
        ".mp3",
        ".m4a",
        ".mp4",
        ".ogg",
        ".opus",
        ".wav",
        ".aiff",
        ".aif",
    }


def test_required_paths_are_not_private_defaults(tmp_path):
    plugin = SmartImportPlugin()
    with pytest.raises(UserError):
        plugin._validate_paths()


def test_configured_paths_are_created(tmp_path):
    plugin = SmartImportPlugin()
    configure_paths(plugin, tmp_path)
    plugin._ensure_directories()
    for key in REQUIRED:
        assert (tmp_path / key).is_dir()


def test_paths_must_be_distinct(tmp_path):
    plugin = SmartImportPlugin()
    configure_paths(plugin, tmp_path)
    plugin.config["failed"].set(str(tmp_path / "manual"))
    with pytest.raises(UserError):
        plugin._validate_paths()


def test_extensions_are_configurable(tmp_path):
    plugin = SmartImportPlugin()
    configure_paths(plugin, tmp_path)
    plugin.config["extensions"].set(["flac", ".wv"])
    assert plugin._audio_extensions() == frozenset({".flac", ".wv"})


def test_public_route_names_are_english():
    plugin = SmartImportPlugin()
    expected = {
        "unreadable": "unreadable",
        "missing_required_tags": "missing-required-tags",
        "ambiguous_match": "ambiguous-match",
        "stale_library_entry": "stale-library-entry",
        "attach_error": "attach-error",
        "missing_album_tag": "missing-album-tag",
        "incoherent_release_group": "incoherent-release-group",
        "cleanup_manual": "beets-match-uncertain",
    }
    assert {key: plugin._route_name(key) for key in expected} == expected
