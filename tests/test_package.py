from importlib.metadata import version


def test_package_version():
    assert version("opensre") == "0.1.0"
