import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.scrim import build_lobby_embed, build_lobby_view, build_match_embed, format_stat_table, lobby_signature
from core.services.scrim_service import clean_player
from web.dashboard import Dashboard


def session(phase="LOBBY", **snap):
    snapshot = {"phase": phase, "players": [], "verified": [], "score": {"Black": 0, "White": 0}, **snap}
    return {"active": True, "link": "https://www.roblox.com/games/1/x?privateServerLinkCode=a", "snapshot": snapshot}


def test_clean_player_keeps_modern_roblox_ids():
    assert clean_player({"id": 9876543210, "name": "a"})["id"] == 9876543210


def test_lobby_embed_lists_only_verified_and_counts_pending():
    players = [{"id": 1, "name": "ok", "team": None}, {"id": 2, "name": "bad", "team": None}]
    embed = build_lobby_embed(session(players=players, verified=[1]), offline=False)
    text = " ".join(f.value for f in embed.fields)
    assert "ok" in text and "bad" not in text
    assert any(f.name == "Awaiting verification" and f.value == "1" for f in embed.fields)


def test_lobby_embed_offline_and_ended():
    assert "OFFLINE" in build_lobby_embed(session(), offline=True).title
    assert build_lobby_embed({"active": False}, offline=False).title == "Scrim ended"


def test_join_button_disabled_during_match():
    live = build_lobby_view(session("LIVE")).children[0]
    lobby = build_lobby_view(session("LOBBY")).children[0]
    assert live.disabled is True and lobby.url
    assert build_lobby_view({"active": False}) is None


def test_signature_changes_only_on_visible_changes():
    base = session("LOBBY")
    assert lobby_signature(base, False) == lobby_signature(session("LOBBY"), False)
    assert lobby_signature(base, False) != lobby_signature(base, True)
    assert lobby_signature(base, False) != lobby_signature(session("LOBBY", phase_ends=5), False)


def test_match_embed_flags():
    stats = [{"UserId": 1, "Name": "a", "Team": "Black", "Kills": 3, "Deaths": 1, "Assists": 0}]
    done = build_match_embed({"_id": "abcdef123456", "score": {"Black": 7, "White": 2}, "winner": "Black", "forfeit": True, "stats": stats, "rounds": 9})
    assert "wins by forfeit" in done.description and "Black 7 - 2 White" in done.title
    aborted = build_match_embed({"_id": "abcdef123456", "score": {"Black": 3, "White": 2}, "aborted": True, "stats": stats})
    assert "Aborted" in aborted.title
    assert "a " in format_stat_table(stats)


@pytest.mark.asyncio
async def test_scrim_sync_endpoint_requires_secret():
    cog = SimpleNamespace(process_scrim_sync=AsyncMock(return_value={"ok": 1}))
    dashboard = Dashboard(SimpleNamespace(get_cog=MagicMock(return_value=cog)))
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value={}))
    with patch("web.dashboard.Config.ROBLOX_WEBHOOK_SECRET", "s3cret"):
        response = await dashboard.post_scrim_sync(request)
        assert response.status == 401
        request.json.assert_not_awaited()
        request.headers = {"X-Roblox-Secret": "s3cret"}
        response = await dashboard.post_scrim_sync(request)
        assert response.status == 200
        assert json.loads(response.text) == {"ok": 1}


@pytest.mark.asyncio
async def test_cog_registers_scrim_commands():
    import discord
    from discord.ext import commands
    from cogs.scrim import Scrim

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.db = SimpleNamespace(db=MagicMock())
    cog = Scrim(bot)
    await bot.add_cog(cog)
    group = bot.tree.get_command("scrim")
    assert {c.name for c in group.commands} == {"start", "stop", "status", "config"}
    cog.ui_loop.cancel()
    cog.draft_loop.cancel()
    await bot.close()


# ---- captain draft ---------------------------------------------------------
def draft_session(done=False, turn="Black"):
    draft = {
        "id": "d1",
        "per_side": 3,
        "captains": {"Black": {"id": 1, "name": "capA"}, "White": {"id": 2, "name": "capB"}},
        "pool": [{"id": 3, "name": "p3", "display": "P3"}, {"id": 4, "name": "p4", "display": ""}],
        "picks": [{"seq": 1, "id": 9, "name": "first", "team": "Black"}],
        "turn": None if done else turn,
        "deadline": 1_900_000_000.0,
        "done": done,
    }
    base = session("DRAFT", captain_round=2)
    base["draft"] = draft
    return base


