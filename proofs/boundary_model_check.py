#!/usr/bin/env python3
"""Finite, independent checks for the paper's boundary claims.

This module deliberately imports no CARAT implementation code.  It checks the
combinatorial statements that accompany (rather than replace) the paper proofs.
"""
from __future__ import annotations
import argparse, itertools, json, math
from pathlib import Path
from typing import FrozenSet, Iterable

Endpoint = int
Failure = FrozenSet[Endpoint]


def survives(holder_set: FrozenSet[Endpoint], failure_family: Iterable[Failure]) -> bool:
    """Every allowed failure leaves at least one holder alive."""
    return all(bool(holder_set - failed) for failed in failure_family)


def hitting_condition(holder_set: FrozenSet[Endpoint], failure_family: Iterable[Failure]) -> bool:
    """Equivalent static condition: H is not contained in any allowed failure."""
    return all(not holder_set.issubset(failed) for failed in failure_family)


def powerset(xs):
    xs=tuple(xs)
    for r in range(len(xs)+1):
        for c in itertools.combinations(xs,r):
            yield frozenset(c)


def cardinality_family(n:int,f:int):
    return tuple(x for x in powerset(range(n)) if len(x)<=f)


def check_failure_families():
    checked=0
    # Exhaustively enumerate all downward-closed failure families on up to 4 endpoints
    # by generating each family from an antichain of maximal failures.
    examples=[]
    for n in range(1,5):
        universe=tuple(powerset(range(n)))
        # All subfamilies are feasible at n<=4 (2^(2^4)=65536).
        for mask in range(1<<len(universe)):
            fam={universe[i] for i in range(len(universe)) if (mask>>i)&1}
            if frozenset() not in fam:
                continue
            if any((b.issubset(a) and b not in fam) for a in fam for b in universe):
                continue
            for h in universe:
                assert survives(h,fam)==hitting_condition(h,fam)
                checked+=1
        # Cardinality corollary.
        for f in range(n):
            fam=cardinality_family(n,f)
            safe=[h for h in universe if survives(h,fam)]
            assert min(map(len,safe))==f+1
            examples.append({'n':n,'f':f,'minimum_safe_holders':f+1})
    # Correlated-domain counterexample: count alone does not characterize a
    # non-cardinality failure family.
    fam=(frozenset(),frozenset({0,1}),frozenset({2,3}))
    bad=frozenset({0,1}); good=frozenset({0,2})
    assert not survives(bad,fam) and survives(good,fam) and len(bad)==len(good)==2
    return {'equivalence_cases':checked,'cardinality_examples':examples,
            'correlated_counterexample':{'allowed_maximal_failures':[[0,1],[2,3]],
             'same_size_unsafe_holders':[0,1],'same_size_safe_holders':[0,2]}}


def check_last_fact():
    # Identical received prefix, different oracle-complete histories.
    prefix=(('a',4),('b',6))
    world_complete=prefix
    world_delayed=prefix+(('c',5),)
    observation_complete=prefix
    observation_delayed=prefix
    assert observation_complete==observation_delayed
    assert sum(v for _,v in world_complete)!=sum(v for _,v in world_delayed)
    return {'observation':list(prefix),'oracle_masses':[10,15],
            'conclusion':'received facts alone cannot distinguish completion under unbounded delay'}


def check_payload_identity():
    # The observation available to a payload-only rule is two identical payloads.
    observed_payloads=('digest:x','digest:x')
    one_unit={'units':1,'mass':5,'presentations':2}
    two_units={'units':2,'mass':10,'presentations':1}
    assert observed_payloads==('digest:x','digest:x')
    assert one_unit['mass']!=two_units['mass']
    return {'observed_payloads':list(observed_payloads),'oracle_worlds':[one_unit,two_units]}


def membership_bits(U:int,N:int)->int:
    return math.ceil(math.log2(math.comb(U,N)))


def check_membership_lower_bound():
    rows=[]
    for n in range(1,13):
        u=2*n
        states=math.comb(u,n)
        bits=membership_bits(u,n)
        assert bits>=n
        # Pigeonhole: any encoding with one fewer bit has too few states.
        assert 2**(bits-1)<states<=2**bits
        rows.append({'universe':u,'seen':n,'possible_seen_sets':states,'minimum_state_bits':bits})
    # Unrestricted subsets of a fixed universe require exactly U state bits.
    for u in range(1,13):
        assert math.ceil(math.log2(2**u))==u
    return {'fixed_cardinality':rows,
            'statement':'exact membership for arbitrary N-subsets of a U-element universe requires ceil(log2 binom(U,N)) bits; hence Omega(N) when U>=2N'}


def check_namespace_ablation():
    namespaces=('unit','presentation','transformation','effect')
    witnesses=[]
    for omitted in namespaces:
        retained=set(namespaces)-{omitted}
        old={k:{f'{k}:old'} for k in namespaces}
        replay={k:set() for k in namespaces}; replay[omitted]={f'{omitted}:old'}
        detected=any(old[k]&replay[k] for k in retained)
        assert not detected
        witnesses.append({'omitted_namespace':omitted,'undetected_replay_id':f'{omitted}:old'})
    return {'ablations':witnesses,'all_four_namespaces_necessary_for_this_exact_fence':True}


def run():
    return {
      'schema_version':1,
      'independent_of_implementation':True,
      'failure_family':check_failure_families(),
      'last_fact_indistinguishability':check_last_fact(),
      'payload_identity_indistinguishability':check_payload_identity(),
      'exact_membership_state_lower_bound':check_membership_lower_bound(),
      'projection_namespace_ablation':check_namespace_ablation(),
    }


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path)
    ns=ap.parse_args(); data=run(); s=json.dumps(data,indent=2,sort_keys=True)+'\n'
    if ns.output:
        ns.output.parent.mkdir(parents=True,exist_ok=True); ns.output.write_text(s)
    else: print(s,end='')

if __name__=='__main__': main()
