import { describe, expect, it, vi } from 'vitest';

import {
  addDealWatchListing,
  addDealWatchSearch,
  checkDealWatchListings,
  checkDealWatchSearch,
  dismissDealWatchSearchResult,
  editDealWatchListing,
  previewDealWatchSchedule,
  removeDealWatchListing,
  removeDealWatchSearch,
  saveDealWatchSchedule,
  setDealWatchListingMute,
} from './api';

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Deal Watch mutation API', () => {
  it('sends every backend endpoint its exact URL-encoded form', async () => {
    const mutationResponse = { ok: true, message: 'Done' };
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(jsonResponse(mutationResponse));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ok: true,
        schedule: {
          expression: '30 8,16 * * 1-5',
          description: 'At minute 30 past hours 8 and 16 on weekdays',
          error: null,
          error_code: null,
        },
      }),
    );

    const listing = {
      parser: 'amazon' as const,
      threshold: '55.00',
      url: 'https://example.com/item?a=1&b=two',
      title: 'Camp stove',
    };
    const search = {
      parser: 'ebay' as const,
      url: 'https://www.ebay.com/sch/i.html?_nkw=impact driver',
      title: 'Impact drivers',
    };

    await addDealWatchListing(listing);
    await editDealWatchListing(7, listing);
    await removeDealWatchListing(7);
    await setDealWatchListingMute(7, 14);
    await checkDealWatchListings('all');
    await addDealWatchSearch(search);
    await removeDealWatchSearch(12);
    await dismissDealWatchSearchResult(12, '123456789012');
    await checkDealWatchSearch(12);
    await saveDealWatchSchedule('30 8,16 * * 1-5');
    await previewDealWatchSchedule('30 8,16 * * 1-5');

    const expected = [
      [
        '/api/price-checks/add',
        'parser=amazon&threshold=55.00&url=https%3A%2F%2Fexample.com%2Fitem%3Fa%3D1%26b%3Dtwo&title=Camp+stove',
      ],
      [
        '/api/price-checks/edit',
        'id=7&parser=amazon&threshold=55.00&url=https%3A%2F%2Fexample.com%2Fitem%3Fa%3D1%26b%3Dtwo&title=Camp+stove',
      ],
      ['/api/price-checks/remove', 'id=7'],
      ['/api/price-checks/mute', 'id=7&days=14'],
      ['/api/price-checks/check', 'target=all'],
      [
        '/api/price-checks/searches/add',
        'parser=ebay&url=https%3A%2F%2Fwww.ebay.com%2Fsch%2Fi.html%3F_nkw%3Dimpact+driver&title=Impact+drivers',
      ],
      ['/api/price-checks/searches/remove', 'id=12'],
      ['/api/price-checks/searches/dismiss', 'id=12&item_id=123456789012'],
      ['/api/price-checks/searches/check', 'target=12'],
      ['/api/price-checks/schedule', 'expression=30+8%2C16+*+*+1-5'],
      ['/api/price-checks/schedule/parse', 'expression=30+8%2C16+*+*+1-5'],
    ] as const;

    expect(fetchMock).toHaveBeenCalledTimes(expected.length);
    expected.forEach(([expectedPath, expectedBody], index) => {
      const [path, init] = fetchMock.mock.calls[index]!;
      expect(path).toBe(expectedPath);
      expect(init).toMatchObject({
        cache: 'no-store',
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Van-Dashboard': '1',
        },
      });
      expect(String(init?.body)).toBe(expectedBody);
    });
  });
});
