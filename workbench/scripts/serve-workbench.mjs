// Serve the compiled local workbench; proxy API traffic to the existing backend.
// No public bind, separate model service, or automatic port switching.
import {preview} from 'vite'
import {fileURLToPath} from 'node:url'
const root=fileURLToPath(new URL('../',import.meta.url))
const server=await preview({root,configFile:false,build:{outDir:'dist'},
  preview:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:8001',changeOrigin:true}}}})
server.printUrls()
