# Installing the ProcessLens extension

The extension records a task once, then ProcessLens runs it over a spreadsheet. It comes as a zip
and installs in Chrome in about a minute. Your copy has the same id as everyone else's, so the
server recognises it.

## If you are installing it

You need Chrome (version 116 or later), and the zip your admin sent you
(`processlens-<version>-<date>-<commit>.zip`). The last two groups say which build it is: the day
it was built and the commit it came from.

1. **Unzip it** anywhere you will keep it, for example `Documents\ProcessLens`. Chrome loads the
   folder, not the zip, so if you delete the folder the extension stops working.
2. Open **chrome://extensions**.
3. Turn on **Developer mode**, top right.
4. Press **Load unpacked** and pick the folder you unzipped (the one with `manifest.json` in it).
5. The extension appears as **ProcessLens Recorder**. Press the **puzzle-piece icon** in Chrome's
   toolbar and **pin** it, so its icon stays visible.
6. **Open it** by clicking the icon. The address is already filled in — leave it as it is.
7. Press **Connect**. It says **Chrome connected** when it worked.

At the foot of the popup, in small grey text, the same build is written down
(`ProcessLens 0.1.1 (20261025 c8abcb3)`). Quote that line when reporting a problem: it names the
build you are running.

If the address field is empty, paste the ProcessLens address your admin gave you (it looks like
`https://processlens.up.railway.app`) and press Connect again.

To update: unzip the new zip over the old folder, then press the **reload** arrow on the extension's
card in `chrome://extensions`. Your saved task in ProcessLens is not affected.

## If you are the owner

### Once: make the key

The extension's id comes from a key pair, and it has to be the same for everyone. On your machine,
in the repository:

```powershell
npm --prefix apps/extension run make-key
```

It writes the private key to `private/extension-key.pem` (gitignored — **never** commit it, and
keep a backup somewhere safe) and the **public** key to `apps/extension/extension-key.pub`. Commit
the public key: it is not a secret, and it is what fixes the id. It prints the id:

```
Extension id: <32 letters, a to p>
```

Set that value as `PROCESSLENS_EXTENSION_ID` wherever ProcessLens runs (Railway's variables page).
The API refuses any extension whose origin is not this id, so everyone's copy must be built with
this same public key.

Running `make-key` again refuses while a key exists: a new key is a new id, and every copy already
handed out would be refused. `npm --prefix apps/extension run extension-id` prints the id the
committed public key gives, any time you need it.

### Each release: bump, then build the zip

```powershell
npm --prefix apps/extension run bump
$env:PROCESSLENS_HUB_URL = "https://processlens.up.railway.app"
npm --prefix apps/extension run package
```

`bump` adds one to the patch version in both `apps/extension/manifest.json` and
`apps/extension/package.json`, and prints the new version. Commit that change before packaging, so
the zip's stamp names a commit that carries its own version.

`package` builds, validates and writes
`apps/extension/release/processlens-<version>-<date>-<commit>.zip` (gitignored): the version from
the manifest, the local day, and the short commit the zip was built from. Hand that file out.
Without `PROCESSLENS_HUB_URL` the build still works, but the popup starts at
`http://127.0.0.1:8000`, which is only right on your own machine.

The built manifest also carries that stamp as `version_name`, which is what `chrome://extensions`
and the foot of the popup show. A build never changes the committed `manifest.json`.

### What the build refuses

- `PROCESSLENS_HUB_URL` that is not a bare origin (`https://host` or `https://host:port`, no path).
- A private key path that git does not ignore.
