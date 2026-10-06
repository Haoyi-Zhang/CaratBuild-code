# Fact, window, retention, and projection schema

## 1. Canonical fact set

Replica state is a finite set of canonical UTF-8 JSON texts. Parsing rejects duplicate object fields, unknown fields, malformed numbers, invalid enums, repeated references, and noncanonical structures. Canonical text is the set key. Merge is set union; two different facts at one event coordinate remain distinct and cause refusal rather than last-writer selection.

Every fact has:

- `kind` — `mint`, `presentation`, `transform`, `effect`, or `seal`;
- `event` — an origin-owned coordinate `origin:counter`;
- `batch` — the accounting-window name;
- a kind-specific globally scoped identifier.

The network service bounds each canonical fact at 32 KiB, each length-prefixed JSON frame at 1 MiB, and each endpoint store at 2,048 facts or 32 MiB. A page is also capped at 32 facts, but both page and `put` batching are shortened by the actual encoded JSON byte budget. The complete raw export is paged under the same frame budget and carries a stable manifest containing batch name, fact count, canonical-byte count, and seal IDs. Larger direct in-memory experiments bypass the service limits and are reported separately.

## 2. Fact kinds

### Mint

A mint defines one work-unit identity and positive integer mass:

```text
unit, mass, batch, source, event
```

Gross mass is the sum of masses over uniquely defined `unit` values. A second distinct definition of the same unit is refused. No other fact kind creates a unit or mass.

### Presentation

A presentation names a branch label and a finite duplicate-free signed set of unit identities. Let `A(p)` denote the resulting partial function from unit identities to signs in `{+1,-1}`. Every named unit must have one mint. Presentation facts expose representations without minting the units again.

### Transform

A transform names duplicate-free, disjoint input and output presentation IDs and one declared law. The wire field `fresh` is retained for schema compatibility but **must be the empty list**. Unit creation belongs only to mint facts.

For input presentations `p_i` and output presentations `q_j`:

- `rebase`: exactly one input and one output, with `A(q_1)=A(p_1)`;
- `cherry_pick`: exactly one input and one output, with `A(q_1)=A(p_1)`;
- `fork`: exactly one input and at least one output, with `A(q_j)=A(p_1)` for every output;
- `squash`: at least two inputs and exactly one output. The union of the input signed sets must be sign-consistent: whenever a unit occurs in more than one input, every occurrence has the same sign. The output is exactly that union;
- `revert`: exactly one input and one output, with `A(q_1)(u)=-A(p_1)(u)` on the same unit domain.

The language checks only these declared signed-set relations. It does not infer semantic source equivalence and does not require an acyclic relation graph. Agreement between the decoder and separately structured checker is differential validation of two implementations of this written contract; their agreement alone is not a proof that the contract is correct.

### Effect

An effect names one unit, one presentation, an outcome (`pass`, `fail`, or `skip`), and an opaque dependency label. The named unit must belong to the named presentation. Effects do not affect gross mass. The core checker does not prove that the dependency label is a real executable edge.

### Seal

A seal is zero-mass completion evidence for one origin and batch. It contains:

- `seal`, a globally scoped seal identifier;
- `origin` and `batch`;
- `previous`, the previous frontier/seal coordinate or zero for genesis;
- `frontier`, the last data coordinate allocated to the batch;
- its own `event` coordinate immediately after the frontier.

For origin `o`, the seal declares the interval `(previous, frontier]`. The interval may be empty. To keep validation and diagnostics bounded, `frontier - previous` must not exceed 300,000. Missing-coordinate diagnostics inspect observed coordinates, emit at most 64 individual gaps, and then one aggregate missing-count diagnostic. The positive theorem assumes that each origin truthfully reports its complete allocation to the batch.

## 3. Ordinary integrity

A determined accepted state has:

- one definition per semantic identifier;
- one canonical fact per event coordinate;
- all named units and presentations resolved;
- every transform satisfying the signed-set law above;
- every effect unit belonging to its presentation.

The decoder emits refusal witnesses. The separately structured checker reevaluates their public predicates and verifies inclusion minimality under the fixed support convention. It shares the canonical normalizer and therefore supplies differential validation rather than an independent formal semantics.

## 4. Fixed-roster finality

For a nonempty fixed origin roster, a batch is final only when:

1. every roster origin has exactly one batch seal and no outside origin contributes batch data or a seal;
2. each covered coordinate contains exactly one non-seal batch fact;
3. no same-batch fact lies outside the declared intervals;
4. every predecessor chain is present, origin-consistent, strictly decreasing, and reaches genesis;
5. event coordinates and mint, presentation, transform, effect, and seal IDs are globally unambiguous;
6. the batch is self-contained: its relations do not require definitions from another window;
7. the ordinary decoder and separate checker both accept and agree on unit count, mass, and normalized violations.

