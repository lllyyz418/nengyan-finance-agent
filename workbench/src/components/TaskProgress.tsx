import { CircleAlert, LoaderCircle, Square } from 'lucide-react'
import './taskProgress.css'

export interface TaskActivity {
  kind: 'peer' | 'report'
  stage: string
  progress: number
  stopping: boolean
}

export function TaskProgress({ activity, onStop, floating = false }: {
  activity: TaskActivity; onStop: () => void; floating?: boolean;
}) {
  const progress = Math.max(0, Math.min(100, activity.progress))
  return <div className={floating ? 'task-notice-position' : 'peer-task-progress'}>
    <div className={`task-progress-card ${activity.stopping ? 'stopping' : ''}`}>
      <div className="task-progress-head"><span className="task-progress-symbol"><LoaderCircle size={23} className="spin"/></span>
        <div className="task-progress-copy" role="status" aria-live="polite"><strong>{activity.stopping ? '正在停止任务' : activity.kind === 'peer' ? '正在计算同行比较' : '正在分析公开报告'}</strong><span>{activity.stopping ? '停止请求已提交，正在等待后端确认' : activity.stage}</span></div>
        <b className="task-progress-percent">{progress}%</b>
        <button className="stop-button" disabled={activity.stopping} onClick={onStop}><Square size={15}/>{activity.stopping ? '停止中…' : activity.kind === 'peer' ? '停止同行计算' : '停止分析'}</button>
      </div>
      <div className="task-progress-track" role="progressbar" aria-label="任务阶段进度" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><span style={{width:`${progress}%`}}/></div>
    </div>
  </div>
}

export function TaskMessage({ message, onClose, duringTask = false }: { message: string; onClose: () => void; duringTask?: boolean }) {
  return <div className={`task-notice-position ${duringTask ? 'task-notice-secondary' : ''}`}><div className="task-message" role="status"><CircleAlert size={22}/><span>{message}</span><button onClick={onClose} aria-label="关闭提示">×</button></div></div>
}
