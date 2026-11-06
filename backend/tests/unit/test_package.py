from importlib.metadata import version

import repoatlas


def test_version_matches_installed_metadata() -> None:
    assert repoatlas.__version__ == version("repoatlas")


def test_package_ships_type_information() -> None:
    from importlib.resources import files

    assert files("repoatlas").joinpath("py.typed").is_file()
