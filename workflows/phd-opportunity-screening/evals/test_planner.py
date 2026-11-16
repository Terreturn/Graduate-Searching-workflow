"""Offline checks for turn-scoped inputs, catalog links and the public plan contract."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from planner import plan_search
from sources import find_sources
from store import GoalStore


def direction(title, ids):
    return {"search_direction": title, "source_ids": ids, "search_focus": "Find openings.",
            "ranking_reason": "The publisher sets the terms."}


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "evals/fixtures/graduate-five-turns.json"
        cls.turns = {r["turn_id"]: r for r in json.loads(fixture.read_text(encoding="utf-8"))}

    def test_plan_reads_scoped_event_and_resolves_catalog_links(self):
        with TemporaryDirectory() as work:
            path = Path(work) / "goals.sqlite3"
            store = GoalStore(path)
            for turn in ("t06", "t07"):
                data = self.turns[turn]
                store.db.execute("INSERT INTO turns(owner,goal,turn,message,base,version,result) "
                                 "VALUES(?,?,?,?,?,?,?)", ("u", "g", turn, data["message"],
                                 data["base_version"], data["version"], json.dumps(data)))
            store.db.close()
            reader = GoalStore(path, read_only=True)
            self.addCleanup(reader.db.close)
            event = reader.turn_result("u", "g")
            self.assertEqual(event["turn_id"], "t07")
            self.assertEqual(reader.turn_result("u", "g", "t06"), self.turns["t06"])
            with self.assertRaises(ValueError):
                reader.turn_result("another-user", "g", "t07")
            expected = [direction("Institutional recruitment pages", []),
                        direction("European research jobs portal", ["euraxess"])]
            with patch("planner.call_model", return_value={"directions": expected}) as model:
                plan = plan_search(event)
            payload = model.call_args.args[1]
            self.assertEqual(payload["effective_rules"], event["effective_rules"])
            self.assertNotEqual(payload["effective_rules"], event["rules"])
            self.assertEqual(payload["utterance"], event["message"])
            self.assertEqual(payload["personal_background"], [
                {"field": f["field"], "value": f["value"]} for f in event["personal_background"]["facts"]
            ])
            entries = {s["id"]: s for s in payload["catalog"]}
            self.assertIn("vacancy", entries["euraxess"]["info_types"])
            self.assertEqual([r["priority"] for r in plan], ["P1", "P2"])
            for row in plan:
                self.assertEqual(set(row), {"search_direction", "search_focus", "priority", "ranking_reason"})
            self.assertEqual(plan[0]["search_direction"], expected[0]["search_direction"])
            url = next(s["url"] for s in find_sources() if s["id"] == "euraxess")
            self.assertIn(url, plan[1]["search_direction"])

    def test_legacy_turn_and_empty_catalog(self):
        row = direction("Department admissions pages", [])
        with patch("planner.find_sources", return_value=[]), patch(
                "planner.call_model", return_value={"directions": [row]}) as model:
            plan = plan_search(self.turns["legacy-t01"])
            self.assertEqual(model.call_args.args[1]["personal_background"], [])
            self.assertEqual(plan[0]["search_direction"], row["search_direction"])
            row["source_ids"] = ["imaginary"]
            with self.assertRaises(ValueError):
                plan_search(self.turns["legacy-t01"])

    def test_unknown_ids_and_generated_links_do_not_reach_the_plan(self):
        for row in (direction("Forum", ["unknown-source"]), direction("https://invented.test", [])):
            with self.subTest(row=row), patch("planner.call_model", return_value={"directions": [row]}):
                with self.assertRaises(ValueError):
                    plan_search(self.turns["legacy-t01"])


if __name__ == "__main__":
    unittest.main()
