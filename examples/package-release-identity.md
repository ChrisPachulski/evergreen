# Package release identity

This self-contained example uses only checked-in evidence. It does not query a registry or deployed
site, and it distinguishes current source from the latest published release.

## One package stream

### `package.json` — current source

The manifest owns the current-source version:

```json
{
  "name": "example-tool",
  "version": "1.4.0"
}
```

### CLI version source — current source

The CLI's contract is to report the manifest version, so the manifest also owns this output:

```javascript
const { version } = require("./package.json");
console.log(version);
```

### CLI version output — current source

```text
1.4.0
```

### Registry badge — latest published release

The badge promises the latest published registry release, not the current source:

```markdown
![latest published release: 1.3.2](https://example.invalid/version-badge.svg)
```

The registry badge may be correct while `1.4.0` remains unreleased, if direct registry evidence
confirms `1.3.2`. This checked-in example alone cannot provide that confirmation.

### Installed-command example — latest published release

The README's installed-command output promises the latest published registry release:

```console
$ example-tool --version
1.3.2
```

### Generated API header — current source

The API generator is configured to label current-source documentation, so its generated header is
owned by the manifest version:

```html
<h1>Example Tool API 1.3.2</h1>
```

### Deployed docs label — latest published release

The deployed site label promises the latest published release:

```text
Documentation for latest published release: 1.3.2
```

Expected: release_identity_drift — the generated API header claims current source but disagrees
with the `1.4.0` manifest. Fix its generator input and regenerate; do not hand-edit the output.

Expected: external release state unverified — no registry or deployed docs endpoint was queried,
so the badge, install command, and deployed label cannot prove which version is public.

## Independent package

A workspace plugin has its own manifest and release policy. It is an independent release stream;
the `1.4.0` correction does not justify changing the plugin's version.
