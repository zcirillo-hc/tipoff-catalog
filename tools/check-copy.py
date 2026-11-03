#!/usr/bin/env python3
"""Source checks over user-facing copy and one navigation-bar defect (PLAN §20.2, §19.6.6, §23.1).

    python3 -I Tooling/check-copy.py              # check the tree; exit 1 on any finding
    python3 -I Tooling/check-copy.py --self-test  # prove both checks catch what they are for

Invoked by `Tooling/release-audit.sh`, which runs the self-test first and then the check, so a
check that has stopped working fails the audit rather than quietly passing it.

THREE CHECKS
------------
1. NO EM DASH IN USER-FACING COPY (§20.2). Every Swift string literal under `Sources/` and `App/`,
   every user-facing text field in the bundled catalog, `docs/PRIVACY.md` (which the Privacy
   screen renders verbatim), and the Info.plist strings in `App/project.yml` (the system
   permission dialogs). Em dash only: **en dashes are legal** and are what date ranges use
   ("Oct 23 – Oct 29", PLAN §21.2), so the check must never widen to them.

   It fails closed. A new file is checked because it is under `Sources/` or `App/`, not because
   somebody remembered to list it; everything skipped is skipped by a rule stated below with its
   reason.

2. `.toolbarBackground(.visible, …)` NEEDS AN EXPLICIT INLINE TITLE (§19.6.6). On iOS 26, forcing
   the bar background visible on a **large**-title screen makes the bar reserve the title's space
   and paint nothing, so the screen ships with no title at all. That defect was found live three
   times (§16.34 A.3, then both onboarding screens in §19.3) before anyone wrote this down. The
   rule a script can check: a `.toolbarBackground(.visible` must sit in a modifier chain that also
   pins `.navigationBarTitleDisplayMode(.inline)`, so the safety is a property of the file and not
   of whichever navigation stack happens to present it.

3. NO INTERNAL PROVENANCE IN A USER-FACING CATALOG NOTE (§23.1). Card Detail renders
   `cards[].notes` verbatim, and until this run twenty-five cards used it as a maintainer's log:
   the Amex Platinum told the reader "PLAN §17.7.5 (M12a benefits run): Saks is RETIRED (July
   2026)… Do not re-add one." The provenance is worth keeping, so it moved to
   `cards[].editorialNotes`, which no screen reads and this check deliberately ignores.

   Four markers, the ones §23.1 names: `PLAN §`, a bare section sign, a run name (`M12a`,
   `Run 20`) and the word "ruling". `CatalogValidator.internalProvenance` applies the same four to
   the shipped and to any remote catalog; this is the copy of the rule that runs before a build.
   It cannot catch a note that is merely written for a maintainer rather than a cardholder
   ("modelled at 1.5% flat") - that is an editor's judgement, and the markers are what a script
   can own.

WHY PYTHON AND NOT GREP
-----------------------
An em dash in a `//` comment is out of scope (§20.2) and an em dash in a string literal is not, and
telling those apart on a line that holds both needs the literals actually parsed. A grep would
either miss `let s = "a — b"  // fine` or flag every doc comment in the repo.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile

EM_DASH = "—"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# A Swift double-quoted literal on one line, escapes included. Multi-line (`"""`) literals are
# handled separately below.
LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"')

# ---------------------------------------------------------------------------------------------
# What check 1 does not look at, and why. Every entry is a claim that the strings in it never
# reach a user; if one of these files ever gains real copy, its entry has to go.
# ---------------------------------------------------------------------------------------------
SKIP_FILES = {
    # DEBUG-only screens and plumbing. All of it is inside `#if DEBUG` and `release-audit.sh`
    # check 1 proves the shipping binaries carry none of it (0 `tipoff.debug` literals).
    "App/Tipoff/Features/Debug/DebugView.swift",
    "App/Tipoff/Features/Debug/WidgetPreviewView.swift",
    "App/Tipoff/App/DebugLaunch.swift",
    "App/Tipoff/App/AccessibilityDump.swift",
    # Validator errors. They are thrown as `CatalogError.validationFailed` and surface in
    # `build.py --check` and the tests; no screen renders one.
    "Sources/TipoffCore/Catalog/CatalogValidator.swift",
    # PLAN §20.2 names this file's dashes as out of scope: the dash **set** and the store-number
    # regex match hyphens, en dashes and em dashes on purpose, because store signage uses all three.
    "Sources/TipoffCore/Classification/PlaceKey.swift",
}

