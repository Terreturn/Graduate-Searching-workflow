"""Owner-scoped applicant facts, with provenance for every retained value."""

FIELDS = [
    "education.current_degree", "education.current_field", "education.status",
    "education.completed_degrees", "education.institution", "research.experience",
    "research.interests", "skills.methods", "skills.languages", "experience.other",
    "applicant.status", "background.other",
]


def apply_background(facts, changes, goal, source_turn):
    before = {(f["field"], f["value"]): f for f in facts}
    after = before.copy()
    for c in changes:
        field = c["field"]
        values = set(c["values"])
        for k in list(after):
            if k[0] != field:
                continue
            if (c["op"] == "unset" or (c["op"] == "remove" and k[1] in values)
                    or (c["op"] == "set" and k[1] not in values)):
                del after[k]
        if c["op"] in {"set", "add"}:
            for value in values:
                after.setdefault((field, value), {
                    "field": field, "value": value,
                    "source": {"goal": goal, "turn_id": source_turn, "evidence": c["evidence"]},
                })
    delta = [{"before": before.get(k), "after": after.get(k)}
             for k in sorted(before.keys() | after.keys()) if before.get(k) != after.get(k)]
    return [after[k] for k in sorted(after)], delta
