#!/usr/bin/env python3
"""Collect the bounded stage results and their measurement scope."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUMMARY=ROOT/"results/summary"
load=lambda name: json.loads((SUMMARY/name).read_text())
legacy=load("overview.json")
tests=load("unit_tests.json")
assert tests["successful"] and tests["tests_run"] == 78
assert tests.get("module_level_tests_run", 0) >= 8
names=("identity","transport","service_pilot","service","recovery",
       "compaction_boundaries","public_pair","semantic_boundaries",
       "sealed_windows","multiprocess_retention","completion_boundaries",
       "executable_build")
extended={name: load(name+"_boundary_overview.json") for name in names}
assert all(item.get("completed",True) for item in extended.values())
assert extended["identity"]["mint_only_exact_mass_cases"] == 1120
assert extended["identity"]["carat_integrity_refusals"] == 400
assert extended["transport"]["enumerated_converged"] == 324
assert extended["service"]["runs"] == 12 and extended["service"]["all_exact_union"]
assert extended["recovery"]["acknowledged_sets_preserved"]
assert extended["compaction_boundaries"]["matched_expected_decisions"] == 8
assert extended["public_pair"]["matched_expected_admission"] == 9
assert extended["public_pair"]["service"]["converged_nodes"] == 5
assert extended["semantic_boundaries"]["expected_admissions"] == 4
assert extended["sealed_windows"]["enumerated_arrival_subsets"] == 32
assert extended["sealed_windows"]["final_subsets"] == 1
assert extended["sealed_windows"]["admissible_future_subsets"] == 32
assert extended["sealed_windows"]["future_subsets_preserving_finality"] == 32
assert extended["sealed_windows"]["global_identifier_reuse_refused"]
assert extended["sealed_windows"]["open_phase_final_nodes"] == 0
assert extended["sealed_windows"]["completed_phase_final_nodes"] == 5
assert extended["sealed_windows"]["restart_phase_final_nodes"] == 5
assert extended["sealed_windows"]["future_phase_final_nodes"] == 5
assert extended["sealed_windows"]["illegal_phase_final_nodes"] == 0
assert extended["sealed_windows"]["sealed_projection_determined"]
assert extended["sealed_windows"]["sealed_projection_mass"] == 15
assert extended["sealed_windows"]["retained_seal_anchors"] == 5
assert extended["multiprocess_retention"]["processes"] == 5
assert extended["multiprocess_retention"]["centralized_union_used"] is False
assert extended["multiprocess_retention"]["partitioned_exchange_rounds"] == 2
assert extended["multiprocess_retention"]["pairwise_exchange_rounds"] >= 3
assert extended["multiprocess_retention"]["endpoint_restart_before_heal"]
assert extended["multiprocess_retention"]["converged_exact_union"]
assert extended["multiprocess_retention"]["certified_holders"] == 3
assert extended["multiprocess_retention"]["enumerated_crash_sets_within_bound"] == 16
assert extended["multiprocess_retention"]["all_enumerated_sets_have_raw_survivor"]
assert extended["multiprocess_retention"]["under_threshold_refused"]
assert extended["multiprocess_retention"]["closed_raw_replay_idempotent"]
assert extended["multiprocess_retention"]["closed_raw_conflict_refused"]
assert extended["multiprocess_retention"]["surviving_holder_restart_preserved_pin"]
assert extended["completion_boundaries"]["identity_indistinguishability_worlds"] == 2
assert extended["completion_boundaries"]["payload_observations_identical"]
assert extended["completion_boundaries"]["identity_oracle_masses"] == [5, 10]
assert extended["completion_boundaries"]["single_support_removals_checked"] == 10
assert extended["completion_boundaries"]["single_support_removals_refused"] == 10
assert extended["completion_boundaries"]["ablation_counterexamples"] == 6
assert extended["completion_boundaries"]["fixed_length_information_lower_bound_bits"] == 5
assert extended["executable_build"]["scenarios"] == 3
assert extended["executable_build"]["task_records"] == 12
assert extended["executable_build"]["checker_accepted"]
measured=[tests["measurement"]]+[v["measurement"] for v in legacy.values() if isinstance(v,dict) and "measurement" in v]+[v["measurement"] for v in extended.values()]
result={"documented_commands_completed":True,"scientific_completion":True,
        "completion_scope":"bounded fixed-roster crash-stop last-fact characterization and executable instance",
        "venue_submission_ready":False,
        "tests":{k:tests[k] for k in ("tests_run","failures","errors","successful")},
        "original_campaigns":legacy,"boundary_campaigns":extended,
        "resources":{"measured_stage_cpu_seconds":round(sum(x["cpu_seconds"] for x in measured),6),
                     "largest_worker_rss_mib":max(x["peak_worker_rss_mib"] for x in measured),
                     "scope":"post-import stage CPU; high-water mark of largest worker, not concurrent aggregate",
                     "inherited_cumulative_cpu_seconds":None},
        "nonclaims":["Byzantine omission requires an external witness or stronger trust mechanism",
                     "dynamic membership and multi-host performance are outside the fixed-roster localhost model",
                     "the executable adapter does not run the upstream pytest build"]}
(SUMMARY/"complete_overview.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
print(json.dumps({k:result[k] for k in ("documented_commands_completed","scientific_completion","completion_scope","venue_submission_ready","tests","resources","nonclaims")},indent=2))
