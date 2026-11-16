"""Blind discovery, independent evidence collection and source-grounded publication."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import re
from urllib.parse import urlsplit, urlunsplit

from model_call import MODEL, call_model
from planner import plan_search
from rules import (DEGREES, STRING, TUITION_RATE, array, choice, degree_branches, degree_scope,
                   record, rules_at_degree, validate_schema, within_budget)
from sources import CATALOG

AUDITOR = "gpt-6-astra"
ROLES = {"A": "Begin with the highest-priority relevant catalog or national directory, then trace leads to issuers.",
         "B": "Begin with institutional or issuer pages; independently search for exact terms and exceptions."}
FIELDS = ("subject", "institution", "location", "department", "topic", "supervisor", "award",
          "funding", "tuition", "stipend", "duration", "deadline", "intake", "status", "eligibility",
          "application", "rank", "edition", "experience", "question", "answer",
          "degree", "qualification", "track", "thesis", "campus", "funding_mechanism")
STATE = choice(["complete", "incomplete"])
IDENTITY = record({"subject": STRING, "institution": STRING, "cycle": STRING,
                   "degree": choice(["", *DEGREES]), "qualification": STRING, "track": STRING, "campus": STRING})
DISCOVERY = record({"state": STATE, "candidates": array(IDENTITY)})
URLS = array(STRING)
CELL = record({"field": choice(FIELDS), "value": STRING, "urls": URLS})
DECISION = {"id": STRING, "decision": choice(["confirmed", "not_applicable", "unknown",
                                             "contradicted", "conflict"]), "urls": URLS}
REQUIREMENT = {"anyOf": [record(DECISION), record({**DECISION, "tuition_rate": TUITION_RATE})]}
CHECK = record({"id": STRING, "rules": array(REQUIREMENT), "facts": array(CELL),
                "evidence": array(record({"url": STRING, "excerpt": STRING}))})
COLLECTION = record({"state": STATE, "checks": array(CHECK)})
COLUMN = record({"field": choice(FIELDS), "label": STRING, "availability": choice(["required", "optional"])})
AUDIT_ROW = record({"id": STRING, "decision": choice(["publish", "omit"]), "reason": STRING,
                    "rules": array(REQUIREMENT), "cells": array(CELL)})
AUDIT = record({"state": STATE, "columns": array(COLUMN), "rows": array(AUDIT_ROW),
                "links": array(record({"url": STRING, "label": STRING})),
                "sources_label": STRING, "status_label": STRING, "empty_message": STRING,
                "incomplete_message": STRING})


def url_key(url):
    """Compare exact pages, tolerating fragments, host case and a trailing slash."""
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username
                or parts.password or any(c.isspace() or c in '<>\\' for c in url)):
            return ""
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))
    except ValueError:
        return ""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def live(prompt, payload, schema, *, auditor=False):
    trace = []
    model, effort = (AUDITOR, "xhigh") if auditor else (MODEL, "high")
    result = call_model(prompt, payload, schema, model=model, effort=effort, web_trace=trace)
    validate_schema(result, schema)
    return {"model": model, "effort": effort, "checked_at": now(), "web": trace, "result": result}


def by_id(records, expected):
    mapped = {r["id"]: r for r in records}
    if len(mapped) != len(records) or set(mapped) != set(expected):
        raise ValueError("Response omitted or duplicated an expected identifier.")
    return mapped


def independent(prompt, payloads, schema, save):
    # Each call has its own temporary directory, conversation, web trace and payload.
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(live, prompt, payload, schema): role for role, payload in payloads.items()}
        records = {}
        errors = []
        for job in as_completed(jobs):
            role = jobs[job]
            try:
                records[role] = job.result()
            except (RuntimeError, ValueError, OSError) as exc:
                errors.append(f"{role}: {exc}")
            else:
                save(role, records[role])
        if errors:
            raise RuntimeError("\n".join(errors))
    return records


def identities(discoveries):
    found = {}
    for role in ROLES:
        for entry in discoveries[role]["result"]["candidates"]:
            key = tuple(" ".join(entry.get(k, "").casefold().split()) for k in
                        ("institution", "subject", "degree", "qualification", "track", "campus", "cycle"))
            if not key[1]:
                raise ValueError("Empty candidate identity.")
            if key not in found:
                found[key] = {**entry, "id": f"C{len(found) + 1}"}
    return list(found.values())


def proof_urls(check):
    return {url_key(item["url"]) for item in check["evidence"]
            if item["excerpt"].strip() and url_key(item["url"])}


def rules_for_candidate(candidate, rules):
    degree = candidate.get("degree", "")
    if not degree and any(r.get("when_degree") for r in rules.values()):
        raise ValueError("带学位分支的候选项必须标明学位")
    active = rules_at_degree(rules, degree)
    # Both independent readers and the auditor must verify the identity that selects the branch.
    for name in ("degree", "qualification", "track", "campus"):
        if candidate.get(name):
            active["candidate_" + name] = {"field": "candidate." + name, "role": "scope", "match": "any",
                                           "status": "specified", "when_country": "", "values": [candidate[name]]}
    return active


def supports(rule, item):
    if item["decision"] == "not_applicable":
        return bool(rule["when_country"])
    if item["decision"] != "confirmed":
        return False
    if rule["field"] == "tuition.budget":
        return within_budget(rule["values"][0], item.get("tuition_rate"))
    return True


def missing_rules(check, rules):
    decisions = by_id(check["rules"], rules)
    cited = proof_urls(check)
    return [key for key, rule in rules.items()
            if not supports(rule, decisions[key])
            or not cited.intersection(map(url_key, decisions[key]["urls"]))]


def citation_choices(pair, rules):
    """List original-page URLs independently supported under each rule or display field."""
    proofs = [proof_urls(c) for c in pair]
    def collect(section, name, values):
        sides = [{url_key(u): u for item in c[section] if item[name] in values and
                  (section != "facts" or item["value"].strip())
                  for u in item["urls"] if url_key(u) in proof} for c, proof in zip(pair, proofs)]
        if not all(sides):
            return []
        return list((sides[0] | sides[1]).values())

    field_urls = {}
    for field in FIELDS:
        group = {"funding", "tuition", "stipend", "duration"} if field == "funding" else {field}
        urls = collect("facts", "field", group)
        if urls:
            field_urls[field] = urls
    return {"rules": {key: collect("rules", "id", {key}) for key in rules}, "cells": field_urls}


def page_visits(trace):
    visits = set()
    for item in trace:
        action = item.get("action") or {}
        for hit in item.get("results", []):
            ref = hit.get("ref_id", "")
            # A batched web call can contain both search hits and opened pages.
            opened = (bool(re.fullmatch(r"turn\d+(?:view|fetch|find)\d+", ref)) if ref else
                      action.get("type") in {"open_page", "find_in_page", "openPage", "findInPage"})
            key = url_key(hit.get("url", ""))
            if (opened and key and hit.get("title") and
                    not re.search(r"internal error|failed to fetch|not found|^403\b|^404\b", hit["title"], re.I)):
                visits.add(key)
    return visits


def verified_checks(run):
    visited = page_visits(run["web"])
    return [{**item, "evidence": [ev for ev in item["evidence"] if url_key(ev["url"]) in visited]}
            for item in run["result"]["checks"]]


def safe_text(value):
    value = " ".join(value.split())
    if not value or re.search(r"https?://|www\.|\]\(", value, re.I):
        raise ValueError("Expected plain display text.")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;")


def table(headers, rows):
    return "\n".join("| " + " | ".join(row) + " |" for row in
                     [headers, ["---"] * len(headers), *rows])


def publish(audit_call, checks, candidates, rules, incomplete, limit):
    audit = audit_call["result"]
    candidates_by_id = {c["id"]: c for c in candidates}
    by_id(audit["rows"], candidates_by_id)
    visits = page_visits(audit_call["web"])
    columns = [col["field"] for col in audit["columns"]]
    required = {col["field"] for col in audit["columns"] if col.get("availability", "required") == "required"}
    if len(set(columns)) != len(columns):
        raise ValueError("Duplicate publication columns.")
    labels = {url_key(link["url"]): safe_text(link["label"]) for link in audit["links"]}
    rows, blocked = [], []
    for row in audit["rows"]:
        if row["decision"] != "publish":
            continue
        cid = row["id"]
        pair = [checks[role][cid] for role in ROLES]
        active = rules_for_candidate(candidates_by_id[cid], rules)
        choices = citation_choices(pair, active)
        rules_out = by_id(row["rules"], active)
        proofs = [proof_urls(c) for c in pair]
        verified = set.union(*proofs) & visits & labels.keys()
        links = {}
        def backed(urls, allowed):
            keys = [url_key(url) for url in urls]
            return bool(keys) and all(key in allowed and key in verified for key in keys)

        valid = all(not missing_rules(c, active) for c in pair)
        for key, rule in active.items():
            decision = rules_out[key]
            original = [next(item for item in c["rules"] if item["id"] == key) for c in pair]
            eligible = set(map(url_key, choices["rules"][key]))
            valid &= (supports(rule, decision)
                      and all(item["decision"] == decision["decision"] for item in original)
                      and backed(decision["urls"], eligible))
            if valid and rule["field"] == "tuition.budget" and decision["decision"] == "confirmed":
                rates = [item["tuition_rate"] for item in [*original, decision]]
                valid = len({Decimal(rate["amount"]) for rate in rates}) == 1
        cells = {cell["field"]: cell for cell in row["cells"]}
        valid &= bool(required) and required <= set(cells) <= set(columns) and len(cells) == len(row["cells"])
        for field, cell in cells.items():
            valid &= backed(cell["urls"], set(map(url_key, choices["cells"].get(field, []))))
            links.update((url_key(url), url) for url in cell["urls"])
        if not valid:
            blocked.append(cid)
            continue
        refs = [f'[{labels[key]}](<{url}>)' for key, url in links.items()]
        rows.append([safe_text(cells[f]["value"]) if f in cells else "—" for f in columns] + [" · ".join(refs)])
    incomplete |= audit["state"] == "incomplete" or bool(blocked)
    if limit is not None:
        rows = rows[:limit]
    if rows:
        headers = [safe_text(col["label"]) for col in audit["columns"]] + [safe_text(audit["sources_label"])]
        output = table(headers, rows)
        if incomplete:
            output += "\n\n" + table([safe_text(audit["status_label"])], [[safe_text(audit["incomplete_message"])]])
    else:
        msg = audit["incomplete_message" if incomplete else "empty_message"]
        output = table([safe_text(audit["status_label"])], [[safe_text(msg)]])
    return output, blocked


def research(event, catalog_path=CATALOG, checkpoint=lambda report: None):
    if event is None:
        raise ValueError("该目标还没有已保存的需求轮次")
    effective = event["effective_rules"]
    rules = {f"R{i}": rule for i, rule in enumerate(effective, 1)
             if rule["role"] in {"scope", "required"}
             and (rule["status"] != "unrestricted" or rule.get("when_degree"))}
    context = {"utterance": event["message"], "summary": event["proposal"]["summary"],
               "open_questions": event["proposal"].get("questions", []),
               "effective_rules": effective, "rules_by_degree": degree_branches(effective), "hard_rules_by_id": rules,
               "background": [{k: fact[k] for k in ("field", "value")}
                              for fact in event.get("personal_background", {}).get("facts", [])],
               "as_of": now()}
    report = {"stage": "search", "state": "running", **{k: event[k] for k in
              ("owner", "goal", "turn_id", "version")}, "request": context,
              "discoveries": {}, "collections": {}, "rechecks": {}}

    def save(key, role, run):
        report[key][role] = run
        checkpoint(report)

    try:
        checkpoint(report)
        context["directions"] = plan_search(event, catalog_path)
        if not context["directions"]:
            raise ValueError("该轮需求没有可执行的搜索方向")
        checkpoint(report)
        discoveries = independent("discover.md", {r: {**context, "approach": a} for r, a in ROLES.items()},
                                  DISCOVERY, lambda role, run: save("discoveries", role, run))
        candidates = identities(discoveries)
        levels = degree_scope(effective)
        needs_degree = any(r.get("when_degree") for r in effective)
        candidates = [c for c in candidates if (levels is None or c.get("degree") in levels)
                      and (not needs_degree or c.get("degree"))]
        report["candidates"] = candidates
        ids = [c["id"] for c in candidates]
        checks = {r: {} for r in ROLES}
        if candidates:
            applicable = {c["id"]: rules_for_candidate(c, rules) for c in candidates}
            payload = {**context, "candidates": candidates, "candidate_rules_by_id": applicable,
                       "allowed_fields": FIELDS}
            collected = independent("collect.md", {r: payload for r in ROLES}, COLLECTION,
                                    lambda role, run: save("collections", role, run))
            for role, run in collected.items():
                checks[role] = by_id(verified_checks(run), ids)
            # Recheck unresolved candidate requirements once. Only IDs and rule IDs cross roles.
            retry = {}
            for role, other in (("A", "B"), ("B", "A")):
                gaps = {}
                for cid in ids:
                    missing = missing_rules(checks[role][cid], applicable[cid])
                    other_missing = missing_rules(checks[other][cid], applicable[cid])
                    if missing and set(missing) - set(other_missing):
                        gaps[cid] = missing
                if gaps:
                    retry[role] = {**payload, "candidates": [c for c in candidates if c["id"] in gaps],
                                   "rule_ids_to_revisit": gaps}
            if retry:
                rechecked = independent("collect.md", retry, COLLECTION,
                                        lambda role, run: save("rechecks", role, run))
                for role, run in rechecked.items():
                    by_id(verified_checks(run), retry[role]["rule_ids_to_revisit"])
        return review_collections(report, checkpoint)
    except Exception as error:
        report.update(state="failed", error=str(error))
        checkpoint(report)
        raise


def review_collections(report, checkpoint):
    """Review the frozen independent collections; keep prior audit attempts in the test journal."""
    context, candidates = report["request"], report["candidates"]
    ids = [c["id"] for c in candidates]
    rules = context["hard_rules_by_id"]
    checks = {r: {} for r in ROLES}
    incomplete = any(v["result"]["state"] == "incomplete" for v in report["discoveries"].values())
    for stage in ("collections", "rechecks"):
        for role, run in report[stage].items():
            incomplete |= run["result"]["state"] == "incomplete"
            for item in verified_checks(run):
                checks[role][item["id"]] = item
    if any(set(checks[r]) != set(ids) for r in ROLES):
        raise ValueError("Incomplete independent collections.")
    applicable = {c["id"]: rules_for_candidate(c, rules) for c in candidates}
    eligible = {cid: citation_choices([checks[r][cid] for r in ROLES], applicable[cid]) for cid in ids}
    payload = {**context, "candidates": candidates, "checks": checks, "eligible_citations": eligible,
               "candidate_rules_by_id": applicable, "research_incomplete": incomplete, "allowed_fields": FIELDS}
    limit = next((int(r["values"][0]) for r in context["effective_rules"] if r["field"] == "output.limit"
                  and r["status"] != "unrestricted" and r["values"]), None)
    for _ in range(2):
        if "audit" in report:
            report.setdefault("previous_audits", []).append(report.pop("audit"))
        audit = live("audit-search.md", payload, AUDIT, auditor=True)
        report["audit"] = audit
        checkpoint(report)
        output, blocked = publish(audit, checks, candidates, rules, incomplete, limit)
        if not blocked:
            break
        payload["citation_repair"] = {"blocked_candidate_ids": blocked,
                                     "instruction": "Use only an opened eligible URL for its exact field or rule. "
                                     "Supply a link label for each. Omit a row if independent support is lacking."}
    report.update(state="completed", publication_blocks=blocked, table=output)
    checkpoint(report)
    return output
