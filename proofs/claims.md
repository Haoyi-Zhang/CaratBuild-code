# Formal claims and proof arguments

This document expands the paper's proof surface. The executable checker is evidence for the finite reported cases; the arguments below state the general result inside the locked model.

## 1. Definitions

Let `F` be a finite set of canonical facts. A mint fact defines a pair `(u,m)` where `u` is a globally scoped unit identity and `m` is a positive integer. If every unit has exactly one definition, define

```text
G(F) = sum m(u) over the unique mint identities in F.
```

Presentations, transformations, effects, and seals have zero mass. For a presentation `p`, let `A(p)` be its finite unit-to-sign map. The wire field `fresh` is reserved and must be empty: only a mint creates a unit. Rebase and cherry-pick require one input and one equal output; fork requires one input and one or more equal outputs; revert requires one input and one sign-negated output; squash requires at least two inputs and one output equal to their sign-consistent union. A state is **ordinary-accepted** when identifiers and event coordinates are unambiguous, references resolve, each transformation satisfies this written algebra, and each effect unit belongs to its named presentation. The decoder/checker agreement is differential implementation evidence, not the definition of correctness.

For a fixed origin roster `O` and batch `b`, each origin seal declares an interval `(p_o,f_o]`. The **allocated batch facts** of a faithful origin are exactly its batch facts at coordinates in that interval. A batch is **final** when the checked conditions in `SCHEMA.md` hold and the decoder and separate checker agree.

For a fixed endpoint roster `R`, a batch is **raw-survivable under f crash-stop failures** when every subset of at most `f` failed endpoints leaves at least one endpoint that retains and can export the complete raw batch.

## 2. Payload-only identity impossibility

### Theorem 1

No deterministic function of a payload multiset alone can always distinguish duplicate presentation of one unit from independently minted identical work. No randomized function can make a zero-error distinction.

### Proof

Give the observer the same multiset `[x,x]` in two worlds. In world D, both occurrences present one mass-5 unit, so the correct gross mass is 5. In world I, the occurrences correspond to two independently minted mass-5 units, so the correct mass is 10. A deterministic function has the same output on identical input and must be wrong in one world. A randomized function has the same output distribution on identical input and cannot assign probability one to different answers. ∎

### Executable witness

`results/raw/identity_indistinguishability.csv` records both worlds, identical observations, and oracle masses 5 and 10. Occurrence counting succeeds only in world I; content-set counting succeeds only in world D. Declared IDs select a world by external evidence rather than inference.

## 3. Unique-mint conservation and strongest-baseline null result

### Lemma 2

Adding, duplicating, or reordering presentation, transformation, effect, or seal facts without an additional mint does not change `G(F)`.

### Proof

`G` ranges only over unique mint identities. The added facts do not change that domain or any mint mass. Set replay of an identical mint is idempotent; a distinct second definition is refused rather than counted. ∎

### Corollary 2.1

Under honest unique mint identities and the declared transform arities/set equations above, rebase, cherry-pick, fork, squash, revert, and repeated execution cannot amplify gross mass.

### Evidence and boundary

In `results/raw/identity_baseline.csv`, unique-mint counting equals the exact generated oracle in all 720 valid and 400 injected-invalid histories. The decoder also reports the same diagnostic mass. Its added result is refusal of the 400 relation-invalid histories. This is why the paper does not attribute scalar duplicate resistance to transformation certificates.

## 4. Last-fact impossibility

### Theorem 3

Under unbounded message delay, no function of observed accounting facts alone provides both zero-error safety and eventual completion for every finite window.

### Proof

Let the current observation `S` be four valid mint facts with mass 10. In world C, `S` is the complete window. In world L, one origin has already created a mass-5 fact whose delivery is delayed. The observer has identical state in both worlds. Finalizing at 10 is unsafe in L. Refusing forever is incomplete in C. Randomization cannot make a zero-error distinction on identical state. ∎

### Interpretation

The theorem does not make the provisional sum wrong. It shows that data absence is not completion evidence. A timeout adds a delay/error assumption. A coordinator done bit, watermark, snapshot, frontier, or origin seal adds external progress evidence. The artifact's seals are one inspectable representation, not a generic improvement over those mechanisms.

