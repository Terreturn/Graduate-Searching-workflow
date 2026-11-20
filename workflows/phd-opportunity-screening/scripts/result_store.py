"""Append-only evidence and atomic, owner/goal-scoped search snapshots in the intake database."""
import json
from hashlib import sha256
from datetime import date, datetime, timezone, timedelta
from entities import entity_key, norm, program_key

from rules import within_budget


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rule_key(rule):
    return packed({k: v for k, v in rule.items() if k != "branch"})


def fresh(record, field):
    if field in {"mentoring", "supervisor.mentoring", "research_publication"}:
        return True
    days = 7 if field in {"status", "deadline", "recruitment", "supervisor.recruitment"} else 30
    return datetime.fromisoformat(record["checked_at"]) + timedelta(days=days) > datetime.now(timezone.utc)


def prepare(item, language, background):
    c = item["identity"]
    item["key"] = entity_key(c)
    item["language"] = language
    for fact in item["facts"].values():
        fact["checked_at"] = item["checked_at"]
    for d in item["decisions"]:
        d.setdefault("checked_at", item["checked_at"])
        if d["rule"]["field"] == "eligibility.applicant":
            d["background"] = background
    return item


def deadline_elapsed(item):
    deadline = item["facts"].get("deadline",{}).get("raw",{}).get("observation",{}).get("iso","")
    if not deadline:
        return False
    if len(deadline) == 10:
        return date.fromisoformat(deadline) < datetime.now(timezone.utc).date()
    moment = datetime.fromisoformat(deadline.replace("Z","+00:00"))
    return moment < datetime.now(moment.tzinfo) if moment.tzinfo else moment.date() < datetime.now(timezone.utc).date()


def local_decision(item, rule, background):
    if rule["field"] == "supervisor.recruitment" and deadline_elapsed(item):
        return None
    for d in item["decisions"]:
        if rule_key(d["rule"]) == rule_key(rule) and fresh(d, rule["field"]):
            if d.get("background", background) == background:
                return True
    if rule["when_country"]:
        return None  # A conditional exemption needs an audited country.
    c = item["identity"]
    if rule["field"] == "program.country":
        decision = next((d for d in item["decisions"] if d["rule"]["field"] == "candidate.country"
                         and fresh(d,"country")), None)
        if decision:
            code = decision["decision"].get("country_code")
            if code:
                values = rule["values"]
                return (code in values if rule["match"] == "any" else
                        all(v == code for v in values) if rule["match"] == "all" else code not in values)
    if rule["field"] == "tuition.budget":
        fact = item["facts"].get("tuition")
        if fact and fresh(fact, "tuition"):
            rate = fact["raw"].get("observation")
            if rate and within_budget(rule["values"][0], rate):
                return True
    # Positive equality is safe on independently approved structured identities.
    field = {"degree.type":"degree", "supervisor.name":"person", "program.campus":"campus",
             "program.institution":"institution"}.get(rule["field"])
    if field and c.get(field) and fresh(item, field):
        values = [norm(v) for v in rule["values"]]
        actual = norm(c[field])
        if rule["match"] == "any" and actual in values:
            return True
        if rule["match"] == "none" and actual in values:
            return False
        if field == "degree" and rule["match"] == "any":
            return False
    return None


def merge_item(old, new):
    if not old or old["key"] != new["key"]:
        return new
    decisions = {rule_key(d["rule"]): d for d in old["decisions"]}
    decisions.update({rule_key(d["rule"]): d for d in new["decisions"]})
    facts = dict(old["facts"]) if old["language"] == new["language"] else {}
    facts.update(new["facts"])
    return {**new, "facts": facts, "decisions": list(decisions.values()), "sources": old["sources"] | new["sources"]}


def browse(store, event, branch=None):
    """Read an as-of snapshot without creating tables or calling a model."""
    if event is None:
        raise ValueError("该目标还没有已保存的需求轮次")
    owner, goal, turn = (event[k] for k in ("owner","goal","turn_id"))
    result = {"owner":owner,"goal":goal,"turn_id":turn,"branches":{},"associations":[],"answer":None}
    if not store._has_table("search_runs"):
        return result
    seq = store.db.execute("SELECT event FROM turns WHERE owner=? AND goal=? AND turn=?", (owner,goal,turn)).fetchone()[0]
    for b in ([branch] if branch else ["school","advisor"]):
        row = store.db.execute("SELECT data FROM branch_results WHERE owner=? AND goal=? AND branch=? "
                               "AND event<=? ORDER BY event DESC LIMIT 1",(owner,goal,b,seq)).fetchone()
        result["branches"][b] = json.loads(row[0])["items"] if row else []
    row = store.db.execute("SELECT data FROM search_runs WHERE event=?",(seq,)).fetchone()
    if row:
        run = json.loads(row[0])
        result.update(associations=run["associations"],answer=run["table"])
    return result


