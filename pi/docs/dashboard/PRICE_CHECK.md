# Deal Watch

Deal Watch presents individual products as **Listing Watch** and saved searches
as **Query Watch**. Both dashboard panels and `price_check/main.py` share one
private SQLite database:

```text
/home/pi/.local/share/price_check/price_check.sqlite3
```

It stores Amazon price watches, eBay saved searches, current and previously
seen eBay item IDs, permanent result dismissals, and recent check status. The
database directory is mode `700` and the database is mode `600`.

The existing price-check cron entry runs both product and saved-search checks.
The dashboard stores the last successfully resolved human-readable description
beside the price-check database, keyed by the exact five-field expression.
Routine tile and sheet refreshes therefore read the live crontab but do not
repeatedly call the external cron-description service. Editing a schedule still
requests a fresh preview, and a changed crontab expression invalidates the
cached description automatically.
The dashboard's **Check all now** button does the same.

## Haveno XMR offers

Haveno watches read the public RetoSwap `XMR_USD` ask book (USD only) from
`haveno.markets` and compare each offer with the fresh XMR reference price from
Haveno's price node. A stale or unavailable price falls back to the midpoint
of Kraken's XMR/USD bid and ask. Only asks with the configured payment method and XMR size range
are considered. The listed percentage is
`100 × (offer price / reference price − 1)`, where positive means above market.
The configured fee percentage is added to that figure, and a watch matches when
the effective percentage is less than or equal to its signed threshold.

Each matching offer ID is notified at most once for a watch, even if it disappears
and later returns. Muted and `--no-notify` checks still record current matches
without consuming that notification identity. A separate ten-minute cron entry
uses `price_check.sqlite3.haveno.lock`, so Haveno polling does not wait for an
eBay or product check; the combined default run skips Haveno without failing when
that dedicated lock is already held.

### Zelle offer activity

Every recorded all-watch check also snapshots every RetoSwap USD ask and the
public completed-trades feed, even when no Haveno watch is configured. When an
offer vanishes, Deal Watch looks for one unclaimed trade with the same payment
method, a compatible XMR amount, a price within three percent, and a timestamp
near the disappearance. A unique fixed-size match is marked high confidence;
other matches remain low confidence, and an unmatched offer becomes withdrawn
after two hours. Returned offers are tracked again. The dashboard summarizes
Zelle-only 7-day and 30-day activity while retaining all payment methods for
future use; resolved offers and unlinked trades are pruned after 120 days.

This is inference, not an exchange-provided relationship: public trades have no
offer IDs or direction, and an offer that appears and disappears between the
roughly ten-minute snapshots is not observed. Trade-feed failures preserve the
last offer snapshot and appear as the activity error without failing watch checks.

```bash
s price_check haveno-add 1 --fee-pct 0.5 --payment-method ZELLE --max-xmr 10
s price_check haveno-add -2.5
s price_check haveno-check all --no-notify
s price_check haveno-check 1
s price_check haveno-mute 1 7
s price_check haveno-edit 1 -1 --fee-pct 0.5 --payment-method ZELLE
s price_check haveno-remove 1
```

## eBay access and browser recovery

eBay search downloads use anonymous browser headers, not an eBay API token.
Do not use a logged-in eBay session.

### Automatic recovery

A failed eBay check queues a durable request in `search_browser_refresh` in
the existing private database. The Mac's `com.jacobr.deal-watch-refresh`
LaunchAgent polls over SSH to `vanpi.lan` every five minutes. It uses the existing
clean headless browser MCP service at `http://localhost:8931/mcp`, with an
isolated signed-out context and its own private anonymous browser state. It never
launches a browser or accesses daily Chrome.
The Mac must be awake and logged in, with the managed clean service available.
The five-minute LaunchAgent discovers new work; once recovery starts, the worker
stays running and follows the Pi's six-minute retry schedule.

The browser opens the saved search and allows any automatic verification to
finish. A bare 403 response starts eBay's anonymous verification flow explicitly.
It waits for the document to be parsed and real listings to appear, then confirms
the saved-search parameters and signed-out state. Image and advertisement load
completion is not required. A CAPTCHA or persistent rejection
leaves the request pending; the hook does not solve interactive challenges.
Attempts start six minutes apart, up to 50 consecutive failures per search
(about five hours for one pending search while both hosts are available).
The queue shares this cadence across searches and rotates due searches rather
than starting parallel browser recoveries. A completed failure or expired attempt
lease counts once; idle polls and missed intervals during sleep do not count.
Attempt numbers fence late submissions and duplicate failure reports. Counts
survive worker restarts. Successful ordinary checks cancel obsolete requests
and reset the failure streak automatically.
Each claimed attempt first checks the saved search with the existing headers on
the Pi. If they work, normal results and notifications are recorded and recovery
is cancelled without opening a browser or replacing either header file. Only
another gated response starts the browser; network or parser failures defer
the attempt. A 403 is a rejected request, not proof that the cookie expired.

