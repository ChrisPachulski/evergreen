'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const EXTENSIONS = Object.freeze({
  go: 'go',
  java: 'java',
  python: 'py',
  typescript: 'ts',
});

function readInput() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on('data', (chunk) => chunks.push(chunk));
    process.stdin.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch (error) {
        reject(error);
      }
    });
    process.stdin.on('error', reject);
  });
}

async function main() {
  const checkout = path.resolve(process.argv[2]);
  const {detectDocsDrift} = require(path.join(
    checkout, 'src', 'detectors', 'docsDrift.js'
  ));
  if (typeof detectDocsDrift !== 'function') {
    throw new Error('pinned drift-guardian API is unavailable');
  }
  const input = await readInput();
  const rows = [];
  for (const row of input.rows) {
    const extension = EXTENSIONS[row.language];
    if (!extension) throw new Error('unsupported language reached runner');
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'evergreen-drift-guardian-'));
    try {
      const source = `source.${extension}`;
      fs.writeFileSync(path.join(workspace, source), row.code, {encoding: 'utf8', flag: 'wx'});
      fs.writeFileSync(
        path.join(workspace, 'README.md'), row.documentation, {encoding: 'utf8', flag: 'wx'}
      );
      const findings = await detectDocsDrift({
        repoRoot: workspace,
        changedFiles: [{path: source}],
        config: {
          docsDrift: {
            enabled: true,
            codeFiles: [source],
            docFiles: ['README.md'],
            extract: ['function-signatures'],
            fullScan: false,
            fullScanMaxFiles: 200,
            payloadKeysAllowlist: [],
            maxDocChars: 20000,
            maxEntities: 200,
          },
          logicDrift: {enabled: false, rules: []},
          llm: {enabled: false},
          output: {
            format: 'github-comment',
            severity: {docsDrift: 'warning', logicDrift: 'error'},
            failOnError: true,
          },
        },
        llm: null,
      });
      if (!Array.isArray(findings)) throw new Error('pinned API returned an invalid result');
      rows.push({
        opaque_id: row.opaque_id,
        decision: findings.length === 0 ? 'consistent' : 'inconsistent',
      });
    } finally {
      fs.rmSync(workspace, {recursive: true, force: true});
    }
  }
  process.stdout.write(JSON.stringify({
    schema_version: 1,
    kind: 'evergreen-peer-decisions',
    input_sha256: input.input_sha256,
    rows,
  }));
}

main().catch(() => {
  process.exitCode = 1;
});
