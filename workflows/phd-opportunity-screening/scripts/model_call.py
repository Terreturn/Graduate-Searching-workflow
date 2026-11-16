"""Run a structured inference, enabling live web tools only for research roles."""
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-6.1-sol"
EFFORT = "xhigh"


def call_model(prompt_name, payload, output_schema, *, effort=EFFORT, model=MODEL, web_trace=None):
    prompt = (ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="phd-workflow-") as work:
        directory = Path(work)
        schema = directory / "schema.json"
        result = directory / "response.json"
        schema.write_text(json.dumps(output_schema), encoding="utf-8")
        command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--ignore-user-config", "--sandbox", "read-only",
            "--disable", "shell_tool", "--disable", "unified_exec",
            "--disable", "multi_agent", "--disable", "plugins",
            "-c", 'web_search="live"' if web_trace is not None else 'web_search="disabled"',
            "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        ]
        if web_trace is not None:
            # Native structured-output mode can finish without exposing web tools on some backends.
            # Constrain the final message locally after the tool-using turn instead.
            command.append("--json")
            prompt += ("\nYou must invoke live web tools before answering. Return only one JSON object "
                       "in the final message. Follow this JSON Schema exactly.\n" + json.dumps(output_schema))
        else:
            command.extend(["--output-schema", str(schema)])
        command.extend(["--output-last-message", str(result), "-"])
        try:
            run = subprocess.run(command, cwd=directory, text=True, capture_output=True,
                                 input=prompt + "\n" + json.dumps(payload, ensure_ascii=False),
                                 timeout=900 if web_trace is not None else 240)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stderr or b"").decode("utf-8", errors="replace")
            diagnostics = [line for line in output.splitlines()
                           if "ERROR" in line or "reconnecting" in line.lower()]
            raise RuntimeError(f"{model} ({prompt_name}) timed out. Retry using the same turn ID."
                               + "\n".join(diagnostics[-4:])[-900:]) from exc
        if run.returncode or not result.is_file():
            raise RuntimeError(f"Codex call failed ({prompt_name}): " + run.stderr.strip()[-1800:])
        if web_trace is not None:
            for line in run.stdout.splitlines():
                event = json.loads(line)
                item = event.get("item", {})
                if event.get("type") == "item.completed" and item.get("type") == "web_search":
                    web_trace.append({k: item[k] for k in ("query", "action") if k in item}
                                     | {"results": [{k: hit[k] for k in ("url", "title", "ref_id") if k in hit}
                                                     for hit in item.get("results", [])]})
            if not web_trace:
                raise RuntimeError(f"{model} ({prompt_name}) returned no completed web searches.")
        output = result.read_text(encoding="utf-8").strip()
        if web_trace is not None and output.startswith("```") and output.endswith("```"):
            output = output.split("\n", 1)[-1][:-3].strip()
        return json.loads(output)