A final result is stable only under admissible extensions: later batches use new coordinates and globally fresh identifiers; exact old facts may be replayed idempotently. A same-window out-of-range fact, a different fact at a deleted coordinate, an additional same-batch seal, or later identifier reuse produces refusal rather than a silent revised final value.

## 5. Retention receipts and certificates

A raw endpoint may issue a receipt only after it stores the complete batch and both finality paths accept. A receipt names the batch, holder endpoint, crash fault bound `f`, and complete set of origin seal IDs.

Receipt persistence is synchronized before acknowledgment. Receipt records are bounded and newline terminated. Recovery replays complete records, truncates only an incomplete final fragment, and rejects any malformed newline-terminated record. The endpoint reloads each receipt as a permanent raw-data pin before serving requests; a pinned holder refuses projection even under another otherwise valid certificate.

A retention certificate combines receipts and is valid only when the origin and endpoint rosters equal the configured fixed rosters, the fault bound is in range, receipts agree on batch/fault bound/seal set, holder names are distinct and belong to the endpoint roster, and at least `f+1` holders are named. The certificate is structurally trusted and unsigned. It is not a Byzantine attestation.

## 6. Projection summary and projected adjudication

A nonholder may project a final self-contained batch after validating a retention certificate. The summary stores:

- exact `(unit, mass)` pairs and the reported aggregate fields;
- every presentation, transform, and effect identifier;
- effect counts grouped by unit and outcome;
- an exact compressed index of every discarded canonical non-seal fact;
- exact retained seal-anchor facts;
- the certificate.

The exact replay index is bounded at 32 MiB of uncompressed canonical text. It is not a constant-size sketch and it weakens compression relative to an identifier-only summary, but it is what lets a projected endpoint distinguish an idempotent replay from a different fact reusing a deleted event coordinate. On load, the endpoint revalidates the certificate, checks that it is not a named raw holder, decodes the replay index, recomputes all aggregates and identifier sets, and requires the retained anchors to match exactly. Summary and certificate persistence precede atomic raw-log replacement. Recovery rechecks the same conditions before completing an interrupted replacement.

Projected admission is defined as follows:

- an exact replay of a discarded fact is accepted as an idempotent no-op;
- a different fact at a discarded event coordinate is rejected;
- an extra same-batch seal or a seal ID reused in another batch is rejected;
- live reuse of any projected unit, presentation, transform, effect, event, or seal ID is rejected;
- a disjoint valid later batch is admitted and can finalize normally.

Every projected query and cached finality response revalidates these fences against live facts and retained anchors. Thus bypassing the normal admission path and persisting a visible conflict makes query/finalize/restart fail closed instead of serving the cached `True`. The summary still does not preserve arbitrary branch labels, transformation-edge detail, dependency labels, or every discarded witness organization; imported summaries are structurally trusted rather than cryptographically authenticated.

## 7. Services and transport

The original bounded service uses five loopback listeners and five logs in one event-loop process. The retention experiment uses five independent OS processes and five logs. Its controller schedules bounded sender-to-receiver page relays, preserves a two-group partition for two rounds, restarts one endpoint before healing, and never constructs or broadcasts the global union. Frames are four-byte big-endian length-prefixed JSON.

The correctness transport repeatedly requests pages of complete canonical facts. Both fact count and actual encoded JSON bytes bound every response and every `put` request. A mixed raw/projected receiver filters exact replays as idempotent no-ops while admitting the remaining new facts atomically; a genuine coordinate or identifier conflict still rejects the admission. End-of-file resets the cursor so later sweeps revisit earlier sort positions. Under a finite quiescent union, fair successful sweeps, surviving copies, and sufficient capacity, every connected survivor eventually holds the same admitted set. After projection the statement is conditional on the projected adjudication contract above: exact old replay is elided, disjoint later batches propagate, and visible conflicts are refused. This is not a communication-optimal protocol.

A raw pinned endpoint exports a complete batch through bounded pages. The caller verifies one stable manifest, returned offsets, an advancing cursor, total fact count, total canonical bytes, and a final sealed-batch check. It does not separately bind the manifest's batch or seal-ID fields to the reconstructed facts. The export mechanism provides bounded and checkable completeness inside the fixed-roster model; it is not a cryptographic commitment.

A separate coordinate-gap transport is retained as a negative comparator. Permanent low holes can hide later facts, and alternative facts at one coordinate can remain mutually undiscovered.

## 8. Durable-log semantics

A whole admission is validated before append. Complete canonical records are newline terminated, flushed, synchronized, and acknowledged only afterward. Recovery replays duplicate complete records idempotently, truncates only an incomplete final fragment, rejects a malformed complete record, and reloads and revalidates summaries, certificates, replay indexes, seal anchors, and holder pins before serving requests.

A complete unacknowledged record may survive an ambiguous failure. The claim covers tested process termination under the stated synchronization assumptions, not power loss or disk corruption.
