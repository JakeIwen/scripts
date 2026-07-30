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
The dashboard's **Check all now** button does the same.

## eBay browser headers

eBay search downloads use an anonymous Firefox Private Window session. Do not
use a logged-in eBay session.

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
**failed to load** notification, while unrecognized result markup sends a
**parser needs update** notification. All three preserve the most recent
successful results.

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
