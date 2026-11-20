"""Exercise real routing, SQLite, worker concurrency, publication and reuse with controlled page reads."""
import json
from pathlib import Path
from contextlib import closing
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from goals import apply_turn
from research import ADVISOR_DISCOVERY, SCHOOL_DISCOVERY, COLLECTION
from entities import program_key
from result_store import ResultStore
from search_flow import execute, preview, link_rows
from sources import CATALOG
from store import GoalStore
from test_store import proposal

PROGRAM = "https://university.example/graduate/statistics-msc"
PERSON = "https://university.example/people/a-smith"
SCHOOL = {"institution":"Example University","subject":"Statistics MSc","degree":"master","qualification":"MSc",
          "campus":"Main Campus","cycle":"","track":"","program_url":PROGRAM,"person":"","profile_url":"",
          "pathway":"general"}
ADVISOR = {**SCHOOL, "person":"Alex Smith","profile_url":PERSON,"pathway":"thesis"}


def rule(branch, field, value, evidence, role="required",scope="goal"):
    return {"branch":branch,"op":"set","scope":scope,"field":field,"role":role,"match":"any",
            "values":[value],"when_degree":"","when_country":"","evidence":evidence}


class BranchTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/"goals.sqlite3"
        self.goal = "g"
        self.calls = []
        self.barrier = None
        self.seen = set()
        self.lock = Lock()
        def router(_,payload,schema,**kw):
            return {"tasks":[{"branch":b,"focus":payload["message"]} for b in payload["permitted"]]}
        def planner(_,payload,schema):
            return {"directions":[{"search_direction":"Issuer","search_focus":payload["focus"],"source_ids":[],
                                    "ranking_reason":"Primary records."}]}
        for name,func in [("routing.call_model",router),("planner.call_model",planner),("research.call_model",self.web)]:
            mock = patch(name,side_effect=func)
            mock.start()
            self.addCleanup(mock.stop)

    def web(self,prompt,payload,schema,*,web_trace,**kw):
        branch = payload["branch"]
        with self.lock:
            self.calls.append((branch,prompt,list(payload.get("missing_fields",[]))))
            wait = self.barrier and branch not in self.seen
            self.seen.add(branch)
        if wait:
            self.barrier.wait(timeout=5)
        pages = [PROGRAM,PERSON] if branch=="advisor" else [PROGRAM]
        web_trace.append({"action":{"type":"open_page"},"results":[{"url":url,"title":"University record"} for url in pages]})
        if schema in (ADVISOR_DISCOVERY,SCHOOL_DISCOVERY):
            return {"state":"complete","candidates":[ADVISOR if branch=="advisor" else SCHOOL]}
        if schema is COLLECTION:
            checks = []
            for c in payload["candidates"]:
                decisions = [{"id":k,"decision":"confirmed","urls":pages} for k in payload["candidate_rules_by_id"][c["id"]]]
                facts = [{"field":"subject","value":"Statistics MSc","urls":[PROGRAM]}]
                if branch=="advisor":
                    facts += [{"field":"supervisor","value":"Alex Smith","urls":[PERSON]},
                              {"field":"research_project","value":"Causal inference","urls":[PERSON]}]
                    if "supervisor.mentoring" in payload["missing_fields"]:
                        facts.append({"field":"mentoring","value":"Supervised an MSc thesis","urls":[PERSON]})
                if "program.thesis" in payload["missing_fields"]:
                    facts.append({"field":"thesis","value":"Thesis option","urls":[PROGRAM]})
                checks.append({"id":c["id"],"rules":decisions,"facts":facts,
                               "evidence":[{"url":u,"excerpt":"Named university record"} for u in pages]})
            return {"state":"complete","checks":checks}
        checks = list(payload["checks"]["A"].values())
        return {"state":"complete","columns":[{"field":f["field"],"label":f["field"],"availability":"required"}
                 for f in checks[0]["facts"]],"rows":[{"id":c["id"],"decision":"publish","reason":"Confirmed",
                 "cells":c["facts"],"rules":c["rules"]} for c in checks],
                "links":[{"url":u,"label":"Source"} for u in pages],"sources_label":"Sources","status_label":"Status",
                "empty_message":"No verified matches.","incomplete_message":"Incomplete."}

    def step(self,turn,msg,p,*,view="inherit",tasks=(),owner="u"):
        store = GoalStore(self.path)
        try:
            parsed = dict(p,view=view,language="en",tasks=list(tasks))
            apply_turn(store,owner,self.goal,turn,msg,lambda *_:parsed)
            event = store.turn_result(owner,self.goal,turn)
            return event,execute(store,event,CATALOG)
        finally:
            store.db.close()

    def test_four_turns_real_dispatch_and_zero_network_reuse(self):
        t1,r1 = self.step("t1","Find an MSc",proposal(rule("school","degree.type","master","MSc","scope")),view="school")
        self.assertEqual(set(r1["executions"]),{"school"})
        self.assertIn("Statistics MSc",r1["table"])
        _,r2 = self.step("t2","Find Alex Smith",proposal(rule("advisor","supervisor.name","Alex Smith","Alex Smith","scope")),
                        view="joined")
        self.assertEqual(set(r2["executions"]),{"advisor"})
        self.assertEqual(len(r2["associations"]),1)
        self.barrier,self.seen = Barrier(2),set()
        _,r3 = self.step("t3","thesis and mentoring",proposal(rule("school","program.thesis","optional","thesis"),
                         rule("advisor","supervisor.mentoring","thesis supervision","mentoring")))
        self.assertEqual(set(r3["executions"]),{"school","advisor"})
        workers = list(r3["executions"].values())
        self.assertLess(max(w["start_ns"] for w in workers),min(w["finish_ns"] for w in workers))
        self.assertTrue(all(len(w["runs"])==1 and not w["runs"][0]["discoveries"] for w in workers))
        current = r3["executions"]["advisor"]["runs"][0]
        cached = current["candidates"][0]["_advisor_evidence"]
        self.assertIn("candidate_supervision",cached)
        self.assertTrue(all(d["id"] != "candidate_supervision" for worker in current["collections"].values()
                            for check in worker["result"]["checks"] for d in check["rules"]))
        self.assertIn("Supervised an MSc thesis",r3["table"])
        calls = list(self.calls)
        _,r4 = self.step("t4","Show 1",proposal(rule("school","output.limit","1","1","output","turn")))
        self.assertEqual(r4["executions"],{})
        self.assertEqual(r4["web_calls"],0)
        self.assertEqual(r4["associations"],r3["associations"])
        self.assertEqual(r4["table"], r3["table"])
        self.assertEqual(self.calls,calls)
        self.barrier=None
        store=GoalStore(self.path)
        self.addCleanup(store.db.close)
        self.assertTrue(store.context("u",self.goal)["last_results"])
        self.assertEqual(execute(store,t1,CATALOG)["table"],r1["table"])
        self.assertEqual(self.calls,calls)
        self.assertGreater(store.db.execute("SELECT count(*) FROM verified_facts").fetchone()[0],0)
        self.assertGreater(store.db.execute("SELECT count(*) FROM supervision_links").fetchone()[0],0)

    def test_advisor_institution_filter_and_cache_reuse_on_single_branch(self):
        self.step("t1","Find Alex",proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope")),view="advisor")
        _,r = self.step("t2","Only Example University",proposal(
            rule("advisor","program.institution","Example University","Example University","scope")))
        self.assertEqual(set(r["executions"]),{"advisor"})
        self.assertEqual(r["web_calls"],0)
        self.assertEqual(r["executions"]["advisor"]["cache_hits"],r["displayed"])
        _,other=self.step("t1","Find Alex",proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope")),
                          view="advisor",owner="other")
        self.assertGreater(other["web_calls"],0)

    def test_only_audited_degree_specific_links_can_join(self):
        self.step("t1","Find MSc",proposal(rule("school","degree.type","master","MSc","scope")),view="school")
        event,_=self.step("t2","Find Alex",proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope")),view="joined")
        store=GoalStore(self.path)
        self.addCleanup(store.db.close)
        cache=ResultStore(store,event)
        s=cache.previous("school")["items"]
        advisor=json.loads(store.db.execute("SELECT data FROM branch_results WHERE branch='advisor'").fetchone()[0])["items"][0]
        self.assertFalse(link_rows(s,[{**advisor,"decisions":[]}],True))
        # One UBC-style official page can describe two distinct MSc programs.
        self.assertFalse(link_rows([{**s[0],"identity":{**s[0]["identity"],"subject":"Biostatistics MSc"}}],
                                   [advisor],True))
        self.assertFalse(link_rows(s,[{**advisor,"identity":{**advisor["identity"],"degree":"PhD"}}],True))
        self.assertFalse(link_rows([{**s[0],"identity":{**s[0]["identity"],"pathway":"coursework"}}],[advisor],True))

    def test_expiring_temporary_search_filter_routes_its_own_branch(self):
        self.step("t1", "Find MSc", proposal(rule("school","degree.type","master","MSc","scope")), view="school")
        self.step("t2", "Only Main Campus today", proposal(
            rule("school","program.campus","Main Campus","Main Campus","scope",scope="turn")))
        event, report = self.step("t3", "Continue", proposal())
        self.assertEqual(set(report["executions"]), {"school"})
        [change] = event["branches"]["school"]["diff"]
        self.assertEqual(change["before"]["field"], "program.campus")
        self.assertIsNone(change["after"])
        self.assertEqual(event["version"],1)

    def test_verified_field_lookup_reuses_without_web(self):
        p = proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope"))
        self.step("t1","Find Alex",p,view="advisor")
        _,report = self.step("t2","Show their research",proposal(),tasks=[{"branch":"advisor","action":"lookup",
                            "fields":["research_project"],"evidence":"their research"}])
        self.assertEqual(report["web_calls"],0)
        self.assertIn("Causal inference",report["table"])

    def test_cross_goal_evidence_is_owner_scoped_and_preview_does_not_search(self):
        p = proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope"))
        self.step("t1","Find Alex",p,view="advisor")
        self.goal = "other-goal"
        _,result = self.step("t1","Find Alex",p,view="advisor")
        self.assertEqual(result["web_calls"],0)
        store = GoalStore(self.path)
        self.addCleanup(store.db.close)
        before = list(self.calls)
        directions = preview(store,store.turn_result("u",self.goal),CATALOG)
        self.assertEqual([x["branch"] for x in directions],["advisor"])
        self.assertEqual(self.calls,before)

    def test_published_school_evidence_is_exact_and_cannot_exempt_supervision(self):
        from copy import deepcopy
        from research import bind_program_evidence, rules_for_candidate
        event, _ = self.step("t1","Find MSc",proposal(rule("school","degree.type","master","MSc","scope")),view="school")
        store = GoalStore(self.path)
        self.addCleanup(store.db.close)
        prior = ResultStore(store,event).finished_branch("school")["items"]
        candidate = bind_program_evidence([dict(ADVISOR)],prior)[0]
        active = rules_for_candidate(candidate,{})
        self.assertIn("candidate_program_url",candidate["_program_evidence"])
        self.assertNotIn("candidate_program_url",active)
        self.assertIn("candidate_supervision",active)
        self.assertIn("candidate_profile_url",active)
        self.assertIn("thesis",active["candidate_supervision"]["values"])
        for changed in [{"campus":"Other Campus"},{"subject":"Biostatistics MSc"},{"degree":"phd"}]:
            copy = {**candidate, **changed}
            self.assertNotIn("_program_evidence",bind_program_evidence([copy],prior)[0])
        expired = deepcopy(prior)
        for d in expired[0]["decisions"]:
            d["checked_at"] = "2000-01-01T00:00:00+00:00"
        self.assertNotIn("_program_evidence",bind_program_evidence([dict(candidate)],expired)[0])

    def test_official_page_shared_by_two_awards(self):
        same = {**SCHOOL,"subject":"Master of Science in Statistics (MSc)","qualification":"Master of Science"}
        self.assertEqual(program_key(SCHOOL),program_key(same))
        different = {**same,"subject":"Master of Science in Biostatistics (MSc)"}
        self.assertNotEqual(program_key(SCHOOL),program_key(different))

    def test_source_catalog_rebuild_and_use_tags(self):
        import sqlite3
        with closing(sqlite3.connect(":memory:")) as scratch, closing(sqlite3.connect(CATALOG.as_uri()+"?mode=ro",uri=True)) as catalog:
            scratch.executescript(CATALOG.with_suffix(".sql").read_text())
            query="SELECT id,capabilities,degrees FROM source_catalog ORDER BY id"
            self.assertEqual(scratch.execute(query).fetchall(),catalog.execute(query).fetchall())
            query="SELECT claim FROM source_capabilities WHERE source_id=? AND purpose='verify'"
            self.assertEqual(catalog.execute(query,("google-scholar",)).fetchall(),[])
            self.assertIn(("named_thesis_supervisor",),catalog.execute(query,("ubc-theses",)).fetchall())

    def test_failed_reader_resumes_with_other_readers_original_evidence(self):
        p = proposal(rule("school","degree.type","master","MSc","scope"))
        def fail_b(prompt,payload,schema,**kw):
            if schema is COLLECTION and payload["reader"] == "B":
                raise RuntimeError("reader unavailable")
            return self.web(prompt,payload,schema,**kw)
        with patch("research.call_model", side_effect=fail_b):
            event, partial = self.step("t1","Find MSc",p,view="school")
        self.assertIn("school",partial["errors"])
        self.calls.clear()
        store = GoalStore(self.path)
        self.addCleanup(store.db.close)
        done = execute(store,event,CATALOG)
        self.assertIn("Statistics MSc", done["table"])
        self.assertEqual([c[1] for c in self.calls],["collect.md","audit-search.md"])
        self.assertEqual(done["web_calls"],2)
        trace = done["executions"]["school"]["runs"][0]
        self.assertEqual(trace["reused_web_calls"],3)
        self.assertEqual(trace["collections"]["A"],partial["executions"]["school"]["runs"][0]["collections"]["A"])

    def test_failed_sibling_resumes_without_restarting_completed_branch(self):
        p = proposal(rule("school","degree.type","master","MSc","scope"),
                     rule("advisor","supervisor.name","Alex Smith","Alex","scope"))
        def fail_advisor(prompt,payload,schema,**kw):
            if payload["branch"] == "advisor":
                raise RuntimeError("fixture source unavailable")
            return self.web(prompt,payload,schema,**kw)
        with patch("research.call_model",side_effect=fail_advisor):
            event,partial = self.step("t1","MSc and Alex",p,view="separate")
        self.assertIn("Statistics MSc",partial["table"])
        self.assertEqual(set(partial["errors"]),{"advisor"})
        store = GoalStore(self.path)
        self.addCleanup(store.db.close)
        recovered = execute(store,event,CATALOG)
        self.assertFalse(recovered["errors"])
        self.assertEqual(set(recovered["executions"]),{"advisor"})
        self.assertEqual(recovered["resumed_branches"],["school"])

    def test_explicit_refresh_and_identical_edits(self):
        p=proposal(rule("advisor","supervisor.name","Alex Smith","Alex","scope"))
        self.step("t1","Find Alex",p,view="advisor")
        _,reuse=self.step("t2","Alex again",p)
        self.assertFalse(reuse["executions"])
        _,updated=self.step("t3","Refresh this advisor",proposal(),tasks=[
            {"branch":"advisor","action":"refresh","evidence":"Refresh this advisor"}])
        self.assertEqual(set(updated["executions"]),{"advisor"})
        self.assertGreater(updated["web_calls"],0)