There are no immediate eBay failure ntfy alerts. Failure 50 sends one exhaustion
alert and stops automatic recovery for that search. Delivery failures retry the
alert without restarting browser attempts. Regular scheduled checks continue;
a successful check clears the exhausted state, or the explicit bridge `request`
command can restart recovery. Alerts for new listings and product-price alerts
retain their existing behavior. Errors and the last good results remain visible
in Deal Watch throughout recovery.

The browser sends the rendered HTML and final URL over SSH stdin. Scripts,
styles, embedded frames and hidden inputs are removed from the snapshot. The Pi
validates the exact target query and current attempt, parses this page with the
existing eBay parser, and uses the normal result store and new-listing alerts.
It makes no second eBay request. Dismissals, previously seen item IDs, and the
boundary before "Results matching fewer words" retain their existing behavior.

`search_watch/browser_results.py` owns the submission/receipt contracts and
validation. `browser_capture_limits.json` supplies both hosts' byte limits:
5 MB of rendered HTML and 32 MB for the encoded submission. Raw page contents
are treated as private and are never logged or kept in the result database.
Mismatched URLs, expired attempts, gated pages and unrecognized markup leave
the previous results intact. A notification-delivery failure after successful
storage does not restart browser recovery.

Cookies remain in the Mac's private anonymous browser state. Browser recovery
does not export credentials or replace either host's `.ebay_headers` file.
Normal scheduled checks still use the Pi's existing headers when they work;
otherwise browser recovery supplies the results directly. The old explicit
`install` bridge command remains available for compatibility and validates
submitted headers on the Pi before replacement. It is not used by the worker.
`browser_headers.json` remains the allowlist for those legacy header files;
preserve full Chromium captures without adding the two-line Firefox defaults.

Install the scheduled hook from a normal Mac Terminal in this checkout:

```bash
zsh macbook/scripts/install_deal_watch_refresh.zsh
```

Run one pending recovery attempt manually:

```bash
python3 macbook/scripts/deal_watch_refresh.py --once
```

Inspect `tmp/deal-watch-refresh/worker.log` for non-secret success/failure status,
including the failing browser phase or Pi exception class. The private,
mode-0600 `tmp/deal-watch-refresh/browser-state.json` contains only this hook's
anonymous session and must not be committed or printed.
The Pi bridge is `pi/scripts/price_check/ebay_refresh.py`; `request <search-id>`
starts or explicitly restarts recovery, `claim` leases a pending attempt,
`recheck` tests whether that request still needs recovery using the existing
headers, `failed` acknowledges a failed attempt idempotently, `status` returns
the next retry delay, and `results` accepts the validated browser page.
The bridge owns the retry counter and exhaustion notification. All normal checks
retain the existing schedule.

### Manual header fallback

1. Open the saved search in a Firefox Private Window and verify that eBay shows
   you as signed out.
2. Open **Tools → Browser Tools → Web Developer Tools**, select **Network**,
   clear the requests, and reload the page.
3. Select the final `i.html` document request that contains the real listings.
4. Use **Copy → Copy Request Headers**.
5. Create `pi/secrets/.ebay_headers` locally and retain exactly these two
   header lines:

   ```text
   User-Agent: ...
   Cookie: ...
   ```

6. Secure it before deployment:

   ```bash
   chmod 600 pi/secrets/.ebay_headers
   ```

The secret file is ignored by Git and deploys to
`/home/pi/secrets/.ebay_headers`. The checker passes the file itself to curl;
the cookie is not placed in the command arguments. Gated responses, download
failures and unrecognized eBay markup enter the silent recovery cycle above.
Only 50 consecutive failed recovery attempts produce an ntfy failure alert
through `NTFY_PRICE_URL`. Unrecognized Amazon markup remains dashboard-only.
Every failure preserves the most recent successful price or query results.

## Command line

Product helper functions continue to use the same database:

```bash
add_pricecheck amazon 55 'https://www.amazon.com/dp/…' 'Friendly title'
rm_pricecheck 'Friendly title'
```

Saved-search operations are available through the reusable script entry point:

```bash
s price_check search-add ebay 'https://www.ebay.com/sch/i.html?_nkw=…' 'Search title'
s price_check search-check all
s price_check search-check 1 --no-notify
s price_check search-dismiss 1 123456789012
s price_check search-remove 1
```

Use `--no-notify` for the first saved-search check when the currently visible
results should become the baseline without generating initial notifications.
Only results before eBay's **Results matching fewer words** section are stored.
An eBay `/itm/<item-id>` identity is never forgotten while the saved search
exists, so a dismissed or previously notified listing does not become new again
if it disappears and later returns.
