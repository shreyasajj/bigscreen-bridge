import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.couchside import KINDS, CouchsideHook, validate_content


@pytest.mark.parametrize("kind,value", [("track", "library://track/1"),
    ("album", "spotify--abc://album/1"), ("artist", "library://artist/1"),
    ("playlist", "library://playlist/1"), ("search", "Björk"), ("now_playing", "")])
def test_typed_content_validation(kind, value):
    assert validate_content(kind, value) == (kind, value)


@pytest.mark.parametrize("kind,value", [("track", "library://album/1"),
    ("track", "https://youtube.com/watch?v=a"), ("search", "a\n"),
    ("search", "x" * 513), ("eval", "process.exit()")])
def test_invalid_typed_content(kind, value):
    with pytest.raises(BridgeError):
        validate_content(kind, value)


async def test_content_navigation_and_owner(bridge):
    b, _ = bridge
    state = {"view": {"type": "album", "id": "library://album/1"}, "ma_player_id": "ma-player"}
    b.couchside = SimpleNamespace(enabled=True, state=state, refresh=AsyncMock(), navigate=AsyncMock(return_value=state))
    b.wm.windows = [{"id": "couchside-window", "desktop": APPS[0].flatpak}]
    request = {"id": "request-1", "command": "open", "app": "couchside",
               "content_type": "album", "content_id": "library://album/1"}
    response = await b.execute(request)
    assert response["playback_owner"] == "music_assistant"
    b.couchside.navigate.assert_awaited_once_with("request-1", "album", "library://album/1")
    app = next(a for a in b.snapshot()["apps"] if a["id"] == "couchside")
    assert app["ma_player_id"] == "ma-player"
    assert app["content_types"] == ["couchside_" + kind for kind in KINDS]
    assert app["capabilities"] == ["focus"]  # No duplicate transport controls.


async def test_unsupported_content_never_focuses(bridge):
    b, _ = bridge
    b.couchside = SimpleNamespace(enabled=True, state=None, refresh=AsyncMock())
    with pytest.raises(BridgeError):
        await b.execute({"id": "bad", "command": "open", "app": "couchside",
                         "content_type": "track", "content_id": "library://album/1"})
    assert not b.wm.focused


async def test_hook_owner_loss_clears_cached_identity():
    hook = CouchsideHook(None, enabled=True)
    hook.state = {"ma_player_id": "old-player"}
    hook.request = AsyncMock(side_effect=BridgeError("not_ready", "missing"))
    await hook.refresh()
    assert hook.state is None
    assert hook.enabled  # Installed hook may cold-launch again.


async def test_background_sendspin_does_not_prevent_patched_ui_launch(bridge, monkeypatch):
    b, _ = bridge
    b.couchside = SimpleNamespace(enabled=True, state=None, refresh=AsyncMock())
    monkeypatch.setattr("bigscreen_bridge.server.process", AsyncMock(return_value=APPS[0].flatpak))
    launches = []

    async def launch(*argv):
        launches.append(argv)
        await asyncio.sleep(0)
        b.wm.windows = [{"id": "new-ui", "desktop": APPS[0].flatpak}]
        b.couchside.state = {"ready": True}

    b.launcher = launch
    await b.focus("couchside")
    assert launches == [("flatpak", "run", APPS[0].flatpak)]


async def test_hook_waits_for_ma_readiness_before_navigation():
    hook = CouchsideHook(None, enabled=True)
    hook.state = {"ready": True, "music_assistant_ready": False, "content_types": ["search"]}
    refreshes = []

    async def refresh():
        refreshes.append(True)
        if len(refreshes) > 1:
            hook.state = {**hook.state, "music_assistant_ready": True}

    hook.refresh = refresh
    hook.request = AsyncMock(return_value={"view": {"type": "search", "id": "Music"}})
    await hook.navigate("request", "search", "Music")
    assert len(refreshes) == 2
    hook.request.assert_awaited_once_with("navigate", "request", content_type="search", content_id="Music")


async def test_closing_window_does_not_prevent_a_new_ui_launch(bridge, monkeypatch):
    b, _ = bridge
    b.couchside = SimpleNamespace(enabled=True, state=None, refresh=AsyncMock())
    b.wm.windows = [{"id": "closing", "desktop": APPS[0].flatpak}]

    async def refreshed():
        b.wm.windows = [w for w in b.wm.windows if w["id"] != "closing"]

    async def launch(*argv):
        b.wm.windows = [{"id": "new", "desktop": APPS[0].flatpak}]
        b.couchside.state = {"ready": True}

    b.wm.refresh = refreshed
    b.launcher = launch
    monkeypatch.setattr("bigscreen_bridge.server.process", AsyncMock(return_value=APPS[0].flatpak))
    await b.focus("couchside")
    assert b.wm.focused == ["new"]
