"""Deterministic reconciliation policy operating only on typed evidence."""

from agent.models import (
    DecisionOutcome,
    EvidenceFinding,
    EvidenceSet,
    EvidenceType,
    ReconciliationDecision,
)


def decide(evidence: EvidenceSet) -> ReconciliationDecision:
    """Choose the safest current outcome from the available evidence."""
    unsafe = _findings(
        evidence,
        EvidenceType.INCOMPATIBLE_PRODUCT_IDENTITY,
        EvidenceType.MISSING_WAREHOUSE_STATE,
        EvidenceType.EVENT_HISTORIES_CONTRADICT,
    )
    if unsafe:
        return _decision(
            evidence,
            DecisionOutcome.ESCALATE,
            unsafe,
            reason=(
                "Automatic reconciliation is unsafe because identity, coverage, "
                "or causal histories are contradictory. No mutation was attempted."
            ),
        )

    if (
        len(evidence.agreement_groups) == 1
        and set(evidence.agreement_groups[0].warehouse_ids)
        == set(evidence.observed_records)
    ):
        agreement = _findings(evidence, EvidenceType.WAREHOUSES_AGREE)
        return _decision(
            evidence,
            DecisionOutcome.NO_ACTION,
            agreement,
            reason="All observed warehouses already agree on the logical state.",
        )

    extension = _findings(
        evidence, EvidenceType.EVENT_HISTORY_EXTENDS_KNOWN_STATE
    )
    if len(extension) == 1:
        source = extension[0].warehouse_ids[0]
        return _decision(
            evidence,
            DecisionOutcome.RECONCILE,
            extension,
            targets=sorted(set(evidence.observed_records) - {source}),
            canonical_source=source,
            reason=(
                "The newer warehouse history contains the known event tip and "
                "subsequent events explain its materialised state."
            ),
        )
    if len(extension) > 1:
        return _decision(
            evidence,
            DecisionOutcome.ESCALATE,
            extension,
            reason=(
                "Multiple newer causal extensions compete, so no unique supported "
                "state can be selected. No mutation was attempted."
            ),
        )

    same_version = _findings(evidence, EvidenceType.SAME_VERSION_DIVERGENCE)
    if same_version and evidence.investigated_warehouses:
        histories_agree = _findings(
            evidence, EvidenceType.EVENT_HISTORIES_AGREE
        )
        supported = _findings(
            evidence, EvidenceType.EVENT_HISTORY_SUPPORTS_STATE
        )
        contradicted = _findings(
            evidence, EvidenceType.EVENT_HISTORY_CONTRADICTS_STATE
        )
        supported_warehouses = {
            warehouse_id
            for finding in supported
            for warehouse_id in finding.warehouse_ids
        }
        contradicted_warehouses = {
            warehouse_id
            for finding in contradicted
            for warehouse_id in finding.warehouse_ids
        }
        if (
            histories_agree
            and supported_warehouses
            and len(contradicted_warehouses) == 1
            and supported_warehouses | contradicted_warehouses
            == set(evidence.observed_records)
        ):
            source = sorted(supported_warehouses)[0]
            return _decision(
                evidence,
                DecisionOutcome.RECONCILE,
                histories_agree + supported + contradicted,
                targets=sorted(contradicted_warehouses),
                canonical_source=source,
                reason=(
                    "The warehouses share one complete causal history; replay "
                    "supports one materialised state and contradicts the target."
                ),
            )
        return _decision(
            evidence,
            DecisionOutcome.ESCALATE,
            histories_agree + supported + contradicted,
            reason=(
                "Available event evidence does not establish one uniquely supported "
                "state. No mutation was attempted."
            ),
        )

    insufficient = _findings(
        evidence,
        EvidenceType.INSUFFICIENT_EVIDENCE,
        EvidenceType.EVENT_HISTORY_CONTRADICTS_STATE,
    )
    if evidence.investigated_warehouses and insufficient:
        return _decision(
            evidence,
            DecisionOutcome.ESCALATE,
            insufficient,
            reason=(
                "The gathered history is incomplete or cannot explain the observed "
                "state. No mutation was attempted."
            ),
        )

    ahead = _findings(
        evidence,
        EvidenceType.WAREHOUSE_AHEAD,
        EvidenceType.EVENT_PROGRESS_AHEAD,
    )
    ahead_warehouses = {
        warehouse_id
        for finding in ahead
        for warehouse_id in finding.warehouse_ids
    }
    if len(ahead_warehouses) == 1:
        warehouse_id = next(iter(ahead_warehouses))
        return _decision(
            evidence,
            DecisionOutcome.INVESTIGATE,
            ahead,
            reason=(
                "The differing warehouse is newer and must not be overwritten by "
                "consensus without causal evidence."
            ),
            investigate=[warehouse_id],
        )
    if len(ahead_warehouses) > 1:
        return _decision(
            evidence,
            DecisionOutcome.ESCALATE,
            ahead,
            reason=(
                "Multiple warehouses present competing newer states. No mutation "
                "was attempted."
            ),
        )

    if same_version:
        return _decision(
            evidence,
            DecisionOutcome.INVESTIGATE,
            same_version,
            reason=(
                "Inventory diverges at identical logical progress, so recency cannot "
                "select a canonical state."
            ),
            investigate=sorted(evidence.observed_records),
        )

    stale = _stale_replica_evidence(evidence)
    if stale is not None:
        target, group_warehouses, findings = stale
        source = sorted(group_warehouses)[0]
        return _decision(
            evidence,
            DecisionOutcome.RECONCILE,
            findings,
            targets=[target],
            canonical_source=source,
            reason=(
                "Independent warehouses agree on inventory, revision, and event "
                "progress while the target is strictly behind in both."
            ),
        )

    return _decision(
        evidence,
        DecisionOutcome.ESCALATE,
        evidence.findings,
        reason=(
            "The available evidence does not establish a safe unique canonical "
            "state. No mutation was attempted."
        ),
    )


