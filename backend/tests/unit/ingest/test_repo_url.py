from __future__ import annotations

import pytest

from repoatlas.ingest.errors import InvalidRepoUrl
from repoatlas.ingest.github import RepoRef, parse_repo_url


@pytest.mark.parametrize(
    ("url", "owner", "name"),
    [
        ("https://github.com/pallets/flask", "pallets", "flask"),
        ("https://github.com/pallets/flask.git", "pallets", "flask"),
        ("https://github.com/pallets/flask/", "pallets", "flask"),
        ("  https://github.com/pallets/flask  ", "pallets", "flask"),
        ("https://GitHub.com/Pallets/Flask", "Pallets", "Flask"),
        ("HTTPS://github.com/my-org/repo.name_x-1", "my-org", "repo.name_x-1"),
        ("https://github.com/a/b", "a", "b"),
    ],
)
def test_valid_urls(url: str, owner: str, name: str) -> None:
    assert parse_repo_url(url) == RepoRef(owner=owner, name=name)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", "empty"),
        ("http://github.com/pallets/flask", "https"),
        ("git@github.com:pallets/flask.git", "https"),
        ("ftp://github.com/pallets/flask", "https"),
        ("https://gitlab.com/pallets/flask", "Only https://github.com"),
        ("https://www.github.com/pallets/flask", "Only https://github.com"),
        ("https://github.com.evil.example/pallets/flask", "Only https://github.com"),
        ("https://gíthub.com/pallets/flask", "Only https://github.com"),
        ("https://github.com:443/pallets/flask", "Only https://github.com"),
        ("https://github.com:abc/pallets/flask", "invalid port"),
        ("https://user:secret@github.com/pallets/flask", "credentials"),
        ("https://github.com/pallets/flask?tab=readme", "query string"),
        ("https://github.com/pallets/flask#readme", "query string"),
        ("https://github.com/pallets", "Expected"),
        ("https://github.com/pallets/flask/tree/main", "Expected"),
        ("https://github.com/pallets//flask", "Expected"),
        ("https://github.com/-pallets/flask", "owner"),
        ("https://github.com/pal--lets/flask", "owner"),
        ("https://github.com/pallets/..", "repository name"),
        ("https://github.com/pallets/fl@sk", "repository name"),
        ("https://github.com/pallets/fl%20ask", "repository name"),
    ],
)
def test_invalid_urls(url: str, reason: str) -> None:
    with pytest.raises(InvalidRepoUrl, match=reason):
        parse_repo_url(url)


def test_long_invalid_names_are_shortened_in_messages() -> None:
    with pytest.raises(InvalidRepoUrl) as excinfo:
        parse_repo_url("https://github.com/pallets/" + "x" * 150)
    assert len(str(excinfo.value)) < 120


def test_repo_ref_properties() -> None:
    ref = RepoRef(owner="Pallets", name="Flask")
    assert ref.full_name == "Pallets/Flask"
    assert ref.clone_url == "https://github.com/Pallets/Flask.git"
    assert ref.key == "pallets/flask"
