import { existsSync, readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import ts from 'typescript'

const load=async path=>{
  const source=readFileSync(new URL(path,import.meta.url),'utf8')
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
}
const {trendCharts,historyYears,historyValues,exactFactValue,leverageDetails,leverageTooltip}=await load('../src/data/historyCharts.ts')
const {factNames,financialLabel}=await load('../src/data/financialLabels.ts')
const fixtureUrl=new URL('../backend/data/results/99a21da61d3e4d37a4836932dc6f72a4.json',import.meta.url)
const fixture=existsSync(fixtureUrl)?JSON.parse(readFileSync(fixtureUrl,'utf8')).payload.history:undefined

test('twelve distinct charts use Chinese labels, with leverage in the last slot',()=>{
  assert.equal(trendCharts.length,12)
  assert.equal(new Set(trendCharts.map(chart=>chart.id)).size,12)
  assert.equal(trendCharts.at(-1).id,'leverage')
  for(const chart of trendCharts){
    for(const series of chart.series)assert.notEqual(financialLabel(series.code),'待补充中文名称的财务项目',series.code)
  }
})
test('all saved restatement fact names have Chinese translations',()=>{
  const checks=fixture?.comparison.restatement_checks??Object.keys(factNames).map(metric_code=>({metric_code}))
  for(const item of checks){
    assert.ok(factNames[item.metric_code],item.metric_code)
    assert.match(financialLabel(item.metric_code),/[\u4e00-\u9fff]/)
  }
})
test('legacy saved reports keep eleven charts and do not invent the new liability metric',{skip:!fixture},()=>{
  for(const chart of trendCharts.filter(chart=>chart.id!=='leverage'))assert.ok(chart.series.some(series=>historyValues(fixture,historyYears(fixture),series,chart.unit).filter(value=>value!==null).length>=2),chart.id)
  if(!fixture.comparison.series.liabilities_to_assets)assert.ok(historyValues(fixture,historyYears(fixture),trendCharts.at(-1).series[0],'%').every(value=>value===null))
})
test('missing years remain gaps; annual money is converted only once',()=>{
  const years=historyYears({requested_years:[2023,2025]})
  assert.deepEqual(years,[2023,2024,2025])
  const series={code:'revenue',source:'fact'}
  const history={comparison:{fact_series:{revenue:[{year:2023,value:'100000000',unit:'CNY'},{year:2025,value:'200000000',unit:'CNY'}]}}}
  assert.deepEqual(historyValues(history,years,series,'亿元'),[1,null,2])
  assert.deepEqual(historyValues(undefined,years,series,'亿元'),[null,null,null])
})
test('zero is valid; conflicts, duplicate versions, wrong units and nonnumbers are not',()=>{
  const series={code:'gross_margin',source:'metric'}
  const values=points=>historyValues({comparison:{series:{gross_margin:points}}},[2024],series,'%')
  assert.deepEqual(values([{year:2024,value:'0',unit:'percent'}]),[0])
  for(const value of [null,undefined,'','NaN','Infinity',true])assert.deepEqual(values([{year:2024,value,unit:'percent'}]),[null])
  assert.deepEqual(values([{year:2024,value:'2',unit:'CNY'}]),[null])
  assert.deepEqual(values([{year:2024,value:'2',unit:'percent',quality:'conflict'}]),[null])
  assert.deepEqual(values([{year:2024,value:'2',unit:'percent'},{year:2024,value:'3',unit:'percent'}]),[null])
})
test('restatement values preserve decimal precision instead of rounding differences away',()=>{
  assert.equal(exactFactValue({value:'9007199254740993.010001',unit:'CNY'}),'9,007,199,254,740,993.010001 元')
  assert.equal(exactFactValue({value:'-1234.500',unit:'CNY'}),'-1,234.5 元')
  assert.equal(exactFactValue({value:'0',unit:'CNY'}),'0 元')
  assert.match(exactFactValue(undefined),/未取得/)
})

function leverageFixture(){
  const series={},fact_series={total_assets:[],total_liabilities:[]}
  series.liabilities_to_assets=[2024,2025].map((year,index)=>({year,value:String(50+index*10),unit:'percent',document_id:`d${year}`,input_fact_ids:[`a${year}`,`l${year}`]}))
  for(const year of [2024,2025]){
    fact_series.total_assets.push({year,value:'100000000000',unit:'CNY',document_id:`d${year}`,fact_id:`a${year}`})
    fact_series.total_liabilities.push({year,value:String(year===2024?50000000000:60000000000),unit:'CNY',document_id:`d${year}`,fact_id:`l${year}`})
  }
  return {comparison:{series,fact_series,changes:[{metric_code:'liabilities_to_assets',from_year:2024,to_year:2025,status:'calculated',unit:'percentage_points',absolute_change:'10'}]}}
}
test('leverage tooltips show same-document balances in billions and changes in percentage points',()=>{
  const history=leverageFixture(),points=leverageDetails(history,[2024,2025],[50,60])
  assert.deepEqual([points[1].assets,points[1].liabilities,points[1].change],[1000,600,10])
  const tooltip=leverageTooltip(points[1])
  assert.match(tooltip,/2025 年.*合并期末口径/)
  assert.match(tooltip,/资产负债率：60 %/)
  assert.match(tooltip,/负债合计：600 亿元/)
  assert.match(tooltip,/上升 10 个百分点/)
  assert.ok(!tooltip.includes('上升 20'))
})
test('leverage details reject mismatched sources, duplicate versions and restatement differences',()=>{
  const history=leverageFixture()
  history.comparison.fact_series.total_liabilities[1].document_id='different-report'
  assert.equal(leverageDetails(history,[2024,2025],[50,60])[1].liabilities,null)
  history.comparison.changes[0]={...history.comparison.changes[0],status:'unavailable',reason_code:'RESTATED_COMPARATIVE_CONFLICT'}
  const point=leverageDetails(history,[2024,2025],[50,60])[1]
  assert.equal(point.change,null)
  assert.match(point.changeText,/口径存在差异/)
  assert.equal(leverageDetails(history,[2023,2025],[null,60])[1].change,null)
  history.comparison.fact_series.total_assets.push({...history.comparison.fact_series.total_assets[1]})
  assert.equal(leverageDetails(history,[2024,2025],[50,60])[1].assets,null)
})
