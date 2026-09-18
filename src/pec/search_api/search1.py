#!/usr/bin/env python3
"""CLI：PEC topics 结构化检索，输出 JSON context。"""

from __future__ import annotations

import json
import logging
import sys

from src.pec.search_api.config import PEC_TOPICS_DB
from src.pec.search_api.engine import search_topics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m search_api.search_api.search1 '你的问题' [db_path]", file=sys.stderr)
        sys.exit(1)
    query = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else PEC_TOPICS_DB
    result = search_topics(query, db_path=db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