def exhausted_investigation_decision(
    decision: ReconciliationDecision,
) -> ReconciliationDecision:
    """Terminate a bounded loop when its plan cannot gather anything new."""
    return decision.model_copy(
        update={
            "outcome": DecisionOutcome.ESCALATE,
            "requires_investigation": False,
            "investigation_warehouses": [],
            "reason": (
                "Available automated evidence was exhausted without establishing "
                "a safe canonical state. No mutation was attempted."
            ),
        }
    )


def _stale_replica_evidence(
    evidence: EvidenceSet,
) -> tuple[str, list[str], list[EvidenceFinding]] | None:
    agreements = _findings(evidence, EvidenceType.WAREHOUSES_AGREE)
    behind = _findings(evidence, EvidenceType.WAREHOUSE_BEHIND)
    progress_behind = _findings(
        evidence, EvidenceType.EVENT_PROGRESS_BEHIND
    )
    for agreement in agreements:
        group = set(agreement.warehouse_ids)
        for version_finding in behind:
            target = version_finding.warehouse_ids[0]
            cursor_finding = next(
                (
                    item
                    for item in progress_behind
                    if item.warehouse_ids == [target]
                    and set(item.related_warehouse_ids) == group
                ),
                None,
            )
            if (
                cursor_finding is not None
                and set(version_finding.related_warehouse_ids) == group
            ):
                return target, sorted(group), [
                    agreement,
                    version_finding,
                    cursor_finding,
                ]
    return None


def _findings(
    evidence: EvidenceSet, *types: EvidenceType
) -> list[EvidenceFinding]:
    selected = set(types)
    return [finding for finding in evidence.findings if finding.type in selected]


def _decision(
    evidence: EvidenceSet,
    outcome: DecisionOutcome,
    findings: list[EvidenceFinding],
    *,
    reason: str,
    targets: list[str] | None = None,
    canonical_source: str | None = None,
    investigate: list[str] | None = None,
) -> ReconciliationDecision:
    return ReconciliationDecision(
        sku=evidence.sku,
        outcome=outcome,
        target_warehouses=targets or [],
        canonical_source=canonical_source,
        canonical_state=(
            evidence.observed_records[canonical_source]
            if canonical_source is not None
            else None
        ),
        evidence_ids=[finding.evidence_id for finding in findings],
        reason=reason,
        requires_investigation=outcome == DecisionOutcome.INVESTIGATE,
        investigation_warehouses=investigate or [],
    )
