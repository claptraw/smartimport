from beetsplug.smartimport import SmartImportPlugin


def test_artwork_can_be_disabled_without_touching_library():
    plugin = SmartImportPlugin()
    plugin.config["sync_artwork"].set(False)

    class ExplodingLibrary:
        def get_album(self, album_id):
            raise AssertionError("library should not be touched")

    assert plugin._sync_existing_album_artwork(ExplodingLibrary(), 1) == ""


def test_animated_artwork_is_disabled_by_public_default(monkeypatch):
    plugin = SmartImportPlugin()
    monkeypatch.setattr(plugin, "_loaded_plugin", lambda name: (_ for _ in ()).throw(AssertionError()))
    assert plugin._sync_existing_album_animated_artwork(None, 1) == ""
