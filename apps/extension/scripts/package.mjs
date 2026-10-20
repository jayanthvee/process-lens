// Builds, validates and zips the extension: the one file a coworker is handed (PL-052).
//
//   PROCESSLENS_HUB_URL=https://processlens.up.railway.app npm run package
//
// Writes apps/extension/release/processlens-<version>.zip, holding dist's contents at the zip's
// root, so unzipping gives a folder Chrome's "Load unpacked" accepts.
import { EXTENSION_ROOT, hubUrlFromEnv, packageExtension } from "./release-lib.mjs";

const hub = hubUrlFromEnv();
if (!hub.ok) {
  console.error(`package: ${hub.problem}`);
  process.exit(2);
}

packageExtension({ root: EXTENSION_ROOT, hubUrl: hub.url })
  .then((packaged) => {
    console.log(`package: ${packaged.zipPath}`);
    console.log(`package: ${packaged.entries.length} files, the manifest at the root`);
    console.log(`package: hub address ${hub.url}`);
  })
  .catch((error) => {
    console.error(`package: ${error.message}`);
    process.exit(1);
  });
