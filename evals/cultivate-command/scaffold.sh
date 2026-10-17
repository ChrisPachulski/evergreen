#!/usr/bin/env bash
# A git repo with one orphaned asset, one leaked local secrets file, and no .gitignore.
set -euo pipefail
git init -q .
git config user.email eval@example.invalid
git config user.name eval
mkdir -p src assets
printf 'PNG-BYTES-LOGO\n' > assets/logo.png
printf 'PNG-BYTES-OLD-HERO\n' > assets/old-hero.png
cat > src/app.js <<'J'
const logo = "assets/logo.png";
module.exports = () => `<img src="${logo}">`;
J
cat > README.md <<'J'
# tinysite

Renders the header. Run `node src/app.js`.
J
printf 'API_TOKEN=not-a-real-token-0000\n' > .env.local
git add -A
git commit -q -m "initial"
