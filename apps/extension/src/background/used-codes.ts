// PL-119 item 1: a code ProcessLens typed is never typed again.
//
// The service worker remembers each code it types -- while teaching or in a run -- as a salted
// digest: one random salt made when the worker starts and kept in memory only, and an entry that
// disappears after 15 minutes. The code itself is never stored, logged or sent anywhere. A later
// look in Gmail compares the code in each message with these digests and skips a match.
//
// Chrome's Web Crypto always provides `subtle` in a service worker, so production takes the
// SHA-256 path. The fallback exists only for a runtime without it (the jsdom test environment);
// it is a salted hash too, just a smaller one.

export const USED_CODE_TTL_MS = 15 * 60_000;

/** The two things the lookup and the teaching path need from the worker's memory. */
export interface CodeMemory {
  /** Remember one code that was typed; the code itself is not kept. */
  remember(code: string): Promise<void>;
  /** Whether this exact code was typed in the last 15 minutes. */
  isUsed(code: string): Promise<boolean>;
}

/** One random salt for this service worker's life; never written down. */
function randomSalt(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** FNV-1a, 64-bit. Only for a runtime without Web Crypto. */
function fallbackDigest(input: string): string {
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (const byte of new TextEncoder().encode(input)) {
    hash ^= BigInt(byte);
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, "0");
}

/** The salted digest of one code. Exported so a test can look for the hash itself. */
export async function digestCode(salt: string, code: string): Promise<string> {
  const input = `${salt}\u0000${code}`;
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return fallbackDigest(input);
  const hash = await subtle.digest("SHA-256", new TextEncoder().encode(input));
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** A worker's memory of typed codes: salted hashes, expired after 15 minutes. */
export class SaltedCodeMemory implements CodeMemory {
  private readonly salt: string;
  private readonly now: () => number;
  private readonly expiries = new Map<string, number>();

  constructor(options: { now?: () => number; salt?: string } = {}) {
    this.now = options.now ?? (() => Date.now());
    this.salt = options.salt ?? randomSalt();
  }

  async remember(code: string): Promise<void> {
    const at = this.now();
    this.sweep(at);
    this.expiries.set(await digestCode(this.salt, code), at + USED_CODE_TTL_MS);
  }

  async isUsed(code: string): Promise<boolean> {
    const at = this.now();
    this.sweep(at);
    return this.expiries.has(await digestCode(this.salt, code));
  }

  /** Forget entries whose 15 minutes are up. */
  private sweep(at: number): void {
    for (const [digest, expiry] of this.expiries) {
      if (expiry <= at) this.expiries.delete(digest);
    }
  }
}

/** The worker's one memory. A restarted worker has a new salt and remembers nothing. */
export const typedCodes = new SaltedCodeMemory();
