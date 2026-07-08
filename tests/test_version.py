from needledrop import __version__


def test_version_matches_package_metadata():
    from importlib.metadata import version

    assert __version__ == version("needledrop")