# Literals handed to one of these on their own line, or within the two lines after the call opens,
# are diagnostics rather than copy: `DecodingWarnings` is counted in the report and never shown
# (PLAN §19.6.2), `note(` appends to a service's in-memory trace, and `TipoffLog` goes to the
# system log. None of them is a surface.
SINKS = re.compile(r"\bnote\(|\.add\(|TipoffLog\.|DecodingWarnings")
SINK_LOOKBACK = 2

# PLAN §26.1 — the catalog paths whose strings a reader can NEVER reach, each with its reason.
# Everything else under `cards`, `chains` and `programs` is checked, by **both** the em dash sweep and
# the provenance sweep, so a text field added next year is covered the day it appears and a new machine
# field fails once and earns a line here. Same fail-closed shape as PLIST_SKIP_KEYS below, and the same
# reason it exists: §23.1 listed the one field it was written for (`cards[].notes`), so `benefits[].note`
# shipped "Linked to the `resy` platform chain (§17.7.4)" to a Platinum holder through every check.
#
# List indices collapse to `[]`, so one entry covers every card, chain and row.
CATALOG_SKIP_PATHS = {
    # Identifiers and cross-references: the app looks the target up and prints its own words.
    "cards[].id", "cards[].programID", "cards[].convertedTo", "cards[].poolsWith[]",
    "cards[].earn[].capGroupID", "cards[].earn[].chainIDs[]", "cards[].earn[].excludedChainIDs[]",
    "cards[].benefits[].id", "cards[].benefits[].chainIDs[]",
    "cards[].rotating.program", "chains[].id", "chains[].fuelSiblingID", "programs[].id",
    # Enumerations: each parses into a Swift case whose own displayName is what a screen prints.
    "cards[].network", "cards[].status", "cards[].earn[].category", "cards[].earn[].channel",
    "cards[].earn[].capPeriod", "cards[].earn[].capUnit", "cards[].rotating.calendar[].categories[]",
    "chains[].category", "chains[].confidence", "chains[].acceptedNetworks[]", "programs[].kind",
    # Machine values: dates, hex colours, a URL, an MCC, a compiled regex (§16.8.1).
    "cards[].verifiedAt", "cards[].sourceURL", "cards[].benefits[].verifiedAt",
    "cards[].style.accent", "cards[].style.base", "cards[].style.finish", "cards[].style.textOnCard",
    "chains[].mcc", "chains[].nameRegex",
    # Editorial by definition. These exist **so that** provenance has somewhere to live (§23.1, §26.1);
    # checking them would defeat the split.
    "cards[].editorialNotes", "cards[].benefits[].editorialNote",
    # `chains[].notes` is the chain table's own maintainer column and no screen reads it. If that ever
    # changes, delete this line rather than widening the skip list.
    "chains[].notes",
}

# §23.1, mirrored from `CatalogValidator.internalProvenance`. Order matters only for the message.
PROVENANCE = (
    ("PLAN §", re.compile(r"PLAN §")),
    ("a section sign", re.compile(r"§")),
    # PLAN §26.1 (coder addition, flagged in the Run 29 handoff): a backtick is code formatting. All nine
    # of the notes §26.1 moved used one to quote a chain id or a schema value at the reader, and three
    # further notes used one while tripping none of the other markers.
    ("a backtick", re.compile(r"`")),
    ('the word "ruling"', re.compile(r"ruling", re.IGNORECASE)),
    ("a run name", re.compile(r"\bM[0-9]+(?:\.[0-9]+)?[a-z]?\b")),
    ("a run name", re.compile(r"\bRun [0-9]+\b")),
)


def provenance_marker(text: str) -> str | None:
    """The first §23.1 internal-provenance marker in `text`, or None."""
    for label, pattern in PROVENANCE:
        m = pattern.search(text)
        if m:
            return f"{label} ({m.group(0)!r})"
    return None


