"""Typed proposals and deterministic edits to a conjunction of goal rules."""
from copy import deepcopy
from decimal import Decimal
import re
from personal_background import FIELDS as BACKGROUND_FIELDS


DEGREES = ("master", "PhD", "professional_doctorate")
FIELDS = {
    "search.object": ("scope", {"program", "position", "supervisor"}),
    "degree.type": ("scope", set(DEGREES)),
    "program.country": ("scope preferred", None),
    "program.region": ("scope preferred", None),
    "program.location": ("scope preferred", None),
    "program.institution": ("scope preferred", None),
    "program.topic": ("required preferred", None),
    "program.method": ("required preferred", None),
    "program.track": ("required preferred", {"research", "coursework", "mixed"}),
    "program.thesis": ("required preferred", {"required", "optional", "unavailable"}),
    "supervisor.topic": ("required preferred", None),
    "admission.intake": ("scope preferred", None),
    "eligibility.applicant": ("required", None),
    "funding.coverage": ("required preferred", {"funded", "full", "tuition", "stipend"}),
    "funding.mechanism": ("required preferred", {"guaranteed", "competitive", "separate_application", "self_funded"}),
    "tuition.budget": ("required preferred", None),
    "ranking.by": ("preferred", None),
    "output.limit": ("output", None),
    "output.language": ("output", None),
    "output.format": ("output", None),
}
INTENTS = ["discover", "refine", "compare", "present", "inspect", "clarify", "separate_goal", "undo"]
OPERATIONS = ["set", "add", "remove", "unset", "unrestricted"]
SCOPES = ["goal", "turn", "scenario"]
ROLES = ["scope", "required", "preferred", "output"]
MATCHES = ["any", "all", "none"]


def choice(options):
    return {"type": "string", "enum": list(options)}


