"""End-to-end experiment driver for the CARAT artifact."""

from __future__ import annotations

import csv
from dataclasses import asdict
import itertools
import json
from pathlib import Path
import resource
import statistics
import time
from typing import Any, Callable, Iterable

from .baselines import arrival_order_branch_mass, commit_count, presentation_mass
from .compaction import compact_closed_batches
from .decode import decode
from .emulator import FaultProfile, run_emulation
from .facts import Fact, canonical_text
from .generate import (
    amplification_history,
    ambiguity_variant,
    masses_from_profile,
    mixed_history,
    operation_sequence_history,
    recoordinate,
)
from .independent_check import check, verify_witness


def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter_ns()
    value = call()
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    return value, elapsed_ms


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"no rows for {path}")
    names = fields or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _median(values: Iterable[float]) -> float:
    data = list(values)
    return statistics.median(data) if data else 0.0


def _quantile(values: Iterable[float], fraction: float) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    index = round((len(data) - 1) * fraction)
    return data[index]


def load_profiles(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("public history input is empty")
    return data


def run_manipulation(profiles: list[dict[str, Any]], raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    operations = ("rebase", "cherry_pick", "fork", "squash", "revert")
    copy_counts = (0, 1, 2, 4, 8, 16)
    for profile_index, profile in enumerate(profiles):
        changed = int(profile["added_lines"]) + int(profile["removed_lines"])
        masses = masses_from_profile(changed, int(profile["files_changed"]), profile_index)
        for operation in operations:
            for copies in copy_counts:
                for repetition in range(3):
                    salt = profile_index * 1000 + copies * 17 + repetition
                    scenario = amplification_history(masses, copies, operation, salt=salt)
                    decoded, decode_ms = _timed(lambda: decode(scenario.facts))
                    checked, checker_ms = _timed(lambda: check(scenario.facts))
                    transform_facts = [
                        fact for fact in scenario.facts if fact["kind"] == "transform"
                    ]
                    max_inputs = max(
                        (len(fact["inputs"]) for fact in transform_facts), default=0
                    )
                    max_outputs = max(
                        (len(fact["outputs"]) for fact in transform_facts), default=0
                    )
                    structure_ok = True
                    if operation == "fork" and copies >= 2:
                        structure_ok = len(transform_facts) == 1 and max_outputs == copies
                    elif operation == "squash" and copies >= 1:
                        structure_ok = (
                            len(transform_facts) == copies
                            and min(len(fact["inputs"]) for fact in transform_facts) == 2
                            and max_inputs == 2
                        )
                    if (
                        not decoded.determined
                        or not checked.accepted
                        or decoded.gross_mass != scenario.oracle_mass
                        or checked.gross_mass != scenario.oracle_mass
                        or not structure_ok
                    ):
                        raise AssertionError(
                            (
                                operation,
                                copies,
                                decoded.witnesses,
                                checked.violations,
                                max_inputs,
                                max_outputs,
                            )
                        )
                    rows.append(
                        {
                            "sample": profile["sample"],
                            "operation": operation,
                            "copies": copies,
                            "repetition": repetition,
                            "oracle_mass": scenario.oracle_mass,
                            "carat_mass": decoded.gross_mass,
                            "carat_ratio": f"{decoded.gross_mass / scenario.oracle_mass:.6f}",
                            "presentation_count_ratio": f"{commit_count(scenario.facts) / scenario.base_presentations:.6f}",
                            "presentation_mass_ratio": f"{presentation_mass(scenario.facts) / scenario.oracle_mass:.6f}",
                            "decode_ms": f"{decode_ms:.6f}",
                            "checker_ms": f"{checker_ms:.6f}",
                            "max_transform_inputs": max_inputs,
                            "max_transform_outputs": max_outputs,
                        }
                    )
    _write_csv(raw_dir / "manipulation.csv", rows)

    summary_rows: list[dict[str, Any]] = []
    for copies in copy_counts:
        selected = [row for row in rows if int(row["copies"]) == copies]
        summary_rows.append(
            {
                "copies": copies,
                "carat_ratio_median": f"{_median(float(row['carat_ratio']) for row in selected):.3f}",
                "presentation_count_ratio_median": f"{_median(float(row['presentation_count_ratio']) for row in selected):.3f}",
                "presentation_mass_ratio_median": f"{_median(float(row['presentation_mass_ratio']) for row in selected):.3f}",
                "carat_ratio_max": f"{max(float(row['carat_ratio']) for row in selected):.3f}",
            }
        )
    _write_csv(summary_dir / "manipulation_summary.csv", summary_rows)
    return {
        "runs": len(rows),
        "maximum_copies": max(copy_counts),
        "maximum_carat_ratio": max(float(row["carat_ratio"]) for row in rows),
        "maximum_count_ratio": max(float(row["presentation_count_ratio"]) for row in rows),
        "maximum_presentation_mass_ratio": max(
            float(row["presentation_mass_ratio"]) for row in rows
        ),
        "fork_multi_output_runs": sum(
            row["operation"] == "fork"
            and int(row["copies"]) >= 2
            and int(row["max_transform_outputs"]) == int(row["copies"])
            for row in rows
        ),
        "squash_multi_input_runs": sum(
            row["operation"] == "squash"
            and int(row["copies"]) >= 1
            and int(row["max_transform_inputs"]) == 2
            for row in rows
        ),
    }


def run_faults(profiles: list[dict[str, Any]], raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    fault_profiles = (
        FaultProfile("clean", heal_round=0),
        FaultProfile("partition", partition_until=12, heal_round=12),
        FaultProfile(
            "delay-reorder",
            delay_max=5,
            reorder=True,
            heal_round=18,
        ),
        FaultProfile(
            "duplicate-loss",
            delay_max=3,
            duplicate_probability=0.35,
            drop_probability=0.25,
            reorder=True,
            heal_round=20,
        ),
        FaultProfile(
            "combined",
            partition_until=10,
            delay_max=5,
            duplicate_probability=0.25,
            drop_probability=0.20,
            crash_node="n2",
            crash_start=4,
            crash_end=17,
            reorder=True,
            heal_round=22,
        ),
    )
    rows: list[dict[str, Any]] = []
    for profile_index, public_profile in enumerate(profiles):
        changed = int(public_profile["added_lines"]) + int(public_profile["removed_lines"])
        for repetition in range(4):
            scenario = mixed_history(
                changed + repetition * 11,
                int(public_profile["files_changed"]),
                history_index=profile_index * 20 + repetition,
                rewrite_rounds=2 + repetition % 2,
            )
            for fault_index, profile in enumerate(fault_profiles):
                run, elapsed_ms = _timed(
                    lambda p=profile: run_emulation(
                        scenario.facts,
                        p,
                        salt=profile_index * 100 + repetition * 10 + fault_index,
                    )
                )
                decoded = [decode(replica.facts) for replica in run.replicas]
                checked = [check(replica.facts) for replica in run.replicas]
                masses = [value.gross_mass for value in decoded]
                determined = all(value.determined for value in decoded)
                checker_accepted = all(value.accepted for value in checked)
                branch_values = [arrival_order_branch_mass(replica.arrival_order) for replica in run.replicas]
                if (
                    not run.converged
                    or not determined
                    or not checker_accepted
                    or any(value != scenario.oracle_mass for value in masses)
                    or any(value.gross_mass != scenario.oracle_mass for value in checked)
                ):
                    raise AssertionError(
                        (profile.name, run.as_dict(), decoded, checked, scenario.oracle_mass)
                    )
                rows.append(
                    {
                        "sample": public_profile["sample"],
                        "repetition": repetition,
                        "fault": profile.name,
                        "facts": len(scenario.facts),
                        "converged": int(run.converged),
                        "determined": int(determined),
                        "checker_accepted": int(checker_accepted),
                        "mass_spread": max(masses) - min(masses),
                        "mass_error": max(abs(value - scenario.oracle_mass) for value in masses),
                        "rounds": run.rounds,
                        "messages_sent": run.messages_sent,
                        "summary_messages_sent": run.summary_messages_sent,
                        "fact_messages_sent": run.fact_messages_sent,
                        "summary_messages_delivered": run.summary_messages_delivered,
                        "fact_messages_delivered": run.fact_messages_delivered,
                        "bytes_sent": run.bytes_sent,
                        "summary_bytes": run.summary_bytes,
                        "duplicate_deliveries": run.duplicate_deliveries,
                        "dropped_messages": run.dropped_messages,
                        "arrival_order_branch_spread": max(branch_values) - min(branch_values),
                        "elapsed_ms": f"{elapsed_ms:.6f}",
                    }
                )
    _write_csv(raw_dir / "faults.csv", rows)

    summary_rows: list[dict[str, Any]] = []
    for profile in fault_profiles:
        selected = [row for row in rows if row["fault"] == profile.name]
        summary_rows.append(
            {
                "fault": profile.name,
                "runs": len(selected),
                "converged": sum(int(row["converged"]) for row in selected),
                "mass_error_max": max(int(row["mass_error"]) for row in selected),
                "rounds_median": f"{_median(float(row['rounds']) for row in selected):.1f}",
                "rounds_p95": f"{_quantile((float(row['rounds']) for row in selected), 0.95):.1f}",
                "fact_bytes_median": f"{_median(float(row['bytes_sent']) for row in selected):.1f}",
                "summary_bytes_median": f"{_median(float(row['summary_bytes']) for row in selected):.1f}",
                "branch_spread_runs": sum(
                    int(row["arrival_order_branch_spread"]) > 0 for row in selected
                ),
            }
        )
    _write_csv(summary_dir / "fault_summary.csv", summary_rows)
    result = {
        "runs": len(rows),
        "converged": sum(int(row["converged"]) for row in rows),
        "determined": sum(int(row["determined"]) for row in rows),
        "checker_accepted": sum(int(row["checker_accepted"]) for row in rows),
        "maximum_mass_error": max(int(row["mass_error"]) for row in rows),
        "arrival_order_branch_divergent_runs": sum(
            int(row["arrival_order_branch_spread"]) > 0 for row in rows
        ),
    }
    if (
        result["converged"] != result["runs"]
        or result["determined"] != result["runs"]
        or result["checker_accepted"] != result["runs"]
        or result["maximum_mass_error"] != 0
    ):
        raise AssertionError(result)
    return result


def run_ambiguity(profiles: list[dict[str, Any]], raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    variants = (
        "missing-transform-output",
        "conflicting-presentation",
        "unresolved-effect",
        "conflicting-event",
        "invalid-transform",
    )
    rows: list[dict[str, Any]] = []
    for profile_index, profile in enumerate(profiles):
        changed = int(profile["added_lines"]) + int(profile["removed_lines"])
        for repetition in range(10):
            scenario = mixed_history(
                changed + repetition,
                int(profile["files_changed"]),
                history_index=500 + profile_index * 20 + repetition,
                rewrite_rounds=2,
            )
            for variant_index, variant in enumerate(variants):
                facts = ambiguity_variant(
                    scenario,
                    variant,
                    salt=profile_index * 100 + repetition * 10 + variant_index,
                )
                result, decode_ms = _timed(lambda: decode(facts))
                verified = []
                for witness in result.witnesses:
                    ok, _reason = verify_witness(facts, witness.as_dict())
                    verified.append(ok)
                sizes = [len(witness.facts) + len(witness.missing) for witness in result.witnesses]
                detected = not result.determined and bool(result.witnesses)
                all_verified = bool(verified) and all(verified)
                if not detected or not all_verified:
                    raise AssertionError((variant, result, verified))
                rows.append(
                    {
                        "sample": profile["sample"],
                        "repetition": repetition,
                        "variant": variant,
                        "detected": int(detected),
                        "all_witnesses_verified": int(all_verified),
                        "witness_count": len(result.witnesses),
                        "minimum_witness_size": min(sizes) if sizes else 0,
                        "maximum_witness_size": max(sizes) if sizes else 0,
                        "decode_ms": f"{decode_ms:.6f}",
                    }
                )
    _write_csv(raw_dir / "ambiguity.csv", rows)

    summary_rows: list[dict[str, Any]] = []
    for variant in variants:
        selected = [row for row in rows if row["variant"] == variant]
        summary_rows.append(
            {
                "variant": variant,
                "runs": len(selected),
                "detected": sum(int(row["detected"]) for row in selected),
                "verified": sum(int(row["all_witnesses_verified"]) for row in selected),
                "witness_size_median": f"{_median(float(row['minimum_witness_size']) for row in selected):.1f}",
                "witness_size_max": max(int(row["maximum_witness_size"]) for row in selected),
            }
        )
    _write_csv(summary_dir / "ambiguity_summary.csv", summary_rows)
    result = {
        "runs": len(rows),
        "detected": sum(int(row["detected"]) for row in rows),
        "verified": sum(int(row["all_witnesses_verified"]) for row in rows),
        "maximum_witness_size": max(int(row["maximum_witness_size"]) for row in rows),
    }
    if result["detected"] != result["runs"] or result["verified"] != result["runs"]:
        raise AssertionError(result)
    return result


def run_scale(raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    sizes = (100, 500, 2000, 5000, 10000, 20000)
    rows: list[dict[str, Any]] = []
    for size_index, size in enumerate(sizes):
        for repetition in range(3):
            scenario = amplification_history(
                [1] * size,
                copies=2,
                operation="rebase",
                salt=8000 + size_index * 10 + repetition,
            )
            texts = [canonical_text(fact) for fact in scenario.facts]
            decoded, decode_ms = _timed(lambda: decode(texts))
            checked, checker_ms = _timed(lambda: check(texts))
            if (
                not decoded.determined
                or not checked.accepted
                or decoded.gross_mass != scenario.oracle_mass
                or checked.gross_mass != scenario.oracle_mass
            ):
                raise AssertionError("scale state is not accepted")
            rows.append(
                {
                    "units": size,
                    "facts": len(texts),
                    "repetition": repetition,
                    "state_bytes": sum(len(text.encode("utf-8")) + 1 for text in texts),
                    "decode_ms": f"{decode_ms:.6f}",
                    "checker_ms": f"{checker_ms:.6f}",
                    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                }
            )
    _write_csv(raw_dir / "scale.csv", rows)
    summary_rows: list[dict[str, Any]] = []
    for size in sizes:
        selected = [row for row in rows if int(row["units"]) == size]
        summary_rows.append(
            {
                "units": size,
                "facts": int(selected[0]["facts"]),
                "state_mib": f"{_median(float(row['state_bytes']) for row in selected) / (1024 * 1024):.3f}",
                "decode_ms_median": f"{_median(float(row['decode_ms']) for row in selected):.3f}",
                "checker_ms_median": f"{_median(float(row['checker_ms']) for row in selected):.3f}",
                "peak_rss_mib": f"{max(float(row['peak_rss_kib']) for row in selected) / 1024:.1f}",
            }
        )
    _write_csv(summary_dir / "scale_summary.csv", summary_rows)
    return {
        "largest_units": max(sizes),
        "largest_facts": max(int(row["facts"]) for row in rows),
        "largest_state_bytes": max(int(row["state_bytes"]) for row in rows),
        "largest_decode_ms_median": max(
            float(row["decode_ms_median"]) for row in summary_rows
        ),
        "largest_checker_ms_median": max(
            float(row["checker_ms_median"]) for row in summary_rows
        ),
        "peak_rss_kib": max(int(row["peak_rss_kib"]) for row in rows),
    }


def run_compaction(profiles: list[dict[str, Any]], raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    batch_counts = (1, 5, 10, 20, 40, 80)
    rows: list[dict[str, Any]] = []
    for batch_count in batch_counts:
        scenarios = []
        for index in range(batch_count):
            profile = profiles[index % len(profiles)]
            changed = int(profile["added_lines"]) + int(profile["removed_lines"])
            scenarios.append(
                mixed_history(
                    changed + index % 7,
                    int(profile["files_changed"]),
                    history_index=10000 + index,
                    rewrite_rounds=2,
                )
            )
        facts = recoordinate(fact for scenario in scenarios for fact in scenario.facts)
        texts = [canonical_text(fact) for fact in facts]
        before = decode(texts)
        batches = [f"hist-{10000 + index}" for index in range(batch_count)]
        ledger, elapsed_ms = _timed(lambda: compact_closed_batches(texts, batches))
        after = ledger.accounting()
        if not before.determined or not after["determined"]:
            raise AssertionError("compaction input or output is ambiguous")
        for key in (
            "unique_units",
            "gross_mass",
            "presentations",
            "effects",
            "passing_effects",
            "failing_effects",
        ):
            if getattr(before, key) != after[key]:
                raise AssertionError((key, getattr(before, key), after[key]))
        raw_bytes = sum(len(text.encode("utf-8")) + 1 for text in texts)
        compact_bytes = ledger.serialized_bytes()
        rows.append(
            {
                "batches": batch_count,
                "facts": len(texts),
                "units": before.unique_units,
                "raw_bytes": raw_bytes,
                "compacted_bytes": compact_bytes,
                "reduction_fraction": f"{1 - compact_bytes / raw_bytes:.6f}",
                "compaction_ms": f"{elapsed_ms:.6f}",
                "accounting_equal": 1,
            }
        )
    _write_csv(raw_dir / "compaction.csv", rows)
    _write_csv(summary_dir / "compaction_summary.csv", rows)
    largest = rows[-1]
    return {
        "largest_batches": int(largest["batches"]),
        "largest_facts": int(largest["facts"]),
        "largest_reduction_fraction": float(largest["reduction_fraction"]),
        "all_accounting_equal": all(int(row["accounting_equal"]) for row in rows),
    }


def run_exhaustive(raw_dir: Path, summary_dir: Path) -> dict[str, Any]:
    from .protocol import Replica, states_equal

    scenario = amplification_history([1, 2], 1, "rebase", salt=77)
    texts = [canonical_text(fact) for fact in scenario.facts[:5]]
    schedules = 0
    failures = 0
    for order in itertools.permutations(texts):
        for duplicate_mask in itertools.product((0, 1), repeat=3):
            replicas = [Replica("x0"), Replica("x1"), Replica("x2")]
            for index, text in enumerate(order):
                receiver = replicas[index % 3]
                receiver.append_text(text)
                if index < 3 and duplicate_mask[index]:
                    receiver.append_text(text)
            for left in replicas:
                for right in replicas:
                    left.merge(right)
            schedules += 1
            decoded = [decode(replica.facts) for replica in replicas]
            checked = [check(replica.facts) for replica in replicas]
            valid = (
                states_equal(replicas)
                and all(value.determined for value in decoded)
                and {value.gross_mass for value in decoded} == {scenario.oracle_mass}
                and all(value.accepted for value in checked)
                and {value.gross_mass for value in checked} == {scenario.oracle_mass}
            )
            failures += int(not valid)

    operation_sequences = 0
    operation_failures = 0
    for length in range(1, 4):
        for operations in itertools.product(("rebase", "cherry_pick", "revert"), repeat=length):
            operation_sequences += 1
            generated = operation_sequence_history(
                operations,
                masses=(2, 3, 5),
                salt=20000 + operation_sequences,
            )
            decoded = decode(generated.facts)
            checked = check(generated.facts)
            operation_failures += int(
                not decoded.determined
                or decoded.gross_mass != generated.oracle_mass
                or not checked.accepted
                or checked.gross_mass != generated.oracle_mass
            )

    # A bounded summary cannot guarantee convergence when an origin retains
    # later coordinates while the first 64 missing intervals are permanent.
    # This is the explicit counterexample outside the retained-prefix premise.
    source = Replica("n0")
    receiver = Replica("n1")
    retained_late = {130, 132, 134, 136, 138, 140}
    from .facts import mint

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
    starved_facts = len(source.facts - receiver.facts)
    gap_hole_counterexamples = int(starved_facts == len(retained_late))

    if failures or operation_failures or starved_facts != 6:
        raise AssertionError(
            {
                "delivery_failures": failures,
                "operation_failures": operation_failures,
                "gap_hole_starved_facts": starved_facts,
            }
        )

    rows = [
        {
            "family": "delivery-orders",
            "cases": schedules,
            "failures": failures,
            "starved_facts": 0,
        },
        {
            "family": "operation-sequences",
            "cases": operation_sequences,
            "failures": operation_failures,
            "starved_facts": 0,
        },
        {
            "family": "permanent-prefix-hole-counterexample",
            "cases": 1,
            "failures": 0,
            "starved_facts": starved_facts,
        },
    ]
    _write_csv(raw_dir / "exhaustive.csv", rows)
    _write_csv(summary_dir / "exhaustive_summary.csv", rows)
    return {
        "delivery_cases": schedules,
        "delivery_failures": failures,
        "operation_sequences": operation_sequences,
        "operation_failures": operation_failures,
        "gap_hole_counterexamples": gap_hole_counterexamples,
        "gap_hole_starved_facts": starved_facts,
    }


def run_all(artifact_root: Path) -> dict[str, Any]:
    raw_dir = artifact_root / "results" / "raw"
    summary_dir = artifact_root / "results" / "summary"
    profiles = load_profiles(artifact_root / "external_inputs" / "public_history_slices.json")
    started = time.perf_counter()
    result = {
        "manipulation": run_manipulation(profiles, raw_dir, summary_dir),
        "faults": run_faults(profiles, raw_dir, summary_dir),
        "ambiguity": run_ambiguity(profiles, raw_dir, summary_dir),
        "scale": run_scale(raw_dir, summary_dir),
        "compaction": run_compaction(profiles, raw_dir, summary_dir),
        "exhaustive": run_exhaustive(raw_dir, summary_dir),
    }
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    (summary_dir / "overview.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
