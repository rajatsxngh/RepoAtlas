"""Open every gallery card through the API, in process, on the configured database: each
snapshot's status, the first screen's reads (subgraph and overview), and how long they took.

    cd backend && uv run python scripts/gallery_check.py

Reads only: no model, no build, no network. Exits with 1 when a card does not open."""

import asyncio
import sys
import time

import httpx

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.config import get_settings


async def main() -> int:
    settings = get_settings().model_copy(update={"rate_limit_enabled": False})
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
        started = time.perf_counter()
        gallery = (await client.get(f"{API_PREFIX}/gallery")).json()["entries"]
        print(f"GET /gallery: {len(gallery)} entries in {time.perf_counter() - started:.2f} s")
        failed = 0
        for entry in gallery:
            base = f"{API_PREFIX}/snapshots/{entry['snapshot_id']}"
            t0 = time.perf_counter()
            snapshot = await client.get(base)
            subgraph = await client.get(f"{base}/subgraph")
            overview = await client.get(f"{base}/overview")
            first = time.perf_counter() - t0
            t1 = time.perf_counter()
            again = await client.get(f"{base}/subgraph")
            warm = time.perf_counter() - t1
            ok = (
                snapshot.json()["status"] == "ready"
                and subgraph.status_code == overview.status_code == again.status_code == 200
            )
            failed += not ok
            print(
                f"{entry['repo']} @ {entry['commit_sha'][:10]} | snapshot {entry['snapshot_id']} | "
                f"{snapshot.json()['status']} | {len(subgraph.json()['nodes'])} top-level nodes | "
                f"first open {first:.2f} s | again {warm * 1000:.0f} ms | "
                f"{'ok' if ok else 'FAILED'}"
            )
    app.state.engine.dispose()
    return 1 if failed or not gallery else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
