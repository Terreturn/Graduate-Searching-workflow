"""Offline acceptance checks for research isolation, evidence gates and table rendering."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research import AUDITOR, COLLECTION, DISCOVERY, identities, page_visits, publish, research, review_collections

PAGE = "https://example.edu/department/funding"
RULE = {"field": "funding.coverage", "match": "all", "role": "required", "status": "specified",
        "values": ["full"], "when_country": ""}
RULES = {"R1": RULE}
CANDIDATE = {"id": "C1", "subject": "Example PhD", "institution": "Example University", "cycle": "",
             "degree": "", "qualification": "", "track": "", "campus": ""}
DECISION = {"id": "R1", "decision": "confirmed", "urls": [PAGE]}
CELL = {"field": "funding", "value": "Tuition plus stipend", "urls": [PAGE]}
CHECK = {"id": "C1", "rules": [DECISION], "facts": [CELL], "evidence": [
    {"url": PAGE, "excerpt": "All admitted students receive tuition plus a stipend."}]}
AUDIT_ROW = {"id": "C1", "decision": "publish", "reason": "Supported.", "rules": [DECISION], "cells": [CELL]}
REVIEW = {"web": [{"action": {"type": "open_page", "url": PAGE},
                   "results": [{"url": PAGE, "title": "Department funding terms"}]}],
          "result": {"state": "complete", "columns": [{"field": "funding", "label": "Funding", "availability": "required"}],
                     "rows": [AUDIT_ROW], "links": [{"url": PAGE, "label": "Department terms"}],
                     "sources_label": "Sources", "status_label": "Search", "empty_message": "No verified matches.",
                     "incomplete_message": "Search incomplete."}}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.checks = {r: {"C1": deepcopy(CHECK)} for r in ("A", "B")}
        self.audit = deepcopy(REVIEW)

    def render(self):
        return publish(self.audit, self.checks, [CANDIDATE], RULES, False, None)

    def test_verified_rows_only_contain_display_fields(self):
        output, blocked = self.render()
        self.assertEqual(blocked, [])
        self.assertIn("| Funding | Sources |", output)
        self.assertIn("Tuition plus stipend", output)
        self.assertIn(PAGE, output)
        self.assertNotIn("Supported.", output)

    def test_hard_rule_requires_independent_agreement_and_cannot_be_waived(self):
        for decision in ("unknown", "contradicted", "conflict", "not_applicable"):
            with self.subTest(decision=decision):
                self.checks["B"]["C1"]["rules"][0]["decision"] = decision
                output, blocked = self.render()
                self.assertEqual(blocked, ["C1"])
                self.assertNotIn(PAGE, output)

    def test_absent_supplemental_field_leaves_verified_answer_intact(self):
        self.audit["result"]["columns"].append({"field": "duration", "label": "Duration", "availability": "optional"})
        output, blocked = self.render()
        self.assertFalse(blocked)
        self.assertIn("| Tuition plus stipend | — |", output)

    def test_citations_must_be_reopened_and_backed_by_both_field_checks(self):
        self.audit["web"] = [{"action": {"type": "search", "query": "Department awards"}}]
        self.assertEqual(self.render()[1], ["C1"])

        self.audit = deepcopy(REVIEW)
        self.checks["A"]["C1"]["facts"] = []
        self.assertEqual(self.render()[1], ["C1"])
        self.checks["A"]["C1"] = deepcopy(CHECK)
        self.audit["result"]["rows"][0]["cells"][0]["urls"] = ["https://invented.invalid/"]
        self.assertEqual(self.render()[1], ["C1"])

    def test_audited_cell_only_keeps_sources_supporting_its_exact_wording(self):
        other = "https://example.edu/graduate/funding"
        b = self.checks["B"]["C1"]
        b["facts"] = [{"field":"funding","value":"Stipend and tuition coverage","urls":[other]}]
        b["evidence"].append({"url":other,"excerpt":"Stipend and tuition coverage."})
        self.audit["web"][0]["results"].append({"url":other,"title":"Funding"})
        self.audit["result"]["links"].append({"url":other,"label":"Program policy"})
        self.audit["result"]["rows"][0]["cells"][0]["urls"] = [PAGE,other]
        output, blocked = self.render()
        self.assertFalse(blocked)
        self.assertIn(PAGE,output)
        self.assertNotIn(other,output)
        self.audit["result"]["rows"][0]["cells"][0]["urls"].append("https://unopened.invalid")
        self.assertEqual(self.render()[1],["C1"])

    def test_mentoring_summary_does_not_publish_a_disputed_year(self):
        from research import available_cells, citation_choices, with_condition_facts
        rule = {**RULE,"field":"supervisor.mentoring","values":["Documented graduate thesis supervision"]}
        active = {"C1":{"R1":rule}}
        for role,year in [("A","2020"),("B","2021")]:
            self.checks[role]["C1"]["facts"] = [{"field":"mentoring","value":"Supervised a thesis in "+year,
                                                "urls":[PAGE]}]
        checks = with_condition_facts(self.checks,active,{"language":"en"})
        pair = [checks[r]["C1"] for r in ("A","B")]
        offered = available_cells(pair,citation_choices(pair,active["C1"]))
        self.assertEqual([c["value"] for c in offered],["Documented graduate thesis supervision"])
        self.assertFalse(any("202" in c["value"] for c in offered))

    def test_batched_web_trace_keeps_opened_pages_and_excludes_snippets_or_errors(self):
        hits = [{"url": PAGE, "title": "Terms", "ref_id": "turn3view1"},
                {"url": "https://example.edu/snippet", "title": "Overview", "ref_id": "turn3search1"},
                {"url": "https://example.edu/error", "title": "404 Not Found", "ref_id": "turn3view2"}]
        self.assertEqual(page_visits([{"action": {"type": "other"}, "results": hits}]), {PAGE})

    def test_incorrect_citation_is_reaudited_without_replacing_either_search(self):
        bad = deepcopy(REVIEW)
        bad["result"]["rows"][0]["cells"][0]["urls"] = ["https://example.edu/wrong"]
        stored = {"web": REVIEW["web"], "result": {"state": "complete", "checks": [CHECK]}}
        report = {"request": {"hard_rules_by_id": RULES, "effective_rules": [RULE]},
                  "candidates": [CANDIDATE], "discoveries": {r: stored for r in ("A","B")},
                  "collections": {r: stored for r in ("A","B")}, "rechecks": {}}
        with patch("research.live", side_effect=[bad, REVIEW]) as model:
            self.assertIn(PAGE, review_collections(report, lambda _: None))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(report["previous_audits"], [bad])
        self.assertFalse(report["publication_blocks"])
        self.assertEqual(model.call_args.args[1]["citation_repair"]["blocked_candidate_ids"], ["C1"])

    def test_pipeline_assigns_models_and_pools_identities_without_sharing_checks(self):
        event = {"owner": "u", "goal": "g", "turn_id": "t", "version": 1, "message": "Which funding?",
                 "proposal": {"summary": "Find funding"}, "effective_rules": [RULE]}
        identity = {k: v for k, v in CANDIDATE.items() if k != "id"}
        calls = []
        def run(prompt, payload, schema, *, model, effort, web_trace):
            calls.append((prompt, model, effort, deepcopy(payload)))
            web_trace.extend(REVIEW["web"])
            if schema is DISCOVERY:
                self.assertNotIn("checks", payload)
                return {"state": "complete", "candidates": [identity]}
            if schema is COLLECTION:
                self.assertNotIn("checks", payload)
                self.assertEqual(payload["candidates"], [CANDIDATE])
                return {"state": "complete", "checks": [CHECK]}
            self.assertEqual(set(payload["checks"]), {"A", "B"})
            return REVIEW["result"]
        with patch("research.plan_search", return_value=[{"priority": "P1"}]), patch("research.call_model", run):
            out = research(event)
        self.assertIn(PAGE, out)
        self.assertEqual([(m,e) for _,m,e,_ in calls],
                         [("gpt-6.1-sol","high")]*4 + [(AUDITOR,"xhigh")])
        self.assertNotEqual(calls[0][-1]["approach"], calls[1][-1]["approach"])

    def test_pool_keeps_different_cycles_and_ignores_discovery_order(self):
        lead = {k: v for k, v in CANDIDATE.items() if k != "id"}
        records = {"B": {"result": {"candidates": [lead, dict(lead, cycle="2027")]}},
                   "A": {"result": {"candidates": [lead]}}}
        self.assertEqual([x["cycle"] for x in identities(records)], ["", "2027"])


if __name__ == "__main__":
    unittest.main()
