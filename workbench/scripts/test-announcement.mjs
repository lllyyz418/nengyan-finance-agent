import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import ts from 'typescript'
const source = fs.readFileSync(new URL('../src/services/announcementView.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { formatAnnouncementAmount: format, announcementEventNames, announcementModelNames, announcementModelStatus } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
test('money strings preserve exact decimal digits and currency', () => {
  assert.equal(format('1500000000', 'USD'), '15亿美元')
  assert.equal(format('225000000', 'USD'), '2.25亿美元')
  assert.equal(format('123456789012345678901234567890.12', 'CNY'), '1234567890123456789012.3456789012亿元')
  assert.equal(format('0.000000000000001', 'CNY'), '0.000000000000001元')
  assert.equal(format('-10000.01', 'CNY'), '-1.000001万元')
})
test('invalid and numeric money inputs are visibly rejected', () => {
  for (const value of [null, NaN, Infinity, 225000000, 'NaN', '1e8', '']) assert.equal(format(value, 'USD'), '金额待核对')
})
test('canonical backend event types and all model states have display labels', () => {
  for (const key of ['fund_investment', 'investment_expansion', 'investment_other', 'bid_award']) assert.ok(announcementEventNames[key])
  for (const key of ['success', 'partial', 'failed', 'disabled', 'not_configured']) assert.ok(announcementModelNames[key])
})
test('offline injected providers cannot be presented as real DeepSeek acceptance',()=>{
  assert.match(announcementModelStatus({status:'success',provider_kind:'injected_test_provider'}),/未调用真实/)
  assert.match(announcementModelStatus({status:'success',provider_kind:'deepseek_api_adapter'}),/DeepSeek 已完成/)
})
test('question failure keeps source links and retry bound to the last successful result',()=>{
  const panel=fs.readFileSync(new URL('../src/components/AnnouncementPanel.tsx',import.meta.url),'utf8')
  for(const text of ['askAnnouncement(resultTaskId','getAnnouncementEvidenceUrl(resultTaskId','getTaskFileUrl(resultTaskId','current === revision.current','停止公告分析']) assert.ok(panel.includes(text),text)
  assert.ok(!panel.includes('clauses.slice(0,6)'))
})
