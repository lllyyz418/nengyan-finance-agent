import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import postcss from 'postcss'

const read=file=>readFileSync(new URL(file,import.meta.url),'utf8')
const css=read('../src/sectionHeadings.css')
const root=postcss.parse(css)

test('module divider theme is loaded after existing typography and is screen-only',()=>{
  const entry=read('../src/main.tsx')
  assert.ok(entry.indexOf("import './sectionHeadings.css'")>entry.indexOf("import './readability.css'"))
  root.walkRules(rule=>{
    assert.ok(rule.selector.startsWith('.content .section-block'),rule.selector)
    assert.equal(rule.parent.type,'atrule')
    assert.match(rule.parent.params,/^screen(?: and|$)/)
  })
})

test('all ten existing module headings keep their contents and share the divider theme',()=>{
  const app=read('../src/App.tsx')
  assert.equal((app.match(/className="section-heading"/g)||[]).length,10)
  assert.match(css,/min-height:60px/)
  assert.match(css,/margin-bottom:18px/)
  assert.match(css,/margin-top:32px/)
  assert.match(css,/linear-gradient\(110deg,#0d293b 0%,#124556 42%,#1b616b 74%,#287e78 100%\)/)
  assert.match(css,/border-radius:18px/)
  assert.match(css,/color:#f4fafc/)
  assert.match(css,/\.risk-count \{ color:#ffc1b6/)
})

test('narrow screens wrap status below the title instead of hiding or truncating it',()=>{
  const mobile=root.nodes.find(node=>node.type==='atrule'&&node.params.includes('max-width:760px'))
  assert.ok(mobile)
  const styles=mobile.toString()
  assert.match(styles,/flex-wrap:wrap/)
  assert.match(styles,/flex:1 1 100%/)
  assert.match(styles,/max-width:100%/)
  assert.doesNotMatch(styles,/display:none|text-overflow|overflow:hidden/)
  assert.doesNotMatch(read('../src/components/researchReport.css'),/#agent-report>\.section-heading/)
})
