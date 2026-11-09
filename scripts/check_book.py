#!/usr/bin/env python3
"""Check book navigation, local Markdown links, fragments, fences, and sample SDK commands.

Standard library only. Run from any directory: python3 scripts/check_book.py
This is a structural check, not a Markdown renderer or an external-link crawler.
"""
import collections
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOK = ROOT / "docs/book"
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+\"[^\"]*\")?\s*\)")
errors = []
links = 0


def prose(text, path):
    lines = []
    fence = None
    fence_size = 0
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence, fence_size = marker[0], len(marker)
            elif marker[0] == fence and len(marker) >= fence_size:
                fence = None
            lines.append("")
        else:
            lines.append("" if fence else line)
        if line.rstrip() != line:
            errors.append(f"{path.relative_to(ROOT)}:{number}: trailing whitespace")
    if fence:
        errors.append(f"{path.relative_to(ROOT)}: unclosed code fence")
    return "\n".join(lines)


def anchors(text):
    result = set()
    repeated = collections.Counter()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not match:
            continue
        # GitHub-style approximation; explicit anchors are collected separately.
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"<[^>]+>", "", title).lower()
        slug = re.sub(r"[^\w\-\s]", "", title, flags=re.UNICODE).replace(" ", "-")
        result.add(slug if repeated[slug] == 0 else f"{slug}-{repeated[slug]}")
        repeated[slug] += 1
    result.update(re.findall(r"<(?:a|h[1-6])\b[^>]*\b(?:id|name)=[\"']([^\"']+)", text))
    return result


pages = sorted(BOOK.rglob("*.md"))
if len(pages) != 27:
    errors.append(f"Expected 27 book pages (entry + 21 chapters + 5 appendices); found {len(pages)}")
files = pages + [ROOT / "SUMMARY.md", ROOT / "README.md", ROOT / "samples/IndustrialGateway/README.md"]
content = {}
for path in files:
    if not path.is_file():
        errors.append(f"Missing {path.relative_to(ROOT)}")
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        errors.append(f"Not UTF-8: {path.relative_to(ROOT)}")
        continue
    content[path] = text
    body = prose(text, path)
    if path in pages and not re.match(r"^#\s+\S", text):
        errors.append(f"{path.relative_to(ROOT)}: missing page title")
    if re.search(r"(?m)^\s*(?:TODO|TBD|\[待补充\]|\[To be written\])\s*$", body):
        errors.append(f"{path.relative_to(ROOT)}: unfinished placeholder")
    if re.search(r"(?m)^\s*dotnet run --project samples/", text):
        errors.append(f"{path.relative_to(ROOT)}: run from samples to honor SDK selection")
    for match in LINK.finditer(body):
        target = match.group(1) or match.group(2)
        parts = urllib.parse.urlsplit(target)
        if parts.scheme or target.startswith("//"):
            continue
        links += 1
        destination = (path.parent / urllib.parse.unquote(parts.path)).resolve() if parts.path else path
        if not destination.exists():
            errors.append(f"{path.relative_to(ROOT)}: broken local link {target}")
        elif parts.fragment and destination.suffix == ".md":
            fragment = urllib.parse.unquote(parts.fragment)
            if not re.fullmatch(r"L\d+(?:-L\d+)?", fragment):
                target_body = destination.read_text(encoding="utf-8-sig")
                if fragment not in anchors(target_body):
                    errors.append(f"{path.relative_to(ROOT)}: unknown heading fragment {target}")

summary = content.get(ROOT / "SUMMARY.md", "")
listed = []
for match in LINK.finditer(summary):
    target = match.group(1) or match.group(2)
    listed.append((ROOT / urllib.parse.unquote(target)).resolve())
for path in pages:
    if listed.count(path) != 1:
        errors.append(f"{path.relative_to(ROOT)}: expected once in SUMMARY, found {listed.count(path)}")
config = (ROOT / ".gitbook.yaml").read_text(encoding="utf-8")
for setting in ("root: ./", "readme: docs/book/README.md", "summary: SUMMARY.md"):
    if setting not in config:
        errors.append(f"Missing GitBook setting: {setting}")
if errors:
    print("FAIL\n" + "\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
characters = sum(len(re.findall(r"[\u4e00-\u9fff]", text)) for path, text in content.items() if path in pages)
print(f"PASS {len(pages)} book pages; {links} local links; navigation unique; fences and SDK commands checked; {characters} CJK characters.")
