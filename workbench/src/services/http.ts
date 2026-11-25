import type { UploadFailure } from '../types/finance'

export class RequestError extends Error {
  constructor(message:string,public status:number,public issue?:UploadFailure){super(message);this.name='RequestError'}
}

export async function requestJson<T>(url:string,init:RequestInit={},options:{timeoutMs?:number;timeoutMessage?:string}={}):Promise<T> {
  const timeoutMs=options.timeoutMs??(init.method&&init.method!=='GET'?0:15000)
  const controller=new AbortController(),cancel=()=>controller.abort(init.signal?.reason)
  if(init.signal?.aborted)cancel()
  init.signal?.addEventListener('abort',cancel,{once:true})
  const timer=timeoutMs>0?window.setTimeout(()=>controller.abort(new Error(options.timeoutMessage??'读取超时，请稍后重试；后台任务不一定已停止，请勿重复提交分析。')),timeoutMs):undefined
  try {
    const response=await fetch(url,{...init,signal:controller.signal})
    const body=await response.json().catch(()=>({}))
    if(controller.signal.aborted)throw controller.signal.reason
    if(!response.ok){
      const detail=body.detail
      const issue=detail&&typeof detail==='object'&&!Array.isArray(detail)&&
        ['code','title','message','suggestion'].every(key=>typeof detail[key]==='string')&&typeof detail.retryable==='boolean'?detail as UploadFailure:undefined
      throw new RequestError(issue?`${issue.title}：${issue.message}`:typeof detail==='string'?detail:typeof body.message==='string'?body.message:`请求失败（${response.status}）`,response.status,issue)
    }
    return body as T
  } catch(cause) {
    if(controller.signal.aborted)throw controller.signal.reason
    if(cause instanceof TypeError)throw new Error('无法连接后端，请确认后端正在运行后重试；已有历史文件不会丢失。')
    throw cause
  } finally {
    if(timer!==undefined)window.clearTimeout(timer)
    init.signal?.removeEventListener('abort',cancel)
  }
}

