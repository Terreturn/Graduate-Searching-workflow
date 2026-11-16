"""Publish one JSON file per PhD workflow test from its committed turn journal."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo


WORKFLOW = "phd-opportunity-screening"
TEST_DIR = Path(__file__).resolve().parents[3] / "runs" / "test"
CHICAGO = ZoneInfo("America/Chicago")


def report_path(directory, started):
    stamp = started.astimezone(CHICAGO).replace(tzinfo=None).isoformat(sep=" ")
    return directory / f"{WORKFLOW}, test :{stamp.replace(':', '-')}.json"


def reserve_report(directory):
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        started = datetime.now(CHICAGO)
        path = report_path(directory, started)
        try:
            path.touch(exist_ok=False)
            return started, path
        except FileExistsError:
            continue


def write_report(path, report):
    out = NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False)
    temporary = Path(out.name)
    try:
        with out:
            json.dump(report, out, indent=2, ensure_ascii=False)
            out.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def journal_report(db, owner, goal, after_event, started_at, status):
    turns = db.execute(
        "SELECT result FROM turns WHERE owner=? AND goal=? AND event>? ORDER BY event",
        (owner, goal, after_event))
    return {"workflow": WORKFLOW, "started_at": started_at, "status": status,
            "owner": owner, "goal": goal, "turns": [json.loads(row[0]) for row in turns]}


def start_test(db, owner, goal, directory, include_history=False):
    directory = directory.resolve()
    with db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT path, status FROM test_sessions WHERE owner=? AND goal=?",
                         (owner, goal)).fetchone()
        if old and old["status"] == "running":
            return dict(old)
        after = 0 if include_history else db.execute(
            "SELECT coalesce(max(event), 0) FROM turns WHERE owner=? AND goal=?", (owner, goal)
        ).fetchone()[0]
        started, path = reserve_report(directory)
        try:
            write_report(path, journal_report(db, owner, goal, after, started.isoformat(), "running"))
            db.execute("INSERT OR REPLACE INTO test_sessions "
                       "(owner, goal, path, started, after_event, status, errors) "
                       "VALUES (?, ?, ?, ?, ?, 'running', '[]')",
                       (owner, goal, str(path), started.isoformat(), after))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return {"path": str(path), "status": "running"}


def update_test(db, owner, goal, *, end=False, evaluation=None, failure=None):
    params = (owner, goal)
    with db:
        db.execute("BEGIN IMMEDIATE")
        session = db.execute("SELECT * FROM test_sessions WHERE owner=? AND goal=?", params).fetchone()
        if session is None:
            if end:
                raise ValueError("No test has been started for this goal.")
            return
        path, status = session["path"], session["status"]
        if status == "running":
            if end:
                status = "completed" if evaluation is None else ("passed" if evaluation["passed"] else "failed")
            report = journal_report(db, owner, goal, session["after_event"], session["started"], status)
            errors = json.loads(session["errors"])
            if failure:
                errors.append({"at": datetime.now(CHICAGO).isoformat(), **failure})
            if errors:
                report["attempt_errors"] = errors
            if end:
                report["ended_at"] = datetime.now(CHICAGO).isoformat()
                if evaluation is not None:
                    report["evaluation"] = evaluation
            write_report(Path(path), report)
            if end or failure:
                db.execute("UPDATE test_sessions SET status=?, errors=? WHERE owner=? AND goal=?",
                           (status, json.dumps(errors, ensure_ascii=False), *params))
    return {"path": path, "status": status}
