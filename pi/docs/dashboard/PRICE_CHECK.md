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

## eBay browser headers

eBay search downloads use anonymous browser headers, not an eBay API token.
Do not use a logged-in eBay session.

### Automatic renewal

A rejected/gated check queues a durable request in `search_browser_refresh` in
the existing private database. The Mac's `com.jacobr.deal-watch-refresh`
LaunchAgent polls over SSH to `vanpi.lan` every five minutes. It uses the existing
clean headless browser MCP service at `http://localhost:8931/mcp`, with an
isolated signed-out context and its own private anonymous browser state. It never
launches a browser or accesses daily Chrome.
The Mac must be awake and logged in, with the managed clean service available.

The browser opens the saved search and allows any automatic verification to
finish. A bare 403 response starts eBay's anonymous verification flow explicitly.
It waits for real listings, the completed page load, and session-cookie updates,
then confirms the exact saved-search parameters and signed-out state. A CAPTCHA or persistent rejection
leaves the request pending; the hook does not solve interactive challenges.
Attempts are limited to one per thirty minutes, including crashes or network
failures. Successful ordinary checks cancel obsolete requests automatically.
Each claimed attempt first checks the saved search with the existing headers on
the Pi. If they work, normal results and notifications are recorded and renewal
is cancelled without opening a browser or replacing either header file. Only
another gated response starts browser renewal; network or parser failures defer
the attempt. A 403 is a rejected request, not proof that the cookie expired.

Chromium's user agent, language and client hints use a consistent desktop profile.
The hook retains the successful document request's allowed headers, with its
latest cookies, instead of replaying Chromium cookies with Firefox header
defaults. `search_watch/browser_headers.json` is the canonical header allowlist
shared by the Mac exporter and Pi validator. User-Agent and Cookie are required;
authorization, Host and HTTP/2 pseudo-headers are never exported. Referer is
restricted to HTTPS on `www.ebay.com`. Existing two-line Firefox captures retain
their original request defaults.

Fresh headers travel over SSH stdin, never command arguments or logs. The Pi
uses a private temporary file to download and parse the search itself before
atomically replacing `/home/pi/secrets/.ebay_headers`. A failed validation keeps
the old headers and saved results. A successful install checks the saved search
with normal notification behavior. The Mac also updates its ignored
`pi/secrets/.ebay_headers`, so broad sync retains the current cookie.

Install the scheduled hook from a normal Mac Terminal in this checkout:

```bash
zsh macbook/scripts/install_deal_watch_refresh.zsh
```

Run one pending renewal manually:

```bash
python3 macbook/scripts/deal_watch_refresh.py
```

Inspect `tmp/deal-watch-refresh/worker.log` for non-secret success/failure status,
including the failing browser phase or Pi exception class. The private,
mode-0600 `tmp/deal-watch-refresh/browser-state.json` contains only this hook's
anonymous session and must not be committed or printed.
The Pi bridge is `pi/scripts/price_check/ebay_refresh.py`; `request <search-id>`
queues a deliberate renewal, `claim` leases a pending request with the cooldown,
`recheck` tests whether that request still needs renewal using the existing
headers, and `install` consumes the private JSON submission. All normal checks retain
the existing schedule. Cookie renewal does not change ntfy delivery or retries.

### Manual fallback

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
the cookie is not placed in the command arguments. Browser cookies expire, so
a browser-verification, rejected request, redirect trap, access-denied page, or
other clearly gated response sends an **update eBay browser cookie**
notification through `NTFY_PRICE_URL`. Network/download failures send a
**failed to load** notification. Unrecognized Amazon or eBay result markup is
recorded for the Deal Watch UI without sending a notification. Every failure
preserves the most recent successful price or query results.

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
