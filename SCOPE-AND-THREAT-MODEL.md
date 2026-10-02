# Scope and threat model

| Dimension | Included | Excluded / not inferred |
|---|---|---|
| Sources | Fixed, nonempty, honest roster; exact finite interval/seal declarations | Byzantine omission, equivocation, Sybil identities, dynamic epochs |
| Endpoints | Independent local processes with distinct durable logs | Multi-host or WAN deployment evidence |
| Failures | Declared crash-stop failure family; eventual delivery among survivors | Arbitrary corruption, malicious services, permanent partition liveness |
| Identity | Canonical identifiers validated for non-reuse in four namespaces | Cryptographic origin authentication |
| Finality | Conditional application-level finality for a self-contained window | Consensus finality or global termination detection |
| Retention | Holder-set criterion against an explicit failure family; cardinality `f+1` only for all failures of size at most `f` | Count-only safety under correlated failure domains |
| Projection | Local, certificate-gated projection with exact replay fences | General distributed garbage collection or authenticated summaries |
| Build evidence | Generated histories, a local subprocess DAG, and one reconstructible public patch/backport pair | Production representativeness, human contribution/value attribution |

Safety statements are conditional on the included assumptions. Liveness additionally requires that honest sources produce the required seals and that messages among surviving endpoints are eventually delivered.
