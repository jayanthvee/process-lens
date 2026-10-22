// Types for `release-lib.mjs`, so the tests (and `tsc --noEmit`, which type-checks `tests/`)
// see what the build scripts really export.

export const EXTENSION_ROOT: string;
export const HUB_URL_ENV: string;
export const ACCESS_CODE_ENV: string;
export const DEFAULT_HUB_URL: string;
export const HUB_URL_PROBLEM: string;

export type HubUrlAnswer = { ok: true; url: string } | { ok: false; problem: string };

export function extensionIdFromPublicKey(derBase64: string): string;
export function hubUrlFromEnv(env?: Record<string, string | undefined>): HubUrlAnswer;
export function readPublicKey(path: string): string | null;
export function manifestWithKey(
  manifest: Record<string, unknown>,
  publicKey: string | null,
): Record<string, unknown>;
export function gitIgnored(root: string, path: string): boolean;

export interface BuildOptions {
  root?: string;
  dist?: string;
  hubUrl?: string;
  /** PL-086: the hosted ProcessLens's team access code, baked into the popup. */
  accessCode?: string;
  publicKeyPath?: string;
}

export interface BuildResult {
  dist: string;
  hubUrl: string;
  hasAccessCode: boolean;
  publicKey: string | null;
  extensionId: string | null;
}

export function buildExtension(options?: BuildOptions): Promise<BuildResult>;

export function zipDirectory(dir: string): Buffer;
export function readZipEntries(zipPath: string): string[];

export interface PackageOptions extends BuildOptions {
  outDir?: string;
}

export interface PackageResult {
  zipPath: string;
  entries: string[];
}

export function packageExtension(options?: PackageOptions): Promise<PackageResult>;

export interface MakeKeyOptions {
  root?: string;
  privatePath?: string;
  publicPath?: string;
}

export interface MakeKeyResult {
  id: string;
  publicKeyBase64: string;
  privatePath: string;
  publicPath: string;
}

export function makeKey(options?: MakeKeyOptions): Promise<MakeKeyResult>;
