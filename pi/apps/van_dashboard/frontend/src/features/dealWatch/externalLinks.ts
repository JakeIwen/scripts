/**
 * Resolve a user-configured external URL without ever allowing script, data,
 * credential-bearing, or otherwise non-web links into an anchor element.
 */
export function safeExternalHref(value: string): string | null {
  const candidate = value.startsWith('//') ? `https:${value}` : value;

  try {
    const url = new URL(candidate);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    if (!url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function decodeExternalHttpUrl(
  value: unknown,
  label: string,
  options: { allowProtocolRelative?: boolean } = {},
): string {
  if (typeof value !== 'string' || !value || value.trim() !== value) {
    throw new TypeError(`${label} must be a non-empty URL without surrounding space`);
  }
  if (value.startsWith('//') && !options.allowProtocolRelative) {
    throw new TypeError(`${label} must use http or https`);
  }
  if (safeExternalHref(value) === null) {
    throw new TypeError(`${label} must be a safe http or https URL`);
  }
  return value;
}
