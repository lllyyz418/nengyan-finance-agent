// Isolated QA build using the installed TypeScript compiler in-process.
// This avoids requiring a child esbuild service in restricted desktop sessions.
import {build} from 'vite'
import ts from 'typescript'
import {fileURLToPath} from 'node:url'
import {createRequire} from 'node:module'
import {realpathSync} from 'node:fs'

const root=fileURLToPath(new URL('../',import.meta.url))
const outDir=process.argv[2]??'artifacts/upload-error-qa/build'
await build({root,configFile:false,esbuild:false,resolve:{preserveSymlinks:true},plugins:[{
  name:'upload-qa-typescript',enforce:'pre',
  resolveId(id,importer){
    if(!importer||importer.includes('\0')||id.includes('\0')||id.startsWith('.')||id.startsWith('/')||/^[A-Za-z]:/.test(id))return
    try{return createRequire(realpathSync.native(importer)).resolve(id).replaceAll('\\','/')}catch{return null}
  },
  transform(code,id){
    if(!/\.[cm]?[jt]sx?$/.test(id))return
    code=code.replace(/\bprocess\.env\.NODE_ENV\b/g,'"production"').replace(/\bimport\.meta\.env\.VITE_API_BASE_URL\b/g,'"/api"')
    if(/\.[cm]?tsx?$/.test(id)&&!id.includes('node_modules'))code=ts.transpileModule(code,{fileName:id,compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX,sourceMap:false}}).outputText
    return {code,map:null}
  },
}],build:{outDir,emptyOutDir:false,target:'esnext',minify:false,cssMinify:false}})
