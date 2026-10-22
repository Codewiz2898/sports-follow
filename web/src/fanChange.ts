// What the app says and forgets as the device becomes another fan: signed in, signed out, the account deleted, or
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

/**
 * What a device keeps of the fan it is, each under the key of the part of the app that keeps it: the fan's last look
 * at Alerts (activity.tsx), at the Feed tab and at each player's circle (feedSeen.ts), the address a sign-in code
 * went to (the account page), the save nudge they put away (Today) and that the link they came by was told
 * (visit.ts). Forgotten as the device becomes another fan, so the next person on it isn't shown the last one's
 * (BUG-169), is offered the nudge, and is counted as coming by the link they open (BUG-281). The web keeps them in
 * localStorage, the Android app in AsyncStorage.
 */
export const WEB_KEPT = ['sf-alerts-seen', 'sf-feed-opened', 'sf-feed-seen', 'sf-signin', 'sf-save-nudge', 'sf-source-noted']
export const PHONE_KEPT = ['stann.alerts-seen', 'stann.feed-opened', 'stann.feed-seen', 'stann.signin', 'stann.save-nudge', 'stann.source-noted']
