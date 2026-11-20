"""Blind discovery, independent evidence collection and source-grounded publication."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit
from entities import entity_key, norm, program_key, url_key
from result_store import fresh

from clarifications import question_context
from public_facts import OBSERVATIONS, rate_key, render_fact
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
          "degree", "qualification", "track", "thesis", "campus", "funding_mechanism", "research_project", "mentoring", "recruitment", "supervision", "supervisor_background")
STATE = choice(["complete", "incomplete"])
IDENTITY = record({"subject": STRING, "institution": STRING, "cycle": STRING,
                   "degree": choice(["", *DEGREES]), "qualification": STRING, "track": STRING, "campus": STRING})
DISCOVERY = record({"state": STATE, "candidates": array(IDENTITY)})
LINK_IDENTITY = record({**IDENTITY["properties"], "person": STRING, "profile_url": STRING,
                        "program_url": STRING, "pathway": choice(["general", "thesis", "coursework", "mixed"])})
SCHOOL_DISCOVERY = record({"state": STATE, "candidates": array({"anyOf": [LINK_IDENTITY, IDENTITY]})})
ADVISOR_DISCOVERY = record({"state": STATE, "candidates": array(LINK_IDENTITY)})
URLS = array(STRING)
TEXT_CELL = {"field": choice(FIELDS), "value": STRING, "urls": URLS}
CELL = {"anyOf": [record(TEXT_CELL), *(record({**TEXT_CELL, "field": choice([field]), "observation": schema})
                                     for field, schema in OBSERVATIONS.items())]}
DECISION = {"id": STRING, "decision": choice(["confirmed", "not_applicable", "unknown",
                                             "contradicted", "conflict"]), "urls": URLS}
REQUIREMENT = {"anyOf": [record(DECISION), record({**DECISION, "tuition_rate": TUITION_RATE}),
                         record({**DECISION, "country_code": STRING})]}
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


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def live(prompt, payload, schema, *, auditor=False):
    trace = []
    model, effort = (AUDITOR, "xhigh") if auditor else (MODEL, "high")
    skipped = 0
    for attempt in range(2):
        try:
            result = call_model(prompt,payload,schema,model=model,effort=effort,web_trace=trace)
            validate_schema(result,schema)
            if schema is COLLECTION:
                expected = {c["id"] for c in payload["candidates"]}
                checks = by_id(result["checks"],expected)
                for cid,check in checks.items():
                    by_id(check["rules"],payload["candidate_rules_by_id"][cid])
            return {"model":model,"effort":effort,"checked_at":now(),"web":trace,"result":result,
                    "discarded_web_calls":skipped}
        except (RuntimeError,ValueError) as exc:
            if attempt or (isinstance(exc,RuntimeError) and "no completed web searches" not in str(exc)):
                raise
            skipped += len(trace)
            trace = []
            payload = {**payload,"retry_instruction":"Open the exact official pages using live web tools. "
                       "Return only one complete object satisfying the supplied schema.",
                       "previous_validation_error":str(exc)}


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
            if entry.get("program_url") or entry.get("profile_url"):
                key = entity_key(entry)
            if key not in found:
                found[key] = {**entry, "id": f"C{len(found) + 1}"}
    return list(found.values())


def proof_urls(check):
    return {url_key(item["url"]) for item in check["evidence"]
            if item["excerpt"].strip() and url_key(item["url"])}


# Assigned ISO 3166-1 alpha-2 country/territory codes.
COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM
JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP
MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())


def degree_questions(pending, candidate):
    degree = candidate.get("degree")
    return [q for q in pending if q["importance"] == "required"
            and (not degree or not q["when_degree"] or q["when_degree"] == degree)]


def bind_program_evidence(candidates, trusted):
    """Bind only matching, still-current identity decisions from a published school snapshot."""
    for candidate in candidates:
        candidate.pop("_program_evidence", None)
        key = program_key(candidate)
        if not key or not key[0]:
            continue
        for item in trusted:
            prior = item["identity"]
            if (key != program_key(prior) or not fresh(item, "identity") or
                    any(norm(candidate.get(k, "")) != norm(prior.get(k, "")) for k in ("institution", "campus", "track", "cycle"))):
                continue
            proofs = {}
            for decision in item["decisions"]:
                name = decision["rule"]["field"].removeprefix("candidate.")
                if (name in {"degree", "qualification", "campus", "subject", "institution", "program_url"}
                        and decision["rule"]["field"] == "candidate." + name
                        and decision["rule"]["values"] == [candidate.get(name)]
                        and decision["decision"]["decision"] == "confirmed" and decision["decision"]["urls"]
                        and fresh(decision, name)):
                    proofs["candidate_" + name] = decision
            if proofs:
                candidate["_program_evidence"] = proofs
                break
    return candidates


def bind_advisor_evidence(candidates, trusted):
    for candidate in candidates:
        candidate.pop("_advisor_evidence", None)
        if not candidate.get("person") or not candidate.get("profile_url"):
            continue
        binding = [candidate.get(k, "") for k in
                   ("person", "profile_url", "institution", "subject", "program_url", "degree", "pathway", "campus")]
        for item in trusted:
            if entity_key(candidate) != entity_key(item["identity"]) or not fresh(item, "identity"):
                continue
            proofs = {}
            for key, expected in [("person", [binding[0]]), ("profile_url", [binding[1]]), ("supervision", binding)]:
                for d in item["decisions"]:
                    if (d["rule"]["field"] == "candidate."+key and d["rule"]["values"] == expected
                            and d["decision"]["decision"] == "confirmed" and d["decision"]["urls"] and fresh(d,key)):
                        proofs["candidate_"+key] = d
                        break
            if proofs:
                candidate["_advisor_evidence"] = proofs
                break


def saved_evidence(candidate):
    return {**candidate.get("_program_evidence", {}), **candidate.get("_advisor_evidence", {})}


def rules_for_candidate(candidate, rules, pending=()):
    degree = candidate.get("degree", "")
    if not degree and any(r.get("when_degree") for r in rules.values()):
        raise ValueError("带学位分支的候选项必须标明学位")
    active = rules_at_degree(rules, degree)
    # Both independent readers and the auditor must verify the identity that selects the branch.
    for name in ("degree", "qualification", "track", "campus"):
        if candidate.get(name):
            active["candidate_" + name] = {"field": "candidate." + name, "role": "scope", "match": "any",
                                           "status": "specified", "when_country": "", "values": [candidate[name]]}
    if "program_url" in candidate:
        for name in ("subject", "institution", "person", "pathway"):
            if candidate.get(name) and candidate[name] != "general":
                active["candidate_" + name] = {"field": "candidate." + name, "role": "scope", "match": "any",
                                               "status": "specified", "when_country": "", "values": [candidate[name]]}
    for name in ("program_url", "profile_url"):
        if candidate.get(name):
            active["candidate_" + name] = {"field": "candidate." + name, "role": "scope", "match": "any",
                                           "status": "specified", "when_country": "", "values": [candidate[name]]}
    if candidate.get("person") and candidate.get("program_url"):
        active["candidate_supervision"] = {"field": "candidate.supervision", "role": "required", "match": "all",
            "status": "specified", "when_country": "", "values": [candidate[k] for k in
                ("person", "profile_url", "institution", "subject", "program_url", "degree", "pathway", "campus")]}
    if (any(r["when_country"] or r["field"] == "program.country" or
            (r["field"] == "program.location" and any(v.startswith("country:") for v in r["values"]))
            for r in active.values())
            or any(q["when_country"] for q in degree_questions(pending, candidate))):
        active["candidate_country"] = {"field": "candidate.country", "role": "scope", "match": "any",
                                       "status": "specified", "when_country": "", "values": []}
    for key in saved_evidence(candidate):
        active.pop(key, None)
    if candidate.get("person") and candidate.get("program_url"):
        active.pop("candidate_pathway", None)  # Included in the exact supervision tuple.
    return active


def country_of(decisions, proof):
    decision = decisions.get("candidate_country", {})
    code = decision.get("country_code")
    pages = {url_key(u) for u in decision.get("urls", [])}
    if decision.get("decision") == "confirmed" and code in COUNTRIES and pages and pages <= proof:
        return code


def supports(rule, item, country=None):
    guard = rule["when_country"]
    if guard and not country:
        return False
    if guard and guard != country:
        return item["decision"] == "not_applicable"
    if item["decision"] != "confirmed":
        return False
    if rule["field"] == "candidate.supervision":
        hosts = {urlsplit(value).hostname for value in (rule["values"][1],rule["values"][4])}
        return any(urlsplit(url_key(u)).hostname in hosts for u in item["urls"] if url_key(u))
    if rule["field"] in {"candidate.program_url", "candidate.profile_url"}:
        canonical = url_key(rule["values"][0])
        return bool(canonical) and canonical in {url_key(u) for u in item["urls"]}
    if rule["field"] == "candidate.country":
        return bool(country)
    if rule["field"] in {"program.country", "program.location"} and country:
        values = rule["values"]
        codes = {v.removeprefix("country:") for v in values if not v.startswith("region:")}
        if rule["match"] == "none" and country in codes:
            return False
        if rule["match"] == "all" and codes - {country}:
            return False
        if rule["match"] == "any" and len(codes) == len(values) and country not in codes:
            return False
    if rule["field"] == "tuition.budget":
        return within_budget(rule["values"][0], item.get("tuition_rate"))
    return True


def pending_in_scope(pending, rules):
    levels = degree_scope(rules.values())
    for degree in DEGREES if levels is None else levels:
        for question in degree_questions(pending, {"degree": degree}):
            country = question["when_country"]
            if not country:
                return True
            geography = (r for r in rules_at_degree(rules, degree).values()
                         if r["field"] in {"program.country", "program.location"}
                         and r["when_country"] in {"", country})
            if all(supports(r, {"decision": "confirmed"}, country) for r in geography):
                return True
    return False


def indexed_decisions(items, rules):
    if "candidate_country" in rules and not any(item["id"] == "candidate_country" for item in items):
        # Checkpoints from older runs have no explicit jurisdiction proof.
        items = [*items, {"id": "candidate_country", "decision": "unknown", "urls": []}]
    return by_id(items, rules)


def missing_rules(check, rules):
    decisions = indexed_decisions(check["rules"], rules)
    cited = proof_urls(check)
    country = country_of(decisions, cited)
    missing = []
    for key, rule in rules.items():
        decision = decisions[key]
        opened = [u for u in decision["urls"] if url_key(u) in cited]
        if not opened or not supports(rule,{**decision,"urls":opened},country):
            missing.append(key)
    return missing


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
        urls = collect("facts", "field", {field})
        if urls:
            field_urls[field] = urls
    return {"rules": {key: collect("rules", "id", {key}) for key in rules}, "cells": field_urls}


def condition_facts(check, rules, language, represented):
    """Expose an already-confirmed predicate at exactly the specificity both readers verified."""
    proof = proof_urls(check)
    country = country_of({d["id"]:d for d in check["rules"]},proof)
    facts = list(check["facts"])
    for d in check["rules"]:
        rule = rules.get(d["id"],{})
        if (rule.get("role") != "required" or rule.get("match") not in {"any","all"}
                or d["decision"] != "confirmed" or not supports(rule,d,country)):
            continue
        values = rule["values"]
        if rule["field"] == "supervisor.mentoring" and len(values) == 1 and not re.search(r"\d",values[0]):
            field, wording = "mentoring", values[0]
        elif (rule["field"] == "program.thesis" and values and set(values) <= {"optional","required"}
              and language in {"zh","en"}):
            field = "thesis"
            wording = "该项目设有学位论文路径。" if language == "zh" else "The program offers a thesis pathway."
        else:
            continue
        field_urls = {url_key(u) for f in facts if f["field"] == field and f["value"].strip() for u in f["urls"]}
        urls = [u for u in d["urls"] if url_key(u) in proof & field_urls]
        if field not in represented and urls and wording.strip():
            facts.append({"field":field,"value":wording,"urls":urls})
    return {**check,"facts":facts}


def with_condition_facts(checks, active, context):
    for cid,rules in active.items():
        pair = [checks[r][cid] for r in ROLES]
        represented = {c["field"] for c in available_cells(pair,citation_choices(pair,rules))}
        for role in ROLES:
            checks[role][cid] = condition_facts(checks[role][cid],rules,language_of(context),represented)
    return checks


def language_of(context):
    return context.get("language") or ("zh" if re.search(r"[\u4e00-\u9fff]",context.get("utterance","")) else "en")


def available_cells(pair, eligible):
    """Offer verbatim public cells with a URL that actually supports that wording."""
    proofs = [proof_urls(check) for check in pair]
    result = []
    for check, proof in zip(pair, proofs):
        for fact in check["facts"]:
            allowed = set(map(url_key, eligible["cells"].get(fact["field"], []))) & proof
            urls = [url for url in fact["urls"] if url_key(url) in allowed]
            cell = {**fact, "urls": urls}
            if cell not in result and render_fact(cell, pair, proofs, url_key, set()) is not None:
                result.append(cell)
    return result


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


def combine_checks(original, newer):
    """Keep a reader's own opened pages across attempts and preserve conflicting verdicts."""
    old_rules = {d["id"]:d for d in original["rules"]}
    combined = []
    for d in newer["rules"]:
        old = old_rules.get(d["id"])
        if old and old["decision"] == d["decision"] and old["decision"] in {"confirmed","not_applicable"}:
            if (old.get("tuition_rate") != d.get("tuition_rate") or old.get("country_code") != d.get("country_code")):
                combined.append({"id":d["id"], "decision":"conflict", "urls":list(dict.fromkeys(old["urls"]+d["urls"]))})
            else:
                combined.append({**d,"urls":list(dict.fromkeys(d["urls"]+old["urls"]))})
        else:
            combined.append(d)
    replaced = {f["field"] for f in newer["facts"]}
    return {**newer,"rules":combined, "evidence":original["evidence"]+newer["evidence"],
            "facts":[f for f in original["facts"] if f["field"] not in replaced]+newer["facts"]}


