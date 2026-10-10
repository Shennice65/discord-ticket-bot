import traceback
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Config
from core.services.scrim_bridge import drain_inbox, ensure_indexes
from core.services.scrim_service import CONFIG_SPEC, ScrimService, is_valid_private_link
from utils.roblox_link import build_login_link

PHASE_COLORS = {
    "LOBBY": 0x3498DB,
    "COUNTDOWN": 0xF1C40F,
    "CAPTAINS": 0xE67E22,
    "DRAFT": 0x1ABC9C,
    "LIVE": 0xE74C3C,
    "POSTGAME": 0x9B59B6,
    "BREAK": 0x95A5A6,
}
TEAMS = ("Black", "White")
VERIFY_HINT = "Not verified? Press Verify with Roblox below (or run /link_roblox), or you will be kicked."


def format_stat_table(stats: List[dict]) -> str:
    if not stats:
        return "No stats recorded."
    out = ""
    for team in ("Black", "White"):
        members = sorted((s for s in stats if s.get("Team") == team), key=lambda s: (-s["Kills"], s["Deaths"]))
        if not members:
            continue
        width = max(max(len(m["Name"]) for m in members), 12)
        out += f"**{team} Team**\n```\n{'Player':<{width}} | K  | D  | A\n{'-' * (width + 14)}\n"
        for m in members:
            out += f"{m['Name']:<{width}} | {m['Kills']:<2} | {m['Deaths']:<2} | {m['Assists']:<2}\n"
        out += "```\n"
    return out or "No stats recorded."


def build_match_embed(match: dict) -> discord.Embed:
    score = match.get("score", {})
    black, white = score.get("Black", 0), score.get("White", 0)
    if match.get("aborted"):
        title, color, note = f"Scrim Aborted: Black {black} - {white} White", 0xE67E22, "Host script disconnected mid-match; partial stats."
    else:
        winner = match.get("winner")
        title = f"Scrim Result: Black {black} - {white} White"
        color = 0x2ECC71
        note = f"**{winner} wins{' by forfeit' if match.get('forfeit') else ''}!**" if winner else "No winner recorded."
    embed = discord.Embed(
        title=title,
        description=f"{note}\n\n{format_stat_table(match.get('stats', []))}",
        color=color,
        timestamp=match.get("ended_at") or discord.utils.utcnow(),
    )
    embed.set_footer(text=f"Match {match['_id'][:8]} - {match.get('rounds', 0)} rounds")
    return embed


def draft_text(session: dict) -> str:
    draft = session.get("draft")
    if not draft:
        return "Captains are chosen. The draft is starting..."
    if draft.get("done"):
        return "Draft complete. The match starts shortly."
    team = draft["turn"]
    captain = ((draft.get("captains") or {}).get(team) or {}).get("name") or "?"
    return f"**{captain}** ({team} captain) is picking <t:{int(draft['deadline'])}:R>. Use the dropdown below."


def add_draft_fields(embed: discord.Embed, draft: dict) -> None:
    for team in TEAMS:
        captain = ((draft.get("captains") or {}).get(team) or {}).get("name") or "?"
        picks = [p["name"] for p in draft.get("picks", []) if p["team"] == team]
        embed.add_field(name=team, value="\n".join([f"{captain} (captain)", *picks]), inline=True)
    pool = [p["name"] for p in draft.get("pool", [])]
    embed.add_field(name=f"Available ({len(pool)})", value="\n".join(pool[:25]) or "-", inline=False)


