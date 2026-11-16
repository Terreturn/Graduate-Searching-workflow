"""Run the actual Sol CLI and verify the store from separate OS processes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def field_id(rule):
    return rule["field"] + ("@" + rule["when_country"] if rule["when_country"] else "")


def project(rules):
    result = {}
    for rule in rules:
        name = field_id(rule)
        assert name not in result, f"本用例不应存在重叠规则 {name}"
        role = {"funding.coverage": "required", "ranking.by": "preferred",
                "output.limit": "output"}.get(rule["field"], "scope")
        assert rule["role"] == role and rule["match"] != "none", rule
        if len(rule["values"]) > 1:
            assert rule["match"] == "any", rule
        result[name] = None if rule["status"] == "unrestricted" else rule["values"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--db", type=Path, help="默认保存到该运行的 output/goals.sqlite3")
    parser.add_argument("--goal", default="us-phd-demo")
    parser.add_argument("--test-directory", type=Path, help="自定义实时报告目录，默认 runs/test/")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    for part in ("steps", "output"):
        (run / part).mkdir(parents=True, exist_ok=True)
    db = (args.db or run / "output/goals.sqlite3").resolve()
    command = [sys.executable, "-B", str(ROOT / "scripts/goals.py"),
               "--db", str(db), "--goal", args.goal]

    def invoke(*arguments):
        response = subprocess.run(command + list(arguments), text=True, capture_output=True)
        if response.returncode:
            raise RuntimeError(response.stderr)
        return json.loads(response.stdout)

    catalog = json.loads((ROOT / "evals/cases.yaml").read_text(encoding="utf-8"))
    cases = [case for case in catalog if case.get("kind") != "recorded_session"]
    (run / "run.yaml").write_text(
        f"workflow: phd-opportunity-screening\nworkflow_version: 1\nmodel: gpt-6.1-sol\n"
        f"reasoning_effort: xhigh\ninput_source: workflows/phd-opportunity-screening/evals/cases.yaml\n"
        f"goal: {json.dumps(args.goal)}\ndatabase: {json.dumps(str(db))}\n", encoding="utf-8")
    expected = {}
    previous = 0
    checks = []
    last = {}
    dest = ["--directory", str(args.test_directory)] if args.test_directory else []
    session = invoke("test-start", "--include-history", *dest)
    print(session["path"], flush=True)
    try:
        starting_version = invoke("show")["version"] if db.exists() else 0
        for case in cases:
            current = invoke("turn", "--id", case["id"], case["message"])
            write(run / "steps" / (case["id"] + ".json"), current)
            before = dict(expected)
            expected.update(case.get("set", {}))
            for name in case.get("unset", []):
                del expected[name]
            actual = project(current["rules"])
            assert current["model"] == "gpt-6.1-sol" and current["reasoning_effort"] == "xhigh"
            assert (current["base_version"], current["version"]) == (previous, case["version"])
            assert actual == expected, (case["id"], expected, actual)
            assert project(current["before_rules"]) == before
            assert project(current["effective_rules"]) == {**expected, **case.get("temporary", {})}
            keys_changed = {k for k in before.keys() | expected.keys()
                            if k not in before or k not in expected or before[k] != expected[k]}
            assert {field_id(d["after"] or d["before"]) for d in current["diff"]} == keys_changed
            assert {field_id(d["after"] or d["before"]) for d in current["temporary_diff"]} == set(case.get("temporary", {}))
            if current["version"] < starting_version:
                fresh = invoke("show", "--version", str(current["version"]))
            else:
                fresh = invoke("show")
            assert fresh["version"] == current["version"] and fresh["rules"] == current["rules"]
            checks.append({"turn": case["id"], "goal_version": fresh["version"],
                           "diff": sorted(keys_changed), "temporary_diff": sorted(case.get("temporary", {})),
                           "fresh_process_read": True})
            previous, last = fresh["version"], current
            print(f"{case['id']} v{previous} OK", flush=True)
        replay = invoke("turn", "--id", cases[0]["id"], cases[0]["message"])
        assert replay == json.loads((run / "steps/t01.json").read_text(encoding="utf-8"))
        history = invoke("history")
        assert [event["turn_id"] for event in history] == [case["id"] for case in cases]
        final = invoke("show")
        assert final["rules"] == last["rules"] and final["version"] == previous
        write(run / "output/final-goal.json", final)
        write(run / "eval.json", {"passed": True, "checks": checks, "events": len(history),
                                  "old_turn_replay_idempotent": True, "fresh_final_read": True})
    except Exception as exc:
        write(run / "eval.json", {"passed": False, "completed": checks, "error": repr(exc)})
        invoke("test-end", "--evaluation", str(run / "eval.json"))
        raise
    invoke("test-end", "--evaluation", str(run / "eval.json"))


if __name__ == "__main__":
    main()