def required_pages(candidates):
    return {c["id"]:[c[k] for k in ("profile_url","program_url") if c.get(k) and "candidate_"+k not in saved_evidence(c)] for c in candidates}


def revisit_payload(payload, checks, gaps):
    selected = [c for c in payload["candidates"] if c["id"] in gaps]
    return {**payload, "candidates":selected,"rule_ids_to_revisit":gaps, "must_open":required_pages(selected),
            "own_prior_checks":{cid:checks[cid] for cid in gaps}}


def safe_text(value):
    value = " ".join(value.split())
    if not value or re.search(r"https?://|www\.|\]\(", value, re.I):
        raise ValueError("Expected plain display text.")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;")


def table(headers, rows):
    return "\n".join("| " + " | ".join(row) + " |" for row in
                     [headers, ["---"] * len(headers), *rows])


def publish(audit_call, checks, candidates, rules, incomplete, limit, pending=(), accepted=None):
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
    if not candidates and pending_in_scope(pending, rules):
        incomplete = True
    for row in audit["rows"]:
        cid = row["id"]
        candidate = candidates_by_id[cid]
        pair = [checks[role][cid] for role in ROLES]
        active = rules_for_candidate(candidate, rules, pending)
        proofs = [proof_urls(c) for c in pair]
        countries = [country_of({d["id"]: d for d in c["rules"]}, proof) for c, proof in zip(pair, proofs)]
        country = countries[0] if countries[0] == countries[1] else None
        if any(not q["when_country"] or not country or q["when_country"] == country
               for q in degree_questions(pending, candidate)):
            incomplete = True
            continue
        if "candidate_country" in active and not country:
            incomplete = True
        if row["decision"] != "publish":
            continue
        choices = citation_choices(pair, active)
        rules_out = indexed_decisions(row["rules"], active)
        originals = [indexed_decisions(c["rules"], active) for c in pair]
        verified = set.union(*proofs) & visits & labels.keys()
        links = {}
        def backed(urls, allowed):
            keys = [url_key(url) for url in urls]
            return bool(keys) and all(key in allowed and key in verified for key in keys)

        valid = all(not missing_rules(c, active) for c in pair)
        if "candidate_country" in active:
            valid &= bool(country) and country_of(rules_out, verified) == country
        tuition_rates = set()
        for key, rule in active.items():
            decision = rules_out[key]
            original = [items[key] for items in originals]
            eligible = set(map(url_key, choices["rules"][key]))
            valid &= (supports(rule, decision, country)
                      and all(item["decision"] == decision["decision"] for item in original)
                      and backed(decision["urls"], eligible))
            if valid and rule["field"] == "tuition.budget" and decision["decision"] == "confirmed":
                rates = [item["tuition_rate"] for item in [*original, decision]]
                agreed = {rate_key(rate) for rate in rates}
                valid = len(agreed) == 1
                tuition_rates.update(agreed)
        cells = {cell["field"]: cell for cell in row["cells"]}
        valid &= bool(required) and required <= set(cells) <= set(columns) and len(cells) == len(row["cells"])
        display = {}
        for field, cell in cells.items():
            value = render_fact(cell, pair, proofs, url_key, tuition_rates)
            field_pages = set(map(url_key, choices["cells"].get(field, [])))
            if value is None and len(cell["urls"]) > 1 and backed(cell["urls"], field_pages):
                # Retain only auditor-selected citations that support this exact wording.
                # Each original URL must independently qualify for the same field.
                urls = [u for u in cell["urls"] if render_fact({**cell,"urls":[u]}, pair, proofs,
                                                               url_key, tuition_rates) is not None]
                cell = {**cell,"urls":urls}
                cells[field] = cell
                value = render_fact(cell, pair, proofs, url_key, tuition_rates)
            if value is None:
                valid = False
            else:
                display[field] = safe_text(value)
            valid &= backed(cell["urls"], set(map(url_key, choices["cells"].get(field, []))))
            links.update((url_key(url), url) for url in cell["urls"])
        if not valid:
            blocked.append(cid)
            continue
        if accepted is not None:
            accepted.append({"identity": {k:v for k,v in candidate.items() if k not in {"_program_evidence","_advisor_evidence"}},
                "facts": {f: {"value": display[f], "raw": cell, "label": next(
                    col["label"] for col in audit["columns"] if col["field"] == f)} for f, cell in cells.items()},
                "decisions": [*saved_evidence(candidate).values(), *({"rule": active[k], "decision": d} for k, d in rules_out.items())],
                "sources": {u: labels[url_key(u)] for u in [*links.values(), *(u for d in rules_out.values() for u in d["urls"])]},
                "checked_at": audit_call.get("checked_at") or now()})
        refs = [f'[{labels[key]}](<{url}>)' for key, url in links.items()]
        rows.append([display.get(f, "—") for f in columns] + [" · ".join(refs)])
    incomplete |= audit["state"] == "incomplete" or bool(blocked)
    if limit is not None:
        rows = rows[:limit]
    unresolved = audit["incomplete_message"].strip() or ("核查不完整。" if re.search(r"[\u4e00-\u9fff]", audit["status_label"]) else "Verification incomplete.")
    if rows:
        headers = [safe_text(col["label"]) for col in audit["columns"]] + [safe_text(audit["sources_label"])]
        output = table(headers, rows)
        if incomplete:
            output += "\n\n" + table([safe_text(audit["status_label"])], [[safe_text(unresolved)]])
    else:
        msg = unresolved if incomplete else (audit["empty_message"].strip() or audit["status_label"])
        output = table([safe_text(audit["status_label"])], [[safe_text(msg)]])
    return output, blocked


