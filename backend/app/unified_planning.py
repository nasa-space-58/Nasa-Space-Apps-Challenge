"""Lossless native input envelope and bounded R11/H6 identity adapters."""

import hashlib
import json
from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import Field, model_validator

from shared.campaign import CampaignProblem, campaign_fingerprint
from shared.hybrid import Contract, Digest
from shared.unified_planning import (
    CampaignProposal,
    IntegrationContext,
    ModuleEvidence,
    OptionLink,
)

from .campaign_planning import generate_candidates
from .hybrid_recovery import HybridRecoveryRequest


class UnifiedPlanningEnvelope(Contract):
    schema_version: Literal[1] = 1
    context: IntegrationContext
    campaign: CampaignProblem
    recovery: HybridRecoveryRequest
    option_links: tuple[OptionLink, ...] = Field(min_length=1, max_length=32)
    module_evidence: tuple[ModuleEvidence, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def check_connections(self):
        _check_connections(self)
        return self


class PlanningSnapshot(Contract):
    schema_version: Literal[1] = 1
    envelope: UnifiedPlanningEnvelope
    campaign_sha256: Digest
    recovery_sha256: Digest
    snapshot_sha256: Digest


class AdapterError(ValueError):
    """Stable machine-readable code plus an explanation; no silent fallback."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def seal_inputs(envelope: UnifiedPlanningEnvelope) -> PlanningSnapshot:
    """Revalidate and detach inputs; bind all original constraints and evidence."""
    checked = UnifiedPlanningEnvelope.model_validate(envelope.model_dump(mode="python"))
    return PlanningSnapshot(
        envelope=checked,
        campaign_sha256=campaign_fingerprint(checked.campaign),
        recovery_sha256=_digest(checked.recovery),
        snapshot_sha256=_digest(checked),
    )


def to_r11_problem(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope, now_utc: datetime
) -> CampaignProblem:
    """Return the complete current R11 model, including nonprojected constraints."""
    return _current(snapshot, current, now_utc).campaign


def to_h6_request(
    snapshot: PlanningSnapshot, *, current: UnifiedPlanningEnvelope, now_utc: datetime
) -> HybridRecoveryRequest:
    """Return the native current H6 request without any frame/time conversion."""
    return _current(snapshot, current, now_utc).recovery


def map_h6_proposal(
    snapshot: PlanningSnapshot,
    selected_option_ids: tuple[str, ...],
    *,
    current: UnifiedPlanningEnvelope,
    now_utc: datetime,
) -> CampaignProposal:
    """Map a complete H0 selection to a partial, unvalidated R11 proposal."""
    envelope = _current(snapshot, current, now_utc)
    options = {c.option_id: c for c in envelope.recovery.campaign.candidates}
    if (
        not isinstance(selected_option_ids, tuple)
        or any(not isinstance(oid, str) for oid in selected_option_ids)
        or len(set(selected_option_ids)) != len(selected_option_ids)
        or not set(selected_option_ids) <= options.keys()
    ):
        raise AdapterError("invalid_selection", "Expected unique known H6 option IDs")
    missions = [options[oid].mission_id for oid in selected_option_ids]
    if len(missions) != len(set(missions)) or set(missions) != {
        m.mission_id for m in envelope.recovery.campaign.missions
    }:
        raise AdapterError(
            "invalid_selection", "Select exactly one option per H0 mission"
        )
    links = {link.h6_option_id: link.r11_option_id for link in envelope.option_links}
    return CampaignProposal(
        snapshot_sha256=snapshot.snapshot_sha256,
        campaign_fingerprint=snapshot.campaign_sha256,
        proposed_utc=now_utc,
        selections={
            options[oid].mission_id: links[oid] for oid in sorted(selected_option_ids)
        },
    )


def _digest(model: Contract) -> str:
    text = json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current(snapshot, current, now_utc):
    if not isinstance(now_utc, datetime) or now_utc.utcoffset() != timedelta(0):
        raise AdapterError("invalid_time", "now_utc must be an explicit UTC datetime")
    # Pydantic frozen models can still contain mutable dicts. Reparse at every
    # trust boundary and compare the detached payload to the stored seal.
    checked = seal_inputs(snapshot.envelope)
    if (
        snapshot.campaign_sha256 != checked.campaign_sha256
        or snapshot.recovery_sha256 != checked.recovery_sha256
        or snapshot.snapshot_sha256 != checked.snapshot_sha256
        or type(snapshot.schema_version) is not int
        or snapshot.schema_version != 1
    ):
        raise AdapterError(
            "snapshot_tampered", "Stored hashes do not match native inputs"
        )
    fresh = seal_inputs(current)
    if checked.snapshot_sha256 != fresh.snapshot_sha256:
        raise AdapterError(
            "stale_inputs", "Campaign, checkpoint, evidence or context changed"
        )
    context = checked.envelope.context
    if not context.created_utc <= now_utc < context.decision_deadline_utc:
        raise AdapterError(
            "outside_decision_window", "Decision is not yet valid or has expired"
        )
    return checked.envelope


def _check_connections(envelope):
    context, campaign, job = envelope.context, envelope.campaign, envelope.recovery
    h0 = job.campaign
    state = job.checkpoint.estimated_state
    if not (
        state.epoch_utc
        <= context.created_utc
        < context.decision_deadline_utc
        <= min(campaign.window.end_utc, h0.window.end_utc)
    ):
        raise ValueError(
            "Decision window must follow the estimated checkpoint and fit both horizons"
        )
    if (
        not campaign.window.start_utc
        <= h0.window.start_utc
        < h0.window.end_utc
        <= campaign.window.end_utc
    ):
        raise ValueError("H0 horizon must be contained in the original R11 campaign")
    if any(s.record_kind != "estimate" for s in h0.states):
        raise ValueError(
            "Planner state attachments must be estimates, not truth or observations"
        )
    sources = {s.source_id: s for s in context.sources}
    if len(sources) != len(context.sources):
        raise ValueError("Duplicate integration source IDs")
    lineage = {sid: set(source.parent_source_ids) for sid, source in sources.items()}
    if any(not parents <= sources.keys() for parents in lineage.values()):
        raise ValueError("Unknown integration source parent")
    try:
        tuple(TopologicalSorter(lineage).static_order())
    except CycleError as exc:
        raise ValueError("Cyclic integration provenance") from exc
    modules = envelope.module_evidence
    if len({m.module_id for m in modules}) != len(modules):
        raise ValueError("Duplicate module evidence IDs")
    if any(not set(m.source_ids) <= sources.keys() for m in modules):
        raise ValueError("Unknown module evidence source")
    links = envelope.option_links
    if (
        len({link.h6_option_id for link in links}) != len(links)
        or len({link.r11_option_id for link in links}) != len(links)
        or {link.h6_option_id for link in links} != {c.option_id for c in h0.candidates}
    ):
        raise ValueError(
            "Every H0 option requires one unique, injective R11 correspondence"
        )
    if any(
        link.source_id not in sources or sources[link.source_id].kind != "synthetic"
        for link in links
    ):
        raise ValueError(
            "Stage 2 supports only explicitly sourced synthetic correspondences"
        )
    # Never flatten R11 capacity, rate, dependency or cancellation rules into
    # H0 pairwise conflicts. This candidate lookup verifies identity only.
    r11_options = {c.option_id: c for c in generate_candidates(campaign).candidates}
    r11_missions = {m.mission_id: m for m in campaign.missions}
    h0_missions = {m.mission_id: m for m in h0.missions}
    h0_options = {c.option_id: c for c in h0.candidates}
    sites = {s.site.site_id: s.site for s in campaign.sites}
    targets = {t.target_id: t for t in job.recovery.targets}
    target_by_option = {b.option_id: targets[b.target_id] for b in job.bindings}
    for link in links:
        original = r11_options.get(link.r11_option_id)
        option = h0_options[link.h6_option_id]
        if original is None:
            raise ValueError(
                "R11 option is unknown or rejected by native candidate screening"
            )
        if original.mission_id != option.mission_id:
            raise ValueError("H0 and R11 mission identities must agree explicitly")
        mission = r11_missions[original.mission_id]
        if mission.vehicle_id != h0_missions[option.mission_id].vehicle_id:
            raise ValueError("H0 and R11 vehicle identities disagree")
        if option.site != sites[original.site_id]:
            raise ValueError(
                "Site identity/frame/angles differ; no coordinate conversion is implemented"
            )
        if not option.window.start_utc <= original.landing_utc < option.window.end_utc:
            raise ValueError(
                "R11 nominal landing lies outside H0 choice window; no rounding allowed"
            )
        if context.decision_deadline_utc > original.landing_utc:
            raise ValueError("Decision deadline lies after a mapped nominal landing")
        target = target_by_option.get(option.option_id)
        if target is not None and original.landing_utc > target.touchdown_deadline_utc:
            raise ValueError("Mapped landing exceeds the H6 target deadline")
        if mission.requirements.downlink_bits > 0 and not any(
            rate.mission_id == mission.mission_id and rate.rate_bps > 0
            for rate in campaign.link_rates
        ):
            raise ValueError(
                "Mapped mission needs an explicit direct link rate; relay-only mapping is unsupported"
            )
    # Normalize only semantically unordered metadata, not trajectories/vectors.
    object.__setattr__(
        context, "sources", tuple(sorted(context.sources, key=lambda s: s.source_id))
    )
    object.__setattr__(
        envelope, "option_links", tuple(sorted(links, key=lambda b: b.h6_option_id))
    )
    object.__setattr__(
        envelope, "module_evidence", tuple(sorted(modules, key=lambda m: m.module_id))
    )