class ResultStore:
    def __init__(self, store, event):
        self.db = store.db
        self.owner, self.goal = event["owner"], event["goal"]
        self.seq = self.db.execute("SELECT event FROM turns WHERE owner=? AND goal=? AND turn=?",
                                   (self.owner, self.goal, event["turn_id"])).fetchone()[0]
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS search_routes(event INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_runs(event INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS branch_results(owner TEXT, goal TEXT, branch TEXT, event INTEGER,
                data TEXT NOT NULL, PRIMARY KEY(owner,goal,branch,event));
            CREATE TABLE IF NOT EXISTS verified_entities(owner TEXT, branch TEXT, entity TEXT, event INTEGER,
                data TEXT NOT NULL, PRIMARY KEY(owner,branch,entity,event));
            CREATE TABLE IF NOT EXISTS verified_facts(owner TEXT, entity TEXT, field TEXT, event INTEGER,
                branch TEXT, data TEXT NOT NULL, PRIMARY KEY(owner,entity,field,event,branch));
            CREATE TABLE IF NOT EXISTS supervision_links(owner TEXT, entity TEXT, event INTEGER, branch TEXT,
                data TEXT NOT NULL, PRIMARY KEY(owner,entity,event,branch));
        """)

    def get_route(self, make):
        row = self.db.execute("SELECT data FROM search_routes WHERE event=?", (self.seq,)).fetchone()
        if row is None:
            data = make()
            self.db.execute("INSERT OR IGNORE INTO search_routes VALUES(?,?)", (self.seq, packed(data)))
            row = self.db.execute("SELECT data FROM search_routes WHERE event=?", (self.seq,)).fetchone()
        return json.loads(row[0])

    def previous(self, branch):
        row = self.db.execute("SELECT data FROM branch_results WHERE owner=? AND goal=? AND branch=? "
                              "AND event<? ORDER BY event DESC LIMIT 1", (self.owner,self.goal,branch,self.seq)).fetchone()
        state = json.loads(row[0]) if row else {"items": [], "rules": [], "incomplete": False}
        for item in state["items"]:
            item["key"] = entity_key(item["identity"])
        return state

    def reusable(self, branch):
        rows = self.db.execute("""SELECT v.data FROM verified_entities v JOIN
            (SELECT entity,max(event) AS event FROM verified_entities
             WHERE owner=? AND branch=? AND event<? GROUP BY entity) latest
             ON latest.event=v.event AND latest.entity=v.entity WHERE v.owner=? AND v.branch=?""",
             (self.owner,branch,self.seq,self.owner,branch))
        items = [json.loads(row[0]) for row in rows if row[0] != "null"]
        for item in items:
            item["key"] = entity_key(item["identity"])
        return items

    def finished_branch(self, branch):
        row = self.db.execute("SELECT data FROM branch_results WHERE owner=? AND goal=? AND branch=? AND event=?",
                              (self.owner,self.goal,branch,self.seq)).fetchone()
        return json.loads(row[0]) if row else None

    def input_revision(self):
        digest = sha256()
        for branch in ("school","advisor"):
            row = self.db.execute("SELECT event,data FROM branch_results WHERE owner=? AND goal=? AND branch=? "
                                  "AND event<? ORDER BY event DESC LIMIT 1",
                                  (self.owner,self.goal,branch,self.seq)).fetchone()
            digest.update(packed([branch,list(row) if row else None]).encode())
        return digest.hexdigest()

    def current_inputs(self, run, revision):
        return (run.get("input_revision") == revision if "input_revision" in run
                else bool(run.get("executions")))

    def cached_run(self):
        row = self.db.execute("SELECT data FROM search_runs WHERE event=?", (self.seq,)).fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, branches, report):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.cached_run()
            if (existing and self.current_inputs(existing,report.get("input_revision")) and not existing.get("errors")
                    and (existing.get("displayed") or not existing.get("incomplete",True))):
                self.db.rollback()
                return existing
            for branch, state in branches.items():
                self.db.execute("INSERT OR REPLACE INTO branch_results VALUES(?,?,?,?,?)",
                                (self.owner,self.goal,branch,self.seq,packed(state)))
                for entity in state.get("retired",[]):
                    self.db.execute("INSERT OR REPLACE INTO verified_entities VALUES(?,?,?,?,?)",
                                    (self.owner,branch,entity,self.seq,"null"))
                for item in state["items"]:
                    self.db.execute("INSERT OR REPLACE INTO verified_entities VALUES(?,?,?,?,?)",
                        (self.owner,branch,item["key"],self.seq,packed(item)))
                    for field, fact in item["facts"].items():
                        self.db.execute("INSERT OR REPLACE INTO verified_facts VALUES(?,?,?,?,?,?)",
                            (self.owner,item["key"],field,self.seq,branch,packed(fact)))
                    relation = next((d for d in item["decisions"] if d["rule"]["field"] == "candidate.supervision"), None)
                    if relation:
                        self.db.execute("INSERT OR REPLACE INTO supervision_links VALUES(?,?,?,?,?)",
                            (self.owner,item["key"],self.seq,branch,packed({"identity":item["identity"], **relation})))
            self.db.execute("INSERT OR REPLACE INTO search_runs VALUES(?,?)", (self.seq,packed(report)))
            self.db.commit()
            return report
        except BaseException:
            self.db.rollback()
            raise
