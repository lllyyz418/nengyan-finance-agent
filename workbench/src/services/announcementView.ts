/** Announcement money is a Decimal string; never format it through Number. */
export const announcementEventNames: Record<string, string> = {
  fund_investment: '产业基金投资', investment_expansion: '投资建设／扩产',
  investment_other: '其他对外投资', bid_award: '重大工程中标／合同',
  investment: '对外投资', capacity_expansion: '扩产项目', industry_fund: '产业基金',
}
export function formatAnnouncementAmount(value: unknown, currency: unknown): string {
  if (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(value)) return '金额待核对'
  const negative = value.startsWith('-')
  const [rawWhole, fraction = ''] = (negative ? value.slice(1) : value).split('.')
  const whole = rawWhole.replace(/^0+(?=\d)/, '')
  const shift = whole.length >= 9 ? 8 : whole.length >= 5 ? 4 : 0
  const unit = shift === 8 ? '亿' : shift === 4 ? '万' : ''
  const integer = shift ? whole.slice(0, -shift) : whole
  const decimal = ((shift ? whole.slice(-shift) : '') + fraction).replace(/0+$/, '')
  const sign = negative && /[1-9]/.test(whole + fraction) ? '-' : ''
  const currencyLabel = ({ CNY: '元', USD: '美元', EUR: '欧元', HKD: '港元' } as Record<string, string>)[String(currency)] ?? String(currency ?? '')
  return `${sign}${integer}${decimal ? `.${decimal}` : ''}${unit}${currencyLabel}`
}
export const announcementModelNames: Record<string, string> = {
  success: 'DeepSeek 已完成分析并通过程序检查', partial: '语义摘录已通过，影响解释未通过检查',
  failed: '模型调用或校验失败，保留规则提取', disabled: '模型已关闭', not_configured: '未配置 DeepSeek 密钥',
  provider_initialization_failed: '模型初始化失败，请核对后端依赖', unsupported_or_no_evidence: '无支持事件或可读证据',
  budget_exhausted: '已达到分析预算，未得到通过校验的最终解释',
}

export function announcementModelStatus(meta?: { status?: string; provider_kind?: string }): string {
  if (!meta) return '历史结果未记录公告模型状态，请重新分析。'
  if (meta.provider_kind === 'injected_test_provider') return '离线模拟流程验收结果，未调用真实 DeepSeek 服务'
  return announcementModelNames[meta.status ?? ''] ?? '公告分析状态待核查，请查看运行日志。'
}
