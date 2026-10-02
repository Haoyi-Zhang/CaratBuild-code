"""Exact-oracle experiments for accounting and service boundaries.

All injected faults target this self-contained loopback research service. None
of these fixtures executes a build, mines a new repository or contacts a hosted
service. Timing is descriptive for one bounded worker, not a performance claim.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import itertools
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from typing import Any

from .baselines import unique_mint_mass
from .boundaries import transport_pair, service_fixture, fixture_union
from .compaction import (
    BatchSummary,
    CompactedLedger,
    compact_closed_batches,
    compact_sealed_batch,
)
from .closure import sealed_query
from .decode import decode
from .experiments import load_profiles, _write_csv
from .facts import canonical_text, mint, parse_text, presentation, transform, effect, seal
from .generate import amplification_history, ambiguity_variant, masses_from_profile, mixed_history
from .independent_check import check, check_sealed
from .paged import make_page, PageCursor, split_put_batches
from .protocol import Replica
from .service import Cluster, rpc, NODE_NAMES, MAX_FRAME_BYTES
from .retention import RetentionReceipt, certify, surviving_holders

PAGE_LIMIT = 16


def identity_baseline(root: Path) -> dict[str, Any]:
    profiles = load_profiles(root / "external_inputs" / "public_history_slices.json")
    rows = []
    for index, profile in enumerate(profiles):
        changed = int(profile["added_lines"]) + int(profile["removed_lines"])
        masses = masses_from_profile(changed, int(profile["files_changed"]), index)
        for operation, copies, repetition in itertools.product(
                ("rebase", "cherry_pick", "fork", "squash", "revert"),
                (0, 1, 2, 4, 8, 16), range(3)):
            scenario = amplification_history(masses, copies, operation,
                                             salt=index*1000+copies*17+repetition)
            mass = unique_mint_mass(scenario.facts)
            result = decode(scenario.facts)
            if mass != scenario.oracle_mass or result.gross_mass != mass or not result.determined:
                raise AssertionError("identity-only positive oracle mismatch")
            rows.append({"family": "valid", "sample": profile["sample"],
                         "case": operation, "copies": copies, "repetition": repetition,
                         "oracle_mass": scenario.oracle_mass, "mint_only_mass": mass,
                         "carat_determined": result.determined, "carat_partial_mass": result.gross_mass})
        for repetition in range(10):
            scenario = mixed_history(changed+repetition, int(profile["files_changed"]),
                                     500+index*20+repetition, rewrite_rounds=2)
            for variant_index, variant in enumerate(("missing-transform-output", "conflicting-presentation",
                    "unresolved-effect", "conflicting-event", "invalid-transform")):
                facts = ambiguity_variant(scenario, variant, index*100+repetition*10+variant_index)
                mass, result = unique_mint_mass(facts), decode(facts)
                if mass != scenario.oracle_mass or result.determined or check(facts).accepted:
                    raise AssertionError("identity-only negative discriminator mismatch")
                rows.append({"family": "invalid", "sample": profile["sample"], "case": variant,
                             "copies": "", "repetition": repetition, "oracle_mass": scenario.oracle_mass,
                             "mint_only_mass": mass, "carat_determined": result.determined,
                             "carat_partial_mass": result.gross_mass})
    _write_csv(root / "results/raw/identity_baseline.csv", rows)
    return {"valid_cases": sum(row["family"] == "valid" for row in rows),
            "invalid_cases": sum(row["family"] == "invalid" for row in rows),
            "mint_only_exact_mass_cases": sum(row["mint_only_mass"] == row["oracle_mass"] for row in rows),
            "carat_integrity_refusals": sum(not row["carat_determined"] for row in rows),
            "interpretation": "Unique-mint counting supplies the same mass on this corpus; the decoder adds integrity decisions, not a stronger mass estimator."}


def _page_pair(left: Replica, right: Replica, limit: int, lost_responses: int = 0,
               max_rounds: int = 16) -> tuple[int, int, int, bool]:
    expected = left.facts | right.facts
    cursors = [PageCursor(), PageCursor()]
    delivered, payload_bytes = 0, 0
    for round_number in range(1, max_rounds+1):
        for index, (receiver, sender) in enumerate(((right, left), (left, right))):
            page = make_page(sender.facts, cursors[index].offset, limit)
            if lost_responses:
                lost_responses -= 1
                continue
            received, _added = cursors[index].accept(page, limit,
                lambda values, target=receiver: sum(target.append_text(text) for text in values))
            delivered += received
            payload_bytes += len(json.dumps(page, sort_keys=True, separators=(",", ":")).encode())
        if left.facts == expected and right.facts == expected:
            return round_number, delivered, payload_bytes, True
    return max_rounds, delivered, payload_bytes, False


def transport_boundaries(root: Path) -> dict[str, Any]:
    rows = []
    for fixture in ("prefix-complete", "permanent-holes", "split-coordinate"):
        left, right = transport_pair(fixture)
        expected = left.facts | right.facts
        delivered = 0
        for round_number in range(1, 17):
            for receiver, sender in ((right, left), (left, right)):
                values = sender.delta_for(receiver.summary(), batch_limit=PAGE_LIMIT)
                delivered += len(values)
                for value in values:
                    receiver.append_text(value)
            if left.facts == right.facts == expected:
                break
        rows.append({"fixture": fixture, "transport": "coordinate-gaps", "rounds": round_number,
                     "expected_facts": len(expected), "left_missing": len(expected-left.facts),
                     "right_missing": len(expected-right.facts), "delivered_fact_occurrences": delivered,
                     "page_payload_bytes": "", "same_integrity_decision": decode(left.facts).determined == decode(right.facts).determined})
        left, right = transport_pair(fixture)
        rounds, delivered, size, converged = _page_pair(left, right, PAGE_LIMIT)
        if not converged or decode(left.facts).determined != (fixture != "split-coordinate"):
            raise AssertionError("full-fact boundary fixture failed")
        rows.append({"fixture": fixture, "transport": "full-fact-pages", "rounds": rounds,
                     "expected_facts": len(expected), "left_missing": len(expected-left.facts),
                     "right_missing": len(expected-right.facts), "delivered_fact_occurrences": delivered,
                     "page_payload_bytes": size, "same_integrity_decision": True})
    _write_csv(root / "results/raw/transport_boundaries.csv", rows)
    exhaustive = []
    facts = [mint(f"n0:{index+1}", f"tiny-{index}", index+1, "n0", "tiny") for index in range(4)]
    for placement_index, placements in enumerate(itertools.product(("left", "right", "both"), repeat=4)):
        for limit, lost in itertools.product((1, 2), (0, 2)):
            left, right = Replica("n0"), Replica("n1")
            for fact, placement in zip(facts, placements):
                if placement != "right": left.append(fact)
                if placement != "left": right.append(fact)
            rounds, delivered, size, converged = _page_pair(left, right, limit, lost)
            if not converged or decode(left.facts).gross_mass != 10:
                raise AssertionError("tiny page-transfer exact oracle failed")
            exhaustive.append({"placement": placement_index, "page_limit": limit, "lost_responses": lost,
                               "rounds": rounds, "delivered_fact_occurrences": delivered,
                               "page_payload_bytes": size, "converged": converged})
    _write_csv(root / "results/raw/page_enumeration.csv", exhaustive)
    return {"boundary_rows": rows, "enumerated_cases": len(exhaustive),
            "enumerated_converged": sum(row["converged"] for row in exhaustive),
            "maximum_enumeration_rounds": max(row["rounds"] for row in exhaustive)}


async def _put(port: int, facts: list[dict[str, Any]]) -> None:
    for batch in split_put_batches(facts, MAX_FRAME_BYTES, PAGE_LIMIT):
        await rpc(port, {"op": "put", "facts": batch})


async def _inventory(port: int) -> set[str]:
    cursor, values = PageCursor(), set()
    def accept(page: list[str]) -> int:
        before = len(values)
        values.update(page)
        return len(values)-before
    for _ in range(129):
        page, _size = await rpc(port, {"op": "page", "offset": cursor.offset, "limit": PAGE_LIMIT})
        cursor.accept(page, PAGE_LIMIT, accept)
        if cursor.completed_sweeps:
            return values
    raise AssertionError("bounded inventory did not terminate")


async def _service_run(fixture: str, profile: str, supplied_slots=None) -> dict[str, Any]:
    slots, expected_determined = service_fixture(fixture) if supplied_slots is None else supplied_slots
    expected = fixture_union(slots)
    exact = decode(expected)
    if exact.determined != expected_determined:
        raise AssertionError("fixture integrity oracle mismatch")
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="carat-service-") as temporary:
        cluster = Cluster(Path(temporary))
        await cluster.start_all()
        attempts = failures = successful = received = added = stopped_attempts = 0
        final_queries: dict[str, Any] = {}
        try:
            for name, facts in zip(NODE_NAMES, slots):
                await _put(cluster.ports[name], facts)
            # Replay a complete ingress message, including its original neutral
            # event identities. A successful duplicate adds no fresh fact.
            replay, _ = await rpc(cluster.ports["n0"], {"op": "put", "facts": slots[0][:PAGE_LIMIT]})
            if replay["added"] != 0:
                raise AssertionError("duplicate ingress changed the fact set")
            if profile == "finite-network":
                for target, action in (("n0", "drop_requests"), ("n1", "drop_responses"),
                                       ("n2", "partial_responses"), ("n3", "delay_responses")):
                    await rpc(cluster.ports["n4"], {"op": "control", "node": target,
                                                   "action": action, "value": 1})
            for round_number in range(1, 81):
                if profile == "durable-restart" and round_number in (2, 5):
                    await rpc(cluster.ports["n0"], {"op": "control", "node": "n1",
                        "action": "stop" if round_number == 2 else "restart", "value": 0})
                pairs = [(receiver, sender) for receiver in NODE_NAMES for sender in NODE_NAMES if sender != receiver]
                if profile != "clean" and round_number % 2:
                    pairs.reverse()
                for receiver, sender in pairs:
                    if profile == "finite-network" and round_number <= 4 and int(receiver[1:]) % 2 != int(sender[1:]) % 2:
                        continue
                    attempts += 1
                    if profile == "durable-restart" and 2 <= round_number < 5 and "n1" in (receiver, sender):
                        stopped_attempts += 1
                    try:
                        answer, _size = await rpc(cluster.ports[receiver],
                            {"op": "pull", "peer": sender, "limit": PAGE_LIMIT})
                        successful += 1
                        received += answer["received"]
                        added += answer["added"]
                    except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                        failures += 1
                # Finish only after all scheduled faults have occurred. These
                # query replies are observations; exact inventory is read below.
                if round_number >= (6 if profile != "clean" else 1):
                    final_queries = {name: (await rpc(cluster.ports[name], {"op": "query"}))[0]
                                     for name in NODE_NAMES}
                    if all(query["facts"] == len(expected) for query in final_queries.values()):
                        break
            else:
                raise AssertionError("service failed the finite-round convergence bound")
            inventories = {name: await _inventory(cluster.ports[name]) for name in NODE_NAMES}
            if any(value != expected for value in inventories.values()):
                raise AssertionError("service inventory differs from the exact set-union oracle")
            if any(query["accounting"]["determined"] != expected_determined
                    or query["checker"]["accepted"] != expected_determined
                    or query["accounting"]["gross_mass"] != exact.gross_mass
                    for query in final_queries.values()):
                raise AssertionError("service accounting differs from exact shared-fact decoding")
            stats = dict(cluster.stats)
            return {"fixture": fixture, "fault_profile": profile, "expected_facts": len(expected),
                    "rounds": round_number, "converged_nodes": 5,
                    "determined_nodes": sum(query["accounting"]["determined"] for query in final_queries.values()),
                    "checker_accepted_nodes": sum(query["checker"]["accepted"] for query in final_queries.values()),
                    "gross_mass": exact.gross_mass, "pull_attempts": attempts, "successful_pulls": successful,
                    "failed_pulls": failures, "stopped_endpoint_attempts": stopped_attempts,
                    "received_fact_occurrences": received, "new_fact_insertions": added,
                    "application_frame_bytes": stats["request_bytes"]+stats["response_bytes"],
                    "maximum_frame_bytes": stats["largest_frame_bytes"],
                    "maximum_page_facts": stats["largest_page_facts"],
                    "request_drops": stats["injected_request_drops"],
                    "response_drops": stats["injected_response_drops"],
                    "partial_responses": stats["injected_partial_responses"],
                    "delayed_responses": stats["injected_delays"],
                    "logical_restarts": stats["logical_restarts"],
                    "elapsed_ms": round((time.perf_counter()-start)*1000, 3)}
        finally:
            await cluster.close()


async def service_campaign(root: Path, pilot: bool = False) -> dict[str, Any]:
    cases = [("mixed", "clean")] if pilot else list(itertools.product(
        ("mixed", "permanent-holes", "split-coordinate", "unresolved-effect"),
        ("clean", "finite-network", "durable-restart")))
    rows = []
    for fixture, profile in cases:
        rows.append(await asyncio.wait_for(_service_run(fixture, profile), timeout=20.0))
    filename = "service_pilot.csv" if pilot else "service_campaign.csv"
    _write_csv(root / "results/raw" / filename, rows)
    return {"runs": len(rows), "all_exact_union": all(row["converged_nodes"] == 5 for row in rows),
            "accepted_runs": sum(row["determined_nodes"] == 5 for row in rows),
            "common_refusal_runs": sum(row["determined_nodes"] == 0 for row in rows),
            "maximum_rounds": max(row["rounds"] for row in rows),
            "maximum_frame_bytes": max(row["maximum_frame_bytes"] for row in rows),
            "maximum_page_facts": max(row["maximum_page_facts"] for row in rows),
            "logical_restarts": sum(row["logical_restarts"] for row in rows),
            "total_elapsed_ms": round(sum(row["elapsed_ms"] for row in rows), 3)}


async def process_recovery(root: Path) -> dict[str, Any]:
    """Kill and recreate the actual service process, using only temporary local files."""
    slots, _expected_determined = service_fixture("mixed")
    expected = fixture_union(slots)
    with tempfile.TemporaryDirectory(prefix="carat-process-") as temporary:
        directory, ready = Path(temporary)/"stores", Path(temporary)/"endpoints.json"
        process: subprocess.Popen[bytes] | None = None
        async def start() -> dict[str, int]:
            nonlocal process
            if ready.exists(): ready.unlink()
            process = subprocess.Popen([sys.executable, str(root/"scripts/serve.py"),
                "--directory", str(directory), "--ready-file", str(ready)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            for _ in range(200):
                if process.poll() is not None:
                    _out, error = process.communicate()
                    raise AssertionError("local service process exited: " + error.decode()[:300])
                if ready.exists():
                    try: return json.loads(ready.read_text())
                    except json.JSONDecodeError: pass
                await asyncio.sleep(0.01)
            raise AssertionError("local service did not become ready")
        try:
            ports = await start()
            for name, facts in zip(NODE_NAMES, slots): await _put(ports[name], facts)
            acknowledged = {name: await _inventory(ports[name]) for name in NODE_NAMES}
            assert process is not None
            process.kill()
            killed_returncode = await asyncio.to_thread(process.wait, 5)
            if process.stderr: process.stderr.close()
            ports = await start()
            recovered = {name: await _inventory(ports[name]) for name in NODE_NAMES}
            if recovered != acknowledged:
                raise AssertionError("acknowledged fact set did not survive process-kill recovery")
            for round_number in range(1, 81):
                for receiver in NODE_NAMES:
                    for sender in NODE_NAMES:
                        if receiver != sender:
                            await rpc(ports[receiver], {"op": "pull", "peer": sender, "limit": PAGE_LIMIT})
                inventories = {name: await _inventory(ports[name]) for name in NODE_NAMES}
                if all(values == expected for values in inventories.values()): break
            else: raise AssertionError("recovered service did not converge")
            queries = [(await rpc(ports[name], {"op": "query"}))[0] for name in NODE_NAMES]
            if not all(query["accounting"]["determined"] and query["checker"]["accepted"] for query in queries):
                raise AssertionError("recovered service integrity failed")
            result = {"runs": 1, "nodes": 5, "acknowledged_fact_occurrences": sum(map(len, acknowledged.values())),
                      "acknowledged_sets_preserved": True, "converged_nodes": 5,
                      "union_facts": len(expected), "reconciliation_rounds": round_number,
                      "killed_process_exit_code": killed_returncode,
                      "fault_scope": "one whole process killed after acknowledged ingress; filesystem power loss not tested"}
            (root/"results/raw/process_recovery.json").write_text(json.dumps(result, indent=2)+"\n")
            return result
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                try: await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
                if process.stderr: process.stderr.close()


async def sealed_windows(root: Path) -> dict[str, Any]:
    """Exercise the finality distinction that plain set accounting cannot make."""

    batch = "sealed-window"
    masses = {"n0": 2, "n1": 3, "n2": 4, "n3": 5, "n4": 1}
    data = [mint(f"{origin}:1", f"sealed-{origin}", mass, origin, batch)
            for origin, mass in masses.items()]
    seals = [seal(f"{origin}:2", f"sealed-declaration-{origin}", batch, origin, 0, 1)
             for origin in NODE_NAMES]
    delayed = next(fact for fact in data if fact["event"] == "n3:1")
    observed_data = [fact for fact in data if fact is not delayed]

    # Exact two-world discriminator.  An ordinary unique-mint view sees the same
    # four data facts and mass in both worlds.  A zero-length origin declaration
    # makes that view final in one world; a frontier covering the delayed fact
    # makes it non-final in the other.
    empty_n3_seals = [fact for fact in seals if fact["origin"] != "n3"] + [
        seal("n3:1", "sealed-declaration-n3-empty", batch, "n3", 0, 0)
    ]
    world_complete = sealed_query(observed_data + empty_n3_seals, batch, NODE_NAMES)
    world_delayed = sealed_query(observed_data + seals, batch, NODE_NAMES)
    if (not world_complete.final or world_delayed.final
            or world_complete.gross_mass != world_delayed.gross_mass != 10):
        raise AssertionError("sealed-window two-world discriminator failed")

    enumeration = []
    for mask in range(1 << len(NODE_NAMES)):
        selected = [fact for index, fact in enumerate(data) if mask & (1 << index)]
        result = sealed_query(selected + seals, batch, NODE_NAMES)
        checked = check_sealed(selected + seals, batch, NODE_NAMES)
        expected_final = mask == (1 << len(NODE_NAMES)) - 1
        if result.final != expected_final or checked.accepted != expected_final:
            raise AssertionError("sealed-window subset oracle failed")
        enumeration.append({
            "arrival_mask": mask,
            "arrived_origins": len(selected),
            "provisional_mass": decode(selected).gross_mass,
            "final": result.final,
            "missing_coordinates": sum(
                item.startswith("missing-sealed-coordinate:") for item in result.violations
            ),
        })
    _write_csv(root / "results/raw/sealed_window_enumeration.csv", enumeration)

    # Exhaustively check stability under all subsets of one fresh later-batch
    # mint per origin.  Reusing a finalized unit name is deliberately excluded
    # from this admissible-extension set and is checked as a refusal below.
    future_extensions = []
    for mask in range(1 << len(NODE_NAMES)):
        later = [
            mint(f"{origin}:3", f"future-{origin}", index + 1, origin, "future-window")
            for index, origin in enumerate(NODE_NAMES)
            if mask & (1 << index)
        ]
        result = sealed_query(data + seals + later, batch, NODE_NAMES)
        verified = check_sealed(data + seals + later, batch, NODE_NAMES)
        if not result.final or not verified.accepted or result.gross_mass != 15:
            raise AssertionError("admissible future extension changed finalized accounting")
        future_extensions.append({
            "extension_mask": mask,
            "later_facts": len(later),
            "final": result.final,
            "mass": result.gross_mass,
        })
    _write_csv(root / "results/raw/sealed_window_future_extensions.csv", future_extensions)

    reused = mint("n0:3", "sealed-n0", 99, "n0", "future-window")
    reuse_result = sealed_query(data + seals + [reused], batch, NODE_NAMES)
    reuse_verified = check_sealed(data + seals + [reused], batch, NODE_NAMES)
    if reuse_result.final or reuse_verified.accepted or "global-mint-reuse:sealed-n0" not in reuse_result.violations:
        raise AssertionError("global identifier reuse did not revoke sealed finality")

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="carat-sealed-") as temporary:
        cluster = Cluster(Path(temporary))
        await cluster.start_all()
        try:
            initial = observed_data + seals
            for index, fact in enumerate(initial):
                await _put(cluster.ports[NODE_NAMES[index % 5]], [fact])

            async def converge() -> int:
                for round_number in range(1, 21):
                    for receiver in NODE_NAMES:
                        for sender in NODE_NAMES:
                            if receiver != sender:
                                await rpc(cluster.ports[receiver], {
                                    "op": "pull", "peer": sender, "limit": PAGE_LIMIT})
                    inventories = [await _inventory(cluster.ports[node]) for node in NODE_NAMES]
                    union = set().union(*inventories)
                    if all(items == union for items in inventories):
                        return round_number
                raise AssertionError("sealed-window service did not converge")

            async def observe(phase: str, rounds: int) -> list[dict[str, Any]]:
                phase_rows = []
                for node in NODE_NAMES:
                    query, _ = await rpc(cluster.ports[node], {"op": "query"})
                    final, _ = await rpc(cluster.ports[node], {"op": "finalize", "batch": batch})
                    phase_rows.append({
                        "phase": phase,
                        "node": node,
                        "rounds": rounds,
                        "facts": query["facts"],
                        "ordinary_determined": query["accounting"]["determined"],
                        "ordinary_mass": query["accounting"]["gross_mass"],
                        "sealed_final": final["accounting"]["final"],
                        "sealed_mass": final["accounting"]["gross_mass"],
                        "closure_complete": final["accounting"]["closure_complete"],
                        "violation_count": len(final["accounting"]["violations"]),
                    })
                rows.extend(phase_rows)
                return phase_rows

            first_rounds = await converge()
            open_rows = await observe("late-fact-missing", first_rounds)
            if (any(row["sealed_final"] for row in open_rows)
                    or {row["ordinary_mass"] for row in open_rows} != {10}):
                raise AssertionError("service finalized a window with a declared hole")

            await _put(cluster.ports["n4"], [delayed])
            repaired_rounds = await converge()
            final_rows = await observe("late-fact-arrived", repaired_rounds)
            if (not all(row["sealed_final"] for row in final_rows)
                    or {row["sealed_mass"] for row in final_rows} != {15}):
                raise AssertionError("service did not finalize the completed window")

            await cluster.restart("n2")
            restart_rows = await observe("durable-restart", 0)
            if not all(row["sealed_final"] for row in restart_rows):
                raise AssertionError("restart changed a final sealed result")

            future = mint("n0:3", "future-unit", 99, "n0", "future-window")
            await _put(cluster.ports["n1"], [future])
            future_rounds = await converge()
            future_rows = await observe("later-batch-arrived", future_rounds)
            if (not all(row["sealed_final"] for row in future_rows)
                    or {row["sealed_mass"] for row in future_rows} != {15}):
                raise AssertionError("later batch changed a final window")

            illegal = mint("n1:3", "late-same-batch", 7, "n1", batch)
            await _put(cluster.ports["n4"], [illegal])
            illegal_rounds = await converge()
            illegal_rows = await observe("out-of-range-same-batch", illegal_rounds)
            if any(row["sealed_final"] for row in illegal_rows):
                raise AssertionError("out-of-range same-batch fact changed mass silently")
        finally:
            await cluster.close()

    _write_csv(root / "results/raw/sealed_window_service.csv", rows)

    projection = compact_sealed_batch(data + seals, batch, NODE_NAMES)
    projection_accounting = projection.accounting()
    if (
        not projection_accounting["determined"]
        or projection_accounting["gross_mass"] != 15
        or len(projection.live_facts) != len(NODE_NAMES)
        or not all('"kind":"seal"' in value for value in projection.live_facts)
    ):
        raise AssertionError("sealed projection failed to preserve accounting and anchors")

    result = {
        "two_world_cases": 2,
        "ordinary_data_mass_in_both_worlds": 10,
        "complete_world_final": world_complete.final,
        "delayed_world_final": world_delayed.final,
        "enumerated_arrival_subsets": len(enumeration),
        "final_subsets": sum(row["final"] for row in enumeration),
        "admissible_future_subsets": len(future_extensions),
        "future_subsets_preserving_finality": sum(row["final"] for row in future_extensions),
        "global_identifier_reuse_refused": not reuse_result.final and not reuse_verified.accepted,
        "service_phases": 5,
        "service_observations": len(rows),
        "open_phase_final_nodes": sum(row["sealed_final"] for row in rows if row["phase"] == "late-fact-missing"),
        "completed_phase_final_nodes": sum(row["sealed_final"] for row in rows if row["phase"] == "late-fact-arrived"),
        "restart_phase_final_nodes": sum(row["sealed_final"] for row in rows if row["phase"] == "durable-restart"),
        "future_phase_final_nodes": sum(row["sealed_final"] for row in rows if row["phase"] == "later-batch-arrived"),
        "illegal_phase_final_nodes": sum(row["sealed_final"] for row in rows if row["phase"] == "out-of-range-same-batch"),
        "final_mass": 15,
        "sealed_projection_determined": projection_accounting["determined"],
        "sealed_projection_mass": projection_accounting["gross_mass"],
        "sealed_projection_summaries": len(projection.summaries),
        "retained_seal_anchors": len(projection.live_facts),
        "scope": "five loopback endpoints, fixed honest-origin roster, self-contained batch, and local sealed projection; no authentication, Byzantine origin, or distributed surviving-copy/reclamation claim",
    }
    (root / "results/raw/sealed_window_summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result



def completion_boundaries(root: Path) -> dict[str, Any]:
    """Exact counterexamples for every field in the completion certificate."""

    rows: list[dict[str, Any]] = []
    batch = "certificate-window"
    masses = {"n0": 1, "n1": 2, "n2": 3, "n3": 4, "n4": 5}
    data = [
        mint(f"{origin}:1", f"certificate-{origin}", mass, origin, batch)
        for origin, mass in masses.items()
    ]
    seals = [
        seal(f"{origin}:2", f"certificate-seal-{origin}", batch, origin, 0, 1)
        for origin in NODE_NAMES
    ]
    complete = data + seals
    final = sealed_query(complete, batch, NODE_NAMES)
    if not final.final or final.gross_mass != 15:
        raise AssertionError("completion boundary fixture failed")

    # Identity boundary.  A payload-only observer sees the same two occurrences
    # in both worlds.  In one they are two presentations of one declared unit;
    # in the other they are two independently minted units with identical bytes.
    # Any deterministic function of only the payload multiset must return the
    # same value, although the declared-identity oracle is 5 versus 10.
    identity_worlds = [
        {
            "world": "duplicate-presentation",
            "payload_observation": "same-bytes;same-bytes",
            "declared_units": "u;u",
            "oracle_mass": 5,
            "occurrence_count_mass": 10,
            "content_set_mass": 5,
        },
        {
            "world": "independent-identical-work",
            "payload_observation": "same-bytes;same-bytes",
            "declared_units": "u1;u2",
            "oracle_mass": 10,
            "occurrence_count_mass": 10,
            "content_set_mass": 5,
        },
    ]
    if (identity_worlds[0]["payload_observation"] != identity_worlds[1]["payload_observation"]
            or identity_worlds[0]["oracle_mass"] == identity_worlds[1]["oracle_mass"]):
        raise AssertionError("identity indistinguishability fixture failed")
    _write_csv(root / "results/raw/identity_indistinguishability.csv", identity_worlds)

    # Support-irredundance is relative to this interval certificate, not a claim
    # that no differently encoded protocol could be smaller.
    removed_support = []
    for fact in complete:
        candidate = [item for item in complete if item is not fact]
        observed = sealed_query(candidate, batch, NODE_NAMES)
        if observed.final:
            raise AssertionError("removing one certificate support fact still finalized")
        removed_support.append({
            "removed_kind": fact["kind"],
            "removed_event": fact["event"],
            "final": observed.final,
        })
    _write_csv(root / "results/raw/completion_support_ablation.csv", removed_support)

    # 1. Roster omission.  If the query may silently shrink its roster, the four
    # visible origins finalize mass 10 although the intended fifth origin has a
    # delayed mass-5 fact.
    visible_origins = NODE_NAMES[:4]
    visible = [fact for fact in complete if fact.get("origin", fact["event"].split(":", 1)[0]) in visible_origins]
    shrunk = sealed_query(visible, batch, visible_origins)
    full_roster = sealed_query(visible, batch, NODE_NAMES)
    if not shrunk.final or shrunk.gross_mass != 10 or full_roster.final:
        raise AssertionError("roster ablation did not expose the last-origin ambiguity")
    rows.append({
        "condition": "fixed-roster",
        "naive_admits": shrunk.final,
        "carat_admits": full_roster.final,
        "bad_outcome": "mass-10-final-while-origin-n4-may-have-mass-5",
    })

    # 2. Coordinate coverage.  All five seals can arrive before n4's data over a
    # non-FIFO network.  A boolean all-done predicate admits; interval coverage
    # refuses the declared hole.
    delayed = data[-1]
    missing = [fact for fact in complete if fact is not delayed]
    all_seals_present = {
        fact["origin"] for fact in missing if fact["kind"] == "seal" and fact["batch"] == batch
    } == set(NODE_NAMES)
    covered = sealed_query(missing, batch, NODE_NAMES)
    if not all_seals_present or covered.final:
        raise AssertionError("coordinate-coverage ablation failed")
    rows.append({
        "condition": "exact-coordinate-coverage",
        "naive_admits": all_seals_present,
        "carat_admits": covered.final,
        "bad_outcome": "seal-overtakes-delayed-mass-5-fact",
    })

    # 3. Predecessor continuity.  Every current interval is complete, but n0's
    # current seal points to a missing predecessor coordinate.  Ignoring that
    # link loses evidence that the origin did not skip an earlier window.
    later_batch = "later-certificate-window"
    later_data = [mint("n0:3", "later-u", 7, "n0", later_batch)]
    later_seals = [seal("n0:4", "later-s0", later_batch, "n0", 2, 3)] + [
        seal(f"{origin}:1", f"later-s-{origin}", later_batch, origin, 0, 0)
        for origin in NODE_NAMES[1:]
    ]
    no_predecessor = later_data + later_seals
    ordinary_ok = check(later_data).accepted
    intervals_complete = ordinary_ok and len(later_seals) == len(NODE_NAMES)
    predecessor_checked = sealed_query(no_predecessor, later_batch, NODE_NAMES)
    if not intervals_complete or predecessor_checked.final:
        raise AssertionError("predecessor ablation failed")
    rows.append({
        "condition": "predecessor-seal-continuity",
        "naive_admits": intervals_complete,
        "carat_admits": predecessor_checked.final,
        "bad_outcome": "origin-history-can-skip-an-unaccounted-window",
    })

    # 4. Self-contained relations.  Global checking can accept a presentation in
    # one batch that depends on a mint in a later batch; batch-local checking
    # rejects because the answer would depend on future data.
    relation_batch = "relation-window"
    future_batch = "definition-window"
    cross = [
        mint("n0:1", "cross-u", 9, "n0", future_batch),
        presentation("n0:2", "cross-p", [("cross-u", 1)], "main", 0, relation_batch),
        seal("n0:3", "cross-s0", relation_batch, "n0", 0, 2),
    ] + [
        seal(f"{origin}:1", f"cross-s-{origin}", relation_batch, origin, 0, 0)
        for origin in NODE_NAMES[1:]
    ]
    global_relations_ok = check(cross).accepted
    local_relations = sealed_query(cross, relation_batch, NODE_NAMES)
    if not global_relations_ok or local_relations.final:
        raise AssertionError("self-contained relation ablation failed")
    rows.append({
        "condition": "self-contained-relations",
        "naive_admits": global_relations_ok,
        "carat_admits": local_relations.final,
        "bad_outcome": "final-batch-depends-on-a-definition-in-another-batch",
    })

    # 5. Global identifier freshness.  Looking only at the old batch leaves it
    # final; the global query refuses a later mint that reuses a finalized unit.
    reused = mint("n0:3", "certificate-n0", 99, "n0", "future")
    old_only = sealed_query(complete, batch, NODE_NAMES)
    global_reuse = sealed_query(complete + [reused], batch, NODE_NAMES)
    if not old_only.final or global_reuse.final:
        raise AssertionError("global freshness ablation failed")
    rows.append({
        "condition": "global-identifier-freshness",
        "naive_admits": old_only.final,
        "carat_admits": global_reuse.final,
        "bad_outcome": "later-input-retroactively-reuses-a-finalized-unit",
    })

    # 6. Coordinate uniqueness.  Two replicas can each see one fact at n0:1 and
    # a common seal, finalizing different masses if event equivocation is not
    # checked.  Their merged state is refused.
    left_data = mint("n0:1", "equiv-left", 1, "n0", "equiv-window")
    right_data = mint("n0:1", "equiv-right", 8, "n0", "equiv-window")
    equiv_seals = [seal("n0:2", "equiv-s0", "equiv-window", "n0", 0, 1)] + [
        seal(f"{origin}:1", f"equiv-s-{origin}", "equiv-window", origin, 0, 0)
        for origin in NODE_NAMES[1:]
    ]
    left_final = sealed_query([left_data] + equiv_seals, "equiv-window", NODE_NAMES)
    right_final = sealed_query([right_data] + equiv_seals, "equiv-window", NODE_NAMES)
    merged = sealed_query([left_data, right_data] + equiv_seals, "equiv-window", NODE_NAMES)
    if not left_final.final or not right_final.final or left_final.gross_mass == right_final.gross_mass or merged.final:
        raise AssertionError("event-coordinate ablation failed")
    rows.append({
        "condition": "event-coordinate-uniqueness",
        "naive_admits": True,
        "carat_admits": merged.final,
        "bad_outcome": f"replicas-finalize-different-masses-{left_final.gross_mass}-and-{right_final.gross_mass}",
    })

    # Byzantine omission boundary.  The honest-empty and lying-origin worlds
    # expose byte-identical observations to every other endpoint.  No observer-
    # only zero-error procedure can separate them without an external witness.
    honest_empty = [fact for fact in complete if fact not in (data[-1], seals[-1])] + [
        seal("n4:1", "empty-or-lying-n4", batch, "n4", 0, 0)
    ]
    observed = sealed_query(honest_empty, batch, NODE_NAMES)
    hidden_fact = data[-1]
    if not observed.final or observed.gross_mass != 10:
        raise AssertionError("omission boundary observation should look final")
    rows.append({
        "condition": "faithful-origin-declaration",
        "naive_admits": observed.final,
        "carat_admits": observed.final,
        "bad_outcome": "a-byzantine-origin-can-hide-an-unobserved-mass-5-fact",
    })

    _write_csv(root / "results/raw/completion_boundary_matrix.csv", rows)
    result = {
        "identity_indistinguishability_worlds": len(identity_worlds),
        "payload_observations_identical": identity_worlds[0]["payload_observation"] == identity_worlds[1]["payload_observation"],
        "identity_oracle_masses": [row["oracle_mass"] for row in identity_worlds],
        "certificate_support_facts": len(complete),
        "single_support_removals_checked": len(removed_support),
        "single_support_removals_refused": sum(not row["final"] for row in removed_support),
        "ablation_conditions": len(rows) - 1,
        "ablation_counterexamples": sum(row["naive_admits"] and not row["carat_admits"] for row in rows[:-1]),
        "byzantine_omission_worlds": 2,
        "byte_identical_observation_mass": observed.gross_mass,
        "hidden_fact_mass": hidden_fact["mass"],
        "origin_completion_vectors_at_five_origins": 2 ** len(NODE_NAMES),
        "fixed_length_information_lower_bound_bits": len(NODE_NAMES),
        "scope": "support-irredundance for this interval representation and an observer-only Byzantine omission impossibility; not universal encoding optimality",
    }
    (root / "results/raw/completion_boundaries_summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


async def _start_independent_endpoint(
    root: Path,
    workspace: Path,
    name: str,
) -> tuple[subprocess.Popen[bytes], int]:
    ready = workspace / f"{name}.ready"
    ready.unlink(missing_ok=True)
    command = [
        sys.executable,
        str(root / "scripts" / "node_server.py"),
        "--name", name,
        "--directory", str(workspace / name),
        "--ready-file", str(ready),
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(200):
        if process.poll() is not None:
            raise AssertionError(f"independent endpoint {name} exited before readiness")
        if ready.exists():
            value = json.loads(ready.read_text(encoding="utf-8"))
            return process, int(value["port"])
        await asyncio.sleep(0.01)
    process.kill()
    await asyncio.to_thread(process.wait, 5)
    raise AssertionError(f"independent endpoint {name} did not become ready")


async def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, 5)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait, 5)


async def _remote_inventory(port: int) -> list[str]:
    offset = 0
    values: list[str] = []
    for _ in range(256):
        page, _wire = await rpc(port, {"op": "page", "offset": offset, "limit": PAGE_LIMIT})
        facts = page["facts"]
        if type(facts) is not list or any(type(item) is not str for item in facts):
            raise AssertionError("independent endpoint returned a malformed page")
        values.extend(facts)
        if page["eof"]:
            return sorted(set(values))
        offset = page["next_offset"]
    raise AssertionError("independent endpoint inventory exceeded its bound")


async def _forward_remote_pages(sender_port: int, receiver_port: int) -> dict[str, int]:
    """Relay one bounded, complete sender sweep without constructing a union.

    The experiment controller sees one page at a time and immediately forwards
    it to one receiver.  It keeps only counters and the sender's next offset;
    it never stores, deduplicates or broadcasts a global fact set.
    """

    offset = 0
    pages = occurrences = additions = wire_bytes = maximum_page = 0
    for _ in range(256):
        page, page_wire = await rpc(
            sender_port, {"op": "page", "offset": offset, "limit": PAGE_LIMIT}
        )
        facts = page.get("facts")
        if type(facts) is not list or any(type(item) is not str for item in facts):
            raise AssertionError("independent endpoint returned a malformed page")
        if facts:
            for put_batch in split_put_batches(facts, MAX_FRAME_BYTES, PAGE_LIMIT):
                admitted, put_wire = await rpc(
                    receiver_port, {"op": "put", "facts": put_batch}
                )
                if type(admitted.get("added")) is not int:
                    raise AssertionError("independent endpoint returned a malformed admission count")
                additions += admitted["added"]
                wire_bytes += put_wire
        pages += 1
        occurrences += len(facts)
        maximum_page = max(maximum_page, len(facts))
        wire_bytes += page_wire
        if page.get("eof") is True:
            return {
                "pages": pages,
                "fact_occurrences": occurrences,
                "additions": additions,
                "wire_bytes": wire_bytes,
                "maximum_page_facts": maximum_page,
            }
        next_offset = page.get("next_offset")
        if type(next_offset) is not int or next_offset <= offset:
            raise AssertionError("independent endpoint returned a non-advancing page")
        offset = next_offset
    raise AssertionError("independent endpoint page sweep exceeded its bound")


async def _export_complete(port: int, batch: str) -> list[str]:
    """Collect and verify one bounded immutable raw export."""

    offset = 0
    manifest: dict[str, Any] | None = None
    values: list[str] = []
    for _ in range(256):
        page, _wire = await rpc(port, {
            "op": "export", "batch": batch, "offset": offset, "limit": PAGE_LIMIT,
        })
        if set(page) != {"manifest", "offset", "next_offset", "eof", "facts"}:
            raise AssertionError("raw export page has unexpected fields")
        if page["offset"] != offset or type(page["facts"]) is not list:
            raise AssertionError("raw export page does not match its cursor")
        if manifest is None:
            manifest = page["manifest"]
        elif page["manifest"] != manifest:
            raise AssertionError("raw export manifest changed during one export")
        values.extend(page["facts"])
        if page["eof"]:
            break
        if type(page["next_offset"]) is not int or page["next_offset"] <= offset:
            raise AssertionError("raw export cursor did not advance")
        offset = page["next_offset"]
    else:
        raise AssertionError("raw export exceeded its page bound")
    assert manifest is not None
    if len(values) != manifest["total_facts"]:
        raise AssertionError("raw export fact count disagrees with its manifest")
    if sum(len(text.encode("utf-8")) for text in values) != manifest["total_canonical_bytes"]:
        raise AssertionError("raw export byte total disagrees with its manifest")
    if not check_sealed(values, batch, NODE_NAMES).accepted:
        raise AssertionError("raw export does not reconstruct the sealed batch")
    return values


async def multiprocess_retention(root: Path) -> dict[str, Any]:
    """Run five OS processes and a tight f+1 raw-retention experiment."""

    batch = "retention-window"
    per_origin = 40
    data: list[dict[str, Any]] = []
    seals: list[dict[str, Any]] = []
    expected_mass = 0
    for origin_index, origin in enumerate(NODE_NAMES):
        mass = origin_index + 1
        for sequence in range(1, per_origin + 1):
            data.append(mint(
                f"{origin}:{sequence}",
                f"retained-{origin}-{sequence}",
                mass,
                origin,
                batch,
            ))
            expected_mass += mass
        seals.append(seal(
            f"{origin}:{per_origin + 1}",
            f"retained-seal-{origin}",
            batch,
            origin,
            0,
            per_origin,
        ))
    all_facts = data + seals
    oracle = sealed_query(all_facts, batch, NODE_NAMES)
    if not oracle.final or oracle.gross_mass != expected_mass:
        raise AssertionError("multiprocess fixture is not a final sealed batch")

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="carat-independent-") as temporary:
        workspace = Path(temporary)
        processes: dict[str, subprocess.Popen[bytes]] = {}
        ports: dict[str, int] = {}
        try:
            for name in NODE_NAMES:
                processes[name], ports[name] = await _start_independent_endpoint(root, workspace, name)

            # Each endpoint first receives only its origin-owned interval and seal.
            for name in NODE_NAMES:
                local = [fact for fact in all_facts if fact["event"].startswith(name + ":")]
                for offset in range(0, len(local), PAGE_LIMIT):
                    await rpc(ports[name], {"op": "put", "facts": local[offset:offset + PAGE_LIMIT]})

            # Pairwise bounded sweeps replace the earlier controller-computed
            # global union.  The first two rounds keep two groups partitioned;
            # one endpoint restarts from its own log before the partition heals.
            group = {
                "n0": 0, "n1": 0, "n2": 0,
                "n3": 1, "n4": 1,
            }
            exchange = {
                "pages": 0,
                "fact_occurrences": 0,
                "additions": 0,
                "wire_bytes": 0,
                "maximum_page_facts": 0,
            }
            restart_before_heal = False
            for exchange_round in range(1, 13):
                if exchange_round == 2:
                    await _stop_process(processes["n1"])
                    processes["n1"], ports["n1"] = await _start_independent_endpoint(
                        root, workspace, "n1"
                    )
                    restart_before_heal = True
                pairs = [
                    (receiver, sender)
                    for receiver in NODE_NAMES
                    for sender in NODE_NAMES
                    if receiver != sender
                ]
                if exchange_round % 2 == 0:
                    pairs.reverse()
                for receiver, sender in pairs:
                    if exchange_round <= 2 and group[receiver] != group[sender]:
                        continue
                    forwarded = await _forward_remote_pages(
                        ports[sender], ports[receiver]
                    )
                    for key in ("pages", "fact_occurrences", "additions", "wire_bytes"):
                        exchange[key] += forwarded[key]
                    exchange["maximum_page_facts"] = max(
                        exchange["maximum_page_facts"],
                        forwarded["maximum_page_facts"],
                    )
                statuses = {
                    name: (await rpc(ports[name], {"op": "status"}))[0]
                    for name in NODE_NAMES
                }
                if exchange_round >= 3 and all(
                    status["live_facts"] == len(all_facts)
                    for status in statuses.values()
                ):
                    break
            else:
                raise AssertionError("pairwise process exchange did not converge")

            # Exact inventory reads occur only after convergence as an oracle;
            # they are not used to construct the state delivered to endpoints.
            expected_texts = {canonical_text(fact) for fact in all_facts}
            inventories = {
                name: set(await _remote_inventory(ports[name]))
                for name in NODE_NAMES
            }
            if any(inventory != expected_texts for inventory in inventories.values()):
                raise AssertionError("pairwise process exchange differs from the exact union oracle")

            before_status: dict[str, dict[str, Any]] = {}
            for name in NODE_NAMES:
                final, _ = await rpc(ports[name], {"op": "finalize", "batch": batch})
                if not final["accounting"]["final"] or final["accounting"]["gross_mass"] != expected_mass:
                    raise AssertionError("independent endpoint failed to finalize the union")
                before_status[name], _ = await rpc(ports[name], {"op": "status"})
                rows.append({
                    "phase": "raw-final",
                    "node": name,
                    "final": True,
                    "mass": expected_mass,
                    "fact_log_bytes": before_status[name]["fact_log_bytes"],
                    "projection_bytes": before_status[name]["projection_bytes"],
                })

            receipts: list[RetentionReceipt] = []
            for holder in NODE_NAMES[:3]:
                value, _ = await rpc(ports[holder], {
                    "op": "receipt", "batch": batch, "max_crashes": 2,
                })
                receipts.append(RetentionReceipt.from_dict(value))
            certificate = certify(receipts)

            # f holders are insufficient for f crash losses; the endpoint must
            # refuse this malformed certificate before deleting raw facts.
            under_threshold_refused = False
            invalid = certificate.as_dict()
            invalid["holders"] = invalid["holders"][:2]
            try:
                await rpc(ports["n3"], {"op": "project", "certificate": invalid})
            except ValueError:
                under_threshold_refused = True
            if not under_threshold_refused:
                raise AssertionError("projection accepted fewer than f+1 raw holders")

            projection_results: dict[str, dict[str, Any]] = {}
            for name in NODE_NAMES[3:]:
                projection_results[name], _ = await rpc(ports[name], {
                    "op": "project", "certificate": certificate.as_dict(),
                })
                final, _ = await rpc(ports[name], {"op": "finalize", "batch": batch})
                status, _ = await rpc(ports[name], {"op": "status"})
                if not final["accounting"]["final"] or not final["accounting"].get("projected"):
                    raise AssertionError("projected endpoint lost final accounting")
                rows.append({
                    "phase": "projected",
                    "node": name,
                    "final": True,
                    "mass": final["accounting"]["gross_mass"],
                    "fact_log_bytes": status["fact_log_bytes"],
                    "projection_bytes": status["projection_bytes"],
                })

            # An exact replay of verified projected raw data is idempotent so a
            # mixed raw/projected page cannot stall its cursor.  A different fact
            # at the deleted coordinate remains a visible conflict and is fenced.
            replay_result, _ = await rpc(
                ports["n3"], {"op": "put", "facts": [data[0]]}
            )
            replay_idempotent = replay_result.get("added") == 0
            if not replay_idempotent:
                raise AssertionError("exact projected raw replay was not idempotent")
            conflicting = dict(data[0])
            conflicting["unit"] = str(conflicting["unit"]) + "-conflict"
            conflict_refused = False
            try:
                await rpc(ports["n3"], {"op": "put", "facts": [conflicting]})
            except ValueError:
                conflict_refused = True
            if not conflict_refused:
                raise AssertionError("projected endpoint accepted a conflicting deleted coordinate")

            # Enumerate every crash set within f for the certificate theorem.
            crash_sets = []
            for size in range(0, certificate.max_crashes + 1):
                for failed in itertools.combinations(NODE_NAMES, size):
                    survivors = surviving_holders(certificate, failed)
                    if not survivors:
                        raise AssertionError("f+1 certificate had no surviving holder")
                    crash_sets.append({
                        "failed": ";".join(failed),
                        "surviving_holders": len(survivors),
                    })

            # Exercise the tight case: two of three raw holders terminate.
            for failed in ("n0", "n1"):
                await _stop_process(processes[failed])

            # The surviving holder restarts from its own files.  Its receipt is a
            # durable raw-data pin: even a structurally valid alternate certificate
            # that omits n2 may not make it project the only surviving copy.
            await _stop_process(processes["n2"])
            processes["n2"], ports["n2"] = await _start_independent_endpoint(root, workspace, "n2")
            alternate = certificate.as_dict()
            alternate["holders"] = ["n0", "n3", "n4"]
            holder_pin_refused = False
            try:
                await rpc(ports["n2"], {"op": "project", "certificate": alternate})
            except ValueError:
                holder_pin_refused = True
            if not holder_pin_refused:
                raise AssertionError("restarted raw holder lost its durable retention pin")
            exported = await _export_complete(ports["n2"], batch)
            if len(exported) != len(all_facts):
                raise AssertionError("the remaining certified holder lost raw facts")

            # A projected process restarts independently and reconstructs its
            # summary plus seal-only log without consulting another process.
            await _stop_process(processes["n4"])
            processes["n4"], ports["n4"] = await _start_independent_endpoint(root, workspace, "n4")
            restarted, _ = await rpc(ports["n4"], {"op": "finalize", "batch": batch})
            if (not restarted["accounting"]["final"]
                    or restarted["accounting"]["gross_mass"] != expected_mass
                    or not restarted["accounting"].get("projected")):
                raise AssertionError("projected process restart changed accounting")
            rows.append({
                "phase": "projected-restart",
                "node": "n4",
                "final": True,
                "mass": restarted["accounting"]["gross_mass"],
                "fact_log_bytes": (await rpc(ports["n4"], {"op": "status"}))[0]["fact_log_bytes"],
                "projection_bytes": (await rpc(ports["n4"], {"op": "status"}))[0]["projection_bytes"],
            })
        finally:
            for process in processes.values():
                await _stop_process(process)

    _write_csv(root / "results/raw/multiprocess_retention.csv", rows)
    _write_csv(root / "results/raw/retention_crash_enumeration.csv", crash_sets)
    raw_bytes = sum(before_status[name]["fact_log_bytes"] for name in NODE_NAMES[3:])
    projected_bytes = sum(
        projection_results[name]["bytes_after"] for name in NODE_NAMES[3:]
    )
    reduction = 1.0 - projected_bytes / raw_bytes
    result = {
        "processes": 5,
        "independent_logs": 5,
        "raw_facts_per_endpoint": len(all_facts),
        "pairwise_exchange_rounds": exchange_round,
        "partitioned_exchange_rounds": 2,
        "endpoint_restart_before_heal": restart_before_heal,
        "exchange_pages": exchange["pages"],
        "forwarded_fact_occurrences": exchange["fact_occurrences"],
        "new_fact_insertions": exchange["additions"],
        "duplicate_fact_occurrences": exchange["fact_occurrences"] - exchange["additions"],
        "exchange_application_bytes": exchange["wire_bytes"],
        "maximum_exchange_page_facts": exchange["maximum_page_facts"],
        "centralized_union_used": False,
        "post_convergence_oracle_inventory_reads": len(NODE_NAMES),
        "converged_exact_union": True,
        "expected_mass": expected_mass,
        "fault_bound": 2,
        "certified_holders": len(certificate.holders),
        "enumerated_crash_sets_within_bound": len(crash_sets),
        "all_enumerated_sets_have_raw_survivor": True,
        "under_threshold_refused": under_threshold_refused,
        "projected_endpoints": 2,
        "raw_bytes_on_projected_endpoints_before": raw_bytes,
        "bytes_on_projected_endpoints_after": projected_bytes,
        "projection_reduction_fraction": round(reduction, 6),
        "closed_raw_replay_idempotent": replay_idempotent,
        "closed_raw_conflict_refused": conflict_refused,
        "two_raw_holders_terminated": True,
        "surviving_holder_restart_preserved_pin": holder_pin_refused,
        "surviving_holder_exported_facts": len(exported),
        "projected_restart_preserved_mass": restarted["accounting"]["gross_mass"],
        "scope": "five independent localhost processes; crash-stop fixed membership; f+1 raw retention, not Byzantine attestation or multi-host performance",
    }
    (root / "results/raw/multiprocess_retention_summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result

def compaction_boundaries(root: Path) -> dict[str, Any]:
    facts = [mint("n0:1", "closed-unit", 5, "n0", "closed"),
             presentation("n0:2", "p0", [("closed-unit", 1)], "main", 0, "closed"),
             presentation("n0:3", "p1", [("closed-unit", 1)], "main", 1, "closed"),
             transform("n0:4", "t0", "rebase", ["p0"], ["p1"], [], "closed"),
             effect("n0:5", "e0", "p1", "closed-unit", "pass", "test", "closed")]
    ledger = compact_closed_batches(facts, ["closed"])
    cases: list[tuple[str, CompactedLedger, bool]] = [("valid-projection", ledger, True)]
    for kind in ("mint", "presentation", "transform", "effect"):
        fact = next(fact for fact in facts if fact["kind"] == kind)
        cases.append(("replay-"+kind, replace(ledger, live_facts=(canonical_text(fact),)), False))
    cases += [
        ("duplicate-summary", replace(ledger, summaries=ledger.summaries*2), False),
        ("overlapping-unit-summaries", replace(ledger, summaries=ledger.summaries+
            (replace(ledger.summaries[0], batch="another"),)), False),
        ("live-unit-reuse", replace(ledger, live_facts=(canonical_text(
            mint("n1:1", "closed-unit", 5, "n1", "live")),)), False)]
    rows = []
    for name, candidate, expected in cases:
        result = candidate.accounting()
        if result["determined"] is not expected:
            raise AssertionError("compaction boundary admission mismatch")
        rows.append({"case": name, "expected_determined": expected,
                     "observed_determined": result["determined"],
                     "diagnostic_mass": result["gross_mass"]})
    _write_csv(root/"results/raw/compaction_boundaries.csv", rows)
    return {"cases": len(rows), "matched_expected_decisions": len(rows),
            "valid_projection_mass": ledger.accounting()["gross_mass"],
            "naive_summary_plus_replayed_full_batch_mass": ledger.accounting()["gross_mass"]+decode(facts).gross_mass,
            "original_full_batch_mass": decode(facts).gross_mass,
            "scope": "trusted offline projections with boundary guards; not a replicated compaction protocol"}



def public_pair_experiment(root: Path) -> dict[str, Any]:
    from copy import deepcopy
    from .public_pair import extract_pair, payload
    original = json.loads((root/"external_inputs/pytest_pair.json").read_text())
    cases = [("packaged-pytest-pair", original, True)]
    shifted = deepcopy(original)
    shifted["target"][1]["hunk"] = shifted["target"][1]["hunk"].replace(
        "-814,6 +814,12", "-7,6 +13,12"
    )
    cases.append(("target-coordinate-shift", shifted, True))

    synchronized = deepcopy(original)
    for side in ("source", "target"):
        synchronized[side][0]["hunk"] = synchronized[side][0]["hunk"].replace(
            "Previously this resulted", "Before this change it resulted"
        )
    cases.append(("synchronized-two-sided-payload-change", synchronized, True))

    context_metadata = deepcopy(original)
    context_metadata["target"][1]["hunk"] = context_metadata["target"][1]["hunk"].replace(
        "@@ -814,6 +814,12 @@ def consider_pluginarg(self, arg: str) -> None:",
        "@@ -7,6 +13,12 @@ another context label",
    ).replace(
        "             if name in essential_plugins:",
        "             # different unchanged context",
    )
    context_metadata["repository"] = "example/declared-repository"
    context_metadata["source_record"] = "https://example.test/source"
    context_metadata["target_record"] = "https://example.test/target"
    cases.append(("context-coordinate-and-metadata-change", context_metadata, True))

    for name, field, value in [
        ("one-sided-payload-change", "hunk", original["target"][1]["hunk"].replace('name.endswith("conftest.py")', 'name.endswith("another.py")')),
        ("one-sided-path-change", "path", "different.py"),
        ("truncated-hunk", "hunk", original["target"][1]["hunk"].splitlines(keepends=True)[0]),
        ("binary-input", "hunk", "Binary files differ\n")]:
        changed = deepcopy(original)
        changed["target"][1][field] = value
        cases.append((name, changed, False))
    malformed_metadata = deepcopy(original)
    malformed_metadata["access_date"] = "not-a-date"
    cases.append(("malformed-metadata", malformed_metadata, False))
    rows = []
    for name, data, expected in cases:
        try:
            extracted = extract_pair(data)
            accepted = check(extracted).accepted
        except ValueError:
            accepted = False
        if accepted != expected: raise AssertionError("public-pair adapter discriminator failed")
        rows.append({"case": name, "expected_admitted": expected, "observed_admitted": accepted})
    facts = extract_pair(original)
    slots = [facts[index::5] for index in range(5)]
    service = asyncio.run(asyncio.wait_for(_service_run("public-pair", "clean", (slots, True)), 20.0))
    _write_csv(root/"results/raw/public_pair_adapter.csv", rows)
    _write_csv(root/"results/raw/public_pair_service.csv", [service])
    (root/"results/raw/public_pair_facts.json").write_text(json.dumps(facts, indent=2)+"\n")
    return {"source_inputs": 2, "selected_hunks": 6, "selected_files_per_input": 3,
            "selected_added_lines_per_input": 15, "adapter_cases": len(rows),
            "matched_expected_admission": len(rows), "unit_mass": 15,
            "normalized_facts": len(facts), "service": service,
            "scope": "generic bounded three-file declared-pair adapter evaluated on one packaged pytest fix/backport pair; no upstream tests or builds executed"}



def executable_build(root: Path) -> dict[str, Any]:
    from .executable_build import run_executable_adapter

    result = run_executable_adapter(root)
    _write_csv(root / "results/raw/executable_build.csv", result.pop("rows"))
    (root / "results/raw/executable_build_summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def semantic_boundaries(root: Path) -> dict[str, Any]:
    facts = [mint("n0:1", "u", 5, "n0", "b"),
             presentation("n0:2", "p", [("u",1)], "main", 0, "b"),
             presentation("n0:3", "q", [("u",1)], "main", 0, "b"),
             transform("n0:4", "t", "rebase", ["p"], ["q"], [], "b"),
             transform("n0:5", "s", "rebase", ["q"], ["p"], [], "b")]
    if not check(facts).accepted: raise AssertionError("relation-cycle oracle differs from checker")
    cycle = decode(facts)
    opaque = decode(facts+[effect("n0:6", "e", "p", "u", "pass", "not-a-resolved-build-node", "b")])
    aliases = decode(facts+[mint("n1:1", "u-other", 5, "n1", "b")])
    ledger = compact_closed_batches(facts, ["b"])
    original_summary = ledger.summaries[0]
    forged_replay = tuple(
        canonical_text({**parse_text(text), "mass": 50})
        if parse_text(text)["kind"] == "mint" else text
        for text in original_summary.replay_facts
    )
    # Internal consistency catches one-field edits.  A coherent rewrite of both
    # the exact replay index and its aggregates remains accepted because imported
    # projection state is not authenticated in the crash-stop model.
    forged_summary = replace(
        original_summary,
        units=(("u", 50),),
        replay_facts=forged_replay,
    )
    forged = replace(ledger, summaries=(forged_summary,))
    projection = forged.accounting()
    rows = [{"case":"relation-cycle", "admitted":cycle.determined, "gross_mass":cycle.gross_mass,
             "meaning":"acyclic history is not an implemented constraint"},
            {"case":"opaque-dependency", "admitted":opaque.determined, "gross_mass":opaque.gross_mass,
             "meaning":"dependency is a label, not verified build causality"},
            {"case":"new-mint-identity", "admitted":aliases.determined, "gross_mass":aliases.gross_mass,
             "meaning":"distinct valid mints count separately"},
            {"case":"trusted-summary-edit", "admitted":projection["determined"], "gross_mass":projection["gross_mass"],
             "meaning":"coherently rewritten imported summary and replay index remain structurally trusted"}]
    if [r["gross_mass"] for r in rows] != [5,5,10,50] or not all(r["admitted"] for r in rows):
        raise AssertionError("semantic-boundary expectation changed")
    _write_csv(root/"results/raw/semantic_boundaries.csv", rows)
    return {"cases":4, "expected_admissions":4, "rows":rows,
            "interpretation":"these accepted counterexamples delimit non-claims; they are not successful defenses"}

def run(root: Path, stage: str) -> dict[str, Any]:
    (root/"results/raw").mkdir(parents=True, exist_ok=True)
    if stage == "public-pair": return public_pair_experiment(root)
    if stage == "semantic-boundaries": return semantic_boundaries(root)
    if stage == "identity": return identity_baseline(root)
    if stage == "transport": return transport_boundaries(root)
    if stage == "service-pilot": return asyncio.run(service_campaign(root, pilot=True))
    if stage == "service": return asyncio.run(service_campaign(root))
    if stage == "recovery": return asyncio.run(asyncio.wait_for(process_recovery(root), timeout=20))
    if stage == "compaction-boundaries": return compaction_boundaries(root)
    if stage == "sealed-windows": return asyncio.run(asyncio.wait_for(sealed_windows(root), timeout=30))
    if stage == "multiprocess-retention": return asyncio.run(asyncio.wait_for(multiprocess_retention(root), timeout=60))
    if stage == "completion-boundaries": return completion_boundaries(root)
    if stage == "executable-build": return executable_build(root)
    raise ValueError("unknown boundary stage")
