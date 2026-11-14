"""Clone-URL SSRF (GRD-8): only ``https://github.com/<owner>/<repo>`` is accepted, a bad URL is
refused before any network call, and nothing follows a redirect to another host."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from repoatlas.ingest.errors import FetchError, GitHubError, InvalidRepoUrl
from repoatlas.ingest.fetch import fetch_history, fetch_source
from repoatlas.ingest.git import GIT_CONFIG_ARGS, git_command
from repoatlas.ingest.github import GitHubClient, RepoRef, parse_repo_url
from repoatlas.pipeline.build import resolve_target
from tests.unit.api.helpers import api_settings

SHA = "a" * 40
HOSTILE_URLS = [
    # other hosts
    "https://gitlab.com/o/r",
    "https://evil.example/o/r",
    "https://evil.example/github.com/o/r",
    "https://github.com.evil.example/o/r",
    "https://evilgithub.com/o/r",
    "https://raw.githubusercontent.com/o/r",
    "https://api.github.com/o/r",
    "https://github.com./o/r",
    # IP literals and local names
    "https://127.0.0.1/o/r",
    "https://169.254.169.254/latest/meta-data",
    "https://[::1]/o/r",
    "https://[fe80::1]/o/r",
    "https://2130706433/o/r",
    "https://0x7f000001/o/r",
    "https://localhost/o/r",
    "https://10.0.0.5/o/r",
    # userinfo
    "https://github.com@evil.example/o/r",
    "https://user:pass@github.com/o/r",
    "https://evil.example@github.com/o/r",
    "https://github.com%40evil.example/o/r",
    "https://github.com\\@evil.example/o/r",
    # ports
    "https://github.com:443/o/r",
    "https://github.com:8443/o/r",
    "https://github.com:22/o/r",
    # schemes
    "http://github.com/o/r",
    "git://github.com/o/r",
    "ssh://git@github.com/o/r",
    "file:///etc/passwd",
    "ftp://github.com/o/r",
    "gopher://github.com/o/r",
    "javascript://github.com/o/r",
    "//github.com/o/r",
    "github.com/o/r",
    # unicode look-alike hosts
    "https://gíthub.com/o/r",
    f"https://g{chr(0x456)}thub.com/o/r",  # a Cyrillic letter that looks like i
    "https://" + "".join(chr(0xFF00 + ord(c) - 0x20) for c in "github") + ".com/o/r",  # full-width
    f"https://github{chr(0x3002)}com/o/r",  # an ideographic full stop for the dot
    "https://xn--gthub-cta.com/o/r",
    f"https://github.com{chr(0x200B)}/o/r",  # a zero-width space after the host
    # control characters and smuggling
    "https://github.com/o/r\r\nHost: evil.example",
    "https://github.com/o/r\x00",
    "https://github.com/o/r?url=https://evil.example",
    "https://github.com/o/r#@evil.example",
    "https://github.com/o/../../evil",
    "https://github.com/o/r/../../..",
]


class Counting(httpx.BaseTransport):
    """A transport that records every request and answers 404."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_a_hostile_url_is_refused_before_any_network_call(url: str) -> None:
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(url)
    transport = Counting()
    with GitHubClient(transport=transport) as github, pytest.raises(InvalidRepoUrl):
        resolve_target(url, api_settings(), github=github)
    assert transport.requests == []  # GitHub was never asked about it


def test_a_valid_url_reaches_only_the_github_api() -> None:
    transport = Counting()
    with GitHubClient(transport=transport) as github, pytest.raises(Exception, match="not found"):
        resolve_target("https://github.com/pallets/flask", api_settings(), github=github)
    assert [str(request.url) for request in transport.requests] == [
        "https://api.github.com/repos/pallets/flask"
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/repos/a/b",
        "http://api.github.com/repos/a/b",
        "https://api.github.com:8443/repos/a/b",
        "https://169.254.169.254/latest/meta-data/",
        "https://api.github.com.evil.example/repos/a/b",
    ],
)
def test_the_github_client_never_follows_a_redirect_to_another_host(location: str) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(301, headers={"Location": location})

    slept: list[float] = []
    client = GitHubClient(
        token="ghp_sekrit", transport=httpx.MockTransport(answer), sleep=slept.append
    )
    with client, pytest.raises(GitHubError, match="redirected the request to another host"):
        client.get_repo(RepoRef("a", "b"))
    # One request, to GitHub; the redirect target was never contacted, and it is not retried.
    assert [request.url.host for request in seen] == ["api.github.com"]
    assert slept == []


def test_a_redirect_within_the_github_api_is_followed() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/repos/old/name":
            return httpx.Response(301, headers={"Location": "/repositories/42"})
        return httpx.Response(
            200,
            json={
                "name": "name",
                "owner": {"login": "new"},
                "default_branch": "main",
                "private": False,
                "size": 10,
                "html_url": "https://github.com/new/name",
            },
        )

    with GitHubClient(transport=httpx.MockTransport(answer)) as client:
        meta = client.get_repo(RepoRef("old", "name"))
    assert meta.ref.full_name == "new/name"
    assert seen == [
        "https://api.github.com/repos/old/name",
        "https://api.github.com/repositories/42",
    ]


def test_git_fetches_allow_https_and_file_only_and_follow_no_redirects() -> None:
    args = list(GIT_CONFIG_ARGS)
    assert "protocol.allow=never" in args
    assert "protocol.https.allow=always" in args and "protocol.file.allow=always" in args
    assert "http.followRedirects=false" in args
    assert "credential.helper=" in args
    command = git_command(["fetch", "x"])
    assert command.index("http.followRedirects=false") < command.index("fetch")
    assert not any(arg.startswith("protocol.") and "http.allow" in arg for arg in args)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r.git",
        "https://evil.example/o/r.git",
        "https://github.com.evil.example/o/r.git",
        "git://github.com/o/r.git",
        "ssh://git@github.com/o/r.git",
        "ext::sh -c 'touch pwned'",
        "-uexec=touch pwned",
        "https://127.0.0.1/o/r.git",
    ],
)
def test_fetches_refuse_any_other_origin_before_git_runs(url: str, tmp_path: Path) -> None:
    refusal = "Only https://github.com/ and file://"
    with pytest.raises(FetchError, match=refusal):
        fetch_source(url, SHA, tmp_path / "dest", label="x")
    with pytest.raises(FetchError, match=refusal):
        fetch_history(url, SHA, tmp_path / "dest")
    assert not (tmp_path / "dest").exists()  # git was never started
    assert not (tmp_path / "pwned").exists() and not Path("pwned").exists()
