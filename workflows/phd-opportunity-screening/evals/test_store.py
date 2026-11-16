"""Offline regressions for the parser contract and persistence guarantees."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from goals import apply_turn
from rules import apply_changes
from store import GoalStore, StaleGoal


def edit(value="US", op="set", scope="goal", evidence="美国"):
    return {"op": op, "scope": scope, "field": "program.country", "role": "scope",
            "match": "any", "when_country": "", "values": [value] if value else [],
            "evidence": evidence}


def proposal(*changes, intent="refine"):
    return {"intent": intent, "changes": list(changes), "background_changes": [],
            "questions": [], "summary": "测试"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "goals.sqlite3"
        self.store = GoalStore(self.path)
        self.addCleanup(self.store.db.close)

    def commit(self, turn, message, item, owner="u", goal="g"):
        return self.store.commit(self.store.context(owner, goal), turn, message, item,
                                 "test-double", "test")

    def test_language_repair_preserves_extraction_and_rechecks_display(self):
        message = "Je cherche aux États-Unis."
        original = proposal(edit(evidence="États-Unis"))
        original.update(summary="You are looking in the US.", questions=["Which field?"])
        repair = {"verdict": "revise", "summary": "Votre recherche porte sur les États-Unis.",
                  "questions": ["Dans quel domaine ?"]}
        approved = {"verdict": "match", "summary": "", "questions": []}
        with patch("decomposer._call_model", side_effect=[original, repair, approved]) as model:
            event = apply_turn(self.store, "u", "g", "fr", message)
            self.assertEqual([c.args[0] for c in model.call_args_list],
                             ["decompose.md", "check-language.md", "check-language.md"])
            self.assertEqual(model.call_args_list[-1].args[1]["summary"], repair["summary"])
            self.assertEqual(model.call_args_list[-1].args[1]["original_display"]["questions"], ["Which field?"])
        self.assertEqual(event["proposal"], {**original, "summary": repair["summary"],
                                             "questions": repair["questions"]})
        self.assertEqual(event["rules"][0]["values"], ["US"])

    def test_language_failure_is_not_committed(self):
        original = proposal(edit())
        original.update(summary="United States.", questions=["Where?"])
        revise = {"verdict": "revise", "summary": "美国。", "questions": ["哪里？"]}
        for responses in ([original, revise, revise],
                          [original, {**revise, "questions": []}]):
            with patch("decomposer._call_model", side_effect=responses):
                with self.assertRaisesRegex(RuntimeError, "language check"):
                    apply_turn(self.store, "u", "g", "zh", "美国")
            self.assertEqual(self.store.history("u", "g"), [])

    def test_live_test_file_lifecycle_across_processes(self):
        reports = self.path.parent / "runs/test"
        cli = Path(__file__).resolve().parents[1] / "scripts/goals.py"

        def run(*args, success=True):
            proc = subprocess.run([sys.executable, "-B", str(cli), "--db", str(self.path),
                                   "--user", "u", "--goal", "g", *args], capture_output=True, text=True)
            if not success:
                self.assertNotEqual(proc.returncode, 0)
                return
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

        self.commit("old", "Before test", proposal())
        path = Path(run("test-start", "--directory", str(reports))["path"])
        self.assertEqual(path.parent, reports.resolve())
        self.assertRegex(path.name, r"^phd-opportunity-screening, test :\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}(\.\d{6})?\.json$")
        self.assertEqual(json.loads(path.read_text())["turns"], [])
        self.assertEqual(run("test-start", "--directory", str(reports))["path"], str(path))
        history = []
        for turn, msg, item in [("q1", "美国", proposal(edit())),
                                ("q2", "加拿大", proposal(edit("CA", scope="turn", evidence="加拿大")))]:
            # Commit offline, then replay via the real CLI after reopening the database.
            history.append(self.commit(turn, msg, item))
            self.assertEqual(run("turn", "--id", turn, msg), history[-1])
            self.assertEqual(json.loads(path.read_text())["turns"], history)
        run("turn", "--id", "q1", "美国")
        run("turn", "--id", "q1", "Conflicting retry", success=False)
        evaluation = self.path.parent / "eval.json"
        evaluation.write_text('{"passed": false, "reason": "rejected retry"}')
        self.assertEqual(run("test-end", "--evaluation", str(evaluation))["status"], "failed")
        closed = json.loads(path.read_text())
        self.assertEqual(closed["turns"], history)
        self.assertEqual(closed["evaluation"], json.loads(evaluation.read_text()))
        self.assertEqual([e["turn_id"] for e in closed["attempt_errors"]], ["q1"])
        run("test-end")
        run("turn", "--id", "q1", "美国")
        second = Path(run("test-start", "--directory", str(reports))["path"])
        self.assertNotEqual(path, second)
        self.assertEqual(json.loads(second.read_text())["turns"], [])
        self.assertEqual(json.loads(path.read_text()), closed)

    def test_synthetic_graduate_session(self):
        directory = Path(__file__).resolve().parent
        catalog = json.loads((directory / "cases.yaml").read_text(encoding="utf-8"))
        case = next(item for item in catalog if item["id"] == "graduate-five-turns")
        journal = json.loads((directory / case["fixture"]).read_text(encoding="utf-8"))
        events = {event["turn_id"]: event for event in journal}
        self.assertEqual([q["id"] for q in case["questions"]], ["Q1", "Q2", "Q3", "Q4", "Q5"])
        for q in case["questions"]:
            with self.subTest(question=q["id"]):
                expected = events[q["accepted"]]
                message = events[q["input"]]["message"]
                self.assertEqual(expected["message"], message)
                result = self.commit(q["input"], message, expected["proposal"],
                                     owner=expected["owner"], goal=expected["goal"])
                for field in ("personal_background", "rules", "effective_rules", "background_diff", "temporary_diff"):
                    self.assertEqual(result[field], expected[field])
                self.assertEqual(result["temporary_conditions"]["conditions"],
                                 expected["temporary_conditions"]["conditions"])
                self.assertEqual(result["version"], expected["version"])
        fresh = self.store.context(expected["owner"], expected["goal"])
        self.assertEqual(fresh["temporary_conditions"], [])
        self.assertEqual(fresh["rules"], events["t06"]["rules"])

    def test_same_turn_retries_skip_model_even_after_later_writes(self):
        first = self.commit("1", "美国", proposal(edit()))
        self.commit("2", "加拿大", proposal(edit("CA", "add", evidence="加拿大")))
        never = Mock(side_effect=AssertionError("重试不应重复调用模型"))
        self.assertEqual(apply_turn(self.store, "u", "g", "1", "美国", never), first)
        with self.assertRaises(ValueError):
            apply_turn(self.store, "u", "g", "1", "美国以外", never)
        self.assertEqual(len(self.store.history("u", "g")), 2)

    def test_invalid_second_edit_is_atomic(self):
        with self.assertRaisesRegex(ValueError, "原话"):
            self.commit("1", "美国", proposal(edit(), edit(evidence="编造")))
        self.assertEqual(self.store.snapshot("u", "g")["version"], 0)
        self.assertEqual(self.store.history("u", "g"), [])

    def test_store_rejects_global_edit_in_comparison(self):
        with self.assertRaisesRegex(ValueError, "比较"):
            self.commit("1", "如果美国", proposal(edit(), intent="compare"))
        self.assertEqual(self.store.history("u", "g"), [])

    def test_real_transaction_rolls_back_if_event_insert_fails(self):
        self.store.db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON turns "
                              "BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.commit("1", "美国", proposal(edit()))
        self.assertEqual(self.store.snapshot("u", "g")["rules"], [])

    def test_fresh_connection_and_owner_and_goal_isolation(self):
        self.commit("1", "美国", proposal(edit()))
        reader = GoalStore(self.path, read_only=True)
        self.addCleanup(reader.db.close)
        self.assertEqual(reader.snapshot("u", "g")["version"], 1)
        self.assertEqual(reader.snapshot("other", "g")["rules"], [])
        self.assertEqual(reader.history("u", "other"), [])

    def test_independent_reader_can_reopen_old_version_after_resume(self):
        self.commit("1", "美国", proposal(edit()))
        self.commit("2", "加拿大", proposal(edit("CA", "add", evidence="加拿大")))
        reader = GoalStore(self.path, read_only=True)
        self.addCleanup(reader.db.close)
        self.assertEqual(reader.snapshot_at("u", "g", 1)["rules"][0]["values"], ["US"])
        self.assertEqual(reader.snapshot("u", "g")["rules"][0]["values"], ["CA", "US"])
        with self.assertRaises(ValueError):
            reader.snapshot_at("someone-else", "g", 1)

    def test_noop_unrestricted_and_unset(self):
        self.commit("1", "美国", proposal(edit()))
        noop = self.commit("2", "仍是美国", proposal(edit()))
        self.assertEqual((noop["version"], noop["diff"]), (1, []))
        explicit = self.commit("3", "不限", proposal(edit(None, "unrestricted", evidence="不限")))
        self.assertEqual(explicit["rules"][0]["status"], "unrestricted")
        still_open = self.commit("3a", "加拿大也可以", proposal(edit("CA", "add", evidence="加拿大")))
        self.assertEqual((still_open["version"], still_open["rules"]), (2, explicit["rules"]))
        cleared = self.commit("4", "撤销", proposal(edit(None, "unset", evidence="撤销")))
        self.assertEqual(cleared["rules"], [])
        self.assertEqual(cleared["version"], 3)

    def test_empty_and_idempotent_remove(self):
        initial = apply_changes([], [edit()])
        self.assertEqual(apply_changes(initial, [edit("CA", "remove")]), initial)
        self.assertEqual(apply_changes(initial, [edit("US", "remove")]), [])

    def test_country_or_region_is_one_alternative_list(self):
        self.commit("1", "美国", proposal(edit()))
        message = "also Europe"
        clear = edit(None, "unset", evidence=message)
        mixed = {**edit(evidence=message), "field":"program.location",
                 "values":["country:US","region:Europe"]}
        durable = self.commit("2", message, proposal(clear, mixed))
        self.assertEqual([(r["field"],r["match"],r["values"]) for r in durable["rules"]],
                         [("program.location","any",["country:US","region:Europe"])])
        limit = {**mixed, "scope":"turn", "values":["region:Europe"]}
        local = self.commit("3", message, proposal(limit))
        self.assertEqual(local["version"], durable["version"])
        self.assertEqual(local["effective_rules"][0]["values"], ["region:Europe"])
        self.assertEqual(self.store.modules("u","g")["goal_store"]["rules"], durable["rules"])
        with self.assertRaises(ValueError):
            self.commit("4", message, proposal({**mixed, "values":["EU"]}))

    def test_clarification_reference_invalidates_stale_context(self):
        ctx = self.store.context("u", "g")
        question = proposal(intent="clarify")
        question["questions"] = ["资助需要覆盖哪些费用？"]
        self.commit("1", "要资助", question)
        self.assertEqual(self.store.context("u", "g")["recent"][0]["questions"], question["questions"])
        with self.assertRaises(StaleGoal):
            self.store.commit(ctx, "2", "美国", proposal(edit()), "test-double", "test")

    def test_concurrent_writers_rebase_without_losing_either_add(self):
        self.commit("1", "美国", proposal(edit()))
        barrier = threading.Barrier(2)

        def worker(country):
            store = GoalStore(self.path)
            observed_versions = []

            def parse(context, message):
                observed_versions.append(context["version"])
                if len(observed_versions) == 1:
                    barrier.wait(timeout=5)
                return proposal(edit(country, "add", evidence=country))

            try:
                apply_turn(store, "u", "g", country, country, parse)
                return observed_versions
            finally:
                store.db.close()

        with ThreadPoolExecutor(2) as pool:
            observed = list(pool.map(worker, ["CA", "GB"]))
        self.assertEqual(sorted(map(len, observed)), [1, 2])
        final = self.store.snapshot("u", "g")
        self.assertEqual(final["version"], 3)
        self.assertEqual(final["rules"][0]["values"], ["CA", "GB", "US"])

    def test_background_lifetime_source_and_cross_goal_conflict(self):
        ctx = self.store.context("u", "other")
        p = proposal()
        p["background_changes"] = [{"op":"set", "field":"education.current_field",
                                    "values":["Statistics"], "evidence":"Statistics"}]
        first = self.commit("1", "Studying Statistics", p)
        self.assertEqual((first["version"], first["personal_background"]["version"]), (0, 1))
        with self.assertRaises(StaleGoal):
            self.store.commit(ctx, "2", "美国", proposal(edit()), "test", "test")
        other = self.store.modules("u", "other")
        self.assertEqual(other["personal_background"]["facts"][0]["source"],
                         {"goal":"g", "turn_id":"1", "evidence":"Statistics"})
        self.assertFalse(self.store.modules("other", "g")["personal_background"]["facts"])
        p["background_changes"][0].update(values=["Economics"], evidence="Economics")
        self.commit("2", "Now studying Economics", p, goal="other")
        self.assertEqual(self.store.modules("u", "g")["personal_background"]["facts"][0]["value"], "Economics")
        self.assertEqual(self.store.modules("u", "g", "1")["personal_background"], first["personal_background"])
        p["background_changes"][0].update(op="unset", values=[], evidence="clear")
        self.commit("3", "clear my field", p)
        self.assertEqual(self.store.background("u"), {"version":3, "facts":[]})

    def test_all_three_modules_rollback_together(self):
        self.store.db.execute("CREATE TRIGGER fail_temp BEFORE INSERT ON temporary_conditions "
                              "BEGIN SELECT RAISE(ABORT,'test'); END")
        p = proposal(edit(), edit("CA", scope="turn", evidence="加拿大"))
        p["background_changes"] = [{"op":"add","field":"skills.methods","values":["R"],"evidence":"R"}]
        with self.assertRaises(sqlite3.IntegrityError):
            self.commit("1", "R 美国 加拿大", p)
        for table in ("goals", "turns", "personal_background", "temporary_conditions"):
            self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_background_validation_and_hypothetical_lifetime(self):
        p = proposal()
        p["background_changes"] = [{"op":"add","field":"skills.methods","values":["R"],"evidence":"R"}]
        with self.assertRaises(ValueError):
            self.commit("1", "Python", p)
        self.commit("1", "美国", proposal(edit()))
        self.commit("2", "假设加拿大", proposal(edit("CA", scope="scenario", evidence="加拿大"), intent="compare"))
        self.assertEqual(self.store.modules("u", "g")["temporary_conditions"]["conditions"][0]["scope"], "scenario")
        self.assertEqual(self.store.context("u", "g")["temporary_conditions"], [])
        self.commit("3", "查看", proposal(intent="inspect"))
        self.assertFalse(self.store.modules("u", "g")["temporary_conditions"]["conditions"])
        self.assertTrue(self.store.modules("u", "g", "2")["temporary_conditions"]["conditions"])

    def test_backfill_legacy_temporary_without_changing_journal(self):
        self.commit("1", "本轮美国", proposal(edit(scope="turn")))
        legacy = self.store.history("u", "g")[0]
        for field in ("background_diff","personal_background","background_base_version","temporary_conditions"):
            del legacy[field]
        del legacy["proposal"]["background_changes"]
        raw = json.dumps(legacy)
        self.store.db.execute("UPDATE turns SET result=?", (raw,))
        self.store.db.execute("DROP TABLE temporary_conditions")
        self.store.db.execute("DROP TABLE personal_background")
        upgraded = GoalStore(self.path)
        self.addCleanup(upgraded.db.close)
        temp = upgraded.modules("u", "g", "1")["temporary_conditions"]
        self.assertEqual(temp["conditions"], legacy["proposal"]["changes"])
        self.assertEqual(temp["diff"], legacy["temporary_diff"])
        self.assertEqual(upgraded.db.execute("SELECT result FROM turns").fetchone()[0], raw)
        self.assertEqual(upgraded.modules("u", "g", "1")["personal_background"]["status"], "not_recorded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
