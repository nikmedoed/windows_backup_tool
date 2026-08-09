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

    def test_trigger_time_reads_start_boundary_from_task_xml(self) -> None:
        xml = """<?xml version="1.0"?>
        <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Triggers><CalendarTrigger><StartBoundary>2026-08-09T17:45:00</StartBoundary></CalendarTrigger></Triggers>
        </Task>"""
        result = mock.Mock(returncode=0, stdout=xml.encode("utf-8"))

        with mock.patch("src.scheduler.subprocess.run", return_value=result):
            self.assertEqual(scheduler.trigger_time("daily"), "17:45")

    def test_trigger_details_reads_weekday_from_task_xml(self) -> None:
        xml = """<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Triggers><CalendarTrigger><StartBoundary>2026-08-09T08:15:00</StartBoundary>
          <ScheduleByWeek><DaysOfWeek><Thursday /></DaysOfWeek></ScheduleByWeek>
          </CalendarTrigger></Triggers></Task>"""
        result = mock.Mock(returncode=0, stdout=xml.encode("utf-8"))

        with mock.patch("src.scheduler.subprocess.run", return_value=result):
            self.assertEqual(
                scheduler._trigger_details("weekly"),
                {"time": "08:15", "weekday": "THU"},
            )

    def test_schedule_replaces_default_time(self) -> None:
        commands = []

        with (
            mock.patch("src.scheduler.exists", return_value=False),
            mock.patch("src.scheduler._run", side_effect=lambda command: commands.append(command)),
        ):
            scheduler.schedule("weekly", start_time="21:30", weekday="SAT", allow_on_battery=False)

        create = commands[0]
        self.assertEqual(create[create.index("/ST") + 1], "21:30")
        self.assertEqual(create[create.index("/D") + 1], "SAT")

    def test_schedule_rejects_invalid_time(self) -> None:
        with (
            mock.patch("src.scheduler.exists") as exists,
            mock.patch("src.scheduler.delete") as delete,
        ):
            with self.assertRaises(ValueError):
                scheduler.schedule("daily", start_time="27:99", allow_on_battery=False)
        exists.assert_not_called()
        delete.assert_not_called()

    def test_schedule_rejects_options_unsupported_by_trigger(self) -> None:
        with mock.patch("src.scheduler._run") as run:
            with self.assertRaises(ValueError):
                scheduler.schedule("onlogon", start_time="03:00")
        run.assert_not_called()