def build_lobby_embed(session: dict, offline: bool) -> discord.Embed:
    snap = session.get("snapshot") or {}
    phase = snap.get("phase")
    ends = snap.get("phase_ends")
    verified = set(snap.get("verified") or [])
    players = snap.get("players") or []
    ok = [p for p in players if p["id"] in verified]
    pending = len(players) - len(ok)

    if not session.get("active"):
        embed = discord.Embed(title="Scrim ended", description="No scrim is running.", color=0x7F8C8D)
        return embed
    if offline:
        embed = discord.Embed(title="Scrim - HOST OFFLINE", description="The host script stopped responding. Staff have been pinged.", color=0xFF0000)
    else:
        if phase == "COUNTDOWN" and ends:
            text = f"Starting <t:{ends}:R>. The timer resets when a new verified player joins (stops resetting at 10)."
        elif phase == "LIVE":
            score = snap.get("score", {})
            text = f"Match in progress: **Black {score.get('Black', 0)} - {score.get('White', 0)} White**"
        elif phase == "CAPTAINS":
            text = f"Captain selection: round {snap.get('captain_round') or 1}/2. The last player standing becomes a captain."
        elif phase == "DRAFT":
            text = draft_text(session)
        elif phase == "POSTGAME":
            text = "Match finished. Posting results."
        elif phase == "BREAK" and ends:
            text = f"Next scrim opens <t:{ends}:R>."
        elif phase == "LOBBY":
            text = "Lobby open. The 30s timer starts when the first verified player joins."
        else:
            text = "Waiting for the host script to connect..."
        embed = discord.Embed(title="ATL Scrim", description=text, color=PHASE_COLORS.get(phase, 0x7F8C8D))

    if phase == "DRAFT" and session.get("draft"):
        add_draft_fields(embed, session["draft"])
    elif phase == "CAPTAINS":
        captains = snap.get("captains") or {}
        for team in TEAMS:
            embed.add_field(name=f"{team} captain", value=(captains.get(team) or {}).get("name") or "?", inline=True)
        embed.add_field(name=f"Players ({len(ok)})", value="\n".join(p["name"] for p in ok[:25]) or "-", inline=False)
    elif phase == "LIVE":
        for team in ("Black", "White"):
            names = [p["name"] for p in ok if p.get("team") == team]
            embed.add_field(name=f"{team} ({len(names)})", value="\n".join(names) or "-", inline=True)
    else:
        embed.add_field(name=f"Players ({len(ok)}/10)", value="\n".join(p["name"] for p in ok[:25]) or "-", inline=False)
    if pending:
        embed.add_field(name="Awaiting verification", value=str(pending), inline=False)
    embed.set_footer(text=VERIFY_HINT)
    return embed


