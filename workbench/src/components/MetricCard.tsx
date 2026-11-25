import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import type { MetricCardData } from '../types/finance'

function Sparkline({ values, favorable }: { values: number[]; favorable: boolean }) {
  if (values.length < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const range = max - min || 1
  const points = values.map((v, i) => `${(i / (values.length - 1)) * 100},${30 - ((v - min) / range) * 25}`).join(' ')
  return (
    <svg className="sparkline" viewBox="0 0 100 34" aria-hidden="true">
      <path d={`M ${points.replace(/,/g, ' ')}`} fill="none" stroke={favorable ? '#0f766e' : '#c2413d'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function MetricCard({ metric, pending = false }: { metric: MetricCardData; pending?: boolean }) {
  const Direction = metric.direction === 'up' ? ArrowUpRight : metric.direction === 'down' ? ArrowDownRight : Minus
  return (
    <article className={`metric-card ${pending ? 'is-pending' : ''}`}>
      <div className="metric-card__top">
        <span>{metric.label}</span>
        <span className={`metric-change ${metric.favorable ? 'positive' : 'negative'}`}><Direction size={14} />{metric.change}</span>
      </div>
      <strong className="metric-value" title={metric.value}>{metric.value}</strong>
      <div className="metric-formula">{metric.formula}</div>
      {metric.trend.length >= 2 && <div className="metric-card__bottom metric-trend-only"><Sparkline values={metric.trend} favorable={metric.favorable} /></div>}
    </article>
  )
}
