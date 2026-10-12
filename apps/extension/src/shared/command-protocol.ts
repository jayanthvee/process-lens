// The command protocol between the runner worker and this extension (PL-014).
//
// One WebSocket, relayed by the hub in `services/api/hub.py`. Both ends refuse a
// message whose `protocol` is not exactly `PROTOCOL`: a version must be
// negotiated, never guessed. The Python mirror of this file is
// `services/runner/browser_executor.py`.
//
// The extension owns no workflow state (D-004). It receives one command at a
// time, performs it, and reports what the page showed. It never decides which
// step runs next.

/** The only protocol version this build speaks. */
export const PROTOCOL = 1;

/** The subprotocol the extension offers and the hub selects (the token is the other). */
export const PROTOCOL_SUBPROTOCOL = "processlens.v1";

/** The prefix of the second offered subprotocol, which carries the session token. */
export const TOKEN_SUBPROTOCOL_PREFIX = "pl.token.";

/** How long a command stays valid if the runner does not say otherwise. */
export const DEFAULT_COMMAND_TTL_MS = 60_000;

/** How often both ends exchange a ping while connected. */
export const KEEPALIVE_MS = 20_000;

/** The statuses the extension reports for one command. */
export type CommandStatus =
  | "ok"
  | "locator_failed"
  | "not_dispatched"
  | "rejected"
  | "refused"
  | "timeout"
  | "lost";

/** The run-level events the extension reports, which the runner turns into pauses. */
export type EventKind =
  | "detached"
  | "tab_closed"
  | "login_required"
  | "challenge"
  | "policy_blocked"
  | "off_origin";

/** The kinds of command the runner sends. */
export type CommandKind = "prepare" | "dispatch" | "check";

/** What every command from the runner carries. */
export interface CommandEnvelope {
  protocol: number;
  type: CommandKind;
  /** Unique on the wire; the runner matches every reply against it (R3). */
  command_id: string;
  /** The runner's own `StepCommand.command_id`, for its logs only. */
  step_ref: string;
  run_id: string;
  run_record_id: string;
  lease_epoch: number;
  /** Epoch milliseconds. The extension refuses a command past this, twice. */
  expires_at: number;
}

/** A `prepare` or `dispatch`: one step, with its values already rendered. */
export interface StepCommandMessage extends CommandEnvelope {
  type: "prepare" | "dispatch";
  step: {
    id: string;
    action: string;
    targets?: unknown[];
    within?: string;
    frame?: unknown[];
    value?: unknown;
    clear_first?: boolean;
    option_match?: "text" | "value" | "index";
    timeout_ms?: number;
    allow_destructive_control?: boolean;
    [key: string]: unknown;
  };
  commit: boolean;
  allowed_origins: string[];
}

/** A `check`: evaluate a condition, polling up to `timeout_ms`. */
export interface CheckCommandMessage extends CommandEnvelope {
  type: "check";
  condition: Record<string, unknown>;
  timeout_ms: number;
  step_id?: string | null;
  record_key?: string | null;
}

export type CommandMessage = StepCommandMessage | CheckCommandMessage;

/** Sent the moment a commit's trusted input event has been dispatched. */
export interface AckMessage {
  protocol: number;
  type: "ack";
  command_id: string;
}

/** The answer to one command. */
export interface ResultMessage {
  protocol: number;
  type: "result";
  command_id: string;
  status: CommandStatus;
  rung: number | null;
  observed: Record<string, unknown>;
  error: string | null;
}

/** The answer to a `check`. */
export interface ConditionMessage {
  protocol: number;
  type: "condition";
  command_id: string;
  passed: boolean;
  observed: Record<string, unknown>;
}

/** An unsolicited run-level event. Always sent BEFORE the result it causes (R6). */
export interface EventMessage {
  protocol: number;
  type: "event";
  kind: EventKind;
  detail: string;
}

