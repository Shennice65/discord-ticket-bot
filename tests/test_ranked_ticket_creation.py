import unittest
from unittest.mock import MagicMock
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
        self.assertIn("Elites 4", embed.description)
        if file:
            file.close()

if __name__ == "__main__":
    unittest.main()
