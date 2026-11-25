import { useEffect } from 'react'

/** Track the section under the sticky header without scrolling the document again. */
export function useScrollNavigation(items: { id: string; label: string }[], onChange: (label: string) => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return
    let frame = 0
    const sections = [...new Set(items.map((item) => item.id))]
      .map((id) => document.getElementById(id)).filter((item): item is HTMLElement => !!item)
    const update = () => {
      frame = 0
      const ordered = sections.map((element) => ({ element, top: element.getBoundingClientRect().top }))
        .sort((a, b) => a.top - b.top)
      const anchor = (document.querySelector('.topbar')?.getBoundingClientRect().bottom ?? 58) + 100
      let section = ordered[0]?.element
      for (const entry of ordered) if (entry.top <= anchor) section = entry.element
      if (window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        section = ordered[ordered.length - 1]?.element
      }
      const item = items.find((entry) => entry.id === section?.id)
      if (item) onChange(item.label)
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update) }
    const observer = new ResizeObserver(schedule)
    sections.forEach((section) => observer.observe(section))
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    schedule()
    return () => {
      cancelAnimationFrame(frame); observer.disconnect()
      window.removeEventListener('scroll', schedule); window.removeEventListener('resize', schedule)
    }
  }, [items, onChange, enabled])
}