## 5. Conditional fixed-roster window soundness

### Assumptions

For a batch `b`:

1. the origin roster `O` is fixed, nonempty, and correctly bound to names;
2. every origin truthfully declares its complete local allocation to `b`;
3. canonical facts are immutable;
4. event coordinates and semantic identifiers are globally unambiguous;
5. the batch is self-contained;
6. all checked seal, coverage, predecessor, relation, and freshness conditions hold.

### Theorem 4

The accepted batch fact set equals the union of all facts allocated to `b` by roster origins.

### Proof

For each origin, faithfulness makes the declared interval its complete allocation. Exact coverage requires one batch fact at every covered data coordinate. The outside-interval check rejects any same-batch fact beyond the declaration. Exactly one seal is required from every roster origin, and outside origins are rejected. Taking the union over the fixed roster gives exactly the allocated batch. ∎

### Theorem 5 — admissible-extension stability

Any extension that uses later batches, new event coordinates, globally fresh unit/presentation/transform/effect IDs, and valid predecessor chains leaves an accepted earlier batch's fact set and aggregate unchanged.

### Proof

An extension cannot add a distinct fact at a covered coordinate without event equivocation. It cannot add an old-batch fact outside a sealed interval without refusal. It cannot reuse a finalized identifier without refusal. Identical replay is set-idempotent. Later self-contained batches are excluded from the earlier query. Therefore every admitted extension leaves the earlier selected facts and aggregate unchanged. ∎

### Theorem 6 — conditional convergence

If a finite retained union is eventually delivered in full to every connected survivor, every survivor returns the same finality verdict and aggregate.

### Proof

Repeated complete-fact pages eventually make every surviving set equal to the same finite union under fair successful sweeps and capacity. Finality and decoding are deterministic functions of the canonical set, batch, and fixed roster. ∎

### Evidence

- all 32 arrival subsets: only the complete vector finalizes;
- all 32 admissible future subsets: old mass 15 remains final;
- global identifier reuse and an out-of-range old-batch fact are refused;
- five service phases produce 0/5, 5/5, 5/5, 5/5, and 0/5 final endpoints;
- the primary implementation and separate checker agree on the enumeration and failure matrix.

## 6. Certificate support and the Byzantine omission boundary

### Representation-relative support result

The smallest five-origin complete fixture has five data facts and five seals. Removing any one of the ten facts causes nonfinality. Six further ablations each admit a concrete bad outcome:

1. shrinking the roster hides fifth-origin mass;
2. replacing exact coverage with a boolean done bit allows a delayed fact after completion;
3. dropping predecessor continuity skips an earlier window;
4. allowing cross-window references makes an answer depend on future definitions;
5. dropping global freshness permits retroactive identifier reuse;
6. dropping event uniqueness lets replicas accept different masses at one coordinate.

This proves irredundance for the implemented interval representation and checked predicates. It is not a universal minimal encoding theorem.

### Theorem 7 — observer-only Byzantine omission impossibility

No local checker can distinguish an honestly empty origin from an origin that hides a fact while issuing the same empty declaration.

### Proof

The observer receives identical facts and declaration bytes in both worlds, but the underlying allocation differs. The same indistinguishability argument as Theorem 3 applies. Authentication can establish who made a declaration; it does not by itself prove that the declaration is complete. Cross-checking or a stronger trust model is required. ∎

## 7. Tight raw-retention threshold

### Theorem 8

At least `f+1` distinct durably pinned raw holders are necessary and sufficient to guarantee one raw copy after any crash-stop failure set of size at most `f`.

### Proof

Necessity: with at most `f` holders, the failure set containing all holders is allowed and removes every raw copy. Sufficiency: among `f+1` distinct holders, any failure set of size at most `f` omits at least one holder. Durable pinning is necessary to make holder membership invariant after receipt issuance. ∎

### Certificate conditions

