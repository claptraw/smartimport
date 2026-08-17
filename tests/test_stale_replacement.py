from pathlib import Path
from types import SimpleNamespace

from beetsplug.smartimport import SmartImportPlugin


class FakeNewItem(dict):
    def __init__(self, events, *, fail_write=False):
        super().__init__()
        self.events = events
        self.fail_write = fail_write
        self.id = None
        self.album_id = None

    def get(self, key, default=None, with_album=False):
        return super().get(key, default)

    def write(self):
        self.events.append("write")
        if self.fail_write:
            raise RuntimeError("write failed")

    def move(self):
        self.events.append("move")

    def remove(self, delete=False, with_album=False):
        self.events.append("new-remove")


class FakeStale:
    def __init__(self, events, path):
        self.events = events
        self.path = str(path)

    def remove(self, delete=False, with_album=False):
        self.events.append("stale-remove")


class FakeLib:
    def __init__(self, events, stale):
        self.events = events
        self.stale = stale

    def get_item(self, item_id):
        return self.stale if item_id == 7 else None

    def add(self, item):
        self.events.append("lib-add")
        item.id = 99


class FakeAlbumRow(dict):
    pass


class FakeAlbumSummary:
    def __init__(self):
        self.album_id = 42
        self.album = FakeAlbumRow(album="Album", albumartist="Artist")
        self.disctotals = set()
        self.discs = set()


def make_plugin(monkeypatch):
    plugin = SmartImportPlugin()
    monkeypatch.setattr(plugin, "_target_track_from_existing_release", lambda track, album: None)
    monkeypatch.setattr(plugin, "_apply_target_track", lambda item, album, target: None)
    monkeypatch.setattr(plugin, "_apply_safe_disc_fallback", lambda item, album: False)
    monkeypatch.setattr(plugin, "_sync_existing_album_artwork", lambda *args, **kwargs: "")
    monkeypatch.setattr(plugin, "_sync_existing_album_animated_artwork", lambda *args, **kwargs: "")
    return plugin


def test_stale_row_is_removed_only_after_write_and_move(tmp_path, monkeypatch):
    events = []
    stale = FakeStale(events, tmp_path / "missing.flac")
    lib = FakeLib(events, stale)
    plugin = make_plugin(monkeypatch)
    item = FakeNewItem(events)
    track = SimpleNamespace(item=item)

    ok, detail = plugin._attach(lib, track, FakeAlbumSummary(), stale_item_ids=[7])

    assert ok is True
    assert events[:4] == ["lib-add", "write", "move", "stale-remove"]
    assert "replaced stale Beets item ID(s): 7" in detail


def test_write_failure_keeps_stale_row(tmp_path, monkeypatch):
    events = []
    stale = FakeStale(events, tmp_path / "missing.flac")
    lib = FakeLib(events, stale)
    plugin = make_plugin(monkeypatch)
    item = FakeNewItem(events, fail_write=True)
    track = SimpleNamespace(item=item)

    ok, detail = plugin._attach(lib, track, FakeAlbumSummary(), stale_item_ids=[7])

    assert ok is False
    assert "stale-remove" not in events
    assert events[:2] == ["lib-add", "write"]
    assert "new-remove" in events


def test_reappeared_file_aborts_before_new_item_is_added(tmp_path, monkeypatch):
    events = []
    existing = tmp_path / "returned.flac"
    existing.write_bytes(b"returned")
    stale = FakeStale(events, existing)
    lib = FakeLib(events, stale)
    plugin = make_plugin(monkeypatch)
    item = FakeNewItem(events)
    track = SimpleNamespace(item=item)

    ok, detail = plugin._attach(lib, track, FakeAlbumSummary(), stale_item_ids=[7])

    assert ok is False
    assert events == []
    assert "reappeared" in detail
