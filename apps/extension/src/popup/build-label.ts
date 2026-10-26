/**
 * PL-100: the build a person is running, in small grey text at the foot of the popup.
 *
 * A packaged build's manifest carries `version_name` -- `<version> (<date> <commit>)` -- which is
 * what Chrome shows beside the extension on chrome://extensions. A plain `npm run build` has no
 * stamp, so the label falls back to the manifest's plain version. Nothing here touches `chrome`:
 * the popup hands in the manifest, and the test hands in one it made.
 */
export interface BuildManifest {
  version: string;
  version_name?: string;
}

/** "ProcessLens 0.1.1 (20261025 c8abcb3)", or "ProcessLens 0.1.0" for an unstamped build. */
export function buildLabel(manifest: BuildManifest): string {
  const stamp = (manifest.version_name ?? "").trim();
  return `ProcessLens ${stamp.length > 0 ? stamp : manifest.version}`;
}

/** Write the label into `#build`; a page without the element is left alone. */
export function renderBuildLabel(root: ParentNode, manifest: BuildManifest): void {
  const element = root.querySelector<HTMLElement>("#build");
  if (element !== null) element.textContent = buildLabel(manifest);
}
