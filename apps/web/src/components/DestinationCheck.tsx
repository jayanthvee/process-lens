import { destinationCheckLabel, destinationCheckWhy } from "../pages/needsHelpActions";
import "./destinationCheck.css";

/**
 * The safety tick a commit-attempted row needs before Try again is allowed: one box, its label and
 * one sentence saying why. It lives in one component so the Needs help page and a run's rows show
 * the same words and the same rules (PL-038); the caller owns the ticked state, because a reload
 * must leave every box clear.
 *
 * PL-117 item 3: the label names the task's own site (`app`), as the task cards do. A payload with
 * no site says "the site" rather than falling back to a product name.
 */
export function DestinationCheck({
  id,
  checked,
  onChange,
  app,
}: {
  id: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  app?: string | null;
}): JSX.Element {
  return (
    <div className="destination-check">
      <span className="destination-check-line">
        <input
          id={id}
          className="destination-check-box"
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
        <label className="destination-check-label" htmlFor={id}>
          {destinationCheckLabel(app)}
        </label>
      </span>
      <p className="destination-check-why">{destinationCheckWhy(app)}</p>
    </div>
  );
}
