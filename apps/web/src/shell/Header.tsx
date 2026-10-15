import type { CSSProperties, ReactNode } from "react";

import { Search } from "./Search";

/**
 * The header: the page's title, Search with its `Ctrl K` hint, the page's one primary action, and
 * the avatar.
 *
 * The avatar is a plain circle with a person's outline -- nothing in this repository knows the
 * operator's name, so no initials are invented.
 */
export function Header(props: { title: string; action?: ReactNode }): JSX.Element {
  return (
    <div className="head in" style={{ "--i": 0 } as CSSProperties}>
      <h2>{props.title}</h2>
      <Search />
      {props.action === undefined || props.action === null ? null : props.action}
      <span className="avatar" title="You" aria-label="You">
        <svg className="i" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="8" cy="5.6" r="2.6" />
          <path d="M3.4 13.4c.7-2.4 2.6-3.6 4.6-3.6s3.9 1.2 4.6 3.6" />
        </svg>
      </span>
    </div>
  );
}
