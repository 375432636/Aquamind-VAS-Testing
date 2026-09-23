"""Final Actions gate: reports remain downloadable even when a batch failed."""

import argparse
import json
import logging
from pathlib import Path


def check(directory, *, archive=False):
    try:
        data = json.loads((Path(directory) / "batch.json").read_text(encoding="utf-8"))
        sessions = data["sessions"]
        if data["status"] != "passed" or not isinstance(sessions, list) or not sessions:
            return False
        if data.get("error") or (archive and data.get("archive_status") != "passed"):
            return False
        for session in sessions:
            if session.get("status") != "passed":
                return False
            if archive and session.get("archive_status") != "passed":
                return False
            if (
                not session.get("turn_count")
                or session.get("completed_turns") != session["turn_count"]
            ):
                return False
            report = session.get("report")
            if not isinstance(report, str):
                return False
            path = (Path(directory) / report).resolve()
            if not path.is_relative_to(Path(directory).resolve()) or not path.is_file():
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("download", type=Path)
    parser.add_argument("--outcome", action="append", default=[])
    args = parser.parse_args()
    passed = check(args.run) and check(args.download, archive=True)
    passed = passed and all(outcome == "success" for outcome in args.outcome)
    logging.warning("Batch final result: %s", "passed" if passed else "failed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
