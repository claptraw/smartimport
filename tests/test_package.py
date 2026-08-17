from beetsplug.smartimport import SmartImportPlugin, __version__


def test_version():
    assert __version__ == "1.0.0"


def test_commands_are_registered():
    names = [command.name for command in SmartImportPlugin().commands()]
    assert names == ["smartimport", "smartcleanup", "smartrepair", "smartnotifytest"]