def record(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


STRING = {"type": "string"}
TUITION_RATE = record({
    "amount": STRING, "currency": STRING,
    "period": choice(["program_total", "year", "term", "credit"]),
    "fee_status": choice(["unspecified", "domestic", "international", "all"]),
    "charges": choice(["tuition", "tuition_and_mandatory_fees"]),
})
TUITION_CAP = record({**TUITION_RATE["properties"], "operator": choice(["at_most", "below"])})
CHANGE = record({
    "op": choice(OPERATIONS), "scope": choice(SCOPES), "field": choice(FIELDS),
    "role": choice(ROLES), "match": choice(MATCHES), "when_country": STRING,
    "when_degree": choice(["", *DEGREES]),
    "values": array({"anyOf": [STRING, TUITION_CAP]}), "evidence": STRING,
})
QUESTION_DETAIL = record({
    "field": choice([*FIELDS, "other"]), "when_degree": choice(["", *DEGREES]),
    "when_country": STRING, "scope": choice(SCOPES),
    "importance": choice(["required", "preferred"]), "evidence": STRING,
})
RESOLUTION = record({"id": STRING, "action": choice(["answered", "withdrawn", "partial"]), "evidence": STRING})
PROPOSAL = record({
    "intent": choice(INTENTS), "changes": array(CHANGE),
    "background_changes": array(record({
        "op": choice(["set", "add", "remove", "unset"]), "field": choice(BACKGROUND_FIELDS),
        "values": array(STRING), "evidence": STRING,
    })),
    "questions": array(STRING), "question_details": array(QUESTION_DETAIL),
    "question_resolutions": array(RESOLUTION), "summary": STRING,
})


def validate_schema(value, spec):
    if "anyOf" in spec:
        for option in spec["anyOf"]:
            try:
                validate_schema(value, option)
                return
            except ValueError:
                continue
        raise ValueError("字段值与允许的结构不符")
    kind = spec["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(spec["properties"]):
            raise ValueError("提案字段不符合约定")
        for key, child in spec["properties"].items():
            validate_schema(value[key], child)
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError("需要数组")
        for item in value:
            validate_schema(item, spec["items"])
    elif not isinstance(value, str) or ("enum" in spec and value not in spec["enum"]):
        raise ValueError(f"无效值 {value!r}")


def valid_amount(rate):
    return (isinstance(rate, dict) and isinstance(rate.get("amount"), str)
            and re.fullmatch(r"(0|[1-9][0-9]*)(\.[0-9]+)?", rate["amount"]) is not None
            and re.fullmatch("[A-Z]{3}", rate.get("currency", "")) is not None)


def within_budget(cap, rate):
    if not valid_amount(rate) or any(rate[k] != cap[k] for k in ("currency", "period", "charges")):
        return False
    if rate["fee_status"] != "all" and rate["fee_status"] != cap["fee_status"]:
        return False
    if rate["fee_status"] == "unspecified":
        return False
    amount, maximum = Decimal(rate["amount"]), Decimal(cap["amount"])
    return amount <= maximum if cap["operator"] == "at_most" else amount < maximum


def degree_scope(rules):
    """Return allowed degrees, or None if the goal does not restrict the level."""
    allowed = set(DEGREES)
    restricted = False
    for rule in rules:
        if rule["field"] != "degree.type" or rule["status"] == "unrestricted" or rule["when_country"]:
            continue
        restricted = True
        values = set(rule["values"])
        if rule["match"] == "none":
            allowed -= values
        else:
            allowed &= values
    return sorted(allowed) if restricted else None


def rules_at_degree(indexed, degree):
    """A degree-specific edit overrides the shared rule in the same country/role/match slot."""
    def slot(r):
        return (r["field"], r["when_country"], r["role"], r["match"])

    overrides = {slot(r) for r in indexed.values() if degree and r.get("when_degree") == degree}
    def applies(r):
        if r.get("when_degree"):
            return r["when_degree"] == degree
        return slot(r) not in overrides

    return {k: r for k, r in indexed.items() if r["status"] != "unrestricted" and
            applies(r)}


def degree_branches(rules):
    levels = degree_scope(rules)
    if levels is None and not any(r.get("when_degree") for r in rules):
        return {}
    indexed = dict(enumerate(rules))
    return {level: list(rules_at_degree(indexed, level).values())
            for level in (DEGREES if levels is None else levels)}


def validate(proposal, message):
    # Past proposal journals predate when_degree. Model output uses the full current schema.
    normalized = {"question_details": [], "question_resolutions": [], **proposal}
    if isinstance(proposal.get("changes"), list):
        normalized["changes"] = [{"when_degree": "", **c} if isinstance(c, dict) else c
                                 for c in proposal["changes"]]
    validate_schema(normalized, PROPOSAL)
    details = normalized["question_details"]
    if "question_details" in proposal and len(details) != len(proposal["questions"]):
        raise ValueError("Each new clarification needs a scope and source.")
    for item in [*details, *normalized["question_resolutions"]]:
        if not item["evidence"].strip() or item["evidence"] not in message:
            raise ValueError("Clarifications and resolutions must quote the current utterance.")
    for detail in details:
        if detail["when_country"] and not re.fullmatch("[A-Z]{2}", detail["when_country"]):
            raise ValueError("Invalid clarification country.")
        if proposal["intent"] == "compare" and detail["scope"] == "goal":
            raise ValueError("A hypothetical question cannot change the durable goal.")
    intent = proposal["intent"]
    if intent in {"separate_goal", "undo", "inspect"} and (proposal["changes"] or proposal["background_changes"]):
        raise ValueError(f"{intent} 不接受字段修改")
    for c in proposal["background_changes"]:
        if not c["evidence"].strip() or c["evidence"] not in message:
            raise ValueError("个人背景的依据必须逐字出自本轮原话")
        if (c["op"] == "unset") != (not c["values"]) or any(not v.strip() or v != v.strip() for v in c["values"]):
            raise ValueError("个人背景事实需要具体值，unset 使用空数组")
    for change in proposal["changes"]:
        field, values = change["field"], change["values"]
        roles, choices = FIELDS[field]
        if change["role"] not in roles.split():
            raise ValueError(f"{field} 不能使用 {change['role']}")
        if change["role"] == "output" and change["scope"] != "turn":
            raise ValueError("展示要求仅作用于本轮")
        if intent == "compare" and change["scope"] == "goal":
            raise ValueError("假设性比较不能修改目标")
        if change["scope"] == "scenario" and intent != "compare":
            raise ValueError("比较条件需要 compare 意图")
        if not change["evidence"].strip() or change["evidence"] not in message:
            raise ValueError("证据必须逐字出自本轮原话")
        guard = change["when_country"]
        if guard and not re.fullmatch("[A-Z]{2}", guard):
            raise ValueError("分支国家需要两位大写国家代码")
        if field == "degree.type" and change.get("when_degree"):
            raise ValueError("学位范围不能以同一学位作为适用条件")
        if field == "degree.type" and change["match"] == "all" and len(set(map(str, values))) > 1:
            raise ValueError("多个可选学位须使用 any")
        if change.get("when_degree") and roles == "output":
            raise ValueError("表格展示设置作用于整轮")
        if (change["op"] in {"unset", "unrestricted"}) != (len(values) == 0):
            raise ValueError("只有 unset 或 unrestricted 使用空值列表")
        if field == "tuition.budget":
            if (change["match"] != "any" or change["op"] not in {"set", "unset", "unrestricted"}
                    or (values and (len(values) != 1 or not valid_amount(values[0])))):
                raise ValueError("学费上限需要单一金额、币种、计费周期、费用口径和适用身份")
            continue
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in values):
            raise ValueError("条件值不能为空或带首尾空白")
        if choices and not set(values) <= choices:
            raise ValueError(f"{field} 取值无效")
        if field == "program.country" and any(not re.fullmatch("[A-Z]{2}", v) for v in values):
            raise ValueError("国家值需要两位大写代码")
        if field == "program.location" and (change["match"] == "all" or any(
                not re.fullmatch(r"country:[A-Z]{2}|region:[^:\s][^:]*", v) for v in values)):
            raise ValueError("混合地域范围使用 any 或 none；值为 country:US 或 region:Europe 等")
        if field == "output.limit" and any(not re.fullmatch("[1-9][0-9]*", v) for v in values):
            raise ValueError("展示数量须为正整数")
        if field == "output.limit" and values and (len(values) != 1 or change["op"] != "set"):
            raise ValueError("每轮的展示数量只能设置为一个值")


def key(rule):
    return (rule["field"], rule.get("when_degree", ""), rule["when_country"], rule["role"], rule["match"])


def apply_changes(rules, changes):
    indexed = {key(rule): deepcopy(rule) for rule in rules}
    for change in changes:
        identity = key(change)
        old = indexed.get(identity)
        op = change["op"]
        if op == "unset":
            indexed.pop(identity, None)
            continue
        values = change["values"]
        if op in {"remove", "add"}:
            values = set(values)
            if old and old["status"] == "unrestricted" and op == "add":
                continue
            existing = set(old["values"]) if old else set()
            if op == "remove" and (not old or values.isdisjoint(existing)):
                continue
            values = existing - values if op == "remove" else existing | values
        indexed[identity] = {
            **{k: change[k] for k in ("field", "when_country", "role", "match")},
            **({"when_degree": change["when_degree"]} if change.get("when_degree") else {}),
            "status": "unrestricted" if op == "unrestricted" else "specified",
            "values": deepcopy(list(values)) if change["field"] == "tuition.budget" else sorted(set(values)),
        }
        if not values and op == "remove":
            # Removing the last named selection withdraws that rule.
            indexed.pop(identity)
    return [indexed[k] for k in sorted(indexed)]


def difference(before, after):
    left, right = ({key(rule): rule for rule in side} for side in (before, after))
    return [{"before": left.get(k), "after": right.get(k)}
            for k in sorted(left.keys() | right.keys()) if left.get(k) != right.get(k)]
