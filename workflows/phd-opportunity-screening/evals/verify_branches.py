"""Run a four-turn live acceptance session and journal answers and observed execution."""
import argparse
from contextlib import closing
from datetime import datetime
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from goals import apply_turn
from search_flow import execute, execution_summary, render
from sources import CATALOG
from store import GoalStore
from test_reports import TEST_DIR, reserve_report, write_report

QUESTIONS = [
    "请查英属哥伦比亚大学（UBC）温哥华校区的统计学硕士 MSc in Statistics，只列这一个项目，给出官网。",
    "导师方面，请查 UBC 的 Paul Gustafson，给出他的研究方向，并关联到上轮的统计学硕士项目。",
    "项目方面要求可以做硕士论文；导师方面要求有公开记录证明曾指导研究生学位论文。请列出满足这两个条件的导师与项目组合。",
    "沿用上一轮已核实的结果，只展示第一组。",
]
EXPECTED = [{"school"},{"advisor"},{"school","advisor"},set()]


def add_database_diffs(path):
    """Attach factual before/after values from the committed database to the acceptance record."""
    from entities import entity_key
    report = json.loads(path.read_text())
    before = {"school":{},"advisor":{}}
    replayed = []
    with closing(sqlite3.connect(Path(report["database"]).as_uri()+"?mode=ro",uri=True)) as db:
        for entry in report["rounds"]:
            owner,goal = entry["search"]["owner"],entry["search"]["goal"]
            seq = db.execute("SELECT event FROM turns WHERE owner=? AND goal=? AND turn=?",
                             (owner,goal,entry["turn"])).fetchone()[0]
            event = json.loads(db.execute("SELECT result FROM turns WHERE event=?",(seq,)).fetchone()[0])
            entry["background_diff"] = event.get("background_diff", [])
            entry["background_version"] = event.get("personal_background", {}).get("version")
            entry["previous_goal_version"] = event["base_version"]
            execution = entry["search"]["executions"]
            entry["previous_web_evidence_calls"] = sum(t.get("reused_web_calls",0) for worker in execution.values() for t in worker["runs"])
            from_saved = [b for b,w in execution.items() if w.get("finished") and
                          datetime.fromisoformat(w["finished"]) < datetime.fromisoformat(report["started"])]
            entry["branches_from_earlier_execution"] = from_saved
            entry["web_calls_in_this_invocation"] = sum(w["web_calls"] for b,w in execution.items() if b not in from_saved)
            delta = {}
            states = {}
            for branch in before:
                row = db.execute("SELECT data FROM branch_results WHERE owner=? AND goal=? AND branch=? "
                                 "AND event<=? ORDER BY event DESC LIMIT 1",(owner,goal,branch,seq)).fetchone()
                states[branch] = json.loads(row[0]) if row else {"items":[],"incomplete":False}
                for item in states[branch]["items"]:
                    item["key"] = entity_key(item["identity"])
                current = {i["key"]:i for i in states[branch]["items"]}
                fields = []
                for key in sorted(before[branch].keys() | current.keys()):
                    old = before[branch].get(key,{}).get("facts",{})
                    new = current.get(key,{}).get("facts",{})
                    for name in sorted(old.keys() | new.keys()):
                        if old.get(name) != new.get(name):
                            fields.append({"entity":key,"field":name,"before":old.get(name),"after":new.get(name)})
                delta[branch] = {"entered":sorted(current.keys()-before[branch].keys()),
                                 "left":sorted(before[branch].keys()-current.keys()),"fact_changes":fields}
                before[branch] = current
            entry["result_database_diff"] = delta
            answer,ids,links = render(event,entry["search"]["routing"],states,entry["search"]["incomplete"])
            entry["cross_session_answer"] = answer
            entry["cross_session_links_match"] = links == entry["associations"]
            replayed.append(answer)
    report["same_first_row_on_replay"] = len(replayed) == 4 and replayed[2] == replayed[3]
    write_report(path,report)
    lines = ["# 院校与导师连续验收", "", "测试使用真实模型和网页工具。网页调用数计入搜索、打开网页和页内查找。", ""]
    for row in report["rounds"]:
        lines.extend(["## "+row["turn"].upper(),"",row["question"],""])
        def change_values(change):
            form = change["after"] or change["before"]
            def values(rule):
                return json.dumps(rule["values"],ensure_ascii=False) if rule else "∅"
            return f"{form['field']} {values(change['before'])} → {values(change['after'])}"
        lines.append(f"个人背景 v{row['background_version']}，变更 {len(row['background_diff'])} 项。"
                     f"长期目标 v{row['previous_goal_version']} → v{row['goal_version']}。")
        lines.extend(["", "本轮临时条件 " + ("；".join(change_values(d) for d in row["temporary_diff"]) or "无") + "。", ""])
        for b,label in [("school","院校"),("advisor","导师")]:
            state=row["branches"][b]
            names=[change_values(d) for d in state["diff"]]
            fields=[f["field"] for f in row["result_database_diff"][b]["fact_changes"]]
            actions=[t["action"]+"("+", ".join(t.get("fields",[]))+")" for t in state["tasks"]]
            lines.append(f"- {label}需求变化：{', '.join(names+actions) or '无'}。结果库字段变化：{', '.join(dict.fromkeys(fields)) or '无'}。")
        verified = row["associations"]
        links = "；".join(f"{x['advisor']} ↔ {x['program']} ({x['pathway']})" for x in verified)
        lines.extend(["", "导师与项目关联 " + (links or "无") + "。"])
        if row["branches_from_earlier_execution"]:
            lines.append("本次验收从已完成的真实分支记录恢复 " + ", ".join(row["branches_from_earlier_execution"]) + "。")
        if row["previous_web_evidence_calls"]:
            lines.append(f"本次审核另复用了先前 {row['previous_web_evidence_calls']} 次网页工具调用的记录。")
        if len(row.get("attempts",[])) > 1:
            lines.append("同轮各次实际启动：" + " → ".join(", ".join(a["branches"]) or "无" for a in row["attempts"]) + "。")
        if row["parallel_overlap"] is not None:
            lines.append("两条分支的实际执行时间重叠。" if row["parallel_overlap"] else "两条分支的实际执行时间未重叠。")
        lines.extend(["",f"目标 v{row['goal_version']}。实际启动 {', '.join(row['actual_branches']) or '无'}。"
                     f" 最近一次执行的网页工具调用 {row['web_calls']} 次。","",row["cross_session_answer"],""])
    if report["same_first_row_on_replay"]:
        lines.extend(["", "重新打开数据库后，第三轮与第四轮使用同一组事实和来源，复现的表格完全一致。原始运行输出保存在 JSON 的 answer 字段。"])
    path.with_suffix(".md").write_text("\n".join(lines),encoding="utf-8")
    return path.with_suffix(".md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--user",default="demo")
    parser.add_argument("--goal",default="ubc")
    args = parser.parse_args()
    started, path = reserve_report(TEST_DIR)
    report = {"kind":"live_branch_acceptance","started":started.isoformat(),"database":str(args.db.resolve()),
              "rounds":[],"state":"running"}
    write_report(path,report)
    print(path,flush=True)
    for i,(question,expected) in enumerate(zip(QUESTIONS,EXPECTED),1):
        store = GoalStore(args.db)
        try:
            turn = f"q{i}"
            apply_turn(store,args.user,args.goal,turn,question)
            event = store.turn_result(args.user,args.goal,turn)
            entry = {"turn":turn,"question":question,"goal_version":event["version"],
                     "diff":event["diff"],"temporary_diff":event["temporary_diff"],"branches":event["branches"]}
            report["rounds"].append(entry)
            def checkpoint(data):
                entry["search"] = data
                write_report(path,report)
            result = execute(store,event,CATALOG,checkpoint)
            attempts = [*result.get("prior_attempts",[]), execution_summary(result)]
            observed = {b for attempt in attempts for b in attempt["branches"]}
            concurrent = [a["overlap"] for a in attempts if a["overlap"] is not None]
            overlap = any(concurrent) if concurrent else None
            entry["attempts"] = attempts
            entry["latest_attempt_branches"] = sorted(result["executions"])
            entry["total_observed_web_calls"] = sum(a["web_calls"] for a in attempts)
            entry.update(actual_branches=sorted(observed), parallel_overlap=overlap, web_calls=result["web_calls"],
                         associations=result["associations"],answer=result["table"],
                         displayed_ids=result["displayed"],errors=result["errors"])
            entry["passed"] = (observed==expected and not result["errors"] and bool(result["displayed"])
                               and (i==1 or bool(result["associations"]))
                               and (i!=3 or overlap is True)
                               and (i!=4 or (result["web_calls"]==0 and
                                    result["displayed"]==report["rounds"][2]["displayed_ids"][:1]
                                    and result["associations"]==report["rounds"][2]["associations"][:1]
                                    and not result["routing"]["branches"])))
            write_report(path,report)
            print(turn,sorted(observed),result["web_calls"],entry["passed"],flush=True)
        finally:
            store.db.close()
    report.update(state="completed",passed=all(r["passed"] for r in report["rounds"]))
    write_report(path,report)
    add_database_diffs(path)
    return 0 if report["passed"] else 1


if __name__=="__main__":
    sys.exit(main())
