// Adds one to the extension's patch version, in both manifest.json and package.json (PL-100).
//
//   npm run bump
//
// Prints the new version and nothing else, so a release can capture it. The source manifest is
// bumped at release time; a build never moves it on its own.
import { EXTENSION_ROOT, bumpVersion } from "./release-lib.mjs";

try {
  console.log(bumpVersion({ root: EXTENSION_ROOT }));
} catch (error) {
  console.error(`bump: ${error.message}`);
  process.exit(1);
}
