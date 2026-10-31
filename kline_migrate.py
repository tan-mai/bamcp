# TM - #KL-SQL - Kline storage SQLite
"""python kline_migrate.py import | verify"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    if action not in ("import", "verify"):
        print("cach dung: python kline_migrate.py import|verify", file=sys.stderr)
        return 2
    import server
    report = server._kline_migrate(action)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if action == "import" or report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
