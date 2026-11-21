import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("observer", Path(__file__).resolve().parents[1] / "scripts/observe.py")
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class ObservationTest(unittest.TestCase):
    def test_recovery_and_replayed_logs_count_once(self):
        state, seen = o.initial(), set()
        lines = ["2026-11-21T06:36:57.614597488Z [Yeelighter] [0x123] Disconnected []", "2026-11-21T06:37:02.629855988Z [Yeelighter] [0x123] ceiling20 Connected []"]
        o.events(lines, state, seen)
        o.events(lines, state, seen)
        self.assertEqual(state["disconnect_count"], {"0x123": 1})
        self.assertEqual(state["recoveries"][0]["seconds"], 5.015)
        self.assertEqual(state["disconnected_since"], {})

    def test_initial_connection_is_not_a_recovery(self):
        state = o.initial()
        o.events(["2026-11-21T06:37:02Z [Yeelighter] [0x123] ceiling20 Connected []"], state, set())
        self.assertEqual(state["disconnect_count"], {})
        self.assertEqual(state["recoveries"], [])


if __name__ == "__main__":
    unittest.main()
