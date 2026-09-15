"""Public protocol identity, not a deployment or database health assertion."""

CORE_FLOW_CONTRACT = "2026-09-15-core-flow-v2"


def public_release() -> dict:
    # Deliberately no environment, user data, credentials or server paths.
    return {"service": "job-pursuit-mobile", "workflow_contract": CORE_FLOW_CONTRACT}
