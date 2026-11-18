"""Offline turn -> SQLite -> real planner -> two collectors -> audit -> table."""
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from clarifications import question_context
from goals import apply_turn
from research import AUDIT, COLLECTION, DISCOVERY, publish, research
from store import GoalStore
from test_publication import RATE
from test_research import CANDIDATE, PAGE, REVIEW
from test_store import proposal

MESSAGE = "For master's tuition my ceiling is 40000."
QUESTION = "What currency and billing period does 40000 refer to?"
DETAIL = {"field": "tuition.budget", "importance": "required", "when_country": "", "when_degree": "master",
          "scope": "goal", "evidence": MESSAGE}
CAP = {**RATE, "amount": "40000", "operator": "at_most", "fee_status": "unspecified"}


class ClarificationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "goals.sqlite3"
        self.store = GoalStore(self.path)
        self.addCleanup(self.store.db.close)

    def turn(self, turn, message, p):
        return apply_turn(self.store, "u", "g", turn, message, parse=lambda *_: p)

    def ask(self, **details):
        return self.turn("t1", MESSAGE, {**proposal(), "questions": [QUESTION],
                         "question_details": [{**DETAIL, **details}], "question_resolutions": []})

    def resolve(self, action, message, *edits, turn="resolved"):
        p = {**proposal(*edits), "question_resolutions": [{"id": "t1/q1", "action": action, "evidence": message}]}
        return self.turn(turn, message, p)

    def edit(self, message, **fields):
        return {"field": "tuition.budget", "op": "set", "role": "required", "scope": "goal",
                "match": "any", "when_degree": "master", "when_country": "", "values": [CAP],
                "evidence": message, **fields}

    def test_reopen_repeat_partial_answer_and_frozen_version(self):
        first = self.ask()
        self.assertEqual(first["version"], 1)
        repeated = self.turn("t2", MESSAGE, {**proposal(), "questions": ["Please specify the budget basis."],
                                             "question_details": [DETAIL]})
        self.assertEqual(repeated["pending_questions"], first["pending_questions"])
        partial = self.resolve("partial", "Canadian dollars", turn="t3")
        for i in range(8):
            self.turn(f"other-{i}", "Show my goal.", proposal(intent="inspect"))
        reader = GoalStore(self.path)
        self.addCleanup(reader.db.close)
        old = reader.turn_result("u", "g", "other-7")
        item, = reader.context("u", "g")["pending_questions"]
        self.assertEqual((item["id"], item["origin_turn"], item["evidence"]), ("t1/q1", "t1", MESSAGE))
        self.assertEqual(item["clarifications"], [{"turn_id": "t3", "evidence": "Canadian dollars"}])
        self.assertEqual(reader.context("u", "other")["pending_questions"], [])
        self.assertEqual(reader.context("other", "g")["pending_questions"], [])
        self.assertEqual(reader.modules("u", "g")["goal_store"]["pending_questions"], [item])
        answer = "Per year."
        p = {**proposal(self.edit(answer)), "question_resolutions": [
            {"id": item["id"], "action": "answered", "evidence": answer}]}
        solved = apply_turn(reader, "u", "g", "solved", answer, parse=lambda *_: p)
        self.assertEqual(solved["pending_questions"], [])
        self.assertEqual(solved["rules"][0]["values"], [CAP])
        self.assertEqual(solved["version"], partial["version"] + 1)
        self.assertEqual(reader.snapshot_at("u", "g", first["version"])["pending_questions"], first["pending_questions"])
        self.assertEqual(reader.turn_result("u", "g", "other-7"), old)
        self.assertEqual(apply_turn(reader, "u", "g", "solved", answer, parse=None), solved)
        restored = self.turn("undo", "Undo.", proposal(intent="undo"))
        self.assertEqual((restored["rules"], restored["pending_questions"]), ([], [item]))

    def test_withdrawal_wrong_scope_and_invalid_resolution(self):
        self.ask()
        msg = "CAD per year"
        for p in [proposal(), proposal(self.edit(msg, when_degree="PhD")),
                  proposal(self.edit(msg, role="preferred")), proposal(self.edit(msg, scope="turn"))]:
            with self.assertRaises(ValueError):
                self.resolve("answered", msg, *p["changes"])
        self.assertEqual(len(self.store.history("u", "g")), 1)
        withdrawn = "Drop that budget."
        result = self.resolve("withdrawn", withdrawn, self.edit(withdrawn, op="unset", values=[]))
        self.assertEqual(result["pending_questions"], [])
        self.assertEqual(result["rules"], [])
        self.assertEqual(result["version"], 2)

    def test_temporary_clarification_expires(self):
        event = self.ask(scope="turn")
        self.assertEqual(len(event["pending_questions"]), 1)
        self.assertFalse(self.store.context("u", "g")["pending_questions"])
        self.assertFalse(self.turn("t2", "Show my goal.", proposal(intent="inspect"))["pending_questions"])
        self.assertEqual(self.store.modules("u", "g", "t1")["temporary_conditions"]["pending_questions"],
                         event["pending_questions"])

    def test_explicit_turn_override_then_restore_pending_budget(self):
        first = self.ask()
        message = "For this turn ignore the master's cap."
        current = self.turn("temporary", message, proposal(
            self.edit(message, op="unrestricted", scope="turn", values=[])))
        self.assertFalse(question_context(current)["pending_questions"])
        self.assertEqual(first["pending_questions"], current["pending_questions"])
        later = self.turn("later", "Search again.", proposal())
        self.assertEqual(question_context(later)["pending_questions"], first["pending_questions"])

    def test_empty_discovery_distinguishes_unresolved_and_out_of_scope(self):
        first = self.ask(when_country="CA")
        audit = deepcopy(REVIEW)
        audit["result"]["rows"] = []
        def output(rules):
            return publish(audit, {}, [], rules, False, None, first["pending_questions"])[0]
        self.assertIn("Search incomplete", output({}))
        scope = {"field": "degree.type", "match": "any", "status": "specified", "when_country": "",
                 "values": ["PhD"], "role": "scope"}
        self.assertIn("No verified matches", output({"R1": scope}))
        self.assertIn("No verified matches", output({"R1": {**scope, "field": "program.country",
                                                            "values": ["US"]}}))

    def run_search(self, event):
        # The catalog, planner, two independent collection paths and publisher are real.
        # Only their model/web responses are controlled.
        stages = []
        identities = [{**{k: v for k, v in CANDIDATE.items() if k != "id"}, "subject": name, "degree": degree}
                      for name, degree in [("Canadian MSc", "master"), ("US MSc", "master"), ("Canadian PhD", "PhD")]]
        def plan(prompt, payload, schema):
            self.assertEqual(payload["pending_questions"], event["pending_questions"])
            self.assertEqual(payload["open_questions"], [q["question"] for q in event["pending_questions"]])
            stages.append(prompt)
            return {"directions": [{"search_direction": "Universities", "source_ids": [],
                                    "search_focus": "Programs", "ranking_reason": "Issuer"}]}

        def respond(prompt, payload, schema, *, web_trace, **kwargs):
            stages.append(prompt)
            self.assertEqual(payload["pending_questions"], event["pending_questions"])
            web_trace.extend(deepcopy(REVIEW["web"]))
            if schema is DISCOVERY:
                return {"state": "complete", "candidates": identities}
            if schema is COLLECTION:
                checks = []
                for c in payload["candidates"]:
                    decisions = [{"id": key, "decision": "confirmed", "urls": [PAGE]}
                                 for key in payload["candidate_rules_by_id"][c["id"]]]
                    for decision in decisions:
                        if decision["id"] == "candidate_country":
                            decision["country_code"] = "US" if c["subject"] == "US MSc" else "CA"
                    checks.append({"id": c["id"], "rules": decisions,
                                   "facts": [{"field": "subject", "value": c["subject"], "urls": [PAGE]}],
                                   "evidence": [{"url": PAGE, "excerpt": c["subject"]}]})
                return {"state": "complete", "checks": checks}
            self.assertIs(schema, AUDIT)
            result = deepcopy(REVIEW["result"])
            result["columns"][0].update(field="subject", label="Program")
            result["rows"] = [{"id": cid, "decision": "publish", "reason": "verified",
                               "rules": c["rules"], "cells": c["facts"]} for cid, c in payload["checks"]["A"].items()]
            return result

        with patch("planner.call_model", side_effect=plan), patch("research.call_model", side_effect=respond):
            output = research(event)
        self.assertCountEqual(stages, ["plan-search.md", "discover.md", "discover.md",
                                     "collect.md", "collect.md", "audit-search.md"])
        return output

    def test_frozen_request_gates_only_the_affected_country_and_degree(self):
        self.ask(when_country="CA")
        old = self.turn("t2", "Show my options.", proposal())
        msg = "Withdraw that cap."
        new = self.resolve("withdrawn", msg, self.edit(msg, op="unset", values=[], when_country="CA"))
        output = self.run_search(self.store.turn_result("u", "g", old["turn_id"]))
        self.assertNotIn("Canadian MSc", output)
        self.assertIn("US MSc", output)
        self.assertIn("Canadian PhD", output)
        self.assertIn("Search incomplete", output)
        self.assertIn("Canadian MSc", self.run_search(new))

    def test_unresolved_preference_allows_publication(self):
        event = self.ask(importance="preferred")
        self.assertIn("Canadian MSc", self.run_search(event))
