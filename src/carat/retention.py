"""Crash-stop raw-retention certificates for sealed accounting batches.

The certificate is intentionally not a cryptographic attestation.  In the
artifact's fixed-membership crash-stop model, a receipt is returned by an
identified endpoint only after that endpoint has durably stored and
independently validated the raw sealed batch.  Byzantine lies and endpoint
impersonation are outside this model and are addressed by an impossibility
boundary in the paper.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .closure import sealed_query
from .facts import Fact, canonical_pair, parse_text
from .independent_check import check_sealed


@dataclass(frozen=True)
class RetentionReceipt:
    batch: str
    holder: str
    max_crashes: int
    seal_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "batch": self.batch,
            "holder": self.holder,
            "max_crashes": self.max_crashes,
            "seal_ids": list(self.seal_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RetentionReceipt":
        if type(value) is not dict or set(value) != {
            "batch", "holder", "max_crashes", "seal_ids"
        }:
            raise ValueError("malformed retention receipt")
        batch = value["batch"]
        holder = value["holder"]
        max_crashes = value["max_crashes"]
        seal_ids = value["seal_ids"]
        if type(batch) is not str or not batch or type(holder) is not str or not holder:
            raise ValueError("invalid retention receipt identity")
        if type(max_crashes) is not int or max_crashes < 0:
            raise ValueError("invalid retention fault bound")
        if (type(seal_ids) is not list or not seal_ids
                or any(type(item) is not str or not item for item in seal_ids)
                or len(seal_ids) != len(set(seal_ids))):
            raise ValueError("invalid retention seal set")
        return cls(batch, holder, max_crashes, tuple(sorted(seal_ids)))


@dataclass(frozen=True)
class RetentionCertificate:
    batch: str
    max_crashes: int
    seal_ids: tuple[str, ...]
    holders: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "batch": self.batch,
            "max_crashes": self.max_crashes,
            "seal_ids": list(self.seal_ids),
            "holders": list(self.holders),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RetentionCertificate":
        if type(value) is not dict or set(value) != {
            "batch", "max_crashes", "seal_ids", "holders"
        }:
            raise ValueError("malformed retention certificate")
        batch = value["batch"]
        max_crashes = value["max_crashes"]
        seal_ids = value["seal_ids"]
        holders = value["holders"]
        if type(batch) is not str or not batch:
            raise ValueError("invalid retention batch")
        if type(max_crashes) is not int or max_crashes < 0:
            raise ValueError("invalid retention fault bound")
        if (type(seal_ids) is not list or not seal_ids
                or any(type(item) is not str or not item for item in seal_ids)
                or len(seal_ids) != len(set(seal_ids))):
            raise ValueError("invalid retention seal set")
        if (type(holders) is not list
                or any(type(item) is not str or not item for item in holders)
                or len(holders) != len(set(holders))
                or len(holders) < max_crashes + 1):
            raise ValueError("invalid retention holder set")
        return cls(batch, max_crashes, tuple(sorted(seal_ids)), tuple(sorted(holders)))


def _seal_ids(values: Iterable[str | Fact], batch: str, origins: Iterable[str]) -> tuple[str, ...]:
    roster = tuple(sorted(origins))
    wanted = set(roster)
    seals: dict[str, str] = {}
    for value in values:
        text, fact = canonical_pair(value)
        del text
        if fact["kind"] == "seal" and fact["batch"] == batch and fact["origin"] in wanted:
            if fact["origin"] in seals:
                raise ValueError("multiple batch seals for one origin")
            seals[fact["origin"]] = fact["seal"]
    if set(seals) != wanted:
        raise ValueError("retention receipt requires every origin seal")
    return tuple(sorted(seals.values()))


def issue_receipt(
    values: Iterable[str | Fact],
    batch: str,
    origins: Iterable[str],
    holder: str,
    max_crashes: int,
) -> RetentionReceipt:
    if type(holder) is not str or not holder:
        raise ValueError("holder must be a non-empty endpoint name")
    roster = tuple(origins)
    if type(max_crashes) is not int or not 0 <= max_crashes < len(roster):
        raise ValueError("fault bound must be smaller than the replica count")
    texts = sorted({canonical_pair(value)[0] for value in values})
    closed = sealed_query(texts, batch, roster)
    verified = check_sealed(texts, batch, roster)
    if (
        not closed.final
        or not verified.accepted
        or closed.gross_mass != verified.gross_mass
        or closed.unique_units != verified.unique_units
    ):
        raise ValueError("raw batch is not independently finalized")
    # A receipt holder must actually retain all non-seal batch facts.  The
    # closure checks above establish interval coverage; this explicit check
    # prevents issuing a receipt from a projection-only state.
    if not any(
        fact["kind"] != "seal" and fact.get("batch") == batch
        for fact in (parse_text(text) for text in texts)
    ):
        raise ValueError("receipt holder has no raw batch facts")
    return RetentionReceipt(
        batch=batch,
        holder=holder,
        max_crashes=max_crashes,
        seal_ids=_seal_ids(texts, batch, roster),
    )


def certify(receipts: Iterable[RetentionReceipt]) -> RetentionCertificate:
    values = tuple(receipts)
    if not values:
        raise ValueError("at least one retention receipt is required")
    first = values[0]
    if any(
        receipt.batch != first.batch
        or receipt.max_crashes != first.max_crashes
        or receipt.seal_ids != first.seal_ids
        for receipt in values
    ):
        raise ValueError("retention receipts do not describe one sealed batch")
    holders = tuple(sorted(receipt.holder for receipt in values))
    if len(holders) != len(set(holders)):
        raise ValueError("duplicate retention holder")
    if len(holders) < first.max_crashes + 1:
        raise ValueError("retention threshold is below f+1")
    return RetentionCertificate(
        batch=first.batch,
        max_crashes=first.max_crashes,
        seal_ids=first.seal_ids,
        holders=holders,
    )


def validate_certificate(
    certificate: RetentionCertificate,
    origins: Iterable[str],
    replicas: Iterable[str],
) -> None:
    """Validate a certificate against the fixed crash-stop configuration.

    This is a structural membership check, not a signature verification.  The
    caller's authenticated control plane is assumed to bind endpoint names to
    processes in the fixed-membership model.
    """

    origin_roster = tuple(origins)
    replica_roster = tuple(replicas)
    if (not origin_roster or len(origin_roster) != len(set(origin_roster))
            or any(type(item) is not str or not item for item in origin_roster)):
        raise ValueError("invalid origin roster")
    if (not replica_roster or len(replica_roster) != len(set(replica_roster))
            or any(type(item) is not str or not item for item in replica_roster)):
        raise ValueError("invalid replica roster")
    if not 0 <= certificate.max_crashes < len(replica_roster):
        raise ValueError("certificate fault bound is outside the replica roster")
    if len(certificate.seal_ids) != len(origin_roster):
        raise ValueError("certificate does not identify every origin seal")
    if not set(certificate.holders) <= set(replica_roster):
        raise ValueError("certificate names a holder outside the fixed membership")
    if len(certificate.holders) < certificate.max_crashes + 1:
        raise ValueError("retention threshold is below f+1")


def surviving_holders(
    certificate: RetentionCertificate, failed: Iterable[str]
) -> tuple[str, ...]:
    failed_set = set(failed)
    if len(failed_set) > certificate.max_crashes:
        raise ValueError("failure set exceeds the certified bound")
    return tuple(holder for holder in certificate.holders if holder not in failed_set)
