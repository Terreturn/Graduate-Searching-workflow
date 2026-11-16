"""CLI for creating/refining a goal or reading it in another session."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from decomposer import decompose
from model_call import EFFORT, MODEL, ROOT
from planner import plan_search
from research import research
from sources import CATALOG
from store import GoalStore, StaleGoal
from test_reports import TEST_DIR, reserve_report, start_test, update_test, write_report


DEFAULT_DB = ROOT.parents[1] / "runs/workflows/phd-opportunity-screening/goals.sqlite3"


def apply_turn(store, owner, goal, turn, message, parse=decompose):
    if not all(v.strip() for v in (owner, goal, turn, message)):
        raise ValueError("用户、目标 ID、轮次 ID 和原话均不能为空")
    cached = store.existing(owner, goal, turn, message)
    if cached is not None:
        return cached
    for attempt in range(3):
        ctx = store.context(owner, goal)
        try:
            proposal = parse(ctx, message)
        except ValueError as error:
            proposal = parse(ctx, message, correction=str(error))
        try:
            return store.commit(ctx, turn, message, proposal, MODEL, EFFORT)
        except StaleGoal:
            if attempt == 2:
                raise


def reparse_initial(store, owner, goal, source, turn):
    if not turn.strip() or turn == source:
        raise ValueError("校正记录需要独立的新 ID")
    row = store.db.execute("SELECT message FROM turns WHERE owner=? AND goal=? AND turn=?",
                           (owner, goal, source)).fetchone()
    if not row:
        raise ValueError("找不到原始轮次")
    message = row["message"]
    cached = store.existing(owner, goal, turn, message)
    if cached is not None:
        if cached.get("reparse_of") != source:
            raise ValueError("该 ID 已经用于其他操作")
        return cached
    ctx = store.context(owner, goal)
    initial = {**ctx, "version": 0, "rules": [], "recent": [], "last_event": 0}
    proposal = decompose(initial, message)
    return store.commit(ctx, turn, message, proposal, MODEL, EFFORT, reparse_of=source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--user", default="local")
    parser.add_argument("--goal", default="phd")
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("test-start", help="开始或续接逐轮写入的测试报告")
    start.add_argument("--directory", type=Path, default=TEST_DIR)
    start.add_argument("--include-history", action="store_true", help="将此目标已记录的历史也纳入报告")
    end = commands.add_parser("test-end", help="保存并关闭此目标的测试报告")
    end.add_argument("--evaluation", type=Path, help="可选的验收 JSON，含布尔值 passed")
    add = commands.add_parser("turn")
    add.add_argument("--id", required=True, help="每条新消息使用唯一 ID，同消息重试沿用 ID")
    add.add_argument("message")
    show = commands.add_parser("show")
    show.add_argument("--version", type=int, help="读取指定历史版本")
    commands.add_parser("history")
    modules = commands.add_parser("modules", help="读取个人背景、目标和本轮临时条件")
    modules.add_argument("--turn", help="按轮次读取当时记录的三个模块")
    plan = commands.add_parser("plan", help="根据已保存的需求规划搜索方向")
    plan.add_argument("--turn", help="指定轮次，默认使用该目标的最新轮次")
    plan.add_argument("--catalog", type=Path, default=CATALOG, help="来源库 SQLite 文件")
    plan.add_argument("--save-test", action="store_true", help="把四字段计划保存到 runs/test")
    search = commands.add_parser("search", help="独立检索、交叉核验并输出已审核的表格")
    search.add_argument("--turn", help="指定需求轮次，默认使用目标最新一轮")
    search.add_argument("--catalog", type=Path, default=CATALOG)
    search.add_argument("--save-test", action="store_true", help="在 runs/test 保存证据、审核记录和发布表格")
    repair = commands.add_parser("reparse-initial", help="为尚未修改的旧首轮追加一次迁移校正")
    repair.add_argument("--source", required=True)
    repair.add_argument("--id", required=True)
    args = parser.parse_args()
    if args.action not in {"turn", "test-start"} and not args.db.is_file():
        parser.exit(1, "数据库不存在\n")
    try:
        store = GoalStore(args.db, read_only=args.action in {"show", "history", "modules", "plan", "search"})
        try:
            if args.action in {"turn", "reparse-initial"}:
                try:
                    if args.action == "turn":
                        result = apply_turn(store, args.user, args.goal, args.id, args.message)
                    else:
                        result = reparse_initial(store, args.user, args.goal, args.source, args.id)
                except (ValueError, RuntimeError, OSError, StaleGoal, sqlite3.Error,
                        subprocess.TimeoutExpired) as exc:
                    update_test(store.db, args.user, args.goal, failure={"turn_id": args.id, "error": str(exc)})
                    raise
                update_test(store.db, args.user, args.goal)
            elif args.action == "test-start":
                result = start_test(store.db, args.user, args.goal, args.directory, args.include_history)
            elif args.action == "test-end":
                evaluation = None
                if args.evaluation:
                    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
                    if not isinstance(evaluation, dict) or not isinstance(evaluation.get("passed"), bool):
                        raise ValueError("Test evaluation requires a boolean passed field.")
                result = update_test(store.db, args.user, args.goal, end=True, evaluation=evaluation)
            elif args.action == "history":
                result = store.history(args.user, args.goal)
            elif args.action == "modules":
                result = store.modules(args.user, args.goal, args.turn)
            elif args.action == "search":
                event = store.turn_result(args.user, args.goal, args.turn)
                if event is None:
                    raise ValueError("该目标还没有已保存的需求轮次")
                if args.save_test:
                    _, report = reserve_report(TEST_DIR)
                    print(report, file=sys.stderr, flush=True)
                    result = research(event, args.catalog, lambda data: write_report(report, data))
                else:
                    result = research(event, args.catalog)
            elif args.action == "plan":
                event = store.turn_result(args.user, args.goal, args.turn)
                report = reserve_report(TEST_DIR)[1] if args.save_test else None
                try:
                    result = plan_search(event, args.catalog)
                    if report:
                        write_report(report, result)
                except BaseException:
                    if report:
                        report.unlink(missing_ok=True)
                    raise
                if report:
                    print(report, file=sys.stderr)
            elif args.version is not None:
                result = store.snapshot_at(args.user, args.goal, args.version)
            else:
                result = store.snapshot(args.user, args.goal)
        finally:
            store.db.close()
        print(result if args.action == "search" else json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError, StaleGoal, sqlite3.Error,
            subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
