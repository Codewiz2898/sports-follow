import type { FeedItem } from '../api'
import { sourceMark } from '../feed'

/** The icon of the source a video is from (feed.ts sourceMark: YouTube's, for one of YouTube's), linked to the video
 * where it plays, in its own clear space (.source-mark), so a fan can tell where it's from. Nothing for a post, a
 * story, or a video whose source sends no icon. The Android app's SourceMark draws the same. */
export function SourceMark({ item, height, className }: { item: FeedItem; height?: number; className?: string }) {
  const shown = sourceMark(item, height)
  if (!shown) return null
  const { mark, href, width, label } = shown
  return (
    <a className={className ? `source-mark ${className}` : 'source-mark'} href={href} target="_blank" rel="noreferrer" aria-label={label} title={label}>
      <svg width={width} height={shown.height} viewBox={`0 0 ${mark.width} ${mark.height}`} aria-hidden="true">
        {mark.paths.map((p, i) => <path key={i} fill={p.fill} d={p.d} />)}
      </svg>
    </a>
  )
}
