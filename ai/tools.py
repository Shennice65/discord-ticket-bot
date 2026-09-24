import logging
from config import Config

logger = logging.getLogger(__name__)


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _tool(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


class ReadOnlyToolRegistry:
    """Local, permission-aware tools exposed to the OpenRouter agent."""

    def __init__(self, bot, context_builder):
        self.bot = bot
        self.context_builder = context_builder
        self.plugin_tools = {
            name: value for name, value in getattr(getattr(bot, "plugin_registry", None), "tools", {}).items()
            if value.get("read_only", False)
        }
        self._definitions = [
            _tool(
                "get_player_rank",
                "Read the authoritative current rank and Discord display name for a user. Never treat the rank label as the person's name.",
                {"user_id": {"type": "integer", "description": "Discord user ID"}},
                ("user_id",),
            ),
            _tool(
                "get_player_profile",
                "Read a player's comprehensive profile including rank, lifetime win rate, win/loss streak, nemesis, recent matches, and community memories. Use this to form opinions or roasts.",
                {"user_id": {"type": "integer", "description": "Discord user ID"}},
                ("user_id",),
            ),
            _tool(
                "get_player_history",
                "Read a player's recent ranked and personal-observation history.",
                {
                    "user_id": {"type": "integer", "description": "Discord user ID"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ("user_id",),
            ),
            _tool(
                "get_leaderboard",
                "Read the current authoritative player ladder, including each player's Discord display name and rank.",
                {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            ),
            _tool(
                "get_recent_tickets",
                "Read recent closed or active tickets for a Discord user.",
                {
                    "user_id": {"type": "integer", "description": "Discord user ID"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ("user_id",),
            ),
            _tool(
                "get_server_rules",
                "Return the authoritative rules and workflows for this Discord server.",
                {},
            ),
            _tool(
                "search_server_lore",
                "Search community memories, lore, inside jokes, and slang. Use this to find out what terms mean, who people are, or to remember past events.",
                {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
                ("query",),
            ),
            _tool(
                "search_clips",
                "Read clips saved by a Discord user.",
                {"user_id": {"type": "integer", "description": "Discord user ID"}},
                ("user_id",),
            ),
            _tool(
                "get_gif_for_context",
                "Fetch a community-learned GIF URL matching a specific emotional context. Call this when you want to include a reaction GIF in your response. Valid tags: roast, hype, sadness, laugh, win, loss, reaction, greeting, flex, confused, cringe.",
                {"context_tag": {"type": "string", "description": "The emotional context tag (e.g., 'roast', 'hype', 'laugh')"}},
                ("context_tag",),
            ),
        ]
        self._definitions.append(
            _tool(
                "search_web",
                "Search the internet for factual information (e.g. Roblox game mechanics, updates, lore, general facts).",
                {"query": {"type": "string", "description": "The search query"}},
                ("query",),
            )
        )
        self._definitions.extend(
            value["definition"] for value in self.plugin_tools.values()
        )

    @property
    def definitions(self):
        return list(self._definitions)

    async def execute(self, name, arguments, message, context):
        handlers = {
            "get_player_profile": self._get_player_profile,
            "get_player_rank": self._get_player_rank,
            "get_player_history": self._get_player_history,
            "get_leaderboard": self._get_leaderboard,
            "get_recent_tickets": self._get_recent_tickets,
            "get_server_rules": self._get_server_rules,
            "search_server_lore": self._search_server_lore,
            "search_clips": self._search_clips,
            "search_web": self._search_web,
            "get_gif_for_context": self._get_gif_for_context,
        }
        if name in self.plugin_tools:
            handler = self.plugin_tools[name]["handler"]
            try:
                return _json_value(await handler(arguments or {}, message, context))
            except Exception as error:
                logger.warning("Plugin AI tool failed tool=%s error=%s", name, type(error).__name__)
                return {"error": "The requested plugin lookup is temporarily unavailable."}
        handler = handlers.get(name)
        if handler is None:
            return {"error": "Unknown or disabled tool."}
        try:
            result = await handler(arguments or {}, message, context)
            return _json_value(result)
        except Exception as error:
            logger.warning("Read-only AI tool failed tool=%s error=%s", name, type(error).__name__)
            return {"error": "The requested server lookup is temporarily unavailable."}

    async def _get_player_rank(self, args, message, _context):
        user_id = int(args["user_id"])
        rank = await self.bot.db.get_player_rank(user_id)
        member = message.guild.get_member(user_id) if getattr(message, "guild", None) else None
        player_name = (
            getattr(member, "display_name", None)
            or getattr(member, "name", None)
            or f"Discord user {user_id}"
        )
        return {
            "user_id": user_id,
            "player_name": player_name,
            "player_mention": f"<@{user_id}>",
            "rank": rank or "Unranked",
        }

    async def _get_player_profile(self, args, message, _context):
        user_id = int(args["user_id"])
        member = message.guild.get_member(user_id) if getattr(message, "guild", None) else None
        player_name = (
            getattr(member, "display_name", None)
            or getattr(member, "name", None)
            or f"Discord user {user_id}"
        )
        
        # 1. Rank
        rank = await self.bot.db.get_player_rank(user_id)
        
        # 2. Stats & Nemesis
        stats = await self.bot.db.get_player_profile_stats(user_id, player_name)
        
        # 3. Recent Matches (limit 3)
        history = await self.bot.db.get_user_history(user_id, player_name, limit=3)
        recent = []
        for match in history.get("ranked", []):
            is_win = (match.get("winner_id") == user_id or 
                     (str(match.get("winner", "")).lower() == player_name.lower() and player_name))
            recent.append({
                "opponent": match.get("opponent_name", "Unknown"),
                "result": "Win" if is_win else "Loss"
            })
            
        # 4. Lore/Memories about this player
        query = player_name
        lore = await self._search_server_lore({"query": query}, message, _context)
        
        return {
            "user_id": user_id,
            "player_name": player_name,
            "current_rank": rank or "Unranked",
            "lifetime_stats": stats,
            "last_3_matches": recent,
            "community_memories": lore.get("memories", [])
        }

    async def _get_player_history(self, args, message, _context):
        user_id = int(args["user_id"])
        member = message.guild.get_member(user_id) if message.guild else None
        user_name = getattr(member, "display_name", "")
        limit = max(1, min(int(args.get("limit", 5)), 10))
        history = await self.bot.db.get_user_history(user_id, user_name, limit=limit)
        return {
            "user_id": user_id,
            "ranked": history.get("ranked", [])[:limit],
            "observations": history.get("observations", [])[:limit],
        }

    async def _get_leaderboard(self, args, message, _context):
        limit = max(1, min(int(args.get("limit", 10)), 20))
        players = await self.bot.db.get_all_player_ranks()
        from utils.ladder_utils import get_sort_key

        ranked = [
            player for player in players
            if get_sort_key(player.get("rank", ""))[0] != 99
        ]
        ranked.sort(key=lambda player: get_sort_key(player.get("rank", "")))
        guild = getattr(message, "guild", None)

        def display_name(player):
            user_id = player.get("user_id")
            member = None
            if guild and user_id is not None:
                try:
                    member = guild.get_member(int(user_id))
                except (TypeError, ValueError):
                    pass
            return (
                getattr(member, "display_name", None)
                or player.get("name")
                or f"Discord user {user_id}"
            )

        return {
            "players": [
                f"{display_name(player)} (<@{player.get('user_id')}>) - Rank: {player.get('rank', 'Unranked')}"
                for player in ranked[:limit]
            ]
        }

    async def _get_recent_tickets(self, args, message, _context):
        user_id = int(args["user_id"])
        limit = max(1, min(int(args.get("limit", 5)), 10))
        query = {"$or": [{"user_id": user_id}, {"opponent_id": user_id}]}
        if not message.guild or not getattr(self.bot.db, "tickets", None):
            return {"tickets": []}
        cursor = self.bot.db.tickets.find(query).sort("created_at", -1).limit(limit)
        tickets = await cursor.to_list(length=limit)
        return {"tickets": tickets}

    async def _get_server_rules(self, _args, _message, _context):
        return {
            "game": "Timebomb Duels",
            "workflows": [
                "Ranked 1v1 requests go through the Ranked 1v1 ticket.",
                "Personal observations go through the Personal Observation ticket.",
                "Observers spectate matches and record official results and rank changes.",
                "Current rank answers must come from the player-rank database.",
            ],
            "constraint": "When authoritative data is unavailable, say that it could not be verified.",
        }

    async def _search_server_lore(self, args, message, context):
        from config import Config
        query = str(args.get("query", "")).casefold().strip()
        current = context.current
        records = self.context_builder.retriever.memory_cache.get(current.guild_id, [])
        if not records:
            return {"memories": []}
            
        ignored_channels_str = await self.bot.db.get_setting("AI_IGNORED_MEMORY_CHANNELS", "")
        ignored_channels = {
            int(cid.strip()) for cid in str(ignored_channels_str).split(",")
            if cid.strip().isdigit()
        }
        
        channel_id = getattr(message.channel, "id", None)
        if channel_id in ignored_channels:
            return {"memories": []}

        words = {word for word in query.split() if len(word) >= 3}
        matches = []
        for record in records:
            rec_channel_id = record.get("channel_id")
            if rec_channel_id is not None and int(rec_channel_id) in ignored_channels:
                continue
            searchable = " ".join(str(record.get(field, "")) for field in (
                "summary", "memory_key", "associated_users"
            )).casefold()
            if words and not any(word in searchable for word in words):
                continue
            enriched_record = dict(record)
            source_ids = enriched_record.get("source_message_ids")
            if source_ids and current.guild_id and enriched_record.get("channel_id"):
                enriched_record["discord_jump_url"] = f"https://discord.com/channels/{current.guild_id}/{enriched_record['channel_id']}/{source_ids[0]}"
            matches.append(enriched_record)
        matches.sort(
            key=lambda rec: (
                float(rec.get("importance", 0) or 0),
                float(rec.get("confidence", 0) or 0),
            ),
            reverse=True,
        )
        return {"memories": matches[:5]}

    async def _search_clips(self, args, _message, _context):
        user_id = int(args["user_id"])
        clips = await self.bot.db.get_user_clips(user_id)
        return {"user_id": user_id, "clips": clips[:10]}

    async def _search_web(self, args, _message, _context):
        query = args.get("query")
        if not query:
            return {"error": "Missing query"}
        try:
            import asyncio
            from duckduckgo_search import DDGS
            def _do_search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=3))
            results = await asyncio.to_thread(_do_search)
            if not results:
                return {"message": "No results found"}
            
            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "snippet": str(r.get("body", ""))[:300],
                    "url": r.get("href", "")
                })
            return {"results": formatted}
        except Exception as e:
            return {"error": f"Search failed: {str(e)}"}

    async def _get_gif_for_context(self, args, message, _context):
        context_tag = args.get("context_tag", "reaction")
        try:
            guild_id = message.guild.id if getattr(message, "guild", None) else getattr(_context.current, "guild_id", None)
            if not guild_id:
                return {"found": False, "message": "Cannot determine guild context for GIF search."}
            gif_data = await self.bot.db.get_random_gif(guild_id, context_tag)
            if gif_data and "url" in gif_data:
                import asyncio
                asyncio.create_task(self.bot.db.increment_gif_usage(guild_id, gif_data["url"]))
                return {"gif_url": gif_data["url"], "found": True}
            else:
                return {"found": False, "message": f"No community GIFs found for context: {context_tag}"}
        except Exception as e:
            return {"error": str(e)}
