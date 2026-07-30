"""Saved-search result parsers."""

from .ebay_parser import EbayGateError, EbayParseError, SearchResult, parse

__all__ = ["EbayGateError", "EbayParseError", "SearchResult", "parse"]
