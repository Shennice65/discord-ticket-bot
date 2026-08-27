import unittest

from utils.ticket_utils import validate_and_format_rank


class TicketRankInputTests(unittest.TestCase):
    def test_master_singular_is_accepted(self):
        self.assertEqual("Masters 26", validate_and_format_rank("master 26"))

    def test_masters_plural_is_accepted(self):
        self.assertEqual("Masters 26", validate_and_format_rank("masters 26"))


if __name__ == "__main__":
    unittest.main()
