from __future__ import annotations

import argparse

from src.pec.knowledge.paths import INDEX_DIR
from src.pec.search_api.config import PEC_TOPICS_DB
from src.pec.topics.extractor import extract_topics
from src.pec.topics.repository import topic_count, write_topics


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 PEC Topics SQLite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="从 chunks.jsonl 抽取并写入 topics.db")
    build.add_argument("--scope", default="", help="sources 下的目录或文件范围")
    build.add_argument("--chunks", default=None, help="覆盖 chunks.jsonl 路径")
    build.add_argument("--db", default=None, help="覆盖 topics.db 路径")
    status = subparsers.add_parser("status", help="显示 Topics 数量")
    status.add_argument("--db", default=None, help="覆盖 topics.db 路径")
    args = parser.parse_args()
    db_path = args.db or PEC_TOPICS_DB
    if args.command == "status":
        print(f"topics_db: {db_path}\ntopic_count: {topic_count(db_path)}")
        return
    records = extract_topics(scope=args.scope, chunks_path=args.chunks or INDEX_DIR / "chunks.jsonl")
    sources = {record.source_file for record in records}
    write_topics(records, db_path, replace_sources=sources if args.scope else None)
    print(f"Topics 构建完成: {db_path}\nrecords: {len(records)}\nsources: {len(sources)}")


if __name__ == "__main__":
    main()
