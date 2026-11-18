"""Plan where to search from a single stored turn and the local source catalog."""
import re

from clarifications import question_context
from model_call import call_model
from rules import STRING, array, choice, degree_branches, degree_scope, record, validate_schema
from sources import CATALOG, find_sources


def plan_search(event, catalog_path=CATALOG):
    if event is None:
        raise ValueError("该目标还没有已保存的需求轮次")
    levels = degree_scope(event["effective_rules"])
    if levels == []:
        return []
    sources = {s["id"]: s for s in find_sources(degrees=levels or (), db_path=catalog_path)}
    catalog = []
    for source in sources.values():
        metadata = {k: v for k, v in source.items() if k not in {"url", "official_url"} and v}
        metadata["has_official_reference"] = bool(source.get("official_url"))
        catalog.append(metadata)
    payload = {
        "utterance": event["message"],
        "summary": event["proposal"]["summary"],
        **question_context(event),
        "effective_rules": event["effective_rules"],
        "rules_by_degree": degree_branches(event["effective_rules"]),
        "personal_background": [{"field": f["field"], "value": f["value"]}
                                for f in event.get("personal_background", {}).get("facts", [])],
        "catalog": catalog,
    }
    fields = {"search_direction": STRING, "source_ids": array(choice(sources) if sources else STRING),
              "search_focus": STRING, "ranking_reason": STRING}
    mixed = bool(levels and len(levels) > 1)
    if mixed:
        fields["degrees"] = array(choice(levels))
    schema = record({"directions": array(record(fields))})
    response = call_model("plan-search.md", payload, schema)
    validate_schema(response, schema)
    plan = []
    for rank, direction in enumerate(response["directions"], start=1):
        title, focus, reason = (direction[k].strip() for k in
                                ("search_direction", "search_focus", "ranking_reason"))
        if not all((title, focus, reason)) or re.search(r"https?://|www\.", " ".join((title, focus, reason)), re.I):
            raise ValueError("搜索计划需要非空文字，链接必须取自来源库")
        ids = direction["source_ids"]
        if any(key not in sources for key in ids):
            raise ValueError("搜索计划引用了来源库以外的记录")
        if mixed:
            target = set(direction["degrees"])
            if not target or any(not target.issubset(sources[key]["degrees"]) for key in ids):
                raise ValueError("搜索方向和入口的学位标签不匹配")
            title = " / ".join(sorted(target)) + " · " + title
        entries = [f'[{sources[key]["name"]}]({sources[key]["url"]})' for key in dict.fromkeys(ids)]
        if entries:
            title += " · " + " · ".join(entries)
        plan.append({"search_direction": title, "priority": f"P{rank}",
                     "search_focus": focus, "ranking_reason": reason})
    if mixed and plan and set(levels) != {d for row in response["directions"] for d in row["degrees"]}:
        raise ValueError("搜索方向遗漏了本轮指定的学位")
    return plan
