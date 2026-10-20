// Makes the one key pair the extension's fixed id comes from (PL-052).
//
//   npm run make-key
//
// Writes the private key to `private/extension-key.pem` (gitignored, never read by a build) and
// the public key to `apps/extension/extension-key.pub` (committed: it is public, and it is what
// fixes the id). It prints the id to paste into PROCESSLENS_EXTENSION_ID.
//
// Run it once, on the owner's machine. Running it again refuses while a key already exists.
import { DEFAULT_HUB_URL, EXTENSION_ROOT, makeKey } from "./release-lib.mjs";

const command = process.env.PROCESSLENS_MAKE_KEY_COMMAND ?? "make-key";

makeKey({ root: EXTENSION_ROOT })
  .then((made) => {
    console.log(`${command}: wrote ${made.privatePath}`);
    console.log(`${command}: wrote ${made.publicPath}`);
    console.log("");
    console.log("Public key (base64, SPKI):");
    console.log(made.publicKeyBase64);
    console.log("");
    console.log(`Extension id: ${made.id}`);
    console.log("");
    console.log(`Set PROCESSLENS_EXTENSION_ID=${made.id} where ProcessLens runs.`);
    console.log("Hand out the build with `npm run package` and PROCESSLENS_HUB_URL set.");
    console.log(`(With no PROCESSLENS_HUB_URL the popup starts at ${DEFAULT_HUB_URL}.)`);
  })
  .catch((error) => {
    console.error(`${command}: ${error.message}`);
    process.exit(2);
  });
