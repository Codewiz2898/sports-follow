import { createContext, useContext, type ReactNode } from 'react'

/** What a failed call says, for its page; a 401 (the session ended, or a new password) also goes back to signing in (AdminApp). */
export const Failure = createContext<(error: unknown) => string>(() => '')
export const useFailure = () => useContext(Failure)

/** A short explanation at the top of a tab. */
export function Note({ children }: { children: ReactNode }) {
  return <p className="admin-note">{children}</p>
}

/** 2000 -> "2,000". */
export const count = (n: number) => n.toLocaleString()
