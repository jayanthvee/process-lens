// Builds, validates and zips the extension: the one file a coworker is handed (PL-052).
//
//   PROCESSLENS_HUB_URL=https://processlens-api.onrender.com PROCESSLENS_ACCESS_CODE=... npm run package
//
// Writes apps/extension/release/processlens-<version>-<date>-<commit>.zip, holding dist's contents at the zip's
// root, so unzipping gives a folder Chrome's "Load unpacked" accepts.
import { ACCESS_CODE_ENV, EXTENSION_ROOT, hubUrlFromEnv, packageExtension } from "./release-lib.mjs";

const hub = hubUrlFromEnv();
if (!hub.ok) {
  console.error(`package: ${hub.problem}`);
  process.exit(2);
}

const accessCode = String(process.env[ACCESS_CODE_ENV] ?? "").trim();
if (hub.url.startsWith("https://") && accessCode === "") {
  // A hosted ProcessLens answers nobody without the code, so a zip without it cannot connect.
  console.error(`package: set ${ACCESS_CODE_ENV} too: the hosted ProcessLens asks for it`);
  process.exit(2);
}

packageExtension({ root: EXTENSION_ROOT, hubUrl: hub.url, accessCode })
  .then((packaged) => {
    console.log(`package: ${packaged.zipPath}`);
    console.log(`package: ${packaged.entries.length} files, the manifest at the root`);
    console.log(`package: hub address ${hub.url}`);
    console.log(`package: access code ${accessCode === "" ? "none" : "included (not shown)"}`);
  })
  .catch((error) => {
    console.error(`package: ${error.message}`);
    process.exit(1);
  });
