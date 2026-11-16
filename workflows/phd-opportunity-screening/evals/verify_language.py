"""Live acceptance check for a language switch on one goal using the normal CLI."""
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory


CLI = Path(__file__).resolve().parents[1] / "scripts/goals.py"
CASES = [
    ("en", "I want to find PhD programs in the US. This time show only three. "
           "I also want to stay close to home. Please ask me where home is."),
    ("zh", "我想找美国的 PhD 项目。这次只展示三个。我还想离家近，请问我家在哪里。"),
]


def main():
    with TemporaryDirectory() as tmp:
        command = [sys.executable, "-B", str(CLI), "--db", str(Path(tmp) / "test.db"),
                   "--goal", "reply-language"]

        def call(*args):
            output = subprocess.run(command + list(args), text=True, capture_output=True)
            if output.returncode:
                raise RuntimeError(output.stderr.strip())
            return json.loads(output.stdout)

        print(call("test-start")["path"], flush=True)
        checks = []
        evaluation = Path(tmp) / "evaluation.json"
        try:
            for language, message in CASES:
                turn = call("turn", "--id", language, message)
                proposal = turn["proposal"]
                assert proposal["questions"], proposal
                for line in [proposal["summary"], *proposal["questions"]]:
                    if language == "zh":
                        assert re.search(r"[\u3400-\u9fff]", line), line
                    else:
                        assert not re.search(r"[\u3400-\u9fff]", line), line
                        assert re.search(r"\b(where|what|which|you|your|home|programs|three|city)\b", line, re.I), line
                goal = {r["field"]: r["values"] for r in turn["rules"]}
                assert goal["program.country"] == ["US"] and goal["degree.type"] == ["PhD"], goal
                assert goal["search.object"] == ["program"] and "output.limit" not in goal, goal
                changes = turn["temporary_conditions"]["conditions"]
                assert any(c["field"] == "output.limit" and c["values"] == ["3"] for c in changes), changes
                assert all(c["evidence"] in message for c in proposal["changes"] + proposal["background_changes"])
                checks.append(language)
                print(language, "passed", flush=True)
            result = {"passed": True, "languages_checked": checks, "language_switch": True}
        except (Exception, KeyboardInterrupt) as exc:
            result = {"passed": False, "languages_checked": checks, "error": repr(exc)}
            raise
        finally:
            evaluation.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            call("test-end", "--evaluation", str(evaluation))


if __name__ == "__main__":
    main()
