import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import postcss from 'postcss'

const css=readFileSync(new URL('../src/roundedSurfaces.css',import.meta.url),'utf8')
const root=postcss.parse(css)
test('rounded surfaces remain screen-only without altering geometry or clipping popovers',()=>{
  root.walkRules(rule=>{
    assert.equal(rule.parent.type,'atrule')
    assert.match(rule.parent.params,/^screen(?: and|$)/)
    rule.walkDecls(declaration=>assert.match(declaration.prop,/^border(?:-(?:top|bottom)-(?:left|right))?-radius$/))
  })
  for(const selector of ['.metric-card','.risk-row','.agent-report-shell','.table-wrap','.overview-document','.control-bar','.history-record']) assert.ok(css.includes(selector),selector)
})
test('rounded theme is loaded last and expanded risk disclosures remain visually joined',()=>{
  const main=readFileSync(new URL('../src/main.tsx',import.meta.url),'utf8')
  assert.ok(main.indexOf("import './roundedSurfaces.css'")>main.indexOf("import './sectionHeadings.css'"))
  assert.match(css,/\.risk-summary\[aria-expanded="true"\]/)
  assert.match(css,/border-bottom-left-radius:0/)
})
