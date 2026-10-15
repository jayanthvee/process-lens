import type { CSSProperties, ReactNode } from "react";

/**
 * One sentence about what will appear here, plus "Coming soon".
 *
 * Every screen the ticket does not build yet uses this, so no page is ever blank.
 */
export function EmptyState(props: { sentence: string; action?: ReactNode }): JSX.Element {
  return (
    <section className="panel in" style={{ "--i": 1 } as CSSProperties}>
      <div className="empty">
        <p className="empty-sentence">{props.sentence}</p>
        <p className="empty-soon">Coming soon</p>
        {props.action === undefined ? null : <div className="empty-action">{props.action}</div>}
      </div>
    </section>
  );
}
