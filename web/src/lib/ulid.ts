// ULIDs minted on the till: 48-bit millisecond time + 80 random bits, Crockford base32.
// The server requires every id a till sends (orders, lines, events, payments) to be one,
// so a retry carries the same ids and replays instead of duplicating.

const ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const MAX_TIME = 2 ** 48 - 1;

export const ULID_PATTERN = /^[0-9A-HJKMNP-TV-Z]{26}$/;

function randomBytes(n: number): Uint8Array {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  return bytes;
}

export function ulid(now: number = Date.now(), random: (n: number) => Uint8Array = randomBytes): string {
  if (!Number.isInteger(now) || now < 0 || now > MAX_TIME) {
    throw new RangeError(`ULID time must be an integer from 0 to ${MAX_TIME}`);
  }
  let time = "";
  let t = now;
  for (let i = 0; i < 10; i += 1) {
    time = ENCODING[t % 32] + time;
    t = Math.floor(t / 32);
  }
  // 256 is a multiple of 32, so `byte % 32` keeps every character equally likely.
  const bytes = random(16);
  let rand = "";
  for (let i = 0; i < 16; i += 1) rand += ENCODING[bytes[i] % 32];
  return time + rand;
}
