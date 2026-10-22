/**
 * PL-086: the team access code a hosted ProcessLens asks for.
 *
 * A laptop's API has no door, so an empty code sends nothing. Hosted, every HTTP call the extension
 * makes (open a session, report its version, compile, save a task) carries the code as a bearer
 * token. The sockets need none: the extension's socket proves itself with the session token the
 * access-checked session call returned.
 */
export function accessHeaders(accessCode: string | undefined): Record<string, string> {
  const code = (accessCode ?? "").trim();
  return code === "" ? {} : { authorization: `Bearer ${code}` };
}
