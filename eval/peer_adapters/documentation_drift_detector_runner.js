'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

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
  const runtime = path.resolve(process.argv[3]);
  const {AnalyzerFactory} = require(path.join(runtime, 'out', 'analyzers', 'AnalyzerFactory.js'));
  const {DocumentationIndex} = require(path.join(
    runtime, 'out', 'scanners', 'DocumentationIndex.js'
  ));
  const {DocumentationDriftDetector} = require(path.join(
    runtime, 'out', 'services', 'DocumentationDriftDetector.js'
  ));
  const factory = new AnalyzerFactory();
  const detector = new DocumentationDriftDetector();
  const input = await readInput();
  const rows = [];
  for (const row of input.rows) {
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'evergreen-doc-drift-'));
    try {
      const source = 'source.ts';
      fs.writeFileSync(path.join(workspace, source), row.code, {encoding: 'utf8', flag: 'wx'});
      fs.writeFileSync(
        path.join(workspace, 'README.md'), row.documentation, {encoding: 'utf8', flag: 'wx'}
      );
      const analyzer = factory.getAnalyzer(source);
      if (!analyzer || typeof analyzer.analyze !== 'function') {
        throw new Error('pinned analyzer API is unavailable');
      }
      const analysis = analyzer.analyze(row.code, source);
      const index = await new DocumentationIndex(
        workspace, undefined, {scanPaths: ['README.md']}
      ).build();
      const report = detector.detect(analysis, index);
      if (!report || !Array.isArray(report.findings)) {
        throw new Error('pinned detector API returned an invalid report');
      }
      rows.push({
        opaque_id: row.opaque_id,
        decision: report.findings.length === 0 ? 'consistent' : 'inconsistent',
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
