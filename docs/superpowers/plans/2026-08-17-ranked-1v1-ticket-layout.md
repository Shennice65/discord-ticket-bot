# Ranked 1v1 Ticket Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the new Ranked 1v1 ticket embed layout with a 3-column monospace table (`[Player]`, `[Rank]`, `[Winrate]`), author header, Challenger tier badge thumbnail from `assets/tiers/`, and an observer reminder footer.

**Architecture:** Add a dedicated embed generator `create_ranked_1v1_ticket_embed` in `utils/embeds.py` that formats player stats into an aligned monospace table and attaches the rank tier image from `assets/tiers/`. Integrate this generator into `create_ranked_ticket` and `_finalize_out_of_range_ticket` in `cogs/tickets/core.py`.

**Tech Stack:** Python 3.10+, discord.py v2.0+

## Global Constraints
- Python 3.10+ compatible syntax
- Table formatting using monospace codeblock with dynamic column width padding
- Tier emblem resolved dynamically from `assets/tiers/{tier.lower()}.png` with safe fallback
- Footer text: `Wait for an observer to referee your match before starting.`

---

### Task 1: Add Ranked 1v1 Embed Generator to `utils/embeds.py`

**Files:**
- Modify: `utils/embeds.py`
- Test: `tests/test_ranked_embed.py`

**Interfaces:**
- Consumes: `user` (discord.Member), `opponent_name` (str), `u_rank` (str), `o_rank` (str), `u_rate` (float), `o_rate` (float)
- Produces: `TicketEmbeds.create_ranked_1v1_ticket_embed(...) -> tuple[discord.Embed, Optional[discord.File]]`

- [ ] **Step 1: Write unit test for the embed generator**

Create `tests/test_ranked_embed.py`:
```python
import unittest
from unittest.mock import MagicMock
import os
import discord
from utils.embeds import TicketEmbeds

class TestRankedEmbed(unittest.TestCase):
    def test_table_alignment_and_content(self):
        user = MagicMock()
        user.display_name = "wolfboss5213"
        user.display_avatar.url = "https://example.com/avatar.png"
        
        embed, file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=user,
            opponent_name="opponent_player",
            u_rank="Champions 1",
            o_rank="Champions 3",
            u_rate=75.0,
            o_rate=60.0
        )
        
        self.assertEqual(embed.author.name, "wolfboss5213")
        self.assertEqual(embed.footer.text, "Wait for an observer to referee your match before starting.")
        self.assertIn("[Player]", embed.description)
        self.assertIn("[Rank]", embed.description)
        self.assertIn("[Winrate]", embed.description)
        self.assertIn("wolfboss5213", embed.description)
        self.assertIn("Champions 1", embed.description)
        self.assertIn("75.0%", embed.description)
        self.assertIn("opponent_player", embed.description)
        self.assertIn("Champions 3", embed.description)
        self.assertIn("60.0%", embed.description)

    def test_missing_tier_fallback(self):
        user = MagicMock()
        user.display_name = "new_player"
        user.display_avatar.url = "https://example.com/avatar.png"
        
        embed, file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=user,
            opponent_name="other_player",
            u_rank="Unranked",
            o_rank="Unranked",
            u_rate=0.0,
            o_rate=0.0
        )
        
        self.assertEqual(embed.author.name, "new_player")
        self.assertIsNone(file)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests/test_ranked_embed.py`
Expected: FAIL with AttributeError: `type object 'TicketEmbeds' has no attribute 'create_ranked_1v1_ticket_embed'`

- [ ] **Step 3: Implement `create_ranked_1v1_ticket_embed` in `utils/embeds.py`**