class DraftSelect(discord.ui.Select):
    """Captains pick from this dropdown. A fixed custom_id lets one registered view serve every lobby message
    (including after a bot restart); the real options only live on the message."""

    def __init__(self, cog, options=None):
        self.cog = cog
        options = options or [discord.SelectOption(label="No players available", value="0")]
        super().__init__(custom_id="scrim_draft_pick", placeholder="Pick a player...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_pick(interaction, int(self.values[0]))


class DraftView(discord.ui.View):
    def __init__(self, cog, options=None):
        super().__init__(timeout=None)
        self.add_item(DraftSelect(cog, options))


class VerifyButton(discord.ui.Button):
    """Gives the clicker their own signed Roblox login link (it cannot be a plain link button: it is per user)."""

    def __init__(self, cog):
        self.cog = cog
        super().__init__(label="Verify with Roblox", style=discord.ButtonStyle.success, custom_id="scrim_verify")

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_verify(interaction)


class VerifyView(discord.ui.View):
    """Registered once so the Verify button keeps working on old lobby messages after a bot restart."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.add_item(VerifyButton(cog))


BUSY_LABELS = {"CAPTAINS": "Captain selection", "DRAFT": "Draft in progress", "LIVE": "Match in progress", "POSTGAME": "Match in progress"}


def build_lobby_view(session: dict, cog=None) -> Optional[discord.ui.View]:
    if not session.get("active"):
        return None
    view = discord.ui.View(timeout=None)
    phase = (session.get("snapshot") or {}).get("phase")
    if phase in BUSY_LABELS:
        view.add_item(discord.ui.Button(label=BUSY_LABELS[phase], style=discord.ButtonStyle.secondary, disabled=True))
    else:
        view.add_item(discord.ui.Button(label="Join Private Server", style=discord.ButtonStyle.link, url=session["link"]))
        if cog is not None:
            view.add_item(VerifyButton(cog))
    draft = session.get("draft")
    if phase == "DRAFT" and cog is not None and draft and not draft.get("done") and draft.get("pool"):
        options = [
            discord.SelectOption(label=p["name"][:100], description=(p.get("display") or "")[:100] or None, value=str(p["id"]))
            for p in draft["pool"][:25]
        ]
        view.add_item(DraftSelect(cog, options))
    return view


def lobby_signature(session: dict, offline: bool) -> tuple:
    snap = session.get("snapshot") or {}
    players = tuple(sorted((p["id"], p.get("team")) for p in snap.get("players", [])))
    return (
        session.get("active"),
        offline,
        snap.get("phase"),
        snap.get("phase_ends"),
        tuple(sorted((snap.get("score") or {}).items())),
        players,
        tuple(snap.get("verified") or []),
        snap.get("captain_round"),
        tuple(sorted((team, (c or {}).get("id")) for team, c in (snap.get("captains") or {}).items())),
        draft_signature(session.get("draft")),
    )


def draft_signature(draft: Optional[dict]) -> tuple:
    if not draft:
        return ()
    return (
        draft.get("turn"),
        draft.get("deadline"),
        draft.get("done"),
        len(draft.get("picks", [])),
        tuple(p["id"] for p in draft.get("pool", [])),
        tuple(sorted((team, (c or {}).get("id")) for team, c in (draft.get("captains") or {}).items())),
    )


class Scrim(commands.Cog):
    scrim = app_commands.Group(name="scrim", description="Automated scrim controls")

    def __init__(self, bot):
        self.bot = bot
        self._service: Optional[ScrimService] = None
        self._last_sig = None
        self._last_message_id = None
        self._in_draft = False
        bot.add_view(DraftView(self))
        bot.add_view(VerifyView(self))
        self.ui_loop.start()
        self.draft_loop.start()
        self.bridge_loop.start()

    async def cog_unload(self):
        self.ui_loop.cancel()
        self.draft_loop.cancel()
        self.bridge_loop.cancel()

    @property
    def service(self) -> ScrimService:
        if self._service is None:
            self._service = ScrimService(self.bot.db.db)
        return self._service

    # ---- permissions ----------------------------------------------------
    async def _staff_role_ids(self) -> set:
        ids = {Config.HEAD_OBSERVER_ROLE_ID, Config.CO_OWNER_ROLE_ID}
        doc = await self.bot.db.db.config.find_one({"_id": "api_keys"}) or {}
        try:
            ids.add(int(doc.get("HEAD_OBSERVER_ID") or 0))
        except (TypeError, ValueError):
            pass
        ids.discard(0)
        return ids

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in (Config.MASTER_ADMIN_ID, Config.SHEN_ID):
            return True
        roles = {r.id for r in getattr(interaction.user, "roles", ())}
        return bool(roles & await self._staff_role_ids())

    async def _deny(self, interaction: discord.Interaction):
        await interaction.response.send_message("Only Head Observers and admins can control scrims.", ephemeral=True)

    # ---- helpers ----------------------------------------------------------
    async def _get_channel(self, channel_id: Optional[int]):
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    async def _edit_lobby(self, session: dict, offline: bool = False):
        channel = await self._get_channel(session.get("channel_id"))
        message_id = session.get("message_id")
        if not channel or not message_id:
            return
        await channel.get_partial_message(message_id).edit(
            embed=build_lobby_embed(session, offline), view=build_lobby_view(session, self)
        )

    # ---- commands -----------------------------------------------------------
    @scrim.command(name="start", description="Open a scrim lobby with a private server link")
    @app_commands.describe(link="Roblox private server link")
    async def start(self, interaction: discord.Interaction, link: str):
        if not await self._authorized(interaction):
            return await self._deny(interaction)
        link = link.strip()
        if not is_valid_private_link(link):
            return await interaction.response.send_message(
                "That is not a Roblox private server link (needs roblox.com ... privateServerLinkCode=...).", ephemeral=True
            )
        existing = await self.service.get_session()
        if existing and existing.get("active"):
            return await interaction.response.send_message("A scrim is already running. Use /scrim stop first.", ephemeral=True)
        anc_id, _ = await self.service.get_channel_ids()
        channel = await self._get_channel(anc_id)
        if not channel:
            return await interaction.response.send_message(
                "SCRIM_ANC_ID is missing or not a channel I can see (config > api_keys).", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        session = await self.service.start_session(link, interaction.user.id, channel.id)
        message = await channel.send(embed=build_lobby_embed(session, False), view=build_lobby_view(session, self))
        await self.service.attach_message(message.id)
        self._last_sig = None
        await interaction.followup.send(
            f"Scrim lobby posted in {channel.mention}. It starts once the host script connects.", ephemeral=True
        )

    @scrim.command(name="stop", description="Stop the scrim loop")
    async def stop(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return await self._deny(interaction)
        session = await self.service.stop_session()
        if not session:
            return await interaction.response.send_message("No scrim to stop.", ephemeral=True)
        try:
            await self._edit_lobby(session)
        except discord.HTTPException:
            traceback.print_exc()
        await interaction.response.send_message("Scrim stopped. The host script goes idle on its next sync.", ephemeral=True)

    @scrim.command(name="status", description="Show the scrim state")
    async def status(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return await self._deny(interaction)
        session = await self.service.get_session()
        cfg = await self.service.get_config()
        if not session or not session.get("active"):
            state = "No active scrim."
        else:
            snap = session.get("snapshot") or {}
            state = f"Phase **{snap.get('phase', 'not connected')}**, host {'OFFLINE' if self.service.host_offline(session) else 'online'}"
        settings = ", ".join(f"{k}={v}" for k, v in cfg.items())
        await interaction.response.send_message(f"{state}\nSettings: {settings}", ephemeral=True)

    @scrim.command(name="config", description="Change scrim settings")
    @app_commands.describe(
        min_players="Minimum players to start (even teams)",
        break_minutes="Minutes between scrims",
        verify_grace_seconds="Seconds an unverified player gets before being kicked",
        countdown_seconds="Lobby countdown length",
        captains="Captain draft (True) or random teams (False)",
        pick_seconds="Seconds a captain has per pick",
    )
    async def config(
        self,
        interaction: discord.Interaction,
        min_players: Optional[int] = None,
        break_minutes: Optional[int] = None,
        verify_grace_seconds: Optional[int] = None,
        countdown_seconds: Optional[int] = None,
        captains: Optional[bool] = None,
        pick_seconds: Optional[int] = None,
    ):
        if not await self._authorized(interaction):
            return await self._deny(interaction)
        changes = {
            "min_players": min_players,
            "break_seconds": break_minutes * 60 if break_minutes is not None else None,
            "verify_grace_seconds": verify_grace_seconds,
            "countdown_seconds": countdown_seconds,
            "captains": None if captains is None else int(captains),
            "pick_seconds": pick_seconds,
        }
        for key, value in changes.items():
            if value is not None and key in CONFIG_SPEC:
                await self.service.set_config(key, value)
        cfg = await self.service.get_config()
        await interaction.response.send_message("Settings: " + ", ".join(f"{k}={v}" for k, v in cfg.items()), ephemeral=True)

    # ---- called by the web server -------------------------------------------
    async def process_scrim_sync(self, payload: dict) -> dict:
        result = await self.service.handle_sync(payload)
        if result.new_matches:
            _, log_id = await self.service.get_channel_ids()
            try:
                channel = await self._get_channel(log_id)
            except discord.HTTPException:
                channel = None
            if channel is None:
                print("[SCRIM] SCRIM_LOG_ID missing; match saved to Mongo only")
            for match in result.new_matches:
                if channel is not None:
                    try:
                        await channel.send(embed=build_match_embed(match))
                    except discord.HTTPException:
                        traceback.print_exc()
        return result.response

    # ---- background UI refresh ---------------------------------------------
    @tasks.loop(seconds=3.0)
    async def ui_loop(self):
        try:
            session = await self.service.get_session()
            if not session or not session.get("active") or not session.get("message_id"):
                return
            offline = self.service.host_offline(session)
            self._in_draft = (session.get("snapshot") or {}).get("phase") == "DRAFT"
            sig = lobby_signature(session, offline)
            if offline and not session.get("host_offline_notified"):
                await self.service.mark_host_offline_notified()
                await self._ping_staff(session)
            if sig == self._last_sig and session["message_id"] == self._last_message_id:
                return
            await self._edit_lobby(session, offline)
            self._last_sig = sig
            self._last_message_id = session["message_id"]
        except discord.HTTPException:
            traceback.print_exc()
        except Exception:
            traceback.print_exc()

    @ui_loop.before_loop
    async def _before_ui_loop(self):
        await self.bot.wait_until_ready()

    async def _refresh_lobby(self):
        session = await self.service.get_session()
        if not session or not session.get("active"):
            return
        offline = self.service.host_offline(session)
        await self._edit_lobby(session, offline)
        self._last_sig = lobby_signature(session, offline)
        self._last_message_id = session.get("message_id")

    async def handle_verify(self, interaction: discord.Interaction):
        link = await self.service.oauth_link_for_discord(interaction.user.id)
        if link:
            name = link.get("username") or "your Roblox account"
            return await interaction.response.send_message(
                f"You are already verified as **{name}**. Press Join Private Server. "
                "(Wrong account? Run /link_roblox to link a different one.)",
                ephemeral=True,
            )
        login_link = await build_login_link(self.bot.db, interaction.user.id)
        if not login_link:
            return await interaction.response.send_message("Roblox linking is not configured yet. Tell an admin.", ephemeral=True)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Login with Roblox", style=discord.ButtonStyle.link, url=login_link))
        await interaction.response.send_message(
            "Click below to log in with Roblox and verify. This link expires in 10 minutes and only works for you. "
            "Once it says you are linked, press Join Private Server.",
            view=view,
            ephemeral=True,
        )

    async def handle_pick(self, interaction: discord.Interaction, player_id: int):
        session = await self.service.get_session()
        draft = (session or {}).get("draft")
        if not session or not session.get("active") or not draft or draft.get("done") or not draft.get("turn"):
            return await interaction.response.send_message("There is no pick in progress.", ephemeral=True)
        team = draft["turn"]
        captain = (draft.get("captains") or {}).get(team) or {}
        allowed = await self._authorized(interaction)  # staff can pick for an absent captain
        if not allowed and captain.get("id"):
            allowed = await self.service.discord_id_for_roblox(captain["id"]) == interaction.user.id
        if not allowed:
            return await interaction.response.send_message(
                f"Only the {team} captain ({captain.get('name') or '?'}) can pick right now.", ephemeral=True
            )
        ok, reason, _ = await self.service.make_pick(player_id, team)
        if not ok:
            return await interaction.response.send_message(reason, ephemeral=True)
        await interaction.response.defer()
        await self._refresh_lobby()

    @tasks.loop(seconds=0.5)
    async def bridge_loop(self):
        """Answers script syncs that the clips site relayed through Mongo (no public bot URL needed)."""
        try:
            await drain_inbox(self.bot.db.db, self.process_scrim_sync)
        except Exception:
            traceback.print_exc()

    @bridge_loop.before_loop
    async def _before_bridge_loop(self):
        await self.bot.wait_until_ready()
        await ensure_indexes(self.bot.db.db)

    @tasks.loop(seconds=1.0)
    async def draft_loop(self):
        try:
            if self._in_draft and await self.service.expire_pick():
                await self._refresh_lobby()
        except Exception:
            traceback.print_exc()

    @draft_loop.before_loop
    async def _before_draft_loop(self):
        await self.bot.wait_until_ready()

    async def _ping_staff(self, session: dict):
        channel = await self._get_channel(session.get("channel_id"))
        doc = await self.bot.db.db.config.find_one({"_id": "api_keys"}) or {}
        role_id = Config.HEAD_OBSERVER_ROLE_ID or int(doc.get("HEAD_OBSERVER_ID") or 0)
        mention = f"<@&{role_id}>" if role_id else ""
        if channel:
            await channel.send(f"{mention} Scrim host script went offline.".strip(), allowed_mentions=discord.AllowedMentions(roles=True))


async def setup(bot):
    await bot.add_cog(Scrim(bot))
