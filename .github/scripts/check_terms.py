"""Fail when the repository carries one of its maintainer's private terms.

The terms are regular expressions, one per line, case-insensitive, in sections: `[never]` (must not
appear in a commit message or in any file), `[tip]` (must not appear in any file of the checked-out
tree) and `[ignore]` (expressions to drop from the other two). They come from the `PRIVATE_TERMS`
secret, so they are never in the repository; without it (a pull request from a fork gets no secrets)
the check is skipped and says so.

Only where a term matched is printed, never the term itself: the logs of a public repository's
workflows are public too.

    python .github/scripts/check_terms.py [BASE]

checks every tracked file, and the messages of the commits after BASE (`$BASE` when not given; none
when neither is set). Exit status: 0 clean or skipped, 1 a term matched, 2 the terms cannot be read.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys


def parse(text: str) -> dict[str, list[re.Pattern]]:
    sections: dict[str, list[str]] = {"never": [], "tip": [], "ignore": []}
    current = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in ("[never]", "[tip]", "[ignore]"):
            current = line[1:-1]
            continue
        if current is None:
            raise ValueError(f"line {number}: a term outside a [never]/[tip]/[ignore] section")
        sections[current].append(line)
    drop = set(sections["ignore"])
    out = {}
    for name in ("never", "tip"):
        try:
            out[name] = [re.compile(term, re.IGNORECASE) for term in sections[name] if term not in drop]
        except re.error as err:
            raise ValueError(f"a {name} term is not a valid expression ({err})") from None
    return out


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def scan_text(where: str, text: str, patterns: list[re.Pattern], hits: list[str]) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in patterns):
            hits.append(f"{where}:{number}")


def scan(terms: dict[str, list[re.Pattern]], base: str | None) -> list[str]:
    hits: list[str] = []
    everything = terms["never"] + terms["tip"]
    for path in git("ls-files", "-z").split("\0"):
        if not path:
            continue
        scan_text(f"{path} (its path)", path, everything, hits)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue                       # binary
        scan_text(path, data.decode("utf-8", "replace"), everything, hits)
    if base and set(base) != {"0"}:        # a new branch's push has no "before"
        for sha in git("rev-list", f"{base}..HEAD").split():
            scan_text(f"commit {sha[:9]} message", git("log", "-1", "--format=%B", sha), terms["never"], hits)
    return hits


def main(argv: list[str]) -> int:
    text = os.environ.get("PRIVATE_TERMS", "")
    if not text.strip():
        print("private terms: no PRIVATE_TERMS secret here (a fork's pull request?) - check skipped")
        return 0
    try:
        terms = parse(text)
    except ValueError as err:
        print(f"private terms: the PRIVATE_TERMS secret cannot be read: {err}", file=sys.stderr)
        return 2
    base = argv[0] if argv else os.environ.get("BASE") or None
    hits = scan(terms, base)
    for hit in hits:
        print(f"private term at {hit}")
    print(f"private terms: {len(hits)} hit(s)" if hits else "private terms: clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