Add method in `utils/embeds.py`:
```python
    @staticmethod
    def create_ranked_1v1_ticket_embed(user: discord.Member, opponent_name: str,
                                       u_rank: str, o_rank: str,
                                       u_rate: float, o_rate: float) -> tuple[discord.Embed, Optional[discord.File]]:
        from utils.ranking_utils import parse_rank
        import os

        embed = discord.Embed(
            color=discord.Color(0x2b2d31),
            timestamp=datetime.utcnow()
        )
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url if hasattr(user, 'display_avatar') else None)

        # Truncate names if excessively long
        p1_name = user.display_name[:15]
        p2_name = opponent_name[:15]
        p1_rate = f"{u_rate:.1f}%"
        p2_rate = f"{o_rate:.1f}%"

        # Dynamic column padding
        col1_w = max(len("[Player]"), len(p1_name), len(p2_name)) + 1
        col2_w = max(len("[Rank]"), len(u_rank), len(o_rank)) + 1
        col3_w = max(len("[Winrate]"), len(p1_rate), len(p2_rate)) + 1

        h_col1 = "[Player]".ljust(col1_w)
        h_col2 = "[Rank]".ljust(col2_w)
        h_col3 = "[Winrate]".ljust(col3_w)

        r1_col1 = p1_name.ljust(col1_w)
        r1_col2 = u_rank.ljust(col2_w)
        r1_col3 = p1_rate.ljust(col3_w)

        r2_col1 = p2_name.ljust(col1_w)
        r2_col2 = o_rank.ljust(col2_w)
        r2_col3 = p2_rate.ljust(col3_w)

        table = (
            f"```text\n"
            f"│ {h_col1} │ {h_col2} │ {h_col3} │\n"
            f"│ {r1_col1} │ {r1_col2} │ {r1_col3} │\n"
            f"│ {r2_col1} │ {r2_col2} │ {r2_col3} │\n"
            f"```"
        )
        embed.description = table
        embed.set_footer(text="Wait for an observer to referee your match before starting.")

        # Resolve tier thumbnail
        parsed = parse_rank(u_rank)
        tier_file = None
        if parsed:
            tier_name = parsed[0].lower()
            tier_path = os.path.join("assets", "tiers", f"{tier_name}.png")
            if os.path.exists(tier_path):
                tier_file = discord.File(tier_path, filename="tier.png")
                embed.set_thumbnail(url="attachment://tier.png")

        return embed, tier_file
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests/test_ranked_embed.py`
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add utils/embeds.py tests/test_ranked_embed.py
git commit -m "feat: add ranked 1v1 ticket embed generator with table layout and tier thumbnail"
```

---

### Task 2: Integrate Embed Generator into `cogs/tickets/core.py`

**Files:**
- Modify: `cogs/tickets/core.py:125-145,162-185`
- Test: `tests/test_ranked_ticket_creation.py`

**Interfaces:**
- Consumes: `TicketEmbeds.create_ranked_1v1_ticket_embed`
- Produces: Updated `create_ranked_ticket` and `_finalize_out_of_range_ticket` flows with `file=tier_file` in `channel.send`

- [ ] **Step 1: Write integration test for ticket creation embed dispatch**

Create `tests/test_ranked_ticket_creation.py`:
```python
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from utils.embeds import TicketEmbeds

class TestRankedTicketCreation(unittest.TestCase):
    def test_embed_generation_in_ticket_flow(self):
        user = MagicMock()
        user.display_name = "PlayerOne"
        user.display_avatar.url = "https://example.com/avatar.png"
        
        embed, file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=user,
            opponent_name="PlayerTwo",
            u_rank="Elites 2",
            o_rank="Elites 4",
            u_rate=50.0,
            o_rate=50.0
        )
        self.assertIsNotNone(embed)
        self.assertIn("PlayerOne", embed.description)
        self.assertIn("PlayerTwo", embed.description)
        self.assertIn("Elites 2", embed.description)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Update `cogs/tickets/core.py`**

In `create_ranked_ticket`:
```python
        embed, tier_file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=user,
            opponent_name=opponent.name,
            u_rank=u_rank,
            o_rank=o_rank,
            u_rate=u_rate,
            o_rate=o_rate
        )
        
        send_kwargs = {"content": f"{user.mention} {opponent_member.mention} {observer_mention}", "embed": embed}
        if tier_file:
            send_kwargs["file"] = tier_file

        await channel.send(**send_kwargs)
```

In `_finalize_out_of_range_ticket`:
```python
        embed, tier_file = TicketEmbeds.create_ranked_1v1_ticket_embed(
            user=requester,
            opponent_name=opponent.name,
            u_rank=u_rank,
            o_rank=o_rank,
            u_rate=u_rate,
            o_rate=o_rate
        )
        embed.add_field(
            name="Out-of-Range Match",
            value="This match was accepted outside the 5-rank window.",
            inline=False
        )

        send_kwargs = {"content": f"{requester.mention} {opponent.mention} {observer_mention}", "embed": embed}
        if tier_file:
            send_kwargs["file"] = tier_file

        await channel.send(**send_kwargs)
```

- [ ] **Step 3: Run integration test and python compile checks**

Run:
`python -m unittest discover tests`
`python -m py_compile utils/embeds.py cogs/tickets/core.py`

- [ ] **Step 4: Commit changes**

```bash
git add cogs/tickets/core.py tests/test_ranked_ticket_creation.py
git commit -m "feat: hook new ranked 1v1 embed and tier thumbnail into ticket creation"
```
