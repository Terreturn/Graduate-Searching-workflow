"""Execute only changed branches, reuse verified evidence, and join exact supervision records."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from time import monotonic_ns
from threading import Lock
from urllib.parse import urlsplit
import re

from model_call import call_model

from planner import plan_search
from research import degree_questions, now, pending_in_scope, research, safe_text, table, url_key
from result_store import ResultStore, entity_key, fresh, local_decision, merge_item, norm, prepare, program_key, rule_key
from routing import branch_event, route, snapshot
from rules import BRANCHES, STRING, array, record, rules_at_degree, validate_schema


def requirements(item, rules):
    hard = {str(i): r for i,r in enumerate(rules) if r["role"] in {"scope", "required"}}
    return list(rules_at_degree(hard, item["identity"].get("degree", "")).values())


def eligible(item, state, background):
    if not fresh(item, "identity"):
        return False
    country = next((d["decision"].get("country_code") for d in item["decisions"]
                    if d["rule"]["field"] == "candidate.country" and fresh(d, "country")), None)
    if any(not q["when_country"] or not country or q["when_country"] == country
           for q in degree_questions(state["pending"], item["identity"])):
        return False
    return all(local_decision(item, r, background) is True for r in requirements(item, state["rules"]))


def has_fact(item, field):
    if field == "supervision":
        return any(d["rule"]["field"] == "candidate.supervision" and fresh(d,field) for d in item["decisions"])
    fields = ("topic","research_project") if field == "topic" else (field,)
    return any(k in item["facts"] and fresh(item["facts"][k],k) for k in fields)


def limit_of(event):
    return next((int(r["values"][0]) for r in event["effective_rules"]
                 if r["field"] == "output.limit" and r["values"]), None)


def committed(store, event):
    if event is None:
        raise ValueError("该目标还没有已保存的需求轮次")
    return store.turn_result(event["owner"], event["goal"], event["turn_id"])


def preview(store, event, catalog):
    event = committed(store,event)
    cache = ResultStore(store, event)
    decision = cache.get_route(lambda: route(event))
    return [{"branch": b, **row} for b in decision["branches"]
            for row in plan_search(branch_event(event, b, decision["tasks"][b]), catalog)]


def link_rows(schools, advisors, school_required, rules=()):
    thesis = any(r["field"] == "program.thesis" and r["role"] == "required" and r["status"] == "specified"
                 and r["match"] != "none" and set(r["values"]) <= {"optional","required"} for r in rules)
    found = {}
    for item in [*advisors, *schools]:
        c = item["identity"]
        proof = next((d for d in item["decisions"] if d["rule"]["field"] == "candidate.supervision"
                      and d["decision"]["decision"] == "confirmed" and fresh(d, "supervision")), None)
        if thesis and c.get("pathway") != "thesis":
            continue
        binding = [c.get(k,"") for k in ("person","profile_url","institution","subject","program_url","degree","pathway","campus")]
        if not proof or proof["rule"]["values"] != binding or not c.get("profile_url") or not program_key(c):
            continue
        for advisor in advisors:
            a = advisor["identity"]
            if (url_key(a.get("profile_url","")) != url_key(c["profile_url"])
                    or norm(a.get("person","")) != norm(c["person"])):
                continue
            if a.get("program_url") and (program_key(a) != program_key(c) or
                    a.get("pathway","general") not in {"general",c.get("pathway")}):
                continue
            options = schools if school_required else [None]
            for school in options:
                if school:
                    s = school["identity"]
                    if program_key(s) != program_key(c):
                        continue
                    if s.get("campus") and norm(s["campus"]) != norm(c.get("campus","")):
                        continue
                    if s.get("pathway","general") not in {"general", c.get("pathway")}:
                        continue
                    if s.get("track") and norm(s["track"]) != norm(c.get("track","")):
                        continue
                    if s.get("cycle") and s["cycle"] != c.get("cycle"):
                        continue
                key = (url_key(c["profile_url"]),norm(c["person"]), *program_key(c), c.get("campus"), c.get("pathway"), c.get("cycle"))
                found[key] = (advisor, school, c, proof["decision"]["urls"])
    return list(found.values())


def score(item, rules, background):
    corpus = norm(" ".join(f["value"] for k,f in item["facts"].items() if fresh(f,k)))
    preferences = [v for r in rules if r["role"] == "preferred" for v in r["values"] if isinstance(v,str)]
    return (sum(norm(v) in corpus for v in preferences), sum(norm(f["value"]) in corpus for f in background))


def answer_language(event):
    value = next((r["values"][0] for r in event["effective_rules"] if r["field"] == "output.language"
                  and r["values"]), event.get("language", ""))
    aliases = {"english":"en", "chinese":"zh","中文":"zh","英语":"en","英文":"en","chinese (simplified)":"zh"}
    return aliases.get(value.casefold(), value.casefold().split("-")[0])


def localize_table(markdown, language):
    # Protect links and numerical observations even when the text language changes.
    tokens = []
    def mask(m):
        tokens.append(m.group())
        return f"⟦{len(tokens)-1}⟧"
    masked = re.sub(r"\[[^\]]*\]\(<[^>]+>\)|&#\d+;|\d+(?:[.,:/+-]\d+)*", mask, markdown)
    lines = masked.splitlines()
    schema = record({"lines":array(STRING)})
    reply = call_model("translate-table.md", {"language":language,"lines":lines},schema,effort="low")
    validate_schema(reply,schema)
    if len(reply["lines"]) != len(lines):
        raise ValueError("Translation changed the table structure.")
    for left,right in zip(lines,reply["lines"]):
        def protected(line):
            return [sorted(re.findall(r"⟦\d+⟧",cell)) for cell in line.split("|")]
        if (protected(left) != protected(right) or re.search(r"https?://|\]\(",right)
                or re.search(r"\d",re.sub(r"⟦\d+⟧","",right))):
            raise ValueError("Translation changed an approved source or value.")
    return re.sub(r"⟦(\d+)⟧",lambda m:tokens[int(m.group(1))],"\n".join(reply["lines"]))


def render(event, decision, states, incomplete):
    language = answer_language(event)
    zh = language == "zh" or (not event.get("language") and
            any("\u4e00" <= ch <= "\u9fff" for ch in event["message"]))
    background = event.get("personal_background", {}).get("facts", [])
    branches = snapshot(event)
    rows = {b: sorted((i for i in states[b]["items"] if eligible(i,branches[b],background)),
                      key=lambda i: score(i,branches[b]["rules"],background), reverse=True) for b in BRANCHES}
    for branch in BRANCHES:
        requested = {f for t in branches[branch]["tasks"] for f in t.get("fields", [])}
        if requested:
            enough = [i for i in rows[branch] if all(has_fact(i,f) for f in requested)]
            incomplete |= len(enough) < len(rows[branch])
            rows[branch] = enough
        if branch in ([decision["view"]] if decision["view"] in BRANCHES else BRANCHES) and not rows[branch]:
            state = branches[branch]
            if pending_in_scope(state["pending"],{str(i):r for i,r in enumerate(state["rules"])}):
                incomplete = True
        if any(t["action"] == "more" for t in branches[branch]["tasks"]):
            rows[branch] = [i for i in rows[branch] if i["key"] in states[branch].get("new_keys",[])]
    view = decision["view"]
    limit = limit_of(event)
    displays, public_ids, relations = [], [], []
    source_label = "来源" if zh else "Sources"
    def citations(pages):
        return " · ".join(f"[{i}](<{u}>)" for i,u in enumerate(dict.fromkeys(pages),1) if url_key(u))
    if view == "joined":
        linked = link_rows(rows["school"],rows["advisor"],bool(branches["school"]["rules"]),branches["school"]["rules"])
        linked.sort(key=lambda x: (score(x[0],branches["advisor"]["rules"],background),
                    score(x[1],branches["school"]["rules"],background) if x[1] else (0,0)), reverse=True)
        body = []
        detail_fields = ("research_project","topic","mentoring","recruitment","supervisor_background","thesis","funding","tuition","stipend","deadline")
        extra = {}
        for advisor,school,c,urls in linked[:limit]:
            facts = {**(school["facts"] if school else {}), **advisor["facts"]}
            facts = {k:facts[k] for k in detail_fields if k in facts and fresh(facts[k],k)}
            extra.update({k:v["label"] for k,v in facts.items()})
            pages = list(urls) + [u for fact in facts.values() for u in fact["raw"]["urls"]]
            if school:
                pages.append(school["identity"]["program_url"])
            pathway = c.get("pathway","general")
            pathway = ({"general":"项目层级","thesis":"论文型","mixed":"混合型","coursework":"授课型"}[pathway] if zh else pathway)
            degree = c["degree"]
            if zh:
                degree = {"master":"硕士","PhD":"博士","professional_doctorate":"专业博士"}.get(degree,degree)
            body.append(([safe_text(c["person"]),safe_text(c["institution"]),safe_text(c["subject"]),
                          safe_text(degree),safe_text(pathway)], facts, citations(pages)))
            public_ids.append(advisor["key"])
            relations.append({"advisor":c["person"],"program":c["subject"],"degree":c["degree"],
                              "pathway":c["pathway"],"sources":urls})
        if body:
            headers = ["导师","学校","项目","学位","培养路径"] if zh else ["Supervisor","Institution","Program","Degree","Pathway"]
            displays.append(table(headers + [safe_text(v) for v in extra.values()] + [source_label],
                [base + [facts[k]["value"] if k in facts else "—" for k in extra] + [links] for base,facts,links in body]))
    else:
        for b in BRANCHES if view == "separate" else [view]:
            items = rows[b][:limit]
            columns = {}
            for item in items:
                columns.update({k:item["facts"][k]["label"] for k in sorted(item["facts"]) if fresh(item["facts"][k],k)})
            if items and columns:
                body = []
                for item in items:
                    facts = {k:v for k,v in item["facts"].items() if fresh(v,k)}
                    pages = [u for k in columns if k in facts for u in facts[k]["raw"]["urls"]]
                    body.append([facts[k]["value"] if k in facts else "—" for k in columns] + [citations(pages)])
                    public_ids.append(item["key"])
                displays.append(table([safe_text(v) for v in columns.values()]+[source_label],body))
    if incomplete or not displays:
        note = (("本轮核查不完整。" if zh else "Verification incomplete.") if incomplete else
                ("没有已核实的匹配结果。" if zh else "No verified matches."))
        displays.append(table(["状态" if zh else "Status"], [[note]]))
    output = "\n\n".join(displays)
    displayed_languages = {i["language"].lower().split("-")[0] for values in rows.values() for i in values
                           if i["key"] in public_ids}
    if language and (language not in {"zh","en"} or displayed_languages - {language}):
        output = localize_table(output,language)
    return output, public_ids, relations


def widens(change):
    old, new = change["before"], change["after"]
    if not old or old["role"] == "preferred":
        return False
    if not new or new["status"] == "unrestricted":
        return True
    before, after = old["values"], new["values"]
    if old["match"] == "any":
        return any(v not in before for v in after)
    return any(v not in after for v in before)


def observed_calls(trace):
    calls = [a for stage in ("discoveries","collections","rechecks") for a in trace.get(stage,{}).values()]
    calls.extend(v for batch in trace.get("followup_checks",[]) for v in batch.values())
    calls.extend(trace.get("previous_audits",[]))
    if trace.get("audit"):
        calls.append(trace["audit"])
    return max(0,sum(len(c.get("web",[]))+c.get("discarded_web_calls",0) for c in calls) - trace.get("reused_web_calls",0))


def run_branch(event, branch, focus, previous, reusable, previous_run, catalog, save):
    state = snapshot(event)[branch]
    active = branch_event(event,branch,focus)
    old = previous[branch]
    inventory = {i["key"]:i for i in old["items"]}
    background = event.get("personal_background",{}).get("facts",[])
    actions = {t["action"] for t in state["tasks"]}
    requested_fields = {f for t in state["tasks"] for f in t.get("fields",[])}
    changed = state["diff"]
    pool_items = {i["key"]:i for i in reusable[branch]} | inventory
    local = [i for i in pool_items.values() if eligible(i,state,background)]
    demand = limit_of(event) or 1
    log = {"started":now(), "start_ns":monotonic_ns(), "runs":[],
           "web_calls":0,"cache_hits":[],"added":[],"updated":[]}
    save(branch,log)
    leads = [i["identity"] for s in previous.values() for i in s["items"]]
    fallback = []
    if previous_run:
        for attempt in previous_run.get("executions",{}).get(branch,{}).get("runs",[]):
            rejected = {c.get("program_url","") for c in attempt.get("candidates",[])}
            fallback.extend(ev["url"] for run in attempt.get("collections",{}).values()
                            for check in run["result"]["checks"] for ev in check["evidence"] if ev["url"] not in rejected)
    active["alternative_pages"] = sorted(set(fallback))
    active["trusted_programs"] = [] if "refresh" in actions else previous["school"]["items"]
    active["trusted_advisors"] = [] if "refresh" in actions else previous["advisor"]["items"]
    attempts = previous_run["executions"].get(branch,{}).get("runs",[]) if previous_run else []
    retired = set()
    def search(extra):
        trace = {}
        log["runs"].append(trace)
        def tick(data):
            trace.update(data)
            save(branch,log)
        request = {**active,"cached_leads":leads,**extra}
        previous_trace = next((attempt for attempt in reversed(attempts) if not attempt.get("accepted")
                               and attempt.get("request",{}).get("effective_rules") == request["effective_rules"]
                               and attempt["request"].get("missing_fields",[]) == request.get("missing_fields",[])),None)
        research({**request,"previous_trace":previous_trace}, catalog, tick)
        log["web_calls"] += observed_calls(trace)
        results = {}
        for raw in trace["accepted"]:
            item = prepare(deepcopy(raw),event.get("language",""),background)
            results[item["key"]] = merge_item(inventory.get(item["key"]),item)
        checked = {entity_key(c) for c in trace.get("candidates",[])}
        retired.update(k for k in checked if k in inventory and k not in results)
        return results, trace["incomplete"]
    items = {}
    incomplete = False
    new_prefs = [d["after"] for d in changed if d["after"] and d["after"]["role"] == "preferred"]
    want_new = bool(actions & {"refresh","more","link"})
    removed = any(widens(d) for d in changed)
    if new_prefs and local and all(score(i,new_prefs,[])[0] == sum(len(r["values"]) for r in new_prefs) for i in local):
        new_prefs = []
    def has_fields(i):
        return all(has_fact(i,f) for f in requested_fields)
    if not want_new:
        items.update({i["key"]:i for i in local if has_fields(i)})
        log["cache_hits"] = list(items)
    if items and len(items) >= demand and not want_new and not new_prefs and (not removed or limit_of(event)):
        pass
    elif inventory and "more" not in actions and "link" not in actions and not removed:
        # The discovery identity and previously proved requirements remain valid. Only
        # submit uncovered requirements for each candidate, plus its identity checks.
        groups = {}
        for item in inventory.values():
            required = requirements(item,state["rules"])
            verdicts = [local_decision(item,r,background) for r in required]
            if False in verdicts:
                continue
            related = {"supervisor.recruitment":"recruitment", "supervisor.mentoring":"mentoring",
                       "supervisor.background":"supervisor_background", "supervisor.topic":"research_project",
                       "tuition.budget":"tuition", "funding.coverage":"funding", "program.thesis":"thesis"}
            gaps = [r for r,v in zip(required,verdicts) if v is None or not fresh(item,"identity")
                    or ("refresh" in actions and (not requested_fields or related.get(r["field"]) in requested_fields))]
            fields = sorted(f for f in requested_fields if "refresh" in actions or
                            not has_fact(item,f))
            if gaps or new_prefs or fields or "refresh" in actions:
                batch = gaps + new_prefs
                key = (tuple(rule_key(r) for r in batch),tuple(fields))
                groups.setdefault(key,(batch,fields,[]))[2].append(item["identity"])
        for batch,fields,candidates in groups.values():
            updates, partial = search({"fixed_candidates":candidates,"missing_fields":[r["field"] for r in batch]+fields,
                "effective_rules":batch + [r for r in active["effective_rules"] if r["role"]=="output"]})
            items.update(updates)
            incomplete |= partial
        selected_people = {norm(v) for r in state["rules"] if r["field"]=="supervisor.name"
                           and r["role"]=="scope" and r["match"]=="any" for v in r["values"]}
        all_named_people_known = selected_people and selected_people <= {
            norm(i["identity"].get("person","")) for i in inventory.values()}
        if len(items) < demand and not all_named_people_known:
            updates,partial = search({"missing_fields":sorted(requested_fields)})
            items.update(updates)
            incomplete |= partial
    else:
        updates,incomplete = search({"missing_fields":sorted(requested_fields), "excluded": [i["identity"] for i in inventory.values()] if "more" in actions else []})
        if "more" in actions:
            items.update({i["key"]:i for i in local})
        items.update(updates)
    for k in retired - items.keys():
        log.setdefault("withheld",[]).append(k)
    if "more" in actions and not (items.keys() - inventory.keys()):
        incomplete = True
    if "more" in actions:
        items = {k:items[k] for k in sorted(items,key=lambda k:k in inventory)}
    log.update(added=[k for k in items if k not in inventory],
               updated=[k for k in items if k in inventory and items[k]!=inventory[k]],
               finished=now(), finish_ns=monotonic_ns())
    log["field_diff"] = {k:{f:{"before":inventory.get(k,{}).get("facts",{}).get(f),
                        "after":fact} for f,fact in i["facts"].items()
                        if inventory.get(k,{}).get("facts",{}).get(f) != fact}
                         for k,i in items.items() if k not in inventory or inventory[k] != i}
    save(branch,log)
    return {"rules":state["rules"],"items":list(items.values()),"incomplete":incomplete,
            "new_keys":list(items.keys()-inventory.keys()), "retired":sorted(retired - items.keys())}


def check_missing_links(event, routing, states, updated, report, catalog, checkpoint):
    # A joint request may discover complementary identities in the same fan-out.
    # Probe exact pairs only on a branch that was already dispatched for this turn.
    state = snapshot(event)
    bg = event.get("personal_background",{}).get("facts",[])
    matching = {b:[i for i in states[b]["items"] if eligible(i,state[b],bg)] for b in BRANCHES}
    if (routing["view"] == "joined" and updated and matching["school"] and matching["advisor"]
            and not link_rows(matching["school"], matching["advisor"], True, state["school"]["rules"])):
        branch = "advisor" if "advisor" in updated else "school"
        pairs = {}
        for school in matching["school"]:
            s = school["identity"]
            for advisor in matching["advisor"]:
                a = advisor["identity"]
                if not (s.get("program_url") and a.get("profile_url")):
                    continue
                def domain(u):
                    return urlsplit(u).hostname or ""
                # Only an identity alignment hint; every proposed relation must pass a new two-reader audit.
                if (norm(s["institution"]) != norm(a["institution"]) and
                    domain(s["program_url"]).split(".")[-2:] != domain(a["profile_url"]).split(".")[-2:]):
                    continue
                c = {**s,"person":a["person"],"profile_url":a["profile_url"]}
                if any(r["field"]=="program.thesis" and r["role"]=="required" and r["match"]!="none"
                       and r["status"]=="specified" and "unavailable" not in r["values"] for r in state["school"]["rules"]):
                    c["pathway"] = "thesis"
                pairs[entity_key(c)] = c
        if pairs:
            extra = branch_event(event,branch,routing["tasks"][branch])
            trace = {}
            log = report["executions"][branch]
            log["runs"].append(trace)
            def tick(data):
                trace.update(data)
                checkpoint(report)
            try:
                research({**extra, "fixed_candidates":list(pairs.values()), "trusted_programs":matching["school"], "trusted_advisors":matching["advisor"], "missing_fields":["candidate.supervision"]},
                         catalog,tick)
                additions = [prepare(deepcopy(i),event.get("language",""),bg) for i in trace["accepted"]]
                merged = {i["key"]:i for i in updated[branch]["items"]}
                for item in additions:
                    k = item["key"]
                    if k not in merged:
                        log["added"].append(k)
                    merged[k] = merge_item(merged.get(k),item)
                updated[branch]["items"] = list(merged.values())
            except (ValueError,RuntimeError,OSError) as exc:
                report["errors"][branch] = str(exc)
            log["web_calls"] += observed_calls(trace)
            log["link_check_finished"] = now()


def execution_summary(report):
    """Use recorded worker intervals; a completed web receipt is also a lower bound on its finish."""
    workers = report.get("executions", {})
    ends = {}
    for branch,worker in workers.items():
        receipts = [run["checked_at"] for trace in worker["runs"]
                    for stage in ("discoveries","collections","rechecks")
                    for run in trace.get(stage,{}).values() if run.get("checked_at")]
        ends[branch] = worker.get("finished") or max(receipts, default=worker["started"])
    overlap = None
    if len(workers) == 2:
        if all("finish_ns" in w for w in workers.values()):
            overlap = max(w["start_ns"] for w in workers.values()) < min(w["finish_ns"] for w in workers.values())
        else:
            overlap = max(w["started"] for w in workers.values()) < min(ends.values())
    return {"branches": sorted(workers), "web_calls":report.get("web_calls",0), "overlap":overlap,
            "workers":{b:{"started":w["started"],"observed_until":ends[b]} for b,w in workers.items()},
            "errors":report.get("errors",{})}


def execute(store, event, catalog, checkpoint=lambda _: None):
    event = committed(store,event)
    cache = ResultStore(store, event)
    revision = cache.input_revision()
    previous_run = cache.cached_run()
    if previous_run and not cache.current_inputs(previous_run,revision):
        previous_run = None
    if previous_run and not previous_run.get("errors") and (previous_run.get("displayed") or not previous_run.get("incomplete", True)):
        checkpoint(previous_run)
        return previous_run
    routing = cache.get_route(lambda: route(event))
    previous = {b: cache.previous(b) for b in BRANCHES}
    reusable = {b: cache.reusable(b) for b in routing["branches"]}
    report = {"owner":event["owner"],"goal":event["goal"],"turn_id":event["turn_id"],"question":event["message"],
              "version":event["version"],"input_revision":revision,"branches":snapshot(event),"routing":routing,"executions":{},
              "errors":{}, "state":"running"}
    report["prior_attempts"] = ([*previous_run.get("prior_attempts", []), execution_summary(previous_run)]
                                if previous_run else [])
    lock = Lock()
    def save(branch, data):
        with lock:
            report["executions"][branch] = deepcopy(data)
            checkpoint(report)
    reused = {}
    if previous_run and previous_run.get("errors"):
        for b in routing["branches"]:
            if b not in previous_run["errors"]:
                saved = cache.finished_branch(b)
                if saved and saved["items"]:
                    reused[b] = saved
    report["resumed_branches"] = sorted(reused)
    updated = {}
    checkpoint(report)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(run_branch, event, b, routing["tasks"][b], previous, reusable, previous_run,
                            catalog, save): b for b in routing["branches"] if b not in reused}
        for job in as_completed(jobs):
            b = jobs[job]
            try:
                updated[b] = job.result()
            except (OSError,RuntimeError,ValueError) as exc:
                with lock:
                    report["errors"][b] = str(exc)
                    if b in report["executions"]:
                        report["executions"][b].update(finished=now(), finish_ns=monotonic_ns())
    states = previous | reused | updated
    for b in report["errors"]:
        states[b] = {"items":[],"incomplete":True}
    check_missing_links(event, routing, states, updated, report, catalog, checkpoint)
    relevant = BRANCHES if routing["view"] in {"separate","joined"} else [routing["view"]]
    text,ids,links = render(event,routing,states,bool(report["errors"]) or any(states[b]["incomplete"] for b in relevant))
    records = {i["key"]:i["identity"] for s in states.values() for i in s["items"]}
    report["displayed_identities"] = [records[k] for k in ids]
    report.update(incomplete=bool(report["errors"]) or any(states[b]["incomplete"] for b in relevant),
                  state="partial" if report["errors"] else "completed", table=text, displayed=ids,
                  associations=links, web_calls=sum(x["web_calls"] for x in report["executions"].values()))
    for log in report["executions"].values():
        log["web_calls"] = sum(observed_calls(t) for t in log["runs"])
    report["web_calls"] = sum(log["web_calls"] for log in report["executions"].values())
    saved = cache.commit(updated,report)
    checkpoint(saved)
    return saved