def test_draft_embed_shows_teams_captain_turn_and_pool():
    embed = build_lobby_embed(draft_session(), offline=False)
    assert "capA" in embed.description and "Black captain" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    assert "capA (captain)" in fields["Black"] and "first" in fields["Black"]
    assert "capB (captain)" in fields["White"]
    assert "p3" in fields["Available (2)"]


def test_draft_view_has_dropdown_only_while_picking():
    view = build_lobby_view(draft_session(), cog=MagicMock())
    select = next(c for c in view.children if getattr(c, "custom_id", "") == "scrim_draft_pick")
    assert [o.value for o in select.options] == ["3", "4"]
    assert any(getattr(c, "disabled", False) for c in view.children)
    done_view = build_lobby_view(draft_session(done=True), cog=MagicMock())
    assert all(getattr(c, "custom_id", "") != "scrim_draft_pick" for c in done_view.children)


def test_captain_round_embed_and_signature_track_draft():
    snap = session("CAPTAINS", captain_round=1, captains={"Black": {"id": 1, "name": "capA"}})
    embed = build_lobby_embed(snap, offline=False)
    assert "round 1/2" in embed.description
    assert any(f.name == "Black captain" and f.value == "capA" for f in embed.fields)
    assert lobby_signature(draft_session(), False) != lobby_signature(draft_session(turn="White"), False)


async def make_cog(service):
    import discord
    from discord.ext import commands
    from cogs.scrim import Scrim

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.db = SimpleNamespace(db=SimpleNamespace(config=SimpleNamespace(find_one=AsyncMock(return_value={}))))
    cog = Scrim(bot)
    cog._service = service
    cog._refresh_lobby = AsyncMock()
    return bot, cog


def interaction(user_id):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, roles=[]),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_only_the_current_captain_or_staff_can_pick():
    service = SimpleNamespace(
        get_session=AsyncMock(return_value={**draft_session(), "active": True}),
        discord_id_for_roblox=AsyncMock(return_value=555),
        make_pick=AsyncMock(return_value=(True, "ok", {})),
    )
    bot, cog = await make_cog(service)
    try:
        stranger = interaction(777)
        await cog.handle_pick(stranger, 3)
        assert "Only the Black captain" in stranger.response.send_message.call_args.args[0]
        service.make_pick.assert_not_awaited()

        captain = interaction(555)
        await cog.handle_pick(captain, 3)
        service.make_pick.assert_awaited_once_with(3, "Black")
        captain.response.defer.assert_awaited_once()
        cog._refresh_lobby.assert_awaited_once()

        with patch("cogs.scrim.Config.MASTER_ADMIN_ID", 888):
            service.make_pick.reset_mock()
            staff = interaction(888)
            await cog.handle_pick(staff, 4)
            service.make_pick.assert_awaited_once_with(4, "Black")
    finally:
        cog.ui_loop.cancel()
        cog.draft_loop.cancel()
        await bot.close()


@pytest.mark.asyncio
async def test_pick_rejected_by_service_is_reported_privately():
    service = SimpleNamespace(
        get_session=AsyncMock(return_value={**draft_session(), "active": True}),
        discord_id_for_roblox=AsyncMock(return_value=555),
        make_pick=AsyncMock(return_value=(False, "That player is no longer available.", {})),
    )
    bot, cog = await make_cog(service)
    try:
        captain = interaction(555)
        await cog.handle_pick(captain, 3)
        assert captain.response.send_message.call_args.args[0] == "That player is no longer available."
        cog._refresh_lobby.assert_not_awaited()
    finally:
        cog.ui_loop.cancel()
        cog.draft_loop.cancel()
        await bot.close()


@pytest.mark.asyncio
async def test_no_pick_when_draft_is_over():
    service = SimpleNamespace(get_session=AsyncMock(return_value={**draft_session(done=True), "active": True}))
    bot, cog = await make_cog(service)
    try:
        captain = interaction(555)
        await cog.handle_pick(captain, 3)
        assert "no pick in progress" in captain.response.send_message.call_args.args[0]
    finally:
        cog.ui_loop.cancel()
        cog.draft_loop.cancel()
        await bot.close()
