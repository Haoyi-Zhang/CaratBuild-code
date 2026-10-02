# CARAT research artifact

This directory is the executable artifact for *The Last-Fact Problem: Identity, Completion, and Evidence Retention in Federated Build Accounting*. It contains the implementation, differential checkers, deterministic tests, lawful public inputs, experiment drivers, and retained results.

Run the complete offline reproduction:

```bash
./scripts/reproduce.sh
```

The run executes the full unittest surface, all eighteen experiment campaigns, the finite boundary checker, collectors, provenance checks, environment capture, and fail-closed package audits. A nonzero exit status is a failed reproduction. Historical failed checkpoint trials remain under `results/reproduction_failures/`; they are not overwritten by a later successful run.

The evaluated model is a finite fixed faithful-origin roster, crash-stop endpoints, bounded local resources, and eventual delivery after the final failure. Projection retains an exact bounded replay index so old facts are idempotent, visible conflicts remain rejectable, and disjoint later batches can continue. The artifact does not claim Byzantine security, dynamic membership, WAN performance, cryptographic provenance, or production deployment.