def scoped_check(item, candidate):
    excluded = set(saved_evidence(candidate))
    if candidate.get("person") and candidate.get("program_url"):
        excluded.add("candidate_pathway")
    return {**item, "rules": [d for d in item["rules"] if d["id"] not in excluded]}


def remaining_checks(previous, trusted, advisors, checkpoint):
    """Retry only readers with an actual evidence gap on this exact frozen request."""
    report = deepcopy(previous)
    candidates = bind_program_evidence(report["candidates"], trusted)
    bind_advisor_evidence(candidates, advisors)
    by_candidate = {c["id"]: c for c in candidates}
    context = report["request"]
    active = {c["id"]:rules_for_candidate(c,context["hard_rules_by_id"],context.get("pending_questions",[]))
              for c in candidates}
    checks = {r:{} for r in ROLES}
    for runs in [report["collections"],report["rechecks"],*report.get("followup_checks",[])]:
        for role,run in runs.items():
            for raw in verified_checks(run):
                item = scoped_check(raw, by_candidate[raw["id"]])
                old = checks[role].get(item["id"])
                checks[role][item["id"]] = combine_checks(old,item) if old else item
    payload = {**context,"candidates":candidates,"candidate_rules_by_id":active,"allowed_fields":FIELDS}
    retry = {}
    for role in ROLES:
        if set(checks[role]) != set(active):
            return None
        try:
            gaps = {cid:missing_rules(checks[role][cid],rules) for cid,rules in active.items()}
        except ValueError:
            return None  # A checkpoint with an older identity contract needs fresh collection.
        gaps = {cid:ids for cid,ids in gaps.items() if ids}
        if gaps:
            retry[role] = {**revisit_payload(payload,checks[role],gaps),"reader":role,"approach":ROLES[role]}
    if not retry and not any(saved_evidence(c) for c in candidates):
        return None
    report.update(state="running")
    prior_calls = [v for runs in [report["discoveries"],report["collections"],report["rechecks"],
                   *report.get("followup_checks",[])] for v in runs.values()]
    prior_calls += [*report.get("previous_audits",[]),report["audit"]]
    report["reused_web_calls"] = sum(len(run["web"])+run.get("discarded_web_calls",0) for run in prior_calls)
    if not retry and report.get("audit"):
        checks = with_condition_facts(checks,active,context)
        accepted = []
        partial = any(call["result"]["state"] == "incomplete" for call in prior_calls
                      if "checks" in call["result"] or "candidates" in call["result"])
        limit = next((int(r["values"][0]) for r in context["effective_rules"] if r["field"] == "output.limit"
                      and r["status"] != "unrestricted" and r["values"]), None)
        try:
            output, blocked = publish(report["audit"],checks,candidates,context["hard_rules_by_id"],partial,limit,
                                      context.get("pending_questions", []),accepted)
        except ValueError:
            blocked = [c["id"] for c in candidates]
        if accepted and not blocked:
            report.update(state="completed",publication_blocks=[],accepted=accepted,table=output,
                          incomplete=partial or report["audit"]["result"]["state"] == "incomplete")
            checkpoint(report)
            return output
    attempts = {}
    report.setdefault("followup_checks",[]).append(attempts)
    def save(role,run):
        attempts[role] = run
        checkpoint(report)
    if retry:
        independent("collect.md",retry,COLLECTION,save)
    return review_collections(report,checkpoint)


