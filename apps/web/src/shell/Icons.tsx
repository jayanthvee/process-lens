/**
 * The icon sprite from the approved mock, and the one way to use an icon: `<Icon name="home" />`.
 *
 * The symbols live in one hidden `<svg>` rendered by the shell, exactly as the mock does it, so the
 * stroke weight and the 16 px box are the design's own.
 */

export type IconName =
  | "home"
  | "tasks"
  | "hist"
  | "help"
  | "sheet"
  | "doc"
  | "map"
  | "apps"
  | "q"
  | "gear"
  | "search"
  | "plus";

/** One icon from the sprite. Decorative: the label beside it carries the meaning. */
export function Icon({ name }: { name: IconName }): JSX.Element {
  return (
    <svg className="i" aria-hidden="true" focusable="false">
      <use href={`#i-${name}`} />
    </svg>
  );
}

/** The hidden sprite itself, rendered once by the shell. */
export function IconSprite(): JSX.Element {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <symbol id="i-home" viewBox="0 0 16 16">
        <path d="M2.5 7 8 2.5 13.5 7v6.5h-11z" />
        <path d="M6.5 13.5V9.5h3v4" />
      </symbol>
      <symbol id="i-tasks" viewBox="0 0 16 16">
        <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
        <path d="M5.5 6h5M5.5 8.5h5M5.5 11h3" />
      </symbol>
      <symbol id="i-hist" viewBox="0 0 16 16">
        <path d="M2.8 8a5.2 5.2 0 1 0 1.5-3.7" />
        <path d="M2.5 2.8v2.4h2.4M8 5.2V8l2 1.4" />
      </symbol>
      <symbol id="i-help" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="5.5" />
        <path d="M8 5v3.4M8 10.8v.1" />
      </symbol>
      <symbol id="i-sheet" viewBox="0 0 16 16">
        <rect x="2.5" y="2.5" width="11" height="11" rx="1.5" />
        <path d="M2.5 6.2h11M2.5 9.8h11M6.4 2.5v11" />
      </symbol>
      <symbol id="i-doc" viewBox="0 0 16 16">
        <path d="M4 2.5h5.5L12 5v8.5H4z" />
        <path d="M9.5 2.5V5H12M6 8.5h4M6 11h3" />
      </symbol>
      <symbol id="i-map" viewBox="0 0 16 16">
        <path d="M3 5h6M3 11h10" />
        <path d="m7 3 2 2-2 2M11 9l2 2-2 2" />
      </symbol>
      <symbol id="i-apps" viewBox="0 0 16 16">
        <rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" />
        <rect x="9" y="2.5" width="4.5" height="4.5" rx="1" />
        <rect x="2.5" y="9" width="4.5" height="4.5" rx="1" />
        <rect x="9" y="9" width="4.5" height="4.5" rx="1" />
      </symbol>
      <symbol id="i-q" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="5.5" />
        <path d="M6.4 6.4a1.7 1.7 0 0 1 3.2.6c0 1.1-1.6 1.4-1.6 2.4M8 11.2v.1" />
      </symbol>
      <symbol id="i-gear" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="2" />
        <path d="M8 2.2v1.6M8 12.2v1.6M2.2 8h1.6M12.2 8h1.6M3.9 3.9l1.1 1.1M11 11l1.1 1.1M3.9 12.1 5 11M11 5l1.1-1.1" />
      </symbol>
      <symbol id="i-search" viewBox="0 0 16 16">
        <circle cx="7" cy="7" r="4.3" />
        <path d="m10.3 10.3 3.2 3.2" />
      </symbol>
      <symbol id="i-plus" viewBox="0 0 16 16">
        <path d="M8 3.2v9.6M3.2 8h9.6" />
      </symbol>
    </svg>
  );
}
