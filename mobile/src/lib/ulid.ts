// ULIDs minted on the till: 48-bit millisecond time + 80 random bits, Crockford base32.
// The server requires every id a till sends (orders, lines, events, payments) to be one,
// so a retry carries the same ids and replays instead of duplicating.
//
// Monotonic: within one millisecond (or if the clock steps back) the next id is the last
// one plus one, so ids from this till always sort in the order they were minted. An
// order's lines sort item first, then its modifiers.

const ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const MAX_TIME = 2 ** 48 - 1;

export const ULID_PATTERN = /^[0-9A-HJKMNP-TV-Z]{26}$/;

function randomBytes(n: number): Uint8Array {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  return bytes;
}

function encodeTime(now: number): string {
  if (!Number.isInteger(now) || now < 0 || now > MAX_TIME) {
    throw new RangeError(`ULID time must be an integer from 0 to ${MAX_TIME}`);
  }
  let time = "";
  let t = now;
  for (let i = 0; i < 10; i += 1) {
    time = ENCODING[t % 32] + time;
    t = Math.floor(t / 32);
  }
  return time;
}

/** The 16 random base32 digits from 16 random bytes. 256 is a multiple of 32, so
 *  `byte % 32` keeps every digit equally likely. */
function randomDigits(bytes: Uint8Array): number[] {
  return Array.from(bytes.slice(0, 16), (b) => b % 32);
}

/** One ULID from a time and 16 random bytes. */
export function encodeUlid(now: number, bytes: Uint8Array): string {
  return encodeTime(now) + randomDigits(bytes).map((d) => ENCODING[d]).join("");
}

export function monotonicFactory(
  random: (n: number) => Uint8Array = randomBytes,
  clock: () => number = Date.now,
): () => string {
  let lastTime = -1;
  let digits: number[] = [];
  return () => {
    let now = clock();
    if (now <= lastTime) {
      now = lastTime;
      let i = digits.length - 1;
      while (i >= 0 && digits[i] === 31) digits[i--] = 0;
      if (i < 0) throw new RangeError("too many ids in one millisecond");
      digits[i] += 1;
    } else {
      lastTime = now;
      digits = randomDigits(random(16));
    }
    return encodeTime(now) + digits.map((d) => ENCODING[d]).join("");
  };
}

export const ulid = monotonicFactory();
