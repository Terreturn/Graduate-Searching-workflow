"""Scoped clarification snapshots stored with the turn that used them."""


from rules import branch_of


def question_snapshot(event):
    if not event:
        return []
    if "pending_questions" in event:
        return event["pending_questions"]
    # Old journals only recorded display text and cannot prove scope or lifetime.
    return new_questions(event.get("turn_id", ""), event.get("message", ""), event["proposal"])


def new_questions(turn, message, proposal):
    questions = proposal.get("questions", [])
    legacy = {"field": "other", "when_degree": "", "when_country": "", "scope": "turn",
              "importance": "required", "evidence": message}
    details = proposal.get("question_details", [legacy] * len(questions))
    return [{"id": f"{turn}/q{i}", "origin_turn": turn, "question": question, **detail}
            for i, (question, detail) in enumerate(zip(questions, details), 1)]


def update_questions(previous, turn, message, proposal):
    pending = {q["id"]: q for q in previous if q["scope"] == "goal"}
    for change in proposal.get("question_resolutions", []):
        question = pending.get(change["id"])
        if question is None or proposal["intent"] in {"inspect", "compare", "undo", "separate_goal"}:
            raise ValueError("Resolution must refer to an open question in this goal.")
        if change["action"] == "partial":
            note = {"turn_id": turn, "evidence": change["evidence"]}
            pending[change["id"]] = {**question, "clarifications": [*question.get("clarifications", []), note]}
            continue
        if question["field"] != "other":
            operations = {"set", "add", "unrestricted"} if change["action"] == "answered" else {"unset", "unrestricted"}
            if not any(branch_of(c) == branch_of(question) and c["field"] == question["field"] and c.get("when_degree", "") == question["when_degree"]
                       and c["when_country"] == question["when_country"] and c["scope"] == question["scope"]
                       and c["op"] in operations
                       and (question["importance"] != "required" or c["role"] in {"scope", "required"})
                       for c in proposal["changes"]):
                raise ValueError("A resolution must also edit the condition in its original scope.")
        del pending[change["id"]]
    for question in new_questions(turn, message, proposal):
        # A repeated prompt must retain its original turn and supporting words.
        identity = ("field", "when_degree", "when_country", "importance", "scope")
        if question["field"] != "tuition.budget":
            identity += ("question",)
        if not any(branch_of(old) == branch_of(question) and all(old[k] == question[k] for k in identity) for old in pending.values()):
            pending[question["id"]] = question
    return list(pending.values())


def question_context(event):
    local = event.get("temporary_conditions", {}).get("conditions", [])
    def superseded(question):
        return question["scope"] == "goal" and any(
            c["op"] in {"set", "unset", "unrestricted"} and c["field"] == question["field"]
            and branch_of(c) == branch_of(question) and c.get("when_country", "") == question["when_country"]
            and c.get("when_degree", "") == question["when_degree"]
            and (question["importance"] != "required" or c["role"] in {"scope", "required"})
            for c in local)
    pending = [q for q in question_snapshot(event) if not superseded(q)]
    return {"open_questions": [q["question"] for q in pending], "pending_questions": pending}
