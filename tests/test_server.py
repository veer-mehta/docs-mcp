import json

from docs_mcp.adapters import mcp_server as server
from docs_mcp.pipeline import IngestResult


async def test_sync_add_documentation_forwards_prune_missing(monkeypatch):
    captured = {}

    class FakeIndex:
        async def ingest_site(self, name, version, base_url, **kwargs):
            captured.update(kwargs)
            return IngestResult(f"{name}@{version}", 0, 0, 0, 0)

    async def fake_shared_index():
        return FakeIndex()

    monkeypatch.setattr(server, "shared_index", fake_shared_index)
    result = await server.add_documentation("fw", "1.0", "https://fw.dev", prune_missing=True)

    assert json.loads(result)["source_id"] == "fw@1.0"
    assert captured["prune_missing"] is True
