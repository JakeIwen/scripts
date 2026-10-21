// A YouTube video id is 11 URL-safe base64 characters; the player, the cast
// route and the Mac/Firefox senders all validate against this one pattern.
export const VIDEO_ID_PATTERN = /^[\w-]{11}$/;

export function isVideoId(value) {
  return typeof value === 'string' && VIDEO_ID_PATTERN.test(value);
}