/** The hub's first message to an extension socket: a fresh resume token (R5). */
export interface SessionMessage {
  protocol: number;
  type: "session";
  resume_token: string;
}

export interface PeerReadyMessage {
  protocol: number;
  type: "peer_ready";
}

/** The peer's socket dropped. Authoritative, not a hint (R6). */
export interface PeerGoneMessage {
  protocol: number;
  type: "peer_gone";
}

/** The hub could not deliver a message (no peer attached). */
export interface HubErrorMessage {
  protocol: number;
  type: "hub_error";
  code: string;
  command_id?: string | null;
}

export interface PingMessage {
  protocol: number;
  type: "ping";
}

export interface PongMessage {
  protocol: number;
  type: "pong";
}

/** Everything the extension may receive. */
export type IncomingMessage =
  | CommandMessage
  | SessionMessage
  | EventMessage
  | PeerReadyMessage
  | PeerGoneMessage
  | HubErrorMessage
  | PingMessage
  | PongMessage;

/** Everything the extension may send. */
export type OutgoingMessage =
  | AckMessage
  | ResultMessage
  | ConditionMessage
  | EventMessage
  | PingMessage
  | PongMessage;

const COMMAND_TYPES = new Set(["prepare", "dispatch", "check"]);
const EVENT_KINDS = new Set<EventKind>([
  "detached",
  "tab_closed",
  "login_required",
  "challenge",
  "policy_blocked",
  "off_origin",
]);

/**
 * Validate one raw wire value. Returns the typed message, or a reason it was
 * refused. Fail-closed: a wrong protocol version, an unknown type, or a missing
 * required field is a refusal, never a default.
 */
export function parseIncoming(raw: unknown): { message: IncomingMessage } | { error: string } {
  if (typeof raw !== "object" || raw === null) return { error: "not a JSON object" };
  const record = raw as Record<string, unknown>;
  if (record["protocol"] !== PROTOCOL) {
    return { error: `unsupported protocol ${JSON.stringify(record["protocol"])}` };
  }
  const type = record["type"];
  if (typeof type !== "string") return { error: "no message type" };

  if (COMMAND_TYPES.has(type)) {
    const missing = (["command_id", "run_id", "run_record_id", "expires_at"] as const).filter(
      (key) => record[key] === undefined,
    );
    if (missing.length > 0) return { error: `command is missing ${missing.join(", ")}` };
    if (typeof record["lease_epoch"] !== "number") return { error: "command has no lease_epoch" };
    // Fail-closed (F3): a command whose `expires_at` is not a number would compare as NaN and
    // never look expired, so a stale command would be acted on. Refuse it instead.
    if (typeof record["expires_at"] !== "number" || !Number.isFinite(record["expires_at"])) {
      return { error: "command has a non-numeric expires_at" };
    }
    return { message: record as unknown as CommandMessage };
  }
  if (type === "session") {
    if (typeof record["resume_token"] !== "string") return { error: "session has no resume_token" };
    return { message: record as unknown as SessionMessage };
  }
  if (type === "event") {
    if (!EVENT_KINDS.has(record["kind"] as EventKind)) {
      return { error: `unknown event kind ${JSON.stringify(record["kind"])}` };
    }
    return { message: record as unknown as EventMessage };
  }
  if (type === "peer_ready" || type === "peer_gone" || type === "ping" || type === "pong") {
    return { message: record as unknown as IncomingMessage };
  }
  if (type === "hub_error") {
    return { message: record as unknown as HubErrorMessage };
  }
  return { error: `unknown message type ${type}` };
}

/** True when the message is one of the three commands. */
export function isCommand(
  message: IncomingMessage,
): message is CommandMessage {
  return COMMAND_TYPES.has((message as { type?: string }).type ?? "");
}

/** Build an outbound message with the protocol field always correct. */
export function outbound<T extends OutgoingMessage>(message: Omit<T, "protocol">): T {
  return { protocol: PROTOCOL, ...message } as T;
}
