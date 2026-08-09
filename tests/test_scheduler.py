import unittest
from unittest import mock

from src import scheduler


class SchedulerTests(unittest.TestCase):
    def test_existing_keys_queries_each_real_task(self) -> None:
        present = {"daily", "onunlock"}

        with mock.patch("src.scheduler.exists", side_effect=lambda key: key in present) as exists:
            result = scheduler.existing_keys()

        self.assertEqual(result, present)
        self.assertEqual({call.args[0] for call in exists.call_args_list}, set(scheduler.TASKS))

