import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'

test('unread history statistics are unknown, not a claim that archives are empty',()=>{
  const source=readFileSync(new URL('../src/components/HistoryCenter.tsx',import.meta.url),'utf8')
  assert.match(source,/statistics\s*\?\?\s*\{total:'—',completed:'—',review_required:'—',failed:'—'\}/)
  assert.match(source,/loading&&!data/)
  assert.match(source,/setData\(next\)/)
})

test('current-first public results follow the independent history task without hiding the report',()=>{
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  const panel=readFileSync(new URL('../src/components/HistoryBackfillPanel.tsx',import.meta.url),'utf8')
  assert.match(app,/applyResult\(result,task.id,true\)/)
  assert.match(panel,/本期已完成，正在建立历史补齐任务/)
  assert.match(panel,/value.pending\|\|\['queued','processing'\]/)
  assert.match(panel,/value.result_task_id!==taskId/)
})
