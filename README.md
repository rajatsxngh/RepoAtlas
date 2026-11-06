# RepoAtlas

Paste a public GitHub URL, get an interactive map of the code, and ask hard questions like "What breaks if I change this function?" Every answer cites file and line, impact predictions are checked against real test failures, and published numbers show how accurate it all is.

> **Status:** in development — building the foundations milestone (M00). See the [milestone guide](docs/milestones/README.md).

## Documentation

- [Product requirements](docs/PRD.md)
- [Milestones and build order](docs/milestones/README.md)
- [Decision records](docs/decisions/README.md)
- [Guide for coding sessions](CLAUDE.md)

## Development

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [uv](https://docs.astral.sh/uv/). Works on Windows, macOS, and Linux.

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend
uv sync
uv run repoatlas db upgrade
uv run poe check
```

## License

[MIT](LICENSE)
