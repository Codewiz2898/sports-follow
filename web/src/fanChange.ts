// What the app says as the device becomes another fan: signed in, signed out, the account deleted, or
// signed out elsewhere (account.tsx starts the app over). Shared by the web app and the Android app (mobile/): no
// browser here, so tests/test_web_fan_change.py runs it in Node.

/** What the app says once a code or Google signed this device in: the players its anonymous fan brought, and how many
 * of its teams didn't fit in the account, which follows at most so many (server teams.MAX): they're gone (BUG-228). */
export function welcome(added: number, teamsDropped = 0): string {
  const players = added
    ? ` ${added === 1 ? 'The player' : `The ${added} players`} you followed on this device ${added === 1 ? 'is' : 'are'} in your account now.`
    : ''
  const teams = teamsDropped
    ? ` ${teamsDropped === 1 ? 'One team' : `${teamsDropped} teams`} you followed on this device didn't fit: your account follows as many teams as it can.`
    : ''
  return `Signed in.${players}${teams}`
}

