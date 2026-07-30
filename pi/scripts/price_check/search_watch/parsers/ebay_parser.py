"""Parse exact-match eBay search results from a rendered search page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit


ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?([0-9]{9,15})(?:[/?#]|$)")
GATED_CONTENT_TEXT = (
    "pardon our interruption",
    "checking your browser before you access ebay",
    "pagename:'challengeget'",
    'pagename:"challengeget"',
    "we've detected unusual activity",
    "we have detected unusual activity",
    "verify you are a human",
    "verify you're a human",
    "please verify yourself",
    "access to this page has been denied",
    "your request has been blocked",
)
TITLE_CLASSES = {"s-item__title", "s-card__title"}
PRICE_CLASSES = {"s-item__price", "s-card__price"}
SHIPPING_CLASSES = {"s-item__shipping", "s-card__shipping"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class EbayParseError(ValueError):
    pass


class EbayGateError(EbayParseError):
    pass


@dataclass(frozen=True)
class SearchResult:
    item_id: str
    title: str
    url: str
    price: str | None = None
    shipping: str | None = None
    image_url: str | None = None


def item_id_from_url(url: str) -> str | None:
    match = ITEM_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _clean(parts: list[str]) -> str | None:
    text = " ".join(" ".join(parts).split())
    return text or None


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self.result_ids: set[str] = set()
        self.current: dict | None = None
        self.container_depth = 0
        self.capture_field: str | None = None
        self.capture_depth = 0
        self.ignored_depth = 0
        self.stopped = False
        self.saw_search_markup = False
        self.saw_fewer_words = False
        self.visible_text_tail = ""

    @staticmethod
    def _attributes(attributes) -> dict[str, str]:
        return {
            str(name).lower(): value or ""
            for name, value in attributes
            if name
        }

    @staticmethod
    def _is_result_container(classes: set[str]) -> bool:
        return bool(
            {"s-item", "s-card"} & classes
            or any(
                token.endswith("__item-card") or token == "brwrvr__item-card"
                for token in classes
            )
        )

    def handle_starttag(self, tag: str, attributes) -> None:
        attrs = self._attributes(attributes)
        classes = set(attrs.get("class", "").split())
        if any(token.startswith(("srp-", "srp_")) for token in classes):
            self.saw_search_markup = True

        if tag in {"script", "style", "noscript"}:
            self.ignored_depth += 1

        if self.stopped:
            return

        if self.current is None and self._is_result_container(classes):
            self.current = {
                "item_id": None,
                "title": [],
                "link_title": None,
                "price": [],
                "shipping": [],
                "image_url": None,
            }
            self.container_depth = 1
            self.saw_search_markup = True
        elif self.current is not None and tag not in VOID_TAGS:
            self.container_depth += 1

        if self.current is None:
            return

        if tag == "a" and attrs.get("href"):
            item_id = item_id_from_url(attrs["href"])
            if item_id and self.current["item_id"] is None:
                self.current["item_id"] = item_id
                self.current["link_title"] = attrs.get("aria-label") or attrs.get(
                    "title"
                )
        elif tag == "img" and not self.current["image_url"]:
            self.current["image_url"] = (
                attrs.get("src") or attrs.get("data-src") or None
            )

        field = None
        if classes & TITLE_CLASSES:
            field = "title"
        elif classes & PRICE_CLASSES:
            field = "price"
        elif classes & SHIPPING_CLASSES:
            field = "shipping"
        if field:
            self.capture_field = field
            self.capture_depth = self.container_depth

    def handle_startendtag(self, tag: str, attributes) -> None:
        self.handle_starttag(tag, attributes)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1
        if self.current is None or self.stopped or tag in VOID_TAGS:
            return
        if (
            self.capture_field is not None
            and self.container_depth == self.capture_depth
        ):
            self.capture_field = None
            self.capture_depth = 0
        self.container_depth -= 1
        if self.container_depth <= 0:
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        combined = f"{self.visible_text_tail} {text}".strip()
        self.visible_text_tail = combined[-80:]
        if "results matching fewer words" in combined.casefold():
            self.saw_fewer_words = True
            self.stopped = True
            self.current = None
            self.capture_field = None
            return
        if self.current is not None and self.capture_field:
            self.current[self.capture_field].append(text)

    def _finish_current(self) -> None:
        current = self.current
        self.current = None
        self.container_depth = 0
        self.capture_field = None
        self.capture_depth = 0
        if not current or not current["item_id"]:
            return
        item_id = current["item_id"]
        if item_id in self.result_ids:
            return
        title = _clean(current["title"]) or current["link_title"]
        if not title:
            return
        title = re.sub(r"^(?:New Listing|Open box)\s+", "", title).strip()
        self.result_ids.add(item_id)
        self.results.append(
            SearchResult(
                item_id=item_id,
                title=title,
                url=f"https://www.ebay.com/itm/{item_id}",
                price=_clean(current["price"]),
                shipping=_clean(current["shipping"]),
                image_url=current["image_url"],
            )
        )


def parse(page: str) -> list[SearchResult]:
    lowered = page.casefold()
    if any(marker in lowered for marker in GATED_CONTENT_TEXT):
        raise EbayGateError(
            "eBay returned a browser verification, access-denied, or gated page"
        )
    parser = _SearchParser()
    parser.feed(page)
    parser.close()
    if parser.current is not None and not parser.stopped:
        parser._finish_current()
    if not parser.saw_search_markup:
        raise EbayParseError(
            "eBay search result markup was not found; the parser may need updating"
        )
    return parser.results
