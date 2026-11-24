"""Conservative, dependency-free readable text for the direct HTML fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit


_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_DISCARD = frozenset("head script style template noscript nav footer aside form button svg iframe".split())
_BLOCK = frozenset("address article blockquote div dl dt dd figure figcaption li main ol p pre section table tr ul".split())
_HTML = re.compile(r"^\s*(?:<!doctype\s+html\b|<(?:html|head|body|main|article|div|section|p|h[1-6])(?:\s|>))", re.I)


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _hidden(node):
    style = node.attrs.get("style") or ""
    return ("hidden" in node.attrs or (node.attrs.get("aria-hidden") or "").lower() == "true"
            or bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)", style, re.I)))


def _find(node, predicate):
    # Visibility belongs to the whole ancestor chain, before target selection.
    if node.tag in _DISCARD or _hidden(node):
        return
    if predicate(node):
        yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _find(child, predicate)


def _render(node, url, *, keep_header=True):
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    if node.tag in _DISCARD or _hidden(node) or (node.tag == "header" and not keep_header):
        return ""
    text = "".join(_render(child, url, keep_header=keep_header) for child in node.children)
    if re.fullmatch(r"h[1-6]", node.tag):
        return "\n\n" + "#" * int(node.tag[1]) + " " + text.strip() + "\n\n"
    if node.tag == "a" and text.strip():
        try:
            href = urljoin(url, node.attrs.get("href") or "")
            if urlsplit(href).scheme in ("http", "https"):
                return f"[{text.strip()}]({href})"
        except ValueError:
            pass  # A malformed link should not discard otherwise readable text.
    if node.tag in ("br", "hr"):
        return "\n"
    if node.tag == "li":
        return "\n- " + text.strip() + "\n"
    if node.tag in _BLOCK:
        return "\n\n" + text.strip() + "\n\n"
    return text


def readable_html_fallback(text: str, url: str) -> str:
    """Extract the main/article body before the caller applies its output budget.

    Plain text and Markdown are unchanged. Empty/JS-only HTML stays empty so the
    existing caller can report failure instead of returning metadata as evidence.
    This is static extraction; it does not execute scripts or follow links.
    """
    probe = re.sub(r"^\s*(?:<!--.*?-->\s*)*", "", text.lstrip("\ufeff"), count=1, flags=re.S)
    if not _HTML.search(probe):
        return text
    document = _Document()
    document.feed(text)
    document.close()
    root = document.root
    rendered = ""
    for predicate in (lambda n: n.tag == "main" or n.attrs.get("role") == "main",
                      lambda n: n.tag == "article", lambda n: n.tag == "body"):
        for selected in _find(root, predicate):
            rendered = _render(selected, url, keep_header=selected.tag != "body").strip()
            if rendered:
                break
        if rendered:
            break
    if not rendered:
        rendered = _render(root, url, keep_header=False)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", rendered).strip()
