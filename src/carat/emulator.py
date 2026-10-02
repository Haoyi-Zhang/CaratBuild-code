"""Deterministic two-leg anti-entropy fault emulator for five CARAT replicas."""

from __future__ import annotations

from dataclasses import dataclass
import json
import random
from typing import Iterable, Literal

from .facts import Fact, canonical_text, parse_text, split_event
from .protocol import OriginSummary, Replica, states_equal


@dataclass(frozen=True)
class FaultProfile:
    name: str
    partition_until: int = 0
    heal_round: int = 20
    delay_max: int = 0
    duplicate_probability: float = 0.0
    drop_probability: float = 0.0
    crash_node: str | None = None
    crash_start: int = -1
    crash_end: int = -1
    reorder: bool = False
    batch_limit: int = 4096


@dataclass(frozen=True)
class EmulationResult:
    converged: bool
    rounds: int
    messages_sent: int
    summary_messages_sent: int
    fact_messages_sent: int
    messages_delivered: int
    summary_messages_delivered: int
    fact_messages_delivered: int
    delivered_facts: int
    bytes_sent: int
    summary_bytes: int
    duplicate_deliveries: int
    dropped_messages: int
    replicas: tuple[Replica, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "converged": self.converged,
            "rounds": self.rounds,
            "messages_sent": self.messages_sent,
            "summary_messages_sent": self.summary_messages_sent,
            "fact_messages_sent": self.fact_messages_sent,
            "messages_delivered": self.messages_delivered,
            "summary_messages_delivered": self.summary_messages_delivered,
            "fact_messages_delivered": self.fact_messages_delivered,
            "delivered_facts": self.delivered_facts,
            "bytes_sent": self.bytes_sent,
            "summary_bytes": self.summary_bytes,
            "duplicate_deliveries": self.duplicate_deliveries,
            "dropped_messages": self.dropped_messages,
        }


@dataclass
class _Message:
    due: int
    sender: str
    receiver: str
    kind: Literal["summary", "facts"]
    summary: dict[str, OriginSummary] | None = None
    facts: tuple[str, ...] = ()


def _alive(name: str, round_index: int, profile: FaultProfile) -> bool:
    if profile.crash_node != name:
        return True
    return not (profile.crash_start <= round_index < profile.crash_end)


def _partitioned(left: str, right: str, round_index: int, profile: FaultProfile) -> bool:
    if round_index >= profile.partition_until:
        return False
    left_group = int(left[1:]) % 2
    right_group = int(right[1:]) % 2
    return left_group != right_group