def research(event, catalog_path=CATALOG, checkpoint=lambda report: None):
    if event is None:
        raise ValueError("该目标还没有已保存的需求轮次")
    previous = event.get("previous_trace")
    same_request = (previous and previous.get("request",{}).get("effective_rules") == event["effective_rules"]
                    and previous["request"].get("missing_fields",[]) == event.get("missing_fields",[])
                    and previous.get("turn_id") == event["turn_id"]
                    and previous.get("owner") == event["owner"] and previous.get("goal") == event["goal"])
    fixed = event.get("fixed_candidates")
    if same_request and fixed:
        same_request = {entity_key(c) for c in fixed} == {entity_key(c) for c in previous.get("candidates",[])}
    if same_request and previous.get("audit") and previous.get("candidates"):
        output = remaining_checks(previous,event.get("trusted_programs", []),event.get("trusted_advisors", []),checkpoint)
        if output is not None:
            return output
    effective = event["effective_rules"]
    rules = {f"R{i}": rule for i, rule in enumerate(effective, 1)
             if rule["role"] in {"scope", "required"}
             and (rule["status"] != "unrestricted" or rule.get("when_degree"))}
    context = {"utterance": event["message"], "summary": event["proposal"]["summary"],
               **question_context(event),
               "effective_rules": effective, "rules_by_degree": degree_branches(effective), "hard_rules_by_id": rules,
               "background": [{k: fact[k] for k in ("field", "value")}
                              for fact in event.get("personal_background", {}).get("facts", [])],
               "as_of": now(), "language":event.get("language", "")}
    if event.get("branch"):
        context.update(branch=event["branch"], view=event["view"], cached_leads=event.get("cached_leads", []),
                       focus=event.get("focus", ""), excluded=event.get("excluded", []),
                       previous_display=event.get("previous_results",[]),
                       alternative_pages=event.get("alternative_pages", []),
                       missing_fields=event.get("missing_fields", []))
    report = {"stage": "search", "state": "running", **{k: event[k] for k in
              ("owner", "goal", "turn_id", "version")}, "request": context,
              "discoveries": {}, "collections": {}, "rechecks": {}}

    def save(key, role, run):
        report[key][role] = run
        checkpoint(report)

    try:
        checkpoint(report)
        context["directions"] = event.get("directions") or plan_search(event, catalog_path)
        if not context["directions"]:
            raise ValueError("该轮需求没有可执行的搜索方向")
        checkpoint(report)
        schema = {"school": SCHOOL_DISCOVERY, "advisor": ADVISOR_DISCOVERY}.get(event.get("branch"), DISCOVERY)
        partial = (previous if same_request and previous.get("candidates") and previous.get("collections")
                   and not previous.get("audit") else None)
        if partial:
            candidates = deepcopy(partial["candidates"])
            report["discoveries"] = deepcopy(partial.get("discoveries",{}))
        elif event.get("fixed_candidates"):
            candidates = [{**c, "id": f"C{i}"} for i, c in enumerate(event["fixed_candidates"], 1)]
        else:
            discoveries = independent("discover.md", {r: {**context, "approach": a} for r, a in ROLES.items()},
                                      schema, lambda role, run: save("discoveries", role, run))
            candidates = identities(discoveries)
        if event.get("branch") == "advisor":
            candidates = [c for c in candidates if c.get("person") and url_key(c.get("profile_url", ""))]
        candidates = [c for c in candidates if not c.get("program_url") or url_key(c["program_url"])]
        levels = degree_scope(effective)
        needs_degree = any(r.get("when_degree") for r in effective)
        candidates = [c for c in candidates if (levels is None or c.get("degree") in levels)
                      and (not needs_degree or c.get("degree"))]
        bind_program_evidence(candidates, event.get("trusted_programs", []))
        bind_advisor_evidence(candidates, event.get("trusted_advisors", []))
        report["candidates"] = candidates
        ids = [c["id"] for c in candidates]
        checks = {r: {} for r in ROLES}
        if candidates:
            applicable = {c["id"]: rules_for_candidate(c, rules, context.get("pending_questions", [])) for c in candidates}
            payload = {**context, "candidates": candidates, "candidate_rules_by_id": applicable,
                       "allowed_fields": FIELDS, "must_open":required_pages(candidates)}
            index = {c["id"]:c for c in candidates}
            seeds = {c["id"]:c.get("profile_url") or c.get("program_url") for c in candidates}
            payload["starting_pages"] = seeds
            if partial:
                for role,run in partial["collections"].items():
                    try:
                        saved = by_id(verified_checks(run),ids)
                        for cid,check in saved.items():
                            by_id(scoped_check(check,index[cid])["rules"],applicable[cid])
                    except ValueError:
                        continue
                    report["collections"][role] = deepcopy(run)
                retained = [*report["discoveries"].values(), *report["collections"].values()]
                report["reused_web_calls"] = sum(len(t["web"])+t.get("discarded_web_calls",0) for t in retained)
            jobs = {r:{**payload,"reader":r,"approach":ROLES[r]} for r in ROLES if r not in report["collections"]}
            if jobs:
                independent("collect.md", jobs, COLLECTION, lambda role, run: save("collections", role, run))
            for role, run in report["collections"].items():
                checks[role] = by_id([scoped_check(check,index[check["id"]]) for check in verified_checks(run)], ids)
            # Recheck unresolved candidate requirements once. Only IDs and rule IDs cross roles.
            retry = {}
            for role in ROLES:
                gaps = {}
                for cid in ids:
                    missing = missing_rules(checks[role][cid], applicable[cid])
                    if missing:
                        gaps[cid] = missing
                if gaps:
                    retry[role] = {**revisit_payload(payload,checks[role],gaps),"reader":role,"approach":ROLES[role]}
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
    by_candidate = {c["id"]: c for c in candidates}
    ids = [c["id"] for c in candidates]
    rules = context["hard_rules_by_id"]
    checks = {r: {} for r in ROLES}
    incomplete = any(v["result"]["state"] == "incomplete" for v in report["discoveries"].values())
    for runs in [report["collections"],report["rechecks"],*report.get("followup_checks",[])]:
        for role, run in runs.items():
            incomplete |= run["result"]["state"] == "incomplete"
            for raw in verified_checks(run):
                item = scoped_check(raw, by_candidate[raw["id"]])
                older = checks[role].get(item["id"])
                checks[role][item["id"]] = combine_checks(older,item) if older else item
    if any(set(checks[r]) != set(ids) for r in ROLES):
        raise ValueError("Incomplete independent collections.")
    applicable = {c["id"]: rules_for_candidate(c, rules, context.get("pending_questions", [])) for c in candidates}
    checks = with_condition_facts(checks,applicable,context)
    eligible = {cid: citation_choices([checks[r][cid] for r in ROLES], applicable[cid]) for cid in ids}
    payload = {**context, "candidates": candidates, "must_open":required_pages(candidates), "checks": checks, "eligible_citations": eligible,
               "candidate_rules_by_id": applicable, "research_incomplete": incomplete, "allowed_fields": FIELDS,
               "available_cells": {cid:available_cells([checks[r][cid] for r in ROLES], eligible[cid]) for cid in ids}}
    limit = next((int(r["values"][0]) for r in context["effective_rules"] if r["field"] == "output.limit"
                  and r["status"] != "unrestricted" and r["values"]), None)
    for _ in range(2):
        if "audit" in report:
            report.setdefault("previous_audits", []).append(report.pop("audit"))
        audit = live("audit-search.md", payload, AUDIT, auditor=True)
        report["audit"] = audit
        checkpoint(report)
        accepted = []
        output, blocked = publish(audit, checks, candidates, rules, incomplete, limit,
                                  context.get("pending_questions", []), accepted)
        if not blocked:
            break
        payload["citation_repair"] = {"blocked_candidate_ids": blocked,
                                     "instruction": "Check the country, decision, typed value and selected source wording. "
                                                    "Use only an opened eligible URL for its exact field or rule. "
                                     "Supply a link label for each. Omit a row if independent support is lacking."}
    report.update(state="completed", publication_blocks=blocked, accepted=accepted, table=output,
                  incomplete=incomplete or bool(blocked) or audit["result"]["state"] == "incomplete")
    checkpoint(report)
    return output
