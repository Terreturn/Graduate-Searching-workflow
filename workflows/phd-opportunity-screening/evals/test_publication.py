"""Adversarial publication cases with frozen, independently sourced observations."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research import publish, rules_for_candidate
from test_research import AUDIT_ROW, CANDIDATE, CELL, CHECK, DECISION, PAGE, REVIEW, RULE

RATE = {"amount": "38000", "currency": "CAD", "period": "year", "fee_status": "international",
        "charges": "tuition"}
BUDGET = {**RULE, "field": "tuition.budget", "match": "any",
          "values": [{**RATE, "amount": "40000", "operator": "at_most"}]}


def render(rules, workers, row, pending=()):
    audit = deepcopy(REVIEW)
    audit["result"]["columns"][0].update(field=row["cells"][0]["field"], label=row["cells"][0]["field"])
    audit["result"]["rows"] = [row]
    checks = {role: {"C1": c} for role, c in zip(("A", "B"), workers)}
    return publish(audit, checks, [CANDIDATE], rules, False, None, pending)


class PublicationTests(unittest.TestCase):
    def test_tuition_uses_the_rate_verified_against_the_budget(self):
        decision = {**DECISION, "tuition_rate": RATE}
        fact = {**CELL, "field": "tuition", "value": "{amount} per year, international tuition",
                "observation": RATE}
        check = {**CHECK, "rules": [decision], "facts": [fact]}
        row = {**AUDIT_ROW, "rules": [decision], "cells": [fact]}
        def result(a=check, b=check, audit=row):
            return render({"R1": BUDGET}, [a, b], audit)

        output, blocked = result(b={**check, "facts": [{**fact, "value": "Annual international tuition: {amount}",
                                                        "observation": {**RATE, "amount": "38000.00"}}]})
        self.assertEqual(blocked, [])
        self.assertIn("CAD 38,000 per year, international tuition", output)
        fabricated = {**CELL, "field": "tuition", "value": "CAD 58,000 per year"}
        output, blocked = result(audit={**row, "cells": [fabricated]})
        self.assertEqual(blocked, ["C1"])
        self.assertNotIn("58,000", output)
        for dimension, wrong in [("amount", "58000"), ("currency", "USD"), ("period", "term"),
                                 ("fee_status", "all"), ("charges", "tuition_and_mandatory_fees")]:
            with self.subTest(dimension=dimension):
                # Even unanimous display observations cannot override a different approved rule rate.
                fabricated = {**fact, "observation": {**RATE, dimension: wrong}}
                fake_check = {**check, "facts": [fabricated]}
                self.assertEqual(result(fake_check, fake_check, {**row, "cells": [fabricated]})[1], ["C1"])
        universal = {**decision, "tuition_rate": {**RATE, "fee_status": "all"}}
        self.assertEqual(result(b={**check, "rules": [universal]})[1], ["C1"])

    def test_stipend_and_deadline_require_the_same_independent_observations(self):
        for field, template, data, formatted, conflicting in [
                ("stipend", "{amount}/year", {"amount": "25000", "currency": "CAD", "period": "year"},
                 "CAD 25,000/year", {"currency": "USD"}),
                ("deadline", "截止时间 {date}", {"iso": "2027-01-15T17:00-05:00"},
                 "截止时间 2027-01-15T17:00-05:00", {"iso": "2027-01-15T17:00-08:00"})]:
            with self.subTest(field=field):
                fact = {**CELL, "field": field, "value": template, "observation": data}
                check = {**CHECK, "facts": [fact]}
                row = {**AUDIT_ROW, "cells": [fact]}
                rules = {"R1": RULE}
                self.assertIn(formatted, render(rules, [check, check], row)[0])
                other = {**check, "facts": [{**fact, "observation": {**data, **conflicting}}]}
                self.assertEqual(render(rules, [check, other], row)[1], ["C1"])
                invented = {**row, "cells": [{**fact, "value": "Guaranteed " + template}]}
                self.assertEqual(render(rules, [check, check], invented)[1], ["C1"])

    def test_combined_funding_cannot_invent_or_change_a_currency(self):
        fact = {**CELL, "value": "CAD 38,000 per year"}
        check = {**CHECK, "facts": [fact]}
        row = {**AUDIT_ROW, "cells": [fact]}
        other = {**CHECK, "facts": [{**fact, "value": "USD 38,000 per year"}]}
        self.assertEqual(render({"R1": RULE}, [check, other], row)[1], ["C1"])
        self.assertEqual(render({"R1": RULE}, [check, check], {**row, "cells": [
            {**fact, "value": "CAD 58,000 per year"}]})[1], ["C1"])

    def country_result(self, countries, decision="not_applicable", allowed=(), missing=False):
        rules = {"R1": {**RULE, "when_country": "CA"}}
        if allowed:
            rules["R2"] = {**RULE, "field": "program.country", "role": "scope", "match": "any",
                           "values": list(allowed)}
        def decisions(code):
            return [({**DECISION, "id": key, "country_code": code} if key == "candidate_country"
                     else {**DECISION, "id": key, "decision": decision if key == "R1" else "confirmed"})
                    for key in rules_for_candidate(CANDIDATE, rules)]
        a, b, auditor = map(decisions, countries)
        if missing:
            b.pop()  # Old collection without jurisdiction.
        return render(rules, [{**CHECK, "rules": a}, {**CHECK, "rules": b}], {**AUDIT_ROW, "rules": auditor})

    def test_country_guard_uses_the_delivery_country(self):
        for country, decision, passes in [("CA", "confirmed", True), ("CA", "not_applicable", False),
                                          ("US", "not_applicable", True), ("US", "confirmed", False)]:
            with self.subTest(country=country, decision=decision):
                _, blocked = self.country_result([country] * 3, decision)
                self.assertEqual(not blocked, passes)

    def test_unknown_country_disagreement_and_contradictory_scope(self):
        for trio in [("CA", "US", "US"), ("US", "US", "CA"), ("ZZ", "ZZ", "ZZ"), ("", "", "")]:
            with self.subTest(trio=trio):
                self.assertEqual(self.country_result(trio)[1], ["C1"])
        self.assertEqual(self.country_result(["US"] * 3, missing=True)[1], ["C1"])
        self.assertEqual(self.country_result(["US"] * 3, allowed=["CA"])[1], ["C1"])
        self.assertFalse(self.country_result(["US"] * 3, allowed=["CA", "US"])[1])
