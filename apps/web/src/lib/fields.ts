/**
 * PL-109 items 1 and 4: the words the Check-it step uses about a task field.
 *
 * A field is never just a machine name. Everywhere it appears it shows the value the person typed
 * while teaching ("Contact — you typed “ben baller”"), and an unsure guess is asked about in the
 * person's own words ("Which column has the email addresses?") rather than labelled "Check this".
 */

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MONEY = /[€$£]|^\s*-?\d{1,3}(,\d{3})*(\.\d+)?\s*$/;
const PHONE = /^\+?[\d][\d\s().-]{5,}$/;

/** The words two names share, ignoring tiny words and punctuation. */
function sharesNameWord(left: string, right: string): boolean {
  const words = (text: string): Set<string> =>
    new Set(
      text
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter((word) => word.length > 2),
    );
  const first = words(left);
  return [...words(right)].some((word) => first.has(word));
}

/** The safe quote marks the rest of the app uses. */
const OPEN = "\u201c";
const CLOSE = "\u201d";

/** "Contact — you typed “ben baller”", or just the label when nothing was taught. */
export function taughtLine(label: string, example: string | null | undefined): string {
  const value = (example ?? "").trim();
  return value ? `${label} — you typed ${OPEN}${value}${CLOSE}` : label;
}

/** The question an unsure match asks, in words about the person's own data. */
export function columnQuestion(label: string, example: string | null | undefined): string {
  const value = (example ?? "").trim();
  const name = label.trim().toLowerCase() || "value";
  if (EMAIL.test(value)) return "Which column has the email addresses?";
  if (MONEY.test(value)) return `Which column has the ${name} amounts?`;
  if (PHONE.test(value)) return `Which column has the ${name} numbers?`;
  return `Which column has the ${name}?`;
}

/** How a task field reads on Check it: matched, or a question, never "Check this". */
export interface FieldReading {
  label: string;
  tone: "ok" | "help" | "idle";
  /** The question to show in place of a badge, when the match is only a guess. */
  question?: true;
}

/**
 * What the Match column says for one field.
 *
 * `chosen !== auto` is the person's own pick: they decided, so it is "Matched" whatever the guess
 * thought. An unsure guess is a question, not a warning.
 */
export function fieldReading(
  chosen: string | null,
  confidence: "exact" | "close" | "none",
  auto: string | null,
  label: string,
  example: string | null | undefined,
): FieldReading {
  if (chosen === null) return { label: EMPTY_COLUMN, tone: "idle" };
  if (chosen !== auto || confidence === "exact") return { label: "Matched", tone: "ok" };
  // PL-123: the owner's "Sales price" field was matched to "New Price" by the shared word
  // "price", but the panel still asked its leftover question. A guess that shares a real word
  // with the field's own name is matched; only a guess by shape or synonym with no shared word
  // is still a question.
  if (sharesNameWord(chosen, label)) return { label: "Matched", tone: "ok" };
  return { label: columnQuestion(label, example), tone: "help", question: true };
}

/** What a field with no column does: it is left empty, and the page says so. */
export const EMPTY_COLUMN = "Left empty for every row.";

/** "E-mail → riley.ivanova@…": the matched column and the first row's value, short. */
export function matchedExample(chosen: string, value: unknown, limit = 24): string {
  const text = value === null || value === undefined ? "" : String(value).trim();
  if (!text) return chosen;
  const shown = text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
  return `${chosen} → ${shown}`;
}
