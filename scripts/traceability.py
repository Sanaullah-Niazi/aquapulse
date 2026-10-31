#!/usr/bin/env python3
"""Writes docs/requirements-traceability.md: every functional requirement of
the SRS (docs/SRS.md) and the automated tests and CI checks that prove it.

How it works: each test file names the requirements it checks (for example
"FR-24" in its docstring or test names). This script reads the SRS for the
list of requirements and searches the test folders for their IDs, so the
table is made from the code, not typed by hand.

    python3 scripts/traceability.py          # write the file (make rtm)
    python3 scripts/traceability.py --check  # fail if it is out of date (CI)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRS = ROOT / "docs" / "SRS.md"
OUT = ROOT / "docs" / "requirements-traceability.md"
# Where proof lives: tests of every part, plus the CI workflows (FR-74 is CI itself).
SEARCH = [
    "backend/tests",
    "simulator/tests",
    "web/e2e",
    "web/e2e-site",
    "web/src",
    "mobile/test",
    "firmware/test",
    ".github/workflows",
]
SUFFIXES = {".py", ".ts", ".tsx", ".dart", ".cpp", ".yml", ".sh"}
SKIP = {"node_modules", "__pycache__", ".pio", "build", ".dart_tool"}

HEADER = re.compile(r"^#### (FR-\d{2}) · (.+)$")
META = re.compile(r"\*\*Priority:\*\* (\w+).*\*\*Milestone:\*\* (.+)$")


def requirements() -> list[tuple[str, str, str, str]]:
    rows, current = [], None
    for line in SRS.read_text(encoding="utf-8").splitlines():
        if m := HEADER.match(line):
            current = [m.group(1), m.group(2).strip()]
        elif current and (m := META.search(line)):
            rows.append((current[0], current[1], m.group(1), m.group(2).strip()))
            current = None
    return rows


def is_test(path: Path) -> bool:
    text = str(path)
    return (
        "/tests/" in text
        or "/test/" in text
        or "/e2e/" in text
        or path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", "_test.dart"))
        or text.startswith(".github/")
    )


def evidence() -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for folder in SEARCH:
        for path in sorted((ROOT / folder).rglob("*")):
            if (
                path.suffix not in SUFFIXES
                or SKIP & set(path.parts)
                or not path.is_file()
            ):
                continue
            rel = path.relative_to(ROOT)
            if not is_test(rel):
                continue
            text = path.read_text(encoding="utf-8")
            ids = set(re.findall(r"\bFR-\d{2}\b", text))
            # "FR-42 to FR-47" names every requirement in between too.
            for a, b in re.findall(r"\bFR-(\d{2}) to FR-(\d{2})\b", text):
                ids |= {f"FR-{n:02d}" for n in range(int(a), int(b) + 1)}
            for req in ids:
                found.setdefault(req, set()).add(str(rel))
    return {k: sorted(v) for k, v in found.items()}


def render() -> str:
    reqs, proof = requirements(), evidence()
    covered = sum(1 for r in reqs if proof.get(r[0]))
    must = [r for r in reqs if r[2] == "Must"]
    must_covered = sum(1 for r in must if proof.get(r[0]))
    summary = (
        f"**{covered} of {len(reqs)}** functional requirements have automated tests or CI checks "
        f"(**{must_covered} of {len(must)}** Must requirements)."
    )
    lines = [
        "# Requirements traceability matrix (RTM)",
        "",
        "Made by `make rtm` (`scripts/traceability.py`) from the SRS and the test files;",
        "CI fails if this file is out of date. A file is listed when it names the",
        "requirement (for example `FR-24`, or a range like `FR-42 to FR-47`) in its",
        "docstring, test names or comments.",
        "",
        summary,
        "",
        "| ID | Requirement | Priority | Milestone | Proved by |",
        "|---|---|---|---|---|",
    ]
    for rid, title, priority, milestone in reqs:
        files = proof.get(rid, [])
        cell = (
            "<br>".join(f"[`{f}`]({'../' + f})" for f in files)
            if files
            else "**none yet**"
        )
        lines.append(f"| {rid} | {title} | {priority} | {milestone} | {cell} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit("docs/requirements-traceability.md is out of date: run `make rtm`")
        print("Traceability matrix is up to date.")
        return
    OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
