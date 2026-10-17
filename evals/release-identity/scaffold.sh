#!/usr/bin/env bash
# A package whose generated API header disagrees with the manifest; the badge and install example
# describe the latest published release and cannot be settled from checked-in evidence.
set -euo pipefail
cat > package.json <<'J'
{
  "name": "example-tool",
  "version": "1.4.0",
  "bin": { "example-tool": "src/cli.js" },
  "scripts": { "docs": "node scripts/gen-api.js" }
}
J
mkdir -p src scripts docs
cat > src/cli.js <<'J'
#!/usr/bin/env node
const { version } = require("../package.json");
if (process.argv.includes("--version")) { console.log(version); process.exit(0); }
console.log("example-tool: nothing to do");
J
cat > scripts/gen-api.js <<'J'
// Writes docs/api.html labelled with the CURRENT SOURCE version from package.json.
const fs = require("fs");
const { version } = require("../package.json");
fs.writeFileSync("docs/api.html", `<h1>Example Tool API ${version}</h1>\n<p>Generated; do not hand-edit.</p>\n`);
J
cat > docs/api.html <<'J'
<h1>Example Tool API 1.3.2</h1>
<p>Generated; do not hand-edit.</p>
J
cat > README.md <<'J'
# example-tool

![latest published release: 1.3.2](https://example.invalid/version-badge.svg)

## Install

```console
$ npm install -g example-tool
$ example-tool --version
1.3.2
```

## API docs

`npm run docs` regenerates `docs/api.html` from the current source version.
J
