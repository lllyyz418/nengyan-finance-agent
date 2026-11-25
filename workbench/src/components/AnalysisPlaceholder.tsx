import { FileSearch, type LucideIcon } from 'lucide-react'
import './analysisState.css'

export function AnalysisPlaceholder({ title, description, icon: Icon = FileSearch }: { title: string; description: string; icon?: LucideIcon }) {
  return <div className="analysis-placeholder"><span><Icon size={26}/></span><strong>{title}</strong><p>{description}</p></div>
}