def _summary_payload_bytes(summary: dict[str, OriginSummary]) -> int:
    payload = {origin: item.as_dict() for origin, item in summary.items()}
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def run_emulation(
    facts: Iterable[Fact],
    profile: FaultProfile,
    salt: int,
    node_names: tuple[str, ...] = ("n0", "n1", "n2", "n3", "n4"),
    max_rounds: int = 240,
) -> EmulationResult:
    """Run a two-leg anti-entropy exchange under deterministic finite faults.

    A requester sends its bounded summary to a provider.  When that summary
    arrives, the provider computes a bounded fact delta and sends a separate
    response.  Delay, reordering, duplication, loss, partitions, and temporary
    replica outage apply to both legs.  Replicas retain state while unavailable.
    """

    rng = random.Random(4513 + salt * 101)
    replicas = {name: Replica(name) for name in node_names}
    canonical = sorted(canonical_text(fact) for fact in facts)
    for text in canonical:
        fact = parse_text(text)
        origin, _ = split_event(fact["event"])
        if origin not in replicas:
            raise ValueError(f"fact origin {origin!r} has no replica")
        replicas[origin].append_text(text)

    queued: list[_Message] = []
    messages_sent = 0
    summary_messages_sent = 0
    fact_messages_sent = 0
    messages_delivered = 0
    summary_messages_delivered = 0
    fact_messages_delivered = 0
    delivered_facts = 0
    bytes_sent = 0
    summary_bytes = 0
    duplicate_deliveries = 0
    dropped_messages = 0
    stable_rounds = 0

    def schedule(
        *,
        sender: str,
        receiver: str,
        kind: Literal["summary", "facts"],
        round_index: int,
        summary: dict[str, OriginSummary] | None = None,
        fact_payload: tuple[str, ...] = (),
    ) -> None:
        nonlocal messages_sent, summary_messages_sent, fact_messages_sent
        nonlocal bytes_sent, summary_bytes, dropped_messages

        if kind == "summary":
            if summary is None:
                raise ValueError("summary message requires a summary payload")
            payload_bytes = _summary_payload_bytes(summary)
        else:
            if not fact_payload:
                return
            payload_bytes = sum(len(text.encode("utf-8")) + 1 for text in fact_payload)

        copies = 2 if round_index < profile.heal_round and rng.random() < profile.duplicate_probability else 1
        for _ in range(copies):
            messages_sent += 1
            if kind == "summary":
                summary_messages_sent += 1
                summary_bytes += payload_bytes
            else:
                fact_messages_sent += 1
                bytes_sent += payload_bytes

            if round_index < profile.heal_round and rng.random() < profile.drop_probability:
                dropped_messages += 1
                continue

            delay = (
                rng.randrange(profile.delay_max + 1)
                if round_index < profile.heal_round and profile.delay_max
                else 0
            )
            queued.append(
                _Message(
                    due=round_index + delay + 1,
                    sender=sender,
                    receiver=receiver,
                    kind=kind,
                    summary=summary,
                    facts=fact_payload,
                )
            )

    for round_index in range(max_rounds):
        due = [message for message in queued if message.due <= round_index]
        queued = [message for message in queued if message.due > round_index]
        if profile.reorder:
            rng.shuffle(due)
        else:
            due.sort(key=lambda item: (item.due, item.kind, item.sender, item.receiver))

        for message in due:
            if (
                not _alive(message.receiver, round_index, profile)
                or _partitioned(message.sender, message.receiver, round_index, profile)
            ):
                dropped_messages += 1
                continue

            messages_delivered += 1
            if message.kind == "summary":
                summary_messages_delivered += 1
                provider = replicas[message.receiver]
                assert message.summary is not None
                payload = tuple(
                    provider.delta_for(message.summary, batch_limit=profile.batch_limit)
                )
                if payload:
                    schedule(
                        sender=message.receiver,
                        receiver=message.sender,
                        kind="facts",
                        round_index=round_index,
                        fact_payload=payload,
                    )
            else:
                fact_messages_delivered += 1
                receiver = replicas[message.receiver]
                for text in message.facts:
                    if receiver.append_text(text):
                        delivered_facts += 1
                    else:
                        duplicate_deliveries += 1

        values = tuple(replicas[name] for name in node_names)
        if round_index >= profile.heal_round and not queued and states_equal(values):
            stable_rounds += 1
            if stable_rounds >= 2:
                return EmulationResult(
                    True,
                    round_index + 1,
                    messages_sent,
                    summary_messages_sent,
                    fact_messages_sent,
                    messages_delivered,
                    summary_messages_delivered,
                    fact_messages_delivered,
                    delivered_facts,
                    bytes_sent,
                    summary_bytes,
                    duplicate_deliveries,
                    dropped_messages,
                    values,
                )
            continue
        stable_rounds = 0

        # Every live requester advertises its own state to each live provider.
        # The provider's response is a separate message and can fail independently.
        for requester_name in node_names:
            if not _alive(requester_name, round_index, profile):
                continue
            requester_summary = replicas[requester_name].summary()
            for provider_name in node_names:
                if requester_name == provider_name:
                    continue
                if not _alive(provider_name, round_index, profile):
                    continue
                if _partitioned(requester_name, provider_name, round_index, profile):
                    continue
                schedule(
                    sender=requester_name,
                    receiver=provider_name,
                    kind="summary",
                    round_index=round_index,
                    summary=requester_summary,
                )

    values = tuple(replicas[name] for name in node_names)
    return EmulationResult(
        states_equal(values),
        max_rounds,
        messages_sent,
        summary_messages_sent,
        fact_messages_sent,
        messages_delivered,
        summary_messages_delivered,
        fact_messages_delivered,
        delivered_facts,
        bytes_sent,
        summary_bytes,
        duplicate_deliveries,
        dropped_messages,
        values,
    )
