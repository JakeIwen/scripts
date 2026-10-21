import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import dial from '@patrickkfkan/peer-dial';
import express from 'express';
import { PrivateDataStore } from '../src/private-data-store.mjs';
import { useStableDialUuid } from '../src/stable-dial-uuid.mjs';

test('DIAL server advertises the same persisted UUID across restarts', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'yt-dial-'));
  t.after(() => rm(directory, { recursive: true }));
  const first = new PrivateDataStore(directory);
  await first.initialize();
  const uuid = await useStableDialUuid(first);
  const reopened = new PrivateDataStore(directory);
  assert.equal(await useStableDialUuid(reopened), uuid);
  const server = new dial.Server({ expressApp: express(), port: 0, friendlyName: 'Van TV', delegate: {} });
  assert.equal(server.uuid, uuid);
});
