import { DataStore } from 'yt-cast-receiver';
import { mkdir, chmod, readFile, open, rename, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

// Library identity and user settings share one private, atomic persistence boundary.
export class PrivateDataStore extends DataStore {
  constructor(directory) {
    super();
    this.directory = directory;
    this.writes = Promise.resolve();
  }

  async initialize() {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    await chmod(this.directory, 0o700);
  }

  path(key) {
    if (!/^[a-zA-Z0-9.-]+$/.test(key)) throw new Error('Invalid state key');
    return join(this.directory, `${key}.json`);
  }

  async get(key) {
    await this.writes;
    try {
      const path = this.path(key);
      await chmod(path, 0o600);
      return JSON.parse(await readFile(path, 'utf8'));
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      // Never silently replace a damaged screen identity and lose pairings.
      throw error;
    }
  }

  async set(key, value) {
    const write = async () => {
      const path = this.path(key);
      const temporary = `${path}.${randomUUID()}.tmp`;
      try {
        const file = await open(temporary, 'wx', 0o600);
        try { await file.writeFile(JSON.stringify(value)); await file.sync(); }
        finally { await file.close(); }
        await rename(temporary, path);
        const directory = await open(this.directory, 'r');
        try { await directory.sync(); } finally { await directory.close(); }
      } finally {
        await unlink(temporary).catch(error => {
          if (error.code !== 'ENOENT') throw error;
        });
      }
    };
    const result = this.writes.then(write);
    this.writes = result.catch(() => {});
    return result;
  }
}
