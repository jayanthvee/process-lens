// Prints the extension id the committed public key gives.
//
//   npm run extension-id
//
// The same value PROCESSLENS_EXTENSION_ID has to hold wherever ProcessLens runs: the API refuses
// any origin but this id's. Read from `extension-key.pub`, which is public and committed.
import { EXTENSION_ROOT, extensionIdFromPublicKey, readPublicKey } from "./release-lib.mjs";
import { join } from "node:path";

const publicKey = readPublicKey(join(EXTENSION_ROOT, "extension-key.pub"));
if (publicKey === null) {
  console.error(
    "extension-id: apps/extension/extension-key.pub is missing; run `npm run make-key` once and commit the public key.",
  );
  process.exit(2);
}
console.log(extensionIdFromPublicKey(publicKey));
