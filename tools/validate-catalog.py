#!/usr/bin/env python3
"""validate-catalog.py — the gate on data that reaches phones without an app update (PLAN §30.5).

    Tooling/validate-catalog.py v1/catalog.json [--previous old.json] [--schema v1/catalog.schema.json]

A published catalog is live on every installed device within six hours and no App Store review stands
between it and a user, so this is the only review it gets. `Tooling/publish-catalog.sh` runs it before
pushing, and the catalog repo runs the same file in CI on every push, which is the case that matters:
a file edited on github.com, or pushed from another machine, never touches publish-catalog.sh.

It is deliberately the *same file* in both places. `publish-catalog.sh` copies this script and
`check-copy.py` into the catalog repo's `tools/` beside the data, so the rules cannot drift between
the machine that publishes and the CI that checks. `check-copy.py` is imported rather than restated,
for the same reason: §26.1's skip-path table has one copy.

Checks, each one a mistake that has either happened or would be invisible until a tester saw it:

  1. The file parses, and carries the keys the app requires.
  2. It validates against the schema published beside it, when `jsonschema` is installed. CI installs
     it; a local run without it says so rather than passing quietly.
  3. No em dash and no internal provenance in any string a reader can reach (PLAN §20.2, §23.1, §26.1).
  4. `catalogVersion` changed if the data did. Phones accept a catalog only when its version is
     strictly newer (§16.22.2), so forgetting the bump means every device silently ignores the upload.
  5. The catalog did not collapse: cards, chains and programs are non-empty, and none of the three
     lost more than a fifth of its entries against the previous copy. A truncated build is the one
     failure that looks like a successful publish.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys


def _load_check_copy():
    """`check-copy.py` is not an importable module name, and it sits beside this file in both repos."""
    path = pathlib.Path(__file__).resolve().parent / "check-copy.py"
    if not path.is_file():
        sys.exit(f"check-copy.py is not beside {__file__}; publish-catalog.sh copies both together")
    spec = importlib.util.spec_from_file_location("check_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_copy = _load_check_copy()

REQUIRED_KEYS = ("schemaVersion", "catalogVersion", "updatedAt", "programs", "cards", "chains")
COLLECTIONS = ("cards", "chains", "programs")
# A fifth is wide enough that a deliberate cull passes and narrow enough that a truncated build fails.
MAX_SHRINK = 0.20


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMA_LABEL = "schema"


def schema_findings(catalog: dict, schema_path: pathlib.Path | None) -> list[str]:
    global SCHEMA_LABEL
    if schema_path is None or not schema_path.is_file():
        return [f"no schema beside the catalog (looked for {schema_path})"] if schema_path else []
    try:
        import jsonschema
    except ImportError:
        # Said out loud rather than passed quietly: a skipped check that prints "ok" is worse than none.
        SCHEMA_LABEL = "schema SKIPPED (jsonschema not installed; CI installs it)"
        return []
    validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    return [
        f"schema: {'/'.join(str(p) for p in e.path) or '(root)'}: {e.message[:160]}"
        for e in sorted(validator.iter_errors(catalog), key=lambda e: list(e.path))[:20]
    ]


def version_findings(catalog: dict, previous: dict | None) -> list[str]:
    if previous is None:
        return []
    if previous == catalog:
        return []
    if previous.get("catalogVersion") == catalog.get("catalogVersion"):
        return [
            f"the data changed but catalogVersion is still {catalog.get('catalogVersion')!r} — "
            "every phone will ignore this upload (PLAN §16.22.2)"
        ]
    return []


def size_findings(catalog: dict, previous: dict | None) -> list[str]:
    out: list[str] = []
    for key in COLLECTIONS:
        now = len(catalog.get(key) or [])
        if now == 0:
            out.append(f"{key} is empty")
            continue
        if previous is None:
            continue
        before = len(previous.get(key) or [])
        if before and now < before * (1 - MAX_SHRINK):
            out.append(f"{key} fell from {before} to {now}, more than {int(MAX_SHRINK * 100)}% — truncated build?")
    return out


def validate(catalog_path: pathlib.Path, previous_path: pathlib.Path | None, schema_path: pathlib.Path | None) -> int:
    print(f"Validating {catalog_path}")
    try:
        catalog = load(catalog_path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"  FAIL: does not parse: {error}")
        return 1
    previous = None
    if previous_path is not None and previous_path.is_file():
        try:
            previous = load(previous_path)
        except (OSError, json.JSONDecodeError):
            print(f"  note: previous copy at {previous_path} does not parse, comparisons skipped")

    # Run first: it may rename its own label to say the check was skipped, and a tuple literal would
    # have read the old label before the call.
    schema = schema_findings(catalog, schema_path)
    groups: list[tuple[str, list[str]]] = [
        ("required keys", [f"missing {k!r}" for k in REQUIRED_KEYS if k not in catalog]),
        (SCHEMA_LABEL, schema),
        ("em dash in user-facing copy", check_copy.catalog_findings(catalog)),
        ("internal provenance in user-facing copy", check_copy.provenance_findings(catalog)),
        ("catalogVersion", version_findings(catalog, previous)),
        ("size", size_findings(catalog, previous)),
    ]

    failed = False
    for label, findings in groups:
        if findings:
            failed = True
            print(f"  FAIL: {label}")
            for line in findings[:20]:
                print(f"        {line}")
            if len(findings) > 20:
                print(f"        … and {len(findings) - 20} more")
        else:
            print(f"  ok:   {label}")

    version = catalog.get("catalogVersion")
    counts = ", ".join(f"{len(catalog.get(k) or [])} {k}" for k in COLLECTIONS)
    print(f"  {version} ({counts})")
    print("Catalog validation FAILED." if failed else "Catalog validation passed.")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("catalog", type=pathlib.Path)
    parser.add_argument("--previous", type=pathlib.Path, default=None,
                        help="the copy this one replaces, for the version and size comparisons")
    parser.add_argument("--schema", type=pathlib.Path, default=None,
                        help="defaults to catalog.schema.json beside the catalog")
    args = parser.parse_args(argv)
    schema = args.schema or args.catalog.with_name("catalog.schema.json")
    return validate(args.catalog, args.previous, schema)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
