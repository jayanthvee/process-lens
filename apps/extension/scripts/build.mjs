// Builds the loadable extension into dist/.
//
// The content script is bundled as a classic script (content scripts are not
// ES modules); the service worker is bundled as an ES module. The side panel is
// the web app's own React entry, built by its Vite config into dist/panel/ (PL-105).
//
// A build can carry two things a handed-out copy needs:
//   - the public key in `extension-key.pub`, which fixes the extension's id (PL-052);
//   - PROCESSLENS_HUB_URL, which becomes the popup's starting address;
//   - PROCESSLENS_ACCESS_CODE, the hosted ProcessLens's team access code (PL-086).
// With neither, this is the build the e2e harness loads, unchanged.
import { ACCESS_CODE_ENV, DEFAULT_HUB_URL, buildExtension, hubUrlFromEnv } from "./release-lib.mjs";

const hub = hubUrlFromEnv();
if (!hub.ok) {
  console.error(hub.problem);
  process.exit(2);
}

buildExtension({ hubUrl: hub.url, accessCode: process.env[ACCESS_CODE_ENV] })
  .then((built) => {
    console.log(`built extension into ${built.dist}`);
    console.log(`hub address: ${built.hubUrl === DEFAULT_HUB_URL ? `${built.hubUrl} (default)` : built.hubUrl}`);
    console.log(`access code: ${built.hasAccessCode ? "included (not shown)" : "none"}`);
    console.log(
      built.extensionId === null
        ? "extension key: none, so Chrome picks the id from the folder (the e2e harness's build)"
        : `extension key: ${built.extensionId}`,
    );
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
