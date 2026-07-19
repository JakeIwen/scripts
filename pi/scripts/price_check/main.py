#!/usr/bin/python3
"""Check configured product prices and send ntfy alerts below a threshold."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

from amazon_parser import AmazonParseError, Product, parse as parse_amazon


DEFAULT_CONFIG = Path("/home/pi/configs/price_checks.tsv")
DEFAULT_TITLE_CACHE = Path("/home/pi/.local/state/price_check/titles.json")
CURL = "/usr/bin/curl"
DEFAULT_NTFY_SENDER = "/home/pi/scripts/ntfy_send.sh"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "Chrome/126.0 Safari/537.36"
)


class PriceCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listing:
    parser: str
    threshold: Decimal
    url: str
    title: str | None = None


PARSERS = {"amazon": parse_amazon}


def read_config(path: Path, *, allow_empty: bool = False) -> list[Listing]:
    listings: list[Listing] = []
    with path.open(encoding="utf-8", newline="") as config_file:
        rows = csv.reader(config_file, delimiter="\t")
        for line_number, row in enumerate(rows, 1):
            if not row or row[0].lstrip().startswith("#"):
                continue
            if len(row) not in {3, 4}:
                raise PriceCheckError(
                    f"{path}:{line_number}: expected parser, threshold, URL, "
                    "and optional title"
                )
            parser, threshold_text, url = (field.strip() for field in row[:3])
            title = row[3].strip() if len(row) == 4 else ""
            if parser not in PARSERS:
                raise PriceCheckError(
                    f"{path}:{line_number}: unknown parser {parser!r}"
                )
            try:
                threshold = Decimal(threshold_text)
            except InvalidOperation as error:
                raise PriceCheckError(
                    f"{path}:{line_number}: invalid threshold {threshold_text!r}"
                ) from error
            if threshold <= 0:
                raise PriceCheckError(
                    f"{path}:{line_number}: threshold must be greater than zero"
                )
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise PriceCheckError(f"{path}:{line_number}: invalid URL {url!r}")
            listings.append(Listing(parser, threshold, url, title or None))
    if not listings and not allow_empty:
        raise PriceCheckError(f"{path}: no listings configured")
    return listings


def local_config_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.local{path.suffix}")


def disabled_config_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.disabled")


def normalized_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


def read_disabled_urls(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    except OSError as error:
        raise PriceCheckError(f"could not read disabled listings {path}: {error}") from error
    return {
        normalized_url(line)
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def read_configs(path: Path) -> list[Listing]:
    listings = read_config(path)
    local_path = local_config_path(path)
    if local_path.exists():
        listings.extend(read_config(local_path, allow_empty=True))
    seen_urls: set[str] = set()
    for listing in listings:
        if listing.url in seen_urls:
            raise PriceCheckError(f"duplicate URL across price configs: {listing.url}")
        seen_urls.add(listing.url)
    disabled_urls = read_disabled_urls(disabled_config_path(path))
    return [
        listing
        for listing in listings
        if normalized_url(listing.url) not in disabled_urls
    ]


def fetch(url: str) -> str:
    command = [
        CURL,
        "-LfsS",
        "--compressed",
        "--connect-timeout",
        "10",
        "--max-time",
        "30",
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "-A",
        USER_AGENT,
        "-H",
        "Accept-Language: en-US,en;q=0.9",
        url,
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise PriceCheckError(f"download failed: {detail.strip()}") from error
    return result.stdout


def load_title_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise PriceCheckError(f"could not read title cache {path}: {error}") from error
    if not isinstance(data, dict) or not all(
        isinstance(url, str) and isinstance(title, str)
        for url, title in data.items()
    ):
        raise PriceCheckError(f"title cache {path} is not a string-to-string object")
    return data


def save_title_cache(path: Path, titles: dict[str, str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as cache_file:
            json.dump(titles, cache_file, indent=2, sort_keys=True)
            cache_file.write("\n")
            cache_file.flush()
            os.fsync(cache_file.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def serialize_listings(listings: list[Listing]) -> str:
    output = io.StringIO()
    output.write("# Local additions; parser<TAB>threshold<TAB>URL<TAB>title (optional)\n")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    for listing in listings:
        row = [listing.parser, str(listing.threshold), listing.url]
        if listing.title:
            row.append(listing.title)
        writer.writerow(row)
    return output.getvalue()


def remove_listing(config_path: Path, title_cache: dict[str, str], match: str) -> str:
    base_listings = read_config(config_path)
    local_path = local_config_path(config_path)
    local_listings = (
        read_config(local_path, allow_empty=True) if local_path.exists() else []
    )
    disabled_path = disabled_config_path(config_path)
    disabled_urls = read_disabled_urls(disabled_path)
    active = [
        (source, listing)
        for source, listings in (("base", base_listings), ("local", local_listings))
        for listing in listings
        if normalized_url(listing.url) not in disabled_urls
    ]

    if match.startswith(("http://", "https://")):
        wanted_url = normalized_url(match)
        matches = [item for item in active if normalized_url(item[1].url) == wanted_url]
    else:
        wanted_title = match.strip().casefold()
        matches = [
            item
            for item in active
            if (item[1].title or title_cache.get(item[1].url, "")).strip().casefold()
            == wanted_title
        ]
    if not matches:
        raise PriceCheckError(f"no active price check matches {match!r}")
    if len(matches) > 1:
        urls = ", ".join(listing.url for _, listing in matches)
        raise PriceCheckError(f"multiple price checks match {match!r}: {urls}")

    source, listing = matches[0]
    if source == "local":
        remaining = [item for item in local_listings if item.url != listing.url]
        if remaining:
            write_text_atomic(local_path, serialize_listings(remaining))
        else:
            local_path.unlink()
    else:
        disabled_urls.add(normalized_url(listing.url))
        content = "# Base-config URLs disabled on this Pi\n" + "".join(
            f"{url}\n" for url in sorted(disabled_urls)
        )
        write_text_atomic(disabled_path, content)
    return listing.title or title_cache.get(listing.url) or listing.url


def send_ntfy(title: str, message: str, priority: str, tags: str) -> None:
    endpoint = os.environ.get("NTFY_PRICE_URL")
    if not endpoint:
        raise PriceCheckError("NTFY_PRICE_URL is not set")
    sender = os.environ.get("NTFY_SEND_BIN", DEFAULT_NTFY_SENDER)
    env = os.environ.copy()
    env["NTFY_URL"] = endpoint
    try:
        subprocess.run(
            [sender, title, message, priority, tags],
            check=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PriceCheckError(f"notification failed: {error}") from error


def send_alert(listing: Listing, product: Product, item_title: str) -> None:
    title = f"{item_title}: ${product.price:.2f} < ${listing.threshold:.2f}"
    message = listing.url
    send_ntfy(title, message, "high", "moneybag")


def send_parser_error(
    listing: Listing, error: AmazonParseError, item_title: str
) -> None:
    title = f"{item_title}: price parser needs update"
    message = f"Could not parse the product page HTML.\n{error}\n{listing.url}"
    send_ntfy(title, message, "high", "warning")


def check_listing(
    listing: Listing, title_cache: dict[str, str] | None = None, *, dry_run: bool = False
) -> None:
    if title_cache is None:
        title_cache = {}
    try:
        product = PARSERS[listing.parser](fetch(listing.url))
    except AmazonParseError as error:
        item_title = listing.title or title_cache.get(listing.url) or listing.url
        if not dry_run:
            try:
                send_parser_error(listing, error, item_title)
            except PriceCheckError as notify_error:
                raise PriceCheckError(
                    f"{error}; parser-error notification failed: {notify_error}"
                ) from notify_error
        raise PriceCheckError(str(error)) from error
    scraped_title = product.title if product.title != "Amazon product" else ""
    item_title = listing.title or scraped_title or title_cache.get(listing.url) or listing.url
    if not dry_run and not listing.title and scraped_title:
        title_cache[listing.url] = scraped_title
    relation = "below" if product.price < listing.threshold else "not below"
    print(
        f"{item_title}: ${product.price:.2f} is {relation} "
        f"${listing.threshold:.2f}"
    )
    if product.price < listing.threshold and not dry_run:
        send_alert(listing, product, item_title)


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="tab-separated listings"
    )
    argument_parser.add_argument(
        "--title-cache",
        type=Path,
        default=DEFAULT_TITLE_CACHE,
        help="runtime cache for scraped titles",
    )
    argument_parser.add_argument(
        "--dry-run", action="store_true", help="check prices without notifying"
    )
    argument_parser.add_argument(
        "--remove", metavar="TITLE_OR_URL", help="remove one configured listing"
    )
    args = argument_parser.parse_args()

    failures = 0
    if args.remove:
        try:
            title_cache = load_title_cache(args.title_cache)
        except PriceCheckError as error:
            print(f"price-check: warning: {error}", file=sys.stderr)
            title_cache = {}
        try:
            removed_title = remove_listing(args.config, title_cache, args.remove)
        except (OSError, PriceCheckError) as error:
            print(f"price-check: {error}", file=sys.stderr)
            return 1
        print(f"removed price check: {removed_title}")
        return 0
    try:
        listings = read_configs(args.config)
    except (OSError, PriceCheckError) as error:
        print(f"price-check: {error}", file=sys.stderr)
        return 1
    try:
        title_cache = load_title_cache(args.title_cache)
    except PriceCheckError as error:
        print(f"price-check: warning: {error}", file=sys.stderr)
        title_cache = {}
    original_title_cache = title_cache.copy()
    for listing in listings:
        try:
            check_listing(listing, title_cache, dry_run=args.dry_run)
        except PriceCheckError as error:
            failures += 1
            print(f"price-check: {listing.url}: {error}", file=sys.stderr)
    if not args.dry_run and title_cache != original_title_cache:
        try:
            save_title_cache(args.title_cache, title_cache)
        except OSError as error:
            failures += 1
            print(f"price-check: could not save title cache: {error}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
