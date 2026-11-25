import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/data/emptyState.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
const { createEmptySnapshot } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('initial cards contain labels/formulas but no numeric financial values or trends', () => {
  const snapshot = createEmptySnapshot()
  assert.equal(snapshot.metrics.length, 6)
  for (const metric of snapshot.metrics) {
    assert.equal(metric.value, '—')
    assert.equal(metric.change, '等待分析')
    assert.equal(metric.direction, 'flat')
    assert.equal(metric.trend.length, 0)
    assert.ok(metric.label && metric.formula)
  }
})

test('initial state has no risk conclusions, comparison rows, source facts or sample file', () => {
  const snapshot = createEmptySnapshot()
  assert.deepEqual(snapshot.risks, [])
  assert.deepEqual(snapshot.financialMetrics, [])
  assert.deepEqual(snapshot.comparison, [])
  assert.equal(snapshot.document.status, '等待分析')
  assert.equal(snapshot.document.fileHash, '—')
  assert.equal(snapshot.document.parserVersion, '—')
  assert.equal(snapshot.company, '待分析公司')
  assert.equal(snapshot.document.reportYear, '—')
  assert.ok(!source.includes('mockData'))
})

test('empty snapshots do not retain modified or loaded values across resets', () => {
  const first = createEmptySnapshot()
  first.metrics[0].value = '123.45'
  first.metrics[0].trend.push(123.45)
  first.document.facts = 99
  const second = createEmptySnapshot()
  assert.equal(second.metrics[0].value, '—')
  assert.equal(second.metrics[0].trend.length, 0)
  assert.equal(second.document.facts, 0)
})

test('default UI selects empty state, mock snapshot requires explicit demo mode', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /useState<AnalysisViewMode>\('empty'\)/)
  assert.match(app, /useState<AnalysisSnapshot>\(createEmptySnapshot\)/)
  assert.match(app, /snapshot = isDemo \? snapshots\['宁德时代'\] : isReal \? savedSnapshot : emptySnapshot/)
  assert.match(app, /autoSelectLatest=\{false\}/)
  assert.ok(!app.includes('demo={!taskId}'))
})
