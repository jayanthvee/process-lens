// Run control: the Start / Pause / Resume / Abort seam of an attended run.
//
// The executor checks in with `checkpoint()` between steps. While paused the
// run holds; when aborted it stops before the next step, so an attended run can
// always be stopped by the person watching it.
import { ExecutorError } from "./types";

export class RunControl {
  private state: "running" | "paused" | "aborted" = "running";
  private reason: string | null = null;
  private waiters: Array<() => void> = [];

  pause(): void {
    if (this.state === "running") this.state = "paused";
  }

  resume(): void {
    if (this.state === "paused") {
      this.state = "running";
      this.release();
    }
  }

  /** Stop the run. `reason` is carried into the run summary, for the log. */
  abort(reason?: string): void {
    this.state = "aborted";
    if (reason && !this.reason) this.reason = reason;
    this.release();
  }

  get paused(): boolean {
    return this.state === "paused";
  }

  get aborted(): boolean {
    return this.state === "aborted";
  }

  /** Why the run was aborted, when the abort named a reason. */
  get abortReason(): string | null {
    return this.reason;
  }

  get status(): "running" | "paused" | "aborted" {
    return this.state;
  }

  /** The raw state, read through a method so control-flow narrowing does not apply. */
  private current(): "running" | "paused" | "aborted" {
    return this.state;
  }

  private release(): void {
    const waiters = this.waiters;
    this.waiters = [];
    for (const waiter of waiters) waiter();
  }

  /**
   * Block while paused, throw when aborted, and return immediately while
   * running. Called before each step.
   */
  async checkpoint(): Promise<void> {
    if (this.state === "aborted") throw new ExecutorError("the run was aborted");
    if (this.state !== "running") {
      await new Promise<void>((resolve) => this.waiters.push(resolve));
    }
    // Re-read after awaiting; a resume or abort may have changed the state.
    const state: "running" | "paused" | "aborted" = this.current();
    if (state === "aborted") throw new ExecutorError("the run was aborted");
  }
}
