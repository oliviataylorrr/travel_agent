"""Installed SDK integration smoke test. Runs in Codespaces; skipped when SDK absent."""
import importlib.util
import pytest
@pytest.mark.skipif(importlib.util.find_spec('a2a') is None,reason='a2a-sdk not installed in isolated build environment')
def test_official_sdk_card_http_route(tmp_path,monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import a2a_bridge
    from app.settings import DATA_DIR
    monkeypatch.setattr(a2a_bridge,'DATA_DIR',tmp_path)
    async def executor(payload):return {'ok':True,'echo':payload.get('action')}
    @asynccontextmanager
    async def lifespan(app):
        await store.initialize()
        try:yield
        finally:await engine.dispose()
    app=FastAPI(lifespan=lifespan)
    engine,store=a2a_bridge.attach_sdk(app,executor,'Mock Agent','Mock A2A SDK route','travel')
    with TestClient(app) as client:
        response=client.get('/.well-known/agent-card.json')
        assert response.status_code==200,response.text
        data=response.json()
        assert data['name']=='Mock Agent'
