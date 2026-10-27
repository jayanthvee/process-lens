/**
 * PL-112 item 4: the emails a sign-in task has been run with.
 *
 * The taught email is the recipe's own example. Every address typed since is kept in this
 * Chrome's extension storage, under one key per task -- never on the server, because it is one
 * person's address book, not the task's data.
 */

import type { ExtensionLocalStorage } from "./extension";

const KEY_PREFIX = "processlens.signInEmails.";

/** The choice that opens a text box instead of naming an address. */
export const ANOTHER_EMAIL = "__another__";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function looksLikeAnEmail(value: string): boolean {
  return EMAIL.test(value.trim());
}

export function storageKey(workflowId: string): string {
  return `${KEY_PREFIX}${workflowId}`;
}

/** Taught first, then everything typed before, with repeats removed case-insensitively. */
export function signInEmails(
  taught: string | null | undefined,
  stored: readonly string[] | null | undefined,
): string[] {
  const seen = new Set<string>();
  const emails: string[] = [];
  for (const email of [taught ?? "", ...(stored ?? [])]) {
    const text = email.trim();
    if (text === "") continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    emails.push(text);
  }
  return emails;
}

export async function readSignInEmails(
  storage: ExtensionLocalStorage | undefined,
  workflowId: string,
): Promise<string[]> {
  if (storage === undefined) return [];
  try {
    const stored = await storage.get([storageKey(workflowId)]);
    const value = stored[storageKey(workflowId)];
    if (!Array.isArray(value)) return [];
    return value.filter((email): email is string => typeof email === "string");
  } catch {
    return [];
  }
}

export async function rememberSignInEmail(
  storage: ExtensionLocalStorage | undefined,
  workflowId: string,
  email: string,
): Promise<string[]> {
  const text = email.trim();
  if (storage === undefined || text === "") return [];
  const existing = await readSignInEmails(storage, workflowId);
  const emails = signInEmails(null, [...existing, text]);
  try {
    await storage.set({ [storageKey(workflowId)]: emails });
  } catch {
    // A storage write that fails leaves the dropdown as it was; the run still has its row.
  }
  return emails;
}

/** The column and value a sign-in recording typed: an email-looking value first. */
export function taughtSignIn(
  values: ReadonlyArray<{ column: string; value: string }>,
  keyColumn: string,
): { column: string; email: string } | null {
  const nonEmpty = values.filter((value) => value.value.trim() !== "");
  if (nonEmpty.length === 0) return null;
  const email = nonEmpty.find((value) => looksLikeAnEmail(value.value));
  const key = nonEmpty.find((value) => value.column === keyColumn && keyColumn !== "");
  const fallback = nonEmpty[0];
  if (fallback === undefined) return null;
  const chosen = email ?? key ?? fallback;
  return { column: chosen.column, email: chosen.value.trim() };
}