The implementation validates fixed origin and endpoint rosters, an in-range fault bound, matching batch and seal set, distinct holder names, holder membership, and the `f+1` threshold. A receipt is counted only after its raw holder synchronizes a bounded newline record. Recovery replays complete receipt records, truncates only an incomplete final fragment, rejects a malformed complete record, and reloads the pin before requests are served. A pinned endpoint cannot project under a rotated certificate.

### Executed evidence

For `n=5,f=2`, five independent processes first exchange bounded pages directly. Two rounds preserve a two-group partition, one endpoint restarts before healing, and all five reach the exact 205-fact union in round three; the controller records page counters but never constructs the union. The artifact then refuses two holders, accepts three, enumerates all 16 crash sets of size at most two, kills two of the three holders, restarts the survivor, confirms its pin, and exports all 205 raw facts. This is fixed-membership crash-stop survival, not BFT, repair, or multi-host independence.

## 8. Projection preservation and post-projection adjudication

### Proposition 9

For an ordinary-accepted, final, self-contained batch with no cross-barrier reference, the implemented projection preserves:

- the exact unit/mass map;
- unique unit count and gross mass;
- presentation count;
- total, passing, and failing effect counts;
- every presentation, transformation, effect, seal, and event identifier needed by the visible-conflict rules;
- an exact bounded compressed index of every discarded canonical non-seal fact.

### Proof

The constructor derives the exact aggregates and identifier sets from the accepted raw batch and stores the discarded canonical facts in a lossless bounded replay index. It retains the exact origin seals as anchors. Loading a summary decodes that index, reparses every fact, recomputes all aggregates and fences, and requires equality with the stored summary and anchors. Thus a successful load reconstructs the same batch-local result. ∎

### Post-projection admission

The replay index distinguishes two cases that an identifier-only summary cannot. Reoffering an exact discarded fact is an idempotent no-op; reoffering a different fact at the same deleted event coordinate is a conflict and is rejected. An extra same-batch seal, a seal ID reused by a later batch, or reuse of any projected semantic identifier is also rejected. A disjoint legal later batch is admitted. Every projected query/finalize path revalidates these conditions against live facts and retained anchors, so bypassing ordinary admission and persisting a visible conflict makes query, cached finalization, and restart fail closed.

### Crash ordering

Summary and certificate persistence happen before raw-log replacement. If the process stops between them and raw facts remain, recovery reconstructs the summary, replay index, exact seals, and batch-local final result from those facts. It deletes raw records only on exact equality. Once raw facts are gone, recovery still decodes and validates the replay index and rejects live overlap or anchor mismatch. A malformed complete projected record fails closed. A holder never enters this path because its durable pin forbids projection.

### Boundaries

The exact replay index is bounded at 32 MiB of uncompressed canonical text. It improves adjudication but reduces compression; it is not history-independent metadata. The summary does not preserve arbitrary branch-label, transform-edge, dependency-label, or witness organization. It is structurally trusted and not cryptographically authenticated. The result is not general distributed garbage collection.

## 9. Witness soundness and fixed-predicate inclusion minimality

A witness contains a category, predicate code, canonical observed facts, unresolved-reference labels, and explanatory text. The checker ignores the text, requires cited facts to belong to the supplied state, reevaluates the named predicate, and requires every single cited fact or label to be necessary.

For implemented predicates:

- event and semantic-ID conflicts require exactly two distinct matching facts;
- a missing reference requires one referencing fact and its unresolved label;
- effect membership requires the effect and presentation;
- a transformation-law witness requires the transform and one definition for every distinct named input and output.

The schema makes transform reference lists duplicate-free and disjoint. Unrelated or shadowed facts are removable. Thus accepted witnesses are inclusion-minimal under the fixed support convention. The result is not minimum cardinality among all predicates or human explanations.

All 400 injected violations are detected and all emitted witnesses are accepted by the checker. The maximum support size is three only for the five tested families.

## 10. Transport, export, and acknowledged process recovery

### Proposition 10 — finite complete-page convergence

For a finite quiescent retained union, fair successful repeated sweeps, a surviving copy of each fact, and sufficient receiver capacity, count- and encoded-byte-bounded pages of complete canonical facts eventually make every connected raw survivor hold the union.

### Proof sketch

