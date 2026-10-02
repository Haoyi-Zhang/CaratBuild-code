from __future__ import annotations

import unittest

from carat.decode import decode
from carat.emulator import FaultProfile, run_emulation
from carat.facts import canonical_text, mint, parse_text
from carat.generate import mixed_history
from carat.independent_check import check
from carat.protocol import Replica, states_equal


class ProtocolTests(unittest.TestCase):
    def test_fault_campaigns_converge(self) -> None:
        scenario = mixed_history(130, 7, 12, rewrite_rounds=3)
        profiles = (
            FaultProfile("clean", heal_round=0),
            FaultProfile("partition", partition_until=14, heal_round=14),
            FaultProfile(
                "delay-duplicate-reorder",
                delay_max=5,
                duplicate_probability=0.35,
                reorder=True,
                heal_round=20,
            ),
            FaultProfile(
                "crash-loss",
                partition_until=9,
                delay_max=3,
                drop_probability=0.25,
                crash_node="n2",
                crash_start=5,
                crash_end=18,
                reorder=True,
                heal_round=22,
            ),
        )
        for index, profile in enumerate(profiles):
            with self.subTest(profile=profile.name):
                run = run_emulation(scenario.facts, profile, salt=index)
                self.assertTrue(run.converged, run.as_dict())
                self.assertGreater(run.summary_messages_sent, 0)
                self.assertGreater(run.fact_messages_sent, 0)
                self.assertEqual(
                    run.messages_sent,
                    run.summary_messages_sent + run.fact_messages_sent,
                )
                self.assertTrue(states_equal(run.replicas))
                values = [decode(replica.facts) for replica in run.replicas]
                checks = [check(replica.facts) for replica in run.replicas]
                self.assertTrue(all(value.determined for value in values))
                self.assertEqual({value.gross_mass for value in values}, {scenario.oracle_mass})
                self.assertTrue(all(value.accepted for value in checks))
                self.assertEqual({value.gross_mass for value in checks}, {scenario.oracle_mass})

        # More than the 64-interval summary cap must drain progressively when the
        # honest origin allocated a contiguous coordinate stream.  The receiver
        # initially has odd coordinates only, leaving 70 one-item gaps below its
        # maximum; successive summaries expose the remaining gaps after earlier
        # ones fill.
        source = Replica("n0")
        receiver = Replica("n1")
        for sequence in range(1, 143):
            text = canonical_text(
                mint(
                    f"n0:{sequence}",
                    f"interval-unit-{sequence}",
                    1,
                    "n0",
                    "interval-batch",
                )
            )
            source.append_text(text)
            if sequence % 2:
                receiver.append_text(text)
        rounds = 0
        while source.facts != receiver.facts and rounds < 4:
            payload = source.delta_for(receiver.summary(interval_limit=64))
            self.assertTrue(payload)
            for text in payload:
                receiver.append_text(text)
            rounds += 1
        self.assertEqual(source.facts, receiver.facts)
        self.assertEqual(rounds, 2)


    def test_permanent_prefix_holes_can_starve_later_coordinates(self) -> None:
        source = Replica("n0")
        receiver = Replica("n1")
        retained_late = {130, 132, 134, 136, 138, 140}
        for sequence in range(1, 142):
            text = canonical_text(
                mint(
                    f"n0:{sequence}",
                    f"hole-unit-{sequence}",
                    1,
                    "n0",
                    "hole-batch",
                )
            )
            if sequence % 2:
                source.append_text(text)
                receiver.append_text(text)
            elif sequence in retained_late:
                source.append_text(text)

        for _ in range(3):
            for text in source.delta_for(receiver.summary(interval_limit=64)):
                receiver.append_text(text)
        self.assertEqual(len(source.facts - receiver.facts), 6)
        self.assertEqual(
            {parse_text(text)["event"] for text in source.facts - receiver.facts},
            {f"n0:{value}" for value in retained_late},
        )


if __name__ == "__main__":
    unittest.main()
