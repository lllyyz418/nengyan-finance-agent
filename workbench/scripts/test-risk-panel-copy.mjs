import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'

test('risk details omit repeated disclaimer but preserve conditions and evidence',()=>{
  const source=readFileSync(new URL('../src/components/RiskPanel.tsx',import.meta.url),'utf8')
  assert.ok(!source.includes('方向性复核信号，不构成风险定论'))
  for(const retained of ['risk.trigger','risk.details','risk.missing','onEvidence','aria-expanded'])assert.ok(source.includes(retained))
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  assert.ok(app.includes('组合规则的方向性筛查，不替代审计和投资判断'))
})
