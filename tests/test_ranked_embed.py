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
        self.assertIsNotNone(file)
        self.assertEqual(file.filename, "tier.png")
        self.assertEqual(embed.thumbnail.url, "attachment://tier.png")
        file.close()

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
