"""Transactional, versioned, per-user goal store. The model never opens the DB."""
import json
from pathlib import Path
import sqlite3

from clarifications import question_snapshot, update_questions
from rules import apply_changes, difference, validate
from personal_background import apply_background
from temporary_conditions import from_legacy, resolve


class StaleGoal(Exception):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class GoalStore:
    def __init__(self, path, *, read_only=False):
        path = Path(path)
        if read_only:
            location = path.resolve().as_uri() + "?mode=ro"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            location = str(path)
        self.db = sqlite3.connect(location, uri=read_only, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS goals (
                owner TEXT NOT NULL, goal TEXT NOT NULL, version INTEGER NOT NULL,
                rules TEXT NOT NULL, PRIMARY KEY(owner, goal)
            );
            CREATE TABLE IF NOT EXISTS turns (
                event INTEGER PRIMARY KEY, owner TEXT NOT NULL, goal TEXT NOT NULL,
                turn TEXT NOT NULL, message TEXT NOT NULL, base INTEGER NOT NULL,
                version INTEGER NOT NULL, result TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(owner, goal, turn)
            );
            CREATE INDEX IF NOT EXISTS goal_history ON turns(owner, goal, event);
            CREATE TABLE IF NOT EXISTS test_sessions (
                owner TEXT NOT NULL, goal TEXT NOT NULL, path TEXT NOT NULL, started TEXT NOT NULL,
                after_event INTEGER NOT NULL, status TEXT NOT NULL, errors TEXT NOT NULL,
                PRIMARY KEY(owner, goal)
            );
        """)
        self._upgrade_modules()

    def _has_table(self, table):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                               (table,)).fetchone() is not None

    def _upgrade_modules(self):
        if self._has_table("personal_background") and self._has_table("temporary_conditions"):
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("""CREATE TABLE IF NOT EXISTS personal_background (
                owner TEXT PRIMARY KEY, version INTEGER NOT NULL, facts TEXT NOT NULL)""")
            if not self._has_table("temporary_conditions"):
                self.db.execute("""CREATE TABLE temporary_conditions (
                    owner TEXT NOT NULL, goal TEXT NOT NULL, turn TEXT NOT NULL,
                    conditions TEXT NOT NULL, diff TEXT NOT NULL, PRIMARY KEY(owner,goal,turn))""")
                for row in self.db.execute("SELECT owner,goal,result FROM turns ORDER BY event"):
                    self._save_temporary(row["owner"], row["goal"], from_legacy(json.loads(row["result"])))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def background(self, owner):
        row = None
        if self._has_table("personal_background"):
            row = self.db.execute("SELECT version,facts FROM personal_background WHERE owner=?",
                                  (owner,)).fetchone()
        return {"version": row["version"] if row else 0, "facts": json.loads(row["facts"]) if row else []}

    def _save_temporary(self, owner, goal, temp):
        self.db.execute("INSERT INTO temporary_conditions VALUES (?,?,?,?,?)",
                        (owner, goal, temp["turn_id"], encode(temp["conditions"]), encode(temp["diff"])))

    def turn_result(self, owner, goal, turn=None):
        """Read one committed turn, including its resolved search rules and background."""
        query = "SELECT result FROM turns WHERE owner=? AND goal=?"
        if turn is None:
            row = self.db.execute(query + " ORDER BY event DESC LIMIT 1", (owner, goal)).fetchone()
        else:
            row = self.db.execute(query + " AND turn=?", (owner, goal, turn)).fetchone()
            if not row:
                raise ValueError(f"找不到该目标的轮次 {turn}")
        return json.loads(row[0]) if row else None

    def modules(self, owner, goal, turn=None):
        """Latest durable state plus latest processed turn, or the state recorded at a named turn."""
        self.db.execute("BEGIN")
        try:
            event = self.turn_result(owner, goal, turn)
            profile = self.background(owner)
            durable = self.snapshot(owner, goal)
            temp = {"turn_id": None, "conditions": [], "diff": []}
            if event:
                if turn is not None:
                    profile = event.get("personal_background", {"version": None, "facts": [],
                                                                "status": "not_recorded"})
                    durable = {k: event[k] for k in ("owner", "goal", "version", "rules")}
                temp_row = None
                if self._has_table("temporary_conditions"):
                    temp_row = self.db.execute(
                        "SELECT conditions,diff FROM temporary_conditions WHERE owner=? AND goal=? AND turn=?",
                        (owner, goal, event["turn_id"])).fetchone()
                temp = ({"turn_id": event["turn_id"], "conditions": json.loads(temp_row["conditions"]),
                         "diff": json.loads(temp_row["diff"])} if temp_row else from_legacy(event))
            questions = question_snapshot(event)
            durable["pending_questions"] = [q for q in questions if q["scope"] == "goal"]
            temp["pending_questions"] = [q for q in questions if q["scope"] != "goal"]
            return {"personal_background": profile, "goal_store": durable, "temporary_conditions": temp}
        finally:
            self.db.rollback()

    def snapshot(self, owner, goal):
        row = self.db.execute(
            "SELECT g.version, g.rules, (SELECT json_extract(t.result, '$.pending_questions') "
            "FROM turns t WHERE t.owner=g.owner AND t.goal=g.goal AND t.version=g.version "
            "AND t.version>t.base ORDER BY t.event DESC LIMIT 1) AS questions "
            "FROM goals g WHERE g.owner=? AND g.goal=?", (owner, goal)).fetchone()
        return self._goal_snapshot(owner, goal, row)

    @staticmethod
    def _goal_snapshot(owner, goal, row):
        questions = json.loads(row["questions"] or "[]") if row else []
        return {"owner": owner, "goal": goal, "version": row["version"] if row else 0,
                "rules": json.loads(row["rules"]) if row else [],
                "pending_questions": [q for q in questions if q["scope"] == "goal"]}

    def snapshot_at(self, owner, goal, version):
        if version == 0:
            return self._goal_snapshot(owner, goal, None)
        row = self.db.execute(
            "SELECT version,json_extract(result,'$.rules') AS rules, "
            "json_extract(result,'$.pending_questions') AS questions FROM turns "
            "WHERE owner=? AND goal=? AND version=? AND version>base "
            "ORDER BY event LIMIT 1", (owner, goal, version)).fetchone()
        if not row:
            raise ValueError(f"找不到该目标的 v{version}")
        return self._goal_snapshot(owner, goal, row)

    def existing(self, owner, goal, turn, message):
        row = self.db.execute(
            "SELECT message,result FROM turns WHERE owner=? AND goal=? AND turn=?",
            (owner, goal, turn)).fetchone()
        if row and row["message"] != message:
            raise ValueError("该 turn ID 已用于另一条原话，请使用新的 ID")
        return json.loads(row["result"]) if row else None

    def history(self, owner, goal):
        rows = self.db.execute("SELECT result FROM turns WHERE owner=? AND goal=? ORDER BY event",
                               (owner, goal))
        return [json.loads(r[0]) for r in rows]

    def context(self, owner, goal):
        self.db.execute("BEGIN")
        try:
            snapshot = self.snapshot(owner, goal)
            rows = self.db.execute(
                "SELECT event,message,result FROM turns WHERE owner=? AND goal=? "
                "ORDER BY event DESC LIMIT 6", (owner, goal)).fetchall()
            recent = []
            for row in reversed(rows):
                result = json.loads(row["result"])
                recent.append({"message": row["message"], "turn_id": result["turn_id"],
                               "summary": result["proposal"]["summary"],
                               "questions": result["proposal"]["questions"]})
            return {**snapshot, "personal_background": self.background(owner),
                    "temporary_conditions": [], "recent": recent,
                    "last_event": rows[0]["event"] if rows else 0}
        finally:
            self.db.rollback()

    def commit(self, ctx, turn, message, proposal, model, effort, *, reparse_of=None):
        validate(proposal, message)
        owner, goal = ctx["owner"], ctx["goal"]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.existing(owner, goal, turn, message)
            if prior is not None:
                self.db.rollback()
                return prior
            current = self.snapshot(owner, goal)
            old_profile = self.background(owner)
            last_event = self.db.execute(
                "SELECT coalesce(max(event),0) FROM turns WHERE owner=? AND goal=?",
                (owner, goal)).fetchone()[0]
            if (current["version"] != ctx["version"] or last_event != ctx["last_event"]
                    or old_profile["version"] != ctx["personal_background"]["version"]):
                raise StaleGoal("目标或近期对话已更新，请基于最新上下文重新解析")
            changes = proposal["changes"]
            before = current["rules"]
            initial_rules = before
            if reparse_of is not None:
                source = self.existing(owner, goal, reparse_of, message)
                if (not source or source["base_version"] != 0 or source["version"] != 1
                        or current["version"] != 1 or before != source["rules"]
                        or old_profile["version"] != 0 or "personal_background" in source):
                    raise ValueError("旧首轮之后已有持久修改，不能自动重解析")
                initial_rules = []
            rules = apply_changes(initial_rules, [c for c in changes if c["scope"] == "goal"])
            before_pending = current["pending_questions"]
            pending = update_questions(before_pending, turn, message, proposal)
            if proposal["intent"] == "undo":
                row = self.db.execute(
                    "SELECT result FROM turns WHERE owner=? AND goal=? AND version>base "
                    "ORDER BY event DESC LIMIT 1", (owner, goal)).fetchone()
                if row:
                    previous = json.loads(row[0])
                    rules = previous["before_rules"]
                    pending = previous.get("before_pending_questions", [])
            diff = difference(before, rules)
            durable_pending = [q for q in pending if q["scope"] == "goal"]
            goal_changed = bool(diff) or durable_pending != before_pending
            version = current["version"] + int(goal_changed)
            temp, effective = resolve(turn, rules, changes)
            facts, bg_diff = apply_background(old_profile["facts"], proposal["background_changes"],
                                              goal, reparse_of or turn)
            profile = {"version": old_profile["version"] + int(bool(bg_diff)), "facts": facts}
            result = {
                "owner": owner, "goal": goal, "turn_id": turn, "message": message,
                "model": model, "reasoning_effort": effort, "base_version": current["version"],
                "version": version, "proposal": proposal, "before_rules": before,
                "diff": diff, "rules": rules, "temporary_diff": temp["diff"],
                "effective_rules": effective,
                "before_pending_questions": before_pending, "pending_questions": pending,
                "personal_background": profile, "background_diff": bg_diff,
                "background_base_version": old_profile["version"], "temporary_conditions": temp,
            }
            if reparse_of is not None:
                result["reparse_of"] = reparse_of
            if bg_diff:
                self.db.execute(
                    "INSERT INTO personal_background VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET "
                    "version=excluded.version,facts=excluded.facts",
                    (owner, profile["version"], encode(facts)))
            if goal_changed:
                self.db.execute(
                    "INSERT INTO goals VALUES(?,?,?,?) ON CONFLICT(owner,goal) DO UPDATE SET "
                    "version=excluded.version,rules=excluded.rules",
                    (owner, goal, version, encode(rules)))
            self.db.execute(
                "INSERT INTO turns(owner,goal,turn,message,base,version,result) VALUES(?,?,?,?,?,?,?)",
                (owner, goal, turn, message, current["version"], version, encode(result)))
            self._save_temporary(owner, goal, temp)
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise
