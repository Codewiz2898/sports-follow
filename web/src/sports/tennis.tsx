import './tennis.css'
import type { BoardProps, SportView } from './view'

export interface TennisSet { games: number; tiebreak?: number | null; won?: boolean | null }
export interface TennisPlayer { id: string; name: string; short: string; country?: string | null; seed?: number | null; serving: boolean; winner?: boolean | null; sets: TennisSet[] }
export interface TennisState { kind: 'tennis'; players: TennisPlayer[]; set?: number | null; round?: string | null; tournament?: string | null }

/** One row per player, one column per set; the serving player is marked. */
function TennisBoard({ state, clock }: BoardProps<TennisState>) {
  const sets = Math.max(0, ...state.players.map((p) => p.sets.length))
  return (
    <div className="stack" style={{ gap: 8 }}>
      <table className="linescore tennis">
        <tbody>
          {state.players.map((p) => (
            <tr key={p.id} className={p.winner ? 'won' : undefined}>
              <td className="who">
                <span className="title">{p.name}</span>
                {p.seed ? <span className="tiny muted"> ({p.seed})</span> : null}
                {p.serving && <span className="serve" aria-label="serving" />}
              </td>
              {Array.from({ length: sets }, (_, i) => {
                const set = p.sets[i]
                return (
                  <td key={i} className={set?.won ? 'set-won' : undefined}>
                    {set?.games ?? ''}{set?.tiebreak != null && <sup>{set.tiebreak}</sup>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {clock && <span className="live-note">{[state.round, clock].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

export const tennis: SportView = {
  key: 'tennis',
  kinds: ['tennis'],
  Board: TennisBoard as SportView['Board'],
  inHeadline: ['Sets'],
}
