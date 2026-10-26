# Installing the ProcessLens extension

The extension records a task once, then ProcessLens runs it over a spreadsheet. It comes as a zip
and installs in Chrome in about a minute. Your copy has the same id as everyone else's, so the
server recognises it.

## If you are installing it

You need Chrome (version 116 or later), and the zip your admin sent you
(`processlens-<version>.zip`).

1. **Unzip it** anywhere you will keep it, for example `Documents\ProcessLens`. Chrome loads the
   folder, not the zip, so if you delete the folder the extension stops working.
2. Open **chrome://extensions**.
3. Turn on **Developer mode**, top right.
4. Press **Load unpacked** and pick the folder you unzipped (the one with `manifest.json` in it).
5. The extension appears as **ProcessLens Recorder**. Press the **puzzle-piece icon** in Chrome's
   toolbar and **pin** it, so its icon stays visible.
6. **Open it** by clicking the icon. The ProcessLens panel opens beside the page you are working
   in.
7. Press **Connect** in the line at the top of the panel. It says **Connected** when it worked.

There is no address to type: the copy your admin sent already knows the ProcessLens address.

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

### Each release: build the zip

```powershell
$env:PROCESSLENS_HUB_URL = "https://processlens.up.railway.app"
npm --prefix apps/extension run package
```

It builds, validates and writes `apps/extension/release/processlens-<version>.zip` (gitignored).
Hand that file out. Without `PROCESSLENS_HUB_URL` the build still works, but the panel starts at
`http://127.0.0.1:8000`, which is only right on your own machine.

Bump `version` in `apps/extension/manifest.json` for each release, so a person can see which one
they have on `chrome://extensions`.

### What the build refuses

- `PROCESSLENS_HUB_URL` that is not a bare origin (`https://host` or `https://host:port`, no path).
- A private key path that git does not ignore.
