import unittest

from prediction_arena import _answer, _score


class PredictionScoringTests(unittest.TestCase):
    def test_winner_normalizes_ukrainian_labels(self):
        self.assertEqual(_answer("winner", "Мирні"), "peaceful")
        self.assertEqual(_answer("winner", "Мафія"), "mafia")

    def test_winner_reward(self):
        self.assertEqual(_score("winner", "peaceful", "peaceful"), (0, 50))
        self.assertEqual(_score("winner", "mafia", "peaceful"), (0, 0))

    def test_player_number_validation(self):
        self.assertEqual(_answer("first_killed", "10"), 10)
        with self.assertRaises(ValueError):
            _answer("first_exiled", 11)

    def test_best_move_scoring_ignores_order(self):
        self.assertEqual(_score("best_move", [8, 2, 5], [2, 5, 8]), (3, 100))
        self.assertEqual(_score("best_move", [1, 2, 3], [3, 2, 9]), (2, 60))
        self.assertEqual(_score("best_move", [1, 2, 3], [3, 4, 9]), (1, 20))
        self.assertEqual(_score("best_move", [1, 2, 3], [4, 5, 9]), (0, 0))

    def test_best_move_requires_three_distinct_numbers(self):
        with self.assertRaises(ValueError):
            _answer("best_move", [1, 1, 2])
        with self.assertRaises(ValueError):
            _answer("best_move", [1, 2])


if __name__ == "__main__":
    unittest.main()
