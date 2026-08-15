import pytest
from pydantic import ValidationError

from agent.models import UpdateInventoryAction
from agent.planner import plan_reconciliation
from agent.safety import validate_reconciliation_plan
from tests.test_reconciliation_planner import v2_state


def safety_fixture(scenario: str = "one-stale-warehouse"):
    state = v2_state(scenario)
    sku = "SKU-001"
    plan = plan_reconciliation(
        state.decisions[sku], state.evidence[sku], state.observations
    )
    return state, plan


def test_valid_forward_and_repair_plans_are_safe() -> None:
    for scenario in ("one-stale-warehouse", "same-version-divergence"):
        state, plan = safety_fixture(scenario)
        result = validate_reconciliation_plan(
            plan,
            state.decisions["SKU-001"],
            state.evidence["SKU-001"],
            state.observations,
        )
        assert result.safe is True
        assert all(check.passed for check in result.checks)


@pytest.mark.parametrize(
    "mutation",
    [
        "non_writable",
        "unknown_target",
        "product_mismatch",
        "invalid_inventory",
        "expected_version",
        "backward_version",
        "same_version_different_inventory",
    ],
)
def test_safety_rejects_unsafe_actions(mutation: str) -> None:
    state, plan = safety_fixture()
    action = plan.actions[0]
    if mutation == "non_writable":
        state.observations[action.warehouse_id].writable = False
    elif mutation == "unknown_target":
        action.warehouse_id = "unknown-warehouse"
    elif mutation == "product_mismatch":
        state.evidence["SKU-001"].observed_records[
            action.warehouse_id
        ].product.name = "Different product"
    elif mutation == "invalid_inventory":
        action.inventory.available = 999
    elif mutation == "expected_version":
        action.expected_current_version += 1
    elif mutation == "backward_version":
        action.target_version = 40
    else:
        action.target_version = action.expected_current_version

    result = validate_reconciliation_plan(
        plan,
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )
    assert result.safe is False
    assert result.rejection_reason


def test_safety_rejects_inconsistent_repair_versions() -> None:
    state, plan = safety_fixture("same-version-divergence")
    plan.actions[-1].target_version = 44

    result = validate_reconciliation_plan(
        plan,
        state.decisions["SKU-001"],
        state.evidence["SKU-001"],
        state.observations,
    )
    assert result.safe is False
    assert any(
        check.name == "repair_version_consistent" and not check.passed
        for check in result.checks
    )


def test_invalid_inventory_is_rejected_by_typed_action_model() -> None:
    _, plan = safety_fixture()
    payload = plan.actions[0].model_dump()
    payload["inventory"] = {"on_hand": 4, "reserved": 5, "available": -1}

    with pytest.raises(ValidationError):
        UpdateInventoryAction.model_validate(payload)
