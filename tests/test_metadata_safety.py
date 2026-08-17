from beetsplug.smartimport import SmartImportPlugin


class FakeItem(dict):
    def get(self, key, default=None, with_album=False):
        return super().get(key, default)


class FakeAlbum(dict):
    pass


def test_canonicalize_preserves_track_descriptive_metadata():
    plugin = SmartImportPlugin()
    item = FakeItem(
        album="Incoming release",
        albumartist="Incoming artist",
        mb_albumid="11111111-1111-1111-1111-111111111111",
        mb_releasetrackid="22222222-2222-2222-2222-222222222222",
        genres="Incoming Genre",
        style="Incoming Style",
        comments="Keep this comment",
    )
    album = FakeAlbum(
        album="Canonical Album",
        albumartist="Canonical Artist",
        mb_albumid="33333333-3333-3333-3333-333333333333",
        year=2024,
        genres="Album Genre",
        style="Album Style",
    )

    plugin._canonicalize_item(item, album)

    assert item["album"] == "Canonical Album"
    assert item["albumartist"] == "Canonical Artist"
    assert item["year"] == 2024
    assert item["mb_releasetrackid"] == ""
    assert item["genres"] == "Incoming Genre"
    assert item["style"] == "Incoming Style"
    assert item["comments"] == "Keep this comment"
