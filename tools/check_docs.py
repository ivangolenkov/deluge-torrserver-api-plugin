"""Parse Markdown and validate local links (including images) without network."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    errors = []
    count = 0
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(
            part.startswith(".") or part in ("build", "dist", "node_modules")
            for part in relative.parts
        ):
            continue
        count += 1
        text = path.read_text(encoding="utf-8")
        tokens = MarkdownIt("commonmark").parse(text)
        if not any(t.type == "heading_open" and t.tag == "h1" for t in tokens):
            errors.append(f"{relative}: missing H1")
        for token in tokens:
            for child in token.children or []:
                target = child.attrGet("href") or child.attrGet("src")
                if not target:
                    continue
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                if not (path.parent / unquote(url.path)).exists():
                    errors.append(f"{relative}: missing link target {target}")
    return count, errors


if __name__ == "__main__":
    count, errors = check()
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Markdown parsed, local targets exist: {count} files")
