import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';

it('uses the shared parsing boundary and legacy enum factories in features', () => {
  const root = join(process.cwd(), 'src/features');
  const violations: string[] = [];
  for (const file of readdirSync(root, { recursive: true, withFileTypes: true })) {
    if (!file.isFile() || !/\.tsx?$/.test(file.name)) continue;
    const path = join(file.parentPath, file.name);
    const source = readFileSync(path, 'utf8');
    for (const match of source.matchAll(
      /import\s+(\{[^}]*\}|\*\s+as\s+\w+)\s+from\s+['"]valibot['"]/g,
    )) {
      const clause = match[1] ?? '';
      if (/\b(?:(?:parse|safeParse|parser|safeParser)(?:Async)?|picklist)\b/.test(clause))
        violations.push(path);
      const namespace = /\*\s+as\s+(\w+)/.exec(clause)?.[1];
      if (
        namespace &&
        new RegExp(
          `\\b${namespace}\\s*\\.\\s*(?:(?:parse|safeParse|parser|safeParser)(?:Async)?|picklist)\\b`,
        ).test(source)
      )
        violations.push(path);
    }
  }
  expect(violations).toStrictEqual([]);
});