Every peer's finite sorted fact list is revisited because EOF resets its cursor. Facts inserted before an earlier cursor are therefore exposed on a later sweep. Successful page admissions monotonically add set elements. Under fairness, every retained fact is eventually offered and accepted. Both response pages and `put` requests are split by their actual JSON encoding size, so a legal set whose 32-fact encoding exceeds 1 MiB is transferred in smaller frames rather than violating the wire contract. ∎

### Projection-aware continuation

After projection, the convergence statement changes from raw set equality to the projected admission relation: exact old facts are recognized through the replay index and elided idempotently; disjoint legal later-batch facts are stored and can finalize; visible event, seal, or semantic-ID conflicts are rejected. A mixed page containing both old replays and a legal new batch therefore does not stall its cursor. The positive composition theorem covers this post-projection transfer relation, not reconstruction of the discarded raw set at every nonholder.

### Bounded complete export

A durably pinned raw holder exports a batch in encoded-byte-bounded pages. Every page repeats one stable manifest containing batch, total fact count, total canonical bytes, and seal IDs. The caller checks contiguous offsets, manifest stability, totals, seal set, and final sealed-batch validity before treating the export as complete. This is an integrity-checkable bounded protocol under the fixed honest model, not a cryptographic commitment.

The coordinate-gap comparator does not satisfy the convergence claim: permanent low holes can starve later coordinates, and split occupancy at one coordinate can hide alternatives. In the five-process execution, bounded sender-to-receiver relays require three rounds after two partitioned rounds and a pre-heal restart; post-convergence inventories equal the exact oracle union.

### Acknowledged durability

Under successful file and directory synchronization, an acknowledgment follows a complete durable newline record. Recovery replays complete records, truncates only an incomplete final fragment, and rejects malformed complete records. A complete unacknowledged record may survive; failure does not imply absence. The process-kill experiment preserves 46 acknowledged fact occurrences and reaches their 46-fact union after restart.

The claim excludes power loss, disk corruption, remote filesystems, and adversarial storage.

## 11. Executed build bridge

The retained public input is one licensed pytest fix/backport pair whose full upstream diffs, selected hunks, provenance metadata, and license are packaged. The adapter contract is more general but still finite: it accepts a bounded three-file explicit-backport declaration when both sides have equal path-indexed changed-line payloads. Synchronized changes to both sides, hunk coordinates/context, and bounded record metadata are therefore outside payload identity; one-sided payload/path changes, truncation, binary-style input, or malformed metadata fail closed. The packaged pair produces six normalized facts and gross mass 15. A local four-task graph runs separate subprocesses for eligible tasks in three scenarios, producing seven pass, two fail, and three skip records. Effects do not alter mass. This demonstrates concrete executed effect production but not an upstream pytest build, representative build corpus, distributed scheduler, or proof that arbitrary dependency labels are causal.

## 12. Composed result

### Theorem 11

Under strict normalization, faithful fixed origin membership, finite eventual delivery, synchronized crash-stop storage, and at most `f` endpoint crashes, any nonholder that reaches final-projected has:

1. no mass amplification from presentation or transformation replay;
2. an aggregate equal to the roster's declared batch allocation;
3. eventual agreement among raw survivors receiving the retained union and, after projection, consistent adjudication in which exact old replay is elided, a disjoint legal later batch is admitted, and visible conflict is refused; and
4. at least one complete raw certified copy after every allowed crash set.

### Proof

Combine Lemma 2, Theorems 4–6, Theorem 8, Propositions 9–10, and the projection-aware continuation rule. Before projection, convergence is equality of the retained canonical set. At a projected nonholder it is agreement on the final summary plus the exact replay/no-op, later-batch admission, and conflict-refusal relation; the theorem does not assert that discarded raw data reappears there. The conclusion contains no stronger trust, authentication, timing, or membership property than those premises. ∎

## 13. Scope counterexamples retained as evidence

The artifact deliberately preserves cases that the positive theorem does not reject:

- relation cycles;
- opaque dependency labels;
- a fresh independent mint with identical content;
- a forged but structurally valid imported summary;
- a lying empty origin declaration.

These are not successful defenses. They mark the exact boundary of the result.
