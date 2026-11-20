"""Route committed branch deltas; a small offline model describes only permitted tasks."""
from clarifications import question_context
from model_call import MODEL, call_model
from rules import BRANCHES, STRING, array, branch_of, choice, difference, record, validate_schema


def search_rules(rules, branch):
    return [r for r in rules if branch_of(r) == branch and r["role"] != "output" and r["field"] != "ranking.by"]


def branch_changes(event, previous_turn=None):
    result = {}
    for branch in BRANCHES:
        effective = search_rules(event["effective_rules"], branch)
        previous_rules = (previous_turn["effective_rules"] if previous_turn else event.get("before_rules", []))
        before = search_rules(previous_rules, branch)
        previous_questions = (question_context(previous_turn)["pending_questions"] if previous_turn
                              else event.get("before_pending_questions", []))
        previous = [q for q in previous_questions if branch_of(q) == branch]
        pending = [q for q in question_context(event)["pending_questions"] if branch_of(q) == branch]
        result[branch] = {"rules": effective, "diff": difference(before, effective),
                          "pending": pending, "questions_changed": previous != pending,
                          "tasks": [t for t in event["proposal"].get("tasks", []) if t["branch"] == branch]}
    return result


def snapshot(event):
    return event.get("branches") or branch_changes(event)


def route(event):
    states = snapshot(event)
    selected = [b for b, s in states.items() if s["diff"] or s["questions_changed"] or s["tasks"]]
    tasks = {}
    if selected:
        schema = record({"tasks": array(record({"branch": choice(selected), "focus": STRING}))})
        result = call_model("route.md", {"message": event["message"], "permitted": selected,
                            "branches": {b: states[b] for b in selected}}, schema, model=MODEL, effort="low")
        validate_schema(result, schema)
        tasks = {item["branch"]: item["focus"] for item in result["tasks"]}
        if len(result["tasks"]) != len(selected) or set(tasks) != set(selected) or not all(tasks.values()):
            raise ValueError("The router must schedule each changed branch exactly once.")
    default = "advisor" if states["advisor"]["rules"] and not states["school"]["rules"] else "school"
    return {"branches": selected, "tasks": tasks, "view": event.get("view", default)}


def branch_event(event, branch, task):
    state = snapshot(event)[branch]
    output = [r for r in event["effective_rules"] if r["role"] == "output" or r["field"] == "ranking.by"]
    return {**event, "branch": branch, "focus": task, "effective_rules": state["rules"] + output,
            "pending_questions": state["pending"], "proposal": {**event["proposal"], "summary": task}}
