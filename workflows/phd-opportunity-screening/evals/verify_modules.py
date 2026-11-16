"""Live, isolated two-turn acceptance check using the public CLI for every read/write."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def check_modules(call):
    message = ("I am studying for a master's in chemistry. Find a PhD position in Japan "
               "related to chemistry. Show only three positions this turn.")
    first = call("turn", "--id", "a1", message)
    m1 = call("modules")
    profile = m1["personal_background"]
    facts = {f["field"]: f["value"].lower() for f in profile["facts"]}
    assert facts["education.current_degree"] == "master" and facts["education.status"] == "in_progress", facts
    assert facts["education.current_field"] == "chemistry", facts
    assert all(f["source"]["evidence"] in message for f in profile["facts"])
    rules = {r["field"]: [v.lower() for v in r["values"]] for r in m1["goal_store"]["rules"]}
    assert rules == {"degree.type":["phd"], "program.country":["jp"], "program.topic":["chemistry"],
                     "search.object":["position"]}, rules
    local = m1["temporary_conditions"]["conditions"]
    assert [(c["scope"], c["field"], c["values"]) for c in local] == [("turn", "output.limit", ["3"])], local
    inspect = call("turn", "--id", "a2", "What are my current search criteria?")
    m2 = call("modules")
    assert first["model"] == "gpt-6.1-sol" and first["reasoning_effort"] == "xhigh"
    assert inspect["proposal"]["intent"] == "inspect" and not inspect["background_diff"] and not inspect["diff"]
    assert inspect["effective_rules"] == inspect["rules"]
    assert m2["temporary_conditions"] == {"turn_id":"a2", "conditions":[], "diff":[]}
    assert m1["goal_store"] == m2["goal_store"] and m2["personal_background"] == profile
    assert call("modules", "--turn", "a1") == m1
    other = call("modules", goal="another-goal")
    assert other["personal_background"] == profile and not other["goal_store"]["rules"]
    assert not other["temporary_conditions"]["conditions"]
    assert call("turn", "--id", "a1", message) == first
    assert len(call("history")) == 2
    return {"passed": True, "model": first["model"], "reasoning_effort": first["reasoning_effort"],
            "isolated_test": True, "mixed_turn": m1, "following_inspection": m2,
            "historical_read": True, "cross_goal_profile": True, "idempotent_replay": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--test-directory", type=Path, help="实时报告目录，默认 runs/test/")
    args = parser.parse_args()
    cli = Path(__file__).resolve().parents[1] / "scripts/goals.py"
    with tempfile.TemporaryDirectory() as tmp:
        def call(*opts, goal="acceptance"):
            output = subprocess.run([sys.executable, "-B", str(cli), "--db", str(Path(tmp)/"test.db"),
                                     "--goal", goal, *opts], text=True, capture_output=True)
            if output.returncode:
                raise RuntimeError(output.stderr)
            return json.loads(output.stdout)

        dest = ["--directory", str(args.test_directory)] if args.test_directory else []
        print(call("test-start", *dest)["path"], flush=True)
        report = args.report or Path(tmp) / "eval.json"
        try:
            result = check_modules(call)
        except (Exception, KeyboardInterrupt) as exc:
            report.write_text(json.dumps({"passed": False, "error": repr(exc)}, ensure_ascii=False),
                              encoding="utf-8")
            call("test-end", "--evaluation", str(report))
            raise
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        call("test-end", "--evaluation", str(report))
        print("Passed background routing, position search, temporary limit expiry, fresh-session reads and replay.")


if __name__ == "__main__":
    main()