def swift_findings() -> list[str]:
    out: list[str] = []
    for base in ("Sources", "App"):
        for path in sorted((ROOT / base).rglob("*.swift")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in SKIP_FILES:
                continue
            lines = path.read_text().splitlines()
            in_multiline = False
            for i, line in enumerate(lines, 1):
                # A `"""` delimiter toggles a multi-line literal. Its contents are copy (the
                # Privacy screen's fallback text and the mail body both live in one).
                if line.count('"""') % 2 == 1:
                    in_multiline = not in_multiline
                    continue
                if in_multiline:
                    if EM_DASH in line:
                        out.append(f"{rel}:{i}: {line.strip()}")
                    continue

                spans = [(m.start(), m.end(), m.group(0)) for m in LITERAL.finditer(line)]
                for start, _end, text in spans:
                    if EM_DASH not in text:
                        continue
                    # A literal that begins after a `//` which is itself outside every literal on
                    # the line is inside a comment.
                    before = line[:start]
                    if "//" in before:
                        at = before.index("//")
                        if not any(a <= at < b for a, b, _ in spans):
                            continue
                    context = "\n".join(lines[max(0, i - 1 - SINK_LOOKBACK):i])
                    if SINKS.search(context):
                        continue
                    out.append(f"{rel}:{i}: {text}")
    return out


def catalog_user_facing(catalog: dict) -> list[tuple[str, str, str]]:
    """Every (collapsed path, readable path, string) in the catalog a reader can reach (PLAN §26.1)."""
    out: list[tuple[str, str, str]] = []

    def walk(node: object, collapsed: str, readable: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{collapsed}.{key}" if collapsed else key, f"{readable}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{collapsed}[]", f"{readable}[{index}]")
        elif isinstance(node, str) and collapsed not in CATALOG_SKIP_PATHS:
            out.append((collapsed, readable, node))

    for key in ("cards", "chains", "programs"):
        walk(catalog.get(key, []), key, key)
    return out


def load_catalog() -> dict | None:
    path = ROOT / "Sources/TipoffCore/Resources/catalog.json"
    return json.loads(path.read_text()) if path.exists() else None


def catalog_findings(catalog: dict | None = None) -> list[str]:
    if catalog is None:
        catalog = load_catalog()
    if catalog is None:
        return ["Sources/TipoffCore/Resources/catalog.json: missing"]
    return [
        f"catalog.json {readable}: {text[:120]}"
        for _, readable, text in catalog_user_facing(catalog)
        if EM_DASH in text
    ]


def provenance_findings(catalog: dict | None = None) -> list[str]:
    """§23.1 / §26.1 check: a string a reader can see that was written for a maintainer.

    §23.1 checked `cards[].notes` alone. §26.1 checks every user-facing path, enumerated by exemption
    in CATALOG_SKIP_PATHS, which is what catches `benefits[].note`.
    """
    if catalog is None:
        catalog = load_catalog()
    if catalog is None:
        return ["Sources/TipoffCore/Resources/catalog.json: missing"]
    out: list[str] = []
    for _, readable, text in catalog_user_facing(catalog):
        marker = provenance_marker(text)
        if marker:
            out.append(f"catalog.json {readable}: {marker}: {text[:100]}")
    return out


def doc_findings() -> list[str]:
    # `docs/PRIVACY.md` is bundled as a resource (App/project.yml) and the Privacy screen renders
    # it verbatim, so it is a screen's copy that happens to live in a Markdown file.
    path = ROOT / "docs/PRIVACY.md"
    return [
        f"docs/PRIVACY.md:{i}: {line.strip()[:120]}"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if EM_DASH in line
    ]


# Info.plist keys whose string values iOS never shows a user, so an em dash in one is out of
# scope. Everything NOT listed here is checked, the same way the Swift sweep checks a new file
# because of where it lives: a new user-facing key is covered the day it is added, and a new
# plumbing key fails the check once and gets a line here with its reason.
PLIST_SKIP_KEYS = {
    "CFBundleShortVersionString", "CFBundleVersion", "CFBundleIdentifier",   # version/identity
    "CFBundleExecutable", "CFBundlePackageType", "CFBundleName",             # build plumbing
    "CatalogURL",                                                            # PLAN §10 remote URL
    "NSExtensionPointIdentifier", "NSExtensionPrincipalClass",               # widget wiring
    "CFBundleURLSchemes", "CFBundleURLName",                                 # tipoff:// deep links
    "MinimumOSVersion", "UIApplicationSceneManifest",
}

# A quoted scalar on its own `Key: "value"` line in project.yml. The key must open the line (after
# indentation), so a `#` comment can never match: that is the whole reason this is a regex over the
# YAML and not a parse. The repo has no PyYAML and this check must run on a bare toolchain.
PLIST_LINE = re.compile(r'^\s*([A-Za-z][A-Za-z0-9_]*):\s*"(.*)"\s*$')


def plist_findings(path: pathlib.Path | None = None) -> list[str]:
    """Em dashes in `App/project.yml`'s Info.plist strings (PLAN §20.2).

    The two location permission strings are app copy that lives in YAML, not Swift: iOS renders
    them in the system permission dialog, which is the first Tipoff text a new tester ever reads.
    The Swift sweep cannot see them, and until this check existed one of them shipped an em dash
    through every release audit (found in the §19 archive audit, PLAN §25.1).
    """
    label = "App/project.yml" if path is None else path.name
    path = (ROOT / "App/project.yml") if path is None else path
    out: list[str] = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        match = PLIST_LINE.match(line)
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        if key in PLIST_SKIP_KEYS or EM_DASH not in value:
            continue
        out.append(f"{label}:{i}: {key}: {value[:100]}")
    return out


TOOLBAR = ".toolbarBackground(.visible"
INLINE = ".navigationBarTitleDisplayMode(.inline)"
# How far above the modifier the display mode may sit, counted in **code** lines. Every correct
# site has it two modifiers up (`.navigationTitle`, then the mode, then the two backgrounds); three
# leaves room for one more modifier without letting an unrelated chain vouch for this one.
#
# Counted in code lines because the first version of this lint counted raw lines and the five-line
# §19.6.6 comment it had just asked for pushed the mode out of its own window: three files that had
# been fixed in this very run reported as unfixed. It also matched the modifier's name inside the
# §16.34 A.3 comments that warn against using it, so two files that correctly do NOT use it were
# reported as using it. Both are controls in the self-test now.
INLINE_LOOKBACK = 3


def strip_comment(line: str) -> str:
    """`line` with any `//` comment removed, unless the `//` is inside a string literal."""
    spans = [(m.start(), m.end()) for m in LITERAL.finditer(line)]
    at = -1
    search = 0
    while True:
        at = line.find("//", search)
        if at == -1:
            return line
        if not any(a <= at < b for a, b in spans):
            return line[:at]
        search = at + 2


def toolbar_findings(paths: list[pathlib.Path] | None = None) -> list[str]:
    out: list[str] = []
    for path in paths if paths is not None else sorted((ROOT / "App").rglob("*.swift")):
        raw = path.read_text().splitlines()
        code = [strip_comment(line) for line in raw]
        rel = path.relative_to(ROOT).as_posix() if paths is None else path.name
        for i, line in enumerate(code, 1):
            if TOOLBAR not in line:
                continue
            # Walk up through the preceding code lines, skipping blanks and comment-only lines,
            # and take the last INLINE_LOOKBACK of them.
            window: list[str] = []
            j = i - 2
            while j >= 0 and len(window) < INLINE_LOOKBACK:
                if code[j].strip():
                    window.append(code[j])
                j -= 1
            if not any(INLINE in w for w in window):
                out.append(
                    f"{rel}:{i}: .toolbarBackground(.visible, …) with no "
                    f"{INLINE} in the {INLINE_LOOKBACK} code lines above it"
                )
    return out


def self_test() -> int:
    """Positive controls. Each check is shown failing on a planted defect and passing without it.

    `release-audit.sh` shipped with a `grep -q` bug that made a category which WAS used read as
    unused, and it was found by deliberately breaking the thing the check watches. Same method
    here: a check nobody has seen fail is not yet a check.
    """
    failures: list[str] = []

    def expect(label: str, condition: bool) -> None:
        print(f"  {'ok:  ' if condition else 'FAIL:'} {label}")
        if not condition:
            failures.append(label)

    # 1. The em dash check, against a literal, a comment, an en dash and a diagnostic sink.
    with tempfile.TemporaryDirectory() as tmp:
        planted = pathlib.Path(tmp) / "Planted.swift"
        planted.write_text(
            'let bad = "You’re at Kroger — Use Amex Gold"\n'
            '// A comment — which §20.2 leaves alone\n'
            'let range = "Oct 23 – Oct 29"\n'
            'note("trace — not a surface")\n'
        )
        lines = planted.read_text().splitlines()
        spans_hits = []
        for i, line in enumerate(lines, 1):
            for m in LITERAL.finditer(line):
                if EM_DASH not in m.group(0):
                    continue
                before = line[:m.start()]
                if "//" in before:
                    continue
                context = "\n".join(lines[max(0, i - 1 - SINK_LOOKBACK):i])
                if SINKS.search(context):
                    continue
                spans_hits.append(i)
        expect("em dash check catches an em dash in a string literal", 1 in spans_hits)
        expect("em dash check ignores an em dash in a comment", 2 not in spans_hits)
        expect("em dash check ignores an en dash in a date range (PLAN §21.2)", 3 not in spans_hits)
        expect("em dash check ignores a diagnostic sink", 4 not in spans_hits)

    # 2. The toolbar lint, against a large-title screen and an inline one.
    with tempfile.TemporaryDirectory() as tmp:
        bad = pathlib.Path(tmp) / "BadScreen.swift"
        bad.write_text(
            '        .navigationTitle("Pick your cards")\n'
            '        .navigationBarTitleDisplayMode(.large)\n'
            '        .toolbarBackground(.visible, for: .navigationBar)\n'
        )
        good = pathlib.Path(tmp) / "GoodScreen.swift"
        good.write_text(
            '        .navigationTitle("Privacy")\n'
            '        .navigationBarTitleDisplayMode(.inline)\n'
            '        .toolbarBackground(Tokens.Colors.background, for: .navigationBar)\n'
            '        .toolbarBackground(.visible, for: .navigationBar)\n'
        )
        # The modifier's own name inside a comment that warns against using it. Two real files
        # (CardsView, CardPickerView) carry exactly this and were false-positived by the first
        # version of the lint.
        commented = pathlib.Path(tmp) / "CommentedScreen.swift"
        commented.write_text(
            '        // PLAN §16.34 A.3: no `.toolbarBackground(.visible, …)` on a large title.\n'
            '        .navigationTitle("Pick your cards")\n'
            '        .toolbarBackground(Tokens.Colors.background, for: .navigationBar)\n'
        )
        # The display mode pinned above a multi-line comment. Three real files look like this after
        # the §19.6.6 fix, and a raw-line window counted the comment and reported them unfixed.
        documented = pathlib.Path(tmp) / "DocumentedScreen.swift"
        documented.write_text(
            '        .navigationTitle("Point valuations")\n'
            '        // PLAN §19.6.6: the display mode is pinned rather than inherited, because\n'
            '        // `.toolbarBackground(.visible, …)` below is only safe on an inline title\n'
            '        // and these screens are pushed.\n'
            '        .navigationBarTitleDisplayMode(.inline)\n'
            '        .toolbarBackground(Tokens.Colors.background, for: .navigationBar)\n'
            '        .toolbarBackground(.visible, for: .navigationBar)\n'
        )
        expect("toolbar lint catches a large-title screen (the §16.34 A.3 defect)",
               len(toolbar_findings([bad])) == 1)
        expect("toolbar lint passes an inline-title screen",
               toolbar_findings([good]) == [])
        expect("toolbar lint does not fire on the modifier named inside a comment",
               toolbar_findings([commented]) == [])
        expect("toolbar lint sees a display mode above a multi-line comment",
               toolbar_findings([documented]) == [])

    # 3. The §23.1 provenance check, against the note that prompted the rule, the note it became,
    #    and two strings that must NOT fire: a cardholder note with a dollar figure in it, and a
    #    card name that happens to carry a capital M and a digit.
    planted_note = ("Annual fee raised to $895 in 2025. PLAN §17.7.5 (M12a benefits run): Saks is "
                    "RETIRED (July 2026) and carries no credit. Do not re-add one.")
    clean_note = ("The 5x applies to flights booked direct with the airline or through Amex Travel. "
                  "Everything else earns 1x, including hotels you book with the hotel.")
    expect("provenance check catches the Amex Platinum note that prompted §23.1",
           provenance_marker(planted_note) is not None)
    expect("provenance check catches a bare run name with no section sign",
           provenance_marker("Applied in the M12b data run.") is not None)
    expect('provenance check catches "Run 20"',
           provenance_marker("Added in Run 20 and left alone since.") is not None)
    expect("provenance check passes the rewritten user-facing note",
           provenance_marker(clean_note) is None)
    expect("provenance check does not fire on an ordinary cardholder note",
           provenance_marker("$0 intro annual fee first year, then $150. The 2x covers Delta tickets.") is None)
    expect("provenance check does not fire on 3M or T-Mobile",
           provenance_marker("Earns 3% at 3M, T-Mobile and Exxon.") is None)
    # §26.1: the note that shipped to a Platinum holder for two runs, and the backtick marker that is the
    # only one of the five it would have tripped once the section signs were gone.
    resy_note = ("$400/yr. Linked to the `resy` platform chain (§17.7.4) and never geofenced "
                 "(§17.7.3): the credit applies at qualifying U.S. Resy restaurants.")
    expect("provenance check catches the Resy benefit note that prompted §26.1",
           provenance_marker(resy_note) is not None)
    expect("provenance check catches a backtick on its own",
           (provenance_marker("Recorded as `once` because no cadence value expresses four years.") or "").startswith("a backtick"))
    expect("provenance check passes the rewritten Resy note",
           provenance_marker("$100 a quarter, so $400 over a year. Applies at qualifying U.S. Resy restaurants.") is None)

    # §26.1: the enumeration is what makes the above reach `benefits[].note` at all. A planted marker in a
    # field §23.1 never looked at must be found, and an identical one in an editorial field must not be.
    planted_catalog = {
        "cards": [{
            "id": "test_card", "notes": "A clean cardholder note.",
            "editorialNotes": "PLAN §26.1 (M22): moved here on purpose.",
            "benefits": [{"id": "test_credit", "name": "Test Credit",
                          "note": "Linked to the `resy` platform chain (§17.7.4).",
                          "editorialNote": "PLAN §17.7.4: also moved here on purpose."}],
            "earn": [{"category": "dining", "note": "Applied in the M12b data run."}],
        }],
        "chains": [{"id": "test_chain", "brandNames": ["Clean Brand"],
                    "notes": "Codes 5411 — see PLAN §16.8.7."}],
        "programs": [{"id": "test_program", "name": "Clean Rewards"}],
    }
    found = provenance_findings(planted_catalog)
    expect("§26.1 enumeration catches a planted benefits[].note",
           any("benefits[0].note" in f for f in found))
    expect("§26.1 enumeration catches a planted earn[].note",
           any("earn[0].note" in f for f in found))
    expect("§26.1 enumeration spares both editorial fields and chains[].notes",
           not any("editorialNote" in f or "chains[0].notes" in f for f in found))
    expect("§26.1 enumeration spares ids, enums and a clean brand name",
           not any(f.endswith(("].id", "].category", "Clean Brand")) for f in found))
    expect("§26.1 em dash sweep spares chains[].notes and finds nothing clean",
           catalog_findings(planted_catalog) == [])

    # 4. The Info.plist copy check, against a user-facing key, a YAML comment and a skipped key.
    with tempfile.TemporaryDirectory() as tmp:
        spec = pathlib.Path(tmp) / "project.yml"
        spec.write_text(
            '        NSLocationWhenInUseUsageDescription: "Spot stores near you \u2014 before you pay."\n'
            '        # XcodeGen spec \u2014 source of truth for the generated project.\n'
            '        CatalogURL: "https://example.invalid/v1/catalog.json \u2014 not copy"\n'
            '        NSLocationAlwaysAndWhenInUseUsageDescription: "Allow Always. No server."\n'
        )
        at = [int(f.split(":")[1]) for f in plist_findings(spec)]
        expect("plist check catches an em dash in a permission string", at == [1])
        expect("plist check ignores an em dash in a YAML comment", 2 not in at)
        expect("plist check ignores a non-user-facing key (CatalogURL)", 3 not in at)
        expect("plist check passes a clean permission string", 4 not in at)
    print()
    if failures:
        print(f"Self-test FAILED: {len(failures)} control(s) did not behave.")
        return 1
    print("Self-test passed: all four checks catch what they are for.")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        print("== check-copy.py self-test")
        return self_test()

    em = swift_findings() + catalog_findings() + doc_findings() + plist_findings()
    toolbar = toolbar_findings()
    provenance = provenance_findings()

    if em:
        print(f"  FAIL: {len(em)} em dash(es) in user-facing copy (PLAN §20.2). Rewrite the")
        print("        sentence; do not substitute a comma. An em dash usually marks a sentence")
        print("        that wants splitting.")
        for finding in em:
            print(f"        {finding}")
    else:
        print("  ok:   no em dash in user-facing copy")

    if toolbar:
        print(f"  FAIL: {len(toolbar)} .toolbarBackground(.visible, …) site(s) without an explicit")
        print("        inline title (PLAN §19.6.6). On iOS 26 a large title then does not paint.")
        for finding in toolbar:
            print(f"        {finding}")
    else:
        print("  ok:   every .toolbarBackground(.visible, …) pins an inline title")

    if provenance:
        print(f"  FAIL: {len(provenance)} user-facing catalog note(s) carrying internal provenance")
        print("        (PLAN §23.1). Card Detail renders `notes` verbatim. Move the sentence to")
        print("        `editorialNotes` (the `editorial=` argument in the generators); never delete it.")
        for finding in provenance:
            print(f"        {finding}")
    else:
        print("  ok:   no user-facing catalog note reads like a changelog")

    return 1 if (em or toolbar or provenance) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
