import json

from docs_mcp import server


async def test_sync_add_documentation_forwards_prune_missing(monkeypatch):
    captured = {}

    async def fake_ingest_documentation(_db, name, version, base_url, **kwargs):
        captured.update(kwargs)
        return {"source_id": f"{name}@{version}"}

    monkeypatch.setattr(server, "ingest_documentation", fake_ingest_documentation)
    result = await server.add_documentation("fw", "1.0", "https://fw.dev", prune_missing=True)

    assert json.loads(result) == {"source_id": "fw@1.0"}
    assert captured["prune_missing"] is True
