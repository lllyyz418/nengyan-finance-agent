import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import ts from 'typescript'
const catalog=JSON.parse(readFileSync(new URL('../shared/companyCatalog.json',import.meta.url),'utf8'))
const source=readFileSync(new URL('../src/data/peerCatalog.ts',import.meta.url),'utf8').replace("import catalog from '../../shared/companyCatalog.json'",`const catalog=${JSON.stringify(catalog)}`)
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText
const {localCompanies,localCandidates,defaultPeerSelection,suggestedPeersForTarget,savedPeerSelection,canAddPeer,peerRequestMetadata,identityLabel,dataLabel,MAX_PEERS}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('suggestions follow the loaded target sector and exclude the target',()=>{
  const battery=suggestedPeersForTarget('300750')
  assert.equal(battery.peers.length,3)
  assert.ok(battery.peers.every(item=>item.code!=='300750'&&item.segment==='动力及储能电池'))
  const engineering=suggestedPeersForTarget('601868')
  assert.deepEqual(engineering.peers.map(item=>item.code),['601669'])
  assert.equal(engineering.group,'能源工程建设')
  assert.deepEqual(suggestedPeersForTarget('').peers,[])
  assert.deepEqual(suggestedPeersForTarget('999999').peers,[])
})

test('saved report restores its own peer membership, including outside-list companies',()=>{
  const selected=savedPeerSelection({payload:{peer_binding:{peer_codes:['601669','601611'],peer_group:'工程建设',selection_note:'人工确认的工程建设同行'},peers:{reports:{'601611':{documents:[{company_name:'中国核建'}]}}}}})
  assert.deepEqual(selected.peers.map(item=>item.code),['601669','601611'])
  assert.equal(selected.peers[1].name,'中国核建')
  assert.equal(selected.group,'工程建设')
  assert.equal(savedPeerSelection({payload:{}}),undefined)
})
test('55 official-directory candidates and three editable defaults',()=>{
  assert.equal(localCompanies.length,55)
  assert.equal(new Set(localCompanies.map(item=>item.code)).size,55)
  const defaults=defaultPeerSelection()
  assert.deepEqual(defaults.peers.map(item=>item.code),['300014','002074','300207'])
  assert.equal(MAX_PEERS,5)
  assert.ok(defaults.note.length>=6)
})
test('incomplete Chinese names, code prefixes, pinyin initials and old names autocomplete',()=>{
  for(const [q,code] of [['宁','300750'],['阳光','300274'],['3007','300750'],['nd','300750'],['中伟股份','300919']])assert.ok(localCandidates(q,'',[]).some(item=>item.code===code),q)
  assert.deepEqual(localCandidates('not-a-company','',[]),[])
})
test('filters exclude target and selected companies without reducing the stored catalog',()=>{
  const items=localCandidates('','动力及储能电池',['300750','300014','002074','300207'])
  assert.equal(items.length,1);assert.equal(items[0].code,'300438')
  assert.equal(localCompanies.length,55)
})
test('cannot select a duplicate, target, invalid code, or sixth peer',()=>{
  const peers=defaultPeerSelection().peers
  assert.equal(canAddPeer('300438','300750',peers),true)
  assert.equal(canAddPeer('300750','300750',peers),false)
  assert.equal(canAddPeer('300014','300750',peers),false)
  assert.equal(canAddPeer('bad','300750',peers),false)
  const five=[...peers,...localCompanies.filter(item=>['300438','002594'].includes(item.code))]
  assert.equal(canAddPeer('300037','300750',five),false)
})
test('verified company identity is not conflated with saved report data',()=>{
  const item=localCompanies[0]
  assert.equal(identityLabel(item),'目录身份已核对')
  assert.equal(dataLabel(item,2025),'2025 年 · 尚无本机可用记录')
  assert.equal(dataLabel({...item,data_status:{state:'analysis_saved',metrics:12}},2025),'2025 年 · 已保存 12 项指标')
})

test('removed reason inputs cannot block comparison or fabricate manual confirmation',()=>{
  const peers=defaultPeerSelection().peers
  const before=JSON.stringify(peers)
  const metadata=peerRequestMetadata(peers)
  assert.equal(metadata.peer_group,'动力及储能电池')
  assert.ok(metadata.selection_note.includes('未提供人工选择理由'))
  assert.ok(metadata.selection_note.includes('可比性尚需复核'))
  assert.equal(JSON.stringify(peers),before)
  assert.equal(peerRequestMetadata([]).peer_group,'人工指定同行')
  const view=readFileSync(new URL('../src/components/Comparison.tsx',import.meta.url),'utf8')
  assert.ok(!view.includes('<span>同行业务分组</span>'))
  assert.ok(!view.includes('<span>选择理由</span>'))
  assert.ok(!view.includes('selection.group.trim()'))
  assert.ok(!view.includes('selection.note.trim()'))
  const api=readFileSync(new URL('../src/services/financeApi.ts',import.meta.url),'utf8')
  assert.ok(api.includes('...peerRequestMetadata(selection.peers)'))
})

test('requested small-print elements are removed without disabling core interactions',()=>{
  const search=readFileSync(new URL('../src/components/CompanySearch.tsx',import.meta.url),'utf8')
  const charts=readFileSync(new URL('../src/components/ChartsGrid.tsx',import.meta.url),'utf8')
  const backfill=readFileSync(new URL('../src/components/HistoryBackfillPanel.tsx',import.meta.url),'utf8')
  const peers=readFileSync(new URL('../src/components/Comparison.tsx',import.meta.url),'utf8')
  for(const text of ['按代码查询报告','report-company-selection'])assert.ok(!search.includes(text))
  for(const text of ['金额统一换算为亿元','原报告当期值 · 缺失年度留空'])assert.ok(!charts.includes(text))
  for(const text of ['可同时选择最多 6 份完整年报','文件已取得不等于每项指标都可计算'])assert.ok(!backfill.includes(text))
  assert.ok(!peers.includes('默认候选不是可比性结论'))
  assert.ok(search.includes('onSelect(item)'))
  assert.ok(charts.includes('connectNulls:false'))
  assert.ok(backfill.includes("setError('一次最多补传 6 份文件')"))
})
