import type { SVGProps } from 'react'

const base = { width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true } as const

export const SearchIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
)
export const BackIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="m15 5-7 7 7 7" /></svg>
)
export const CheckIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} strokeWidth={2.6} {...p}><path d="m5 12 5 5 9-10" /></svg>
)
export const PlusIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} strokeWidth={2.6} {...p}><path d="M12 5v14M5 12h14" /></svg>
)
export const HomeIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" /></svg>
)
export const RefreshIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" /></svg>
)
export const SparkIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></svg>
)
export const ChevronIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="m9 6 6 6-6 6" /></svg>
)
export const BellIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0" /></svg>
)
export const CloseIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base} strokeWidth={2.6} {...p}><path d="M6 6l12 12M18 6 6 18" /></svg>
)

/** Faint line art behind a hero card, one per sport. */
export function SportMark({ sport, className }: { sport: string; className?: string }) {
  const paths: Record<string, string> = {
    basketball: 'M4 50h92M50 4v92M17 17c16 16 16 50 0 66M83 17c-16 16-16 50 0 66',
    tennis: 'M18 12c14 22 14 54 0 76M82 12c-14 22-14 54 0 76',
    football: 'M50 30l18 13-7 21H39l-7-21zM50 4v26M68 43l26-8M61 64l15 22M39 64 24 86M32 43 6 35',
    cricket: 'M30 10c10 26 10 54 0 80M38 12l-6 3M40 26l-7 2M41 40h-7M41 54l-7-1M40 68l-7-2M38 82l-6-3',
    chess: 'M38 30h24M44 30c0 22-8 38-14 50h40c-6-12-14-28-14-50',
  }
  return (
    <svg viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth={3.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      {sport !== 'chess' && <circle cx="50" cy="50" r="46" />}
      {sport === 'chess' && <circle cx="50" cy="20" r="10" />}
      {paths[sport] && <path d={paths[sport]} />}
    </svg>
  )
}
