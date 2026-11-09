#!/usr/bin/env python3
"""Stage book Markdown and referenced sources for MkDocs.

This script only prepares content and relative links. MkDocs Material owns HTML,
layout, navigation, search, code highlighting, diagrams and browser behavior.
"""
import os
import re
import shutil
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".build" / "docs"
ENTRY = Path("docs/book/README.md")
REPO = "https://github.com/vccddd/kestrel-networking-handbook"
LINK = re.compile(r"(!?\[[^\]\n]*\]\()\s*(?:<([^>]+)>|([^\s)]+))(\s+\"[^\"]*\")?\s*\)")
PENDING = {p.relative_to(ROOT) for p in (ROOT / "docs/book").rglob("*.md")}
SOURCES, ASSETS = set(), set()


def text(path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unknown source encoding: {path}")


def location(path):
    if path == ENTRY:
        return Path("index.md")
    if path == Path("README.md"):
        return Path("about-repository.md")
    if path.suffix.lower() == ".md":
        return path
    # MkDocs excludes dot-files/directories; keep config sources visible under safe names.
    visible = Path(*(part.replace(".", "_", 1) if part.startswith(".") else part for part in path.parts))
    return Path("source") / Path(str(visible) + ".md")


def relative(current, target):
    return urllib.parse.quote(os.path.relpath(target, current.parent).replace(os.sep, "/"), safe="/._-~")


def rewrite(markdown, source):
    current = location(source)

    def replace(match):
        url = match.group(2) or match.group(3)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme or url.startswith("//") or not parts.path:
            return match.group(0)
        resolved = (ROOT / source.parent / urllib.parse.unquote(parts.path)).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.exists():
            raise ValueError(f"Missing/outside repository target: {source}: {url}")
        target = resolved.relative_to(ROOT)
        if resolved.is_dir():
            result = REPO + "/tree/main/" + urllib.parse.quote(target.as_posix())
        elif match.group(1).startswith("!") or target.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            ASSETS.add(target)
            result = relative(current, Path("files") / target)
        else:
            (PENDING if target.suffix.lower() == ".md" else SOURCES).add(target)
            result = relative(current, location(target))
        if parts.fragment:
            result += "#" + parts.fragment
        return match.group(1) + result + (match.group(4) or "") + ")"

    lines, fence = [], None
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            symbol = marker.group(1)[0]
            fence = None if fence == symbol else symbol if fence is None else fence
            lines.append(line)
        else:
            lines.append(line if fence else LINK.sub(replace, line))
    return "".join(lines)


def main():
    if DEST.exists():
        assert DEST.resolve() == ROOT.resolve() / ".build/docs" and not DEST.is_symlink()
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    rendered = set()
    while remaining := sorted(PENDING - rendered):
        for source in remaining:
            output = DEST / location(source)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rewrite(text(ROOT / source), source), encoding="utf-8")
            rendered.add(source)
    languages = {".cs": "csharp", ".csproj": "xml", ".props": "xml", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".py": "python", ".html": "html", ".sln": "text"}
    for source in sorted(SOURCES):
        output = DEST / location(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        language = languages.get(source.suffix, "text")
        url = REPO + "/blob/main/" + urllib.parse.quote(source.as_posix())
        content = f"# {source.name}\n\n`{source.as_posix()}` · [在 GitHub 查看]({url})\n\n````````{language}\n{text(ROOT / source)}\n````````\n"
        output.write_text(content, encoding="utf-8")
    for asset in ASSETS:
        output = DEST / "files" / asset
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / asset, output)
    print(f"Prepared {len(rendered)} Markdown pages and {len(SOURCES)} referenced source pages for MkDocs Material.")


if __name__ == "__main__":
    main()
