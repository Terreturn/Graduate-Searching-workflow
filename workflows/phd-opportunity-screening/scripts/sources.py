"""Read the shared graduate program source catalog. All filters are optional."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from rules import DEGREES


CATALOG = Path(__file__).resolve().parents[1] / "data" / "sources.sqlite3"
KINDS = ("site", "post", "announcement")
INFO_TYPES = ("program", "vacancy", "funding", "supervisor_lead", "application_experience", "resource_list", "ranking")
RANKING_SCOPES = ("overall", "subject", "graduate_school", "phd_program")
RANKING_FIELDS = ("publisher", "ranking_scope", "ranking_object", "ranking_year", "disciplines")


def find_sources(*, regions=(), degrees=(), info_type=None, kind=None, ranking_scope=None, ranking_year=None, db_path=CATALOG):
    """Match any supplied region tag, intersected with source and ranking filters."""
    if isinstance(regions, str):
        regions = (regions,)
    regions = tuple(regions)
    filters = []
    params = []
    degrees = (degrees,) if isinstance(degrees, str) else tuple(degrees)
    if degrees:
        placeholders = ", ".join("?" for _ in degrees)
        filters.append(f"id IN (SELECT source_id FROM source_degrees WHERE degree IN ({placeholders}))")
        params.extend(degrees)
    if regions:
        placeholders = ", ".join("?" for _ in regions)
        filters.append("id IN (SELECT source_id FROM source_regions "
                       f"WHERE region IN ({placeholders}))")
        params.extend(regions)
    if info_type is not None:
        filters.append("id IN (SELECT source_id FROM source_types WHERE info_type = ?)")
        params.append(info_type)
    if kind is not None:
        filters.append("kind = ?")
        params.append(kind)
    if ranking_scope is not None:
        filters.append("ranking_scope = ?")
        params.append(ranking_scope)
    if ranking_year is not None:
        filters.append("ranking_year = ?")
        params.append(ranking_year)
    query = "SELECT * FROM source_catalog"
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY kind <> 'site', id"

    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if degrees or ranking_scope is not None or ranking_year is not None:
            # Earlier copies of the catalog contain only opportunity sources.
            columns = {col[1] for col in db.execute("PRAGMA table_info(source_catalog)")}
            if degrees and columns and "degrees" not in columns:
                return []
            if (ranking_scope is not None or ranking_year is not None) and columns and "ranking_scope" not in columns:
                return []
        db.row_factory = sqlite3.Row
        records = [dict(row) for row in db.execute(query, params)]
    for record in records:
        record["degrees"] = json.loads(record.get("degrees", "[]"))
        record["regions"] = json.loads(record["regions"])
        record["info_types"] = json.loads(record["info_types"])
        if record.get("ranking_scope") is not None:
            record["disciplines"] = json.loads(record["disciplines"])
        else:
            for field in RANKING_FIELDS:
                record.pop(field, None)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", action="append", default=[], help="覆盖标签，可重复，例如 global 或 country:JP")
    parser.add_argument("--degree", action="append", default=[], choices=DEGREES, help="按适用学位筛选，可重复")
    parser.add_argument("--type", choices=INFO_TYPES, dest="info_type")
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--ranking-scope", choices=RANKING_SCOPES, help="按综合、学科、研究生院或博士项目排名筛选")
    parser.add_argument("--ranking-year", type=int, help="榜单标注的版本年")
    parser.add_argument("--db", type=Path, default=CATALOG)
    args = parser.parse_args()
    try:
        records = find_sources(regions=args.region, degrees=args.degree, info_type=args.info_type, kind=args.kind,
                               ranking_scope=args.ranking_scope, ranking_year=args.ranking_year, db_path=args.db)
    except sqlite3.Error as exc:
        parser.exit(1, f"无法读取来源库 {args.db}：{exc}\n")
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
