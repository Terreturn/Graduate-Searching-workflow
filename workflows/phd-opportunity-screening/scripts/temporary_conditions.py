"""Turn-local operations and their delta against that turn's durable goal."""
from rules import apply_changes, difference


def resolve(turn, rules, changes):
    local = [c for c in changes if c["scope"] in {"turn", "scenario"}]
    effective = apply_changes(rules, local)
    return {"turn_id": turn, "conditions": local, "diff": difference(rules, effective)}, effective


def from_legacy(record):
    return {"turn_id": record["turn_id"],
            "conditions": [c for c in record["proposal"]["changes"]
                           if c["scope"] in {"turn", "scenario"}],
            "diff": record["temporary_diff"]}
