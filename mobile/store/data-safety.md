# Data safety form

Answers for Play Console, **Policy → App content → Data safety**, from what the app and the server do
(read on 19 Oct 2026: `mobile/src/push.ts`, `api.ts`, `account.tsx`, `search.tsx`; `sports_follow/server.py`,
`models.py`, `accounts.py`, `fcm.py`, `agent.py`, `logs.py`). Google's form asks per data type whether it's
collected, shared, optional, and why; "collected" means sent off the device, even to our own server, and
"ephemeral" means used for the request and not stored.

## The short version

- **Collected:** email address (if you sign in), a user id, the players, teams and games followed with their
  alert settings, the alert log, the Android push token with its time zone and quiet hours, what you type into
  search (not kept) and into Research with AI (kept as the player's name), and your network address for up to
  two days (abuse limits).
- **Shared with others:** nothing. Sold: nothing.
- **Encrypted in transit:** yes. **Deletable:** yes, in the app and at stann.in/account.
- **Not collected:** location, contacts, photos, files, messages, calendar, health, financial, browsing
  history, crash logs, advertising id, device id, phone number, name.

## Overview questions

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all of the user data collected by your app encrypted in transit? | **Yes** (the app talks to `https://stann.in` only; `mobile/src/api.ts`) |
| Do you provide a way for users to request that their data is deleted? | **Yes**: Account → Delete account in the app, or `https://stann.in/account`. Without an account: unfollow players, teams and games and turn alerts off, then uninstall (uninstalling alone deletes nothing on the server) |
| Has your app been independently reviewed against a global security standard (MASA)? | **No** |

## Data types

For every row below: **Shared: No.** Purposes are Play's names.

| Category | Data type | Collected? | Ephemeral? | Optional? | Purposes | What it is |
|---|---|---|---|---|---|---|
| Personal info | **Email address** | Yes | No | Optional (only if you sign in) | Account management | `account.email`; sign-in codes go to it (`accounts.py`) |
| Personal info | **User IDs** | Yes | No | Required | App functionality, Account management | the random fan id in the cookie the app keeps from its first request (`server.py` `fan_id`), and the account's id if you sign in (Google's `sub` is the web app's sign-in only; the app has no Google button) |
| Personal info | Name, Address, Phone number, Race, Political or religious beliefs, Sexual orientation, Other info | No | | | | |
| Financial info | all | No | | | | |
| Health and fitness | all | No | | | | |
| Messages | all | No | | | | |
| Photos and videos | all | No | | | | |
| Audio files | all | No | | | | |
| Files and docs | all | No | | | | |
| Calendar | all | No | | | | |
| Contacts | all | No | | | | |
| App activity | **App interactions** | Yes | No | Optional (only with alerts on) | App functionality, Analytics | the log of alerts sent and whether each was opened (`notification`): the noise rules read it, and the open rate is counted from it |
| App activity | **In-app search history** | Yes | **Yes** | Optional | App functionality | what you type into search goes to the server, which asks the sources and keeps nothing; request logs drop the query string (`server.py` `RequestLog`) |
| App activity | **Other user-generated content** | Yes | No | Required | App functionality | the players, teams and games you follow, each one's alert level, muted games, quiet hours (`follow`, `team_follow`, `match_follow`, `notification_mute`, `push_subscription`); and a name typed into Research with AI, kept as that player's name and shown to every fan of them (`server.py` research, `agent.py`) |
| App activity | Other actions, Installed apps | No | | | | where the first link was opened from (`fan_source`) is the web app's only: the Android app sends no link tags (nothing in `mobile/src` calls it) |
| Web browsing | Web browsing history | No | | | | |
| App info and performance | Crash logs, Diagnostics, Other performance data | No | | | | the Android app sends no error reports (only the web app does, `web_errors.py`) and has no crash SDK |
| Device or other IDs | **Device or other IDs** (push token) | Yes | No | Optional (only with alerts on; see below) | App functionality | the FCM registration token for this phone, kept with its time zone and quiet hours (`push_subscription.endpoint`) |
| Device or other IDs | **Device or other IDs** (network address) | Yes | No | Required | Fraud prevention, security and compliance | the client's IP (an IPv6 /64), kept up to two days as a counter that limits sign-in codes, AI research, searches that reach the sources and admin sign-in tries per address (`server.py` `client_address`); never in the logs |
| Location | Approximate, Precise | No | | | | the time zone is kept for quiet hours; Google doesn't count it as location |

Notes for the form's follow-up questions:

- **"Is this data collected, shared, or both?"** Collected only, for every row. Firebase Cloud Messaging
  delivers the pushes, Resend sends the sign-in email, the data sources answer a search and OpenRouter's model
  answers Research with AI: each works on our behalf, on that request alone, which Google's form counts as a
  service provider, not sharing. Nothing goes to analytics or ads companies: the app has no analytics SDK.
- **Alerts on by default on Android 12 and older.** Those phones grant notifications at install, so the app
  registers its token, time zone and live-cards setting at its first start without a tap (`push.ts`
  `usePushRegistration`); Android 13 and later ask the fan the first time they follow someone. The row stays
  Optional because Alerts' Settings turns them off, which takes the phone off the server.
- **Firebase's own collection.** Google's
  [data disclosure page for Firebase](https://firebase.google.com/docs/android/play-data-disclosure)
  says the Messaging SDK collects the app's version and a Firebase user agent (never linked to a user or
  device), and depends on the Installations SDK, which makes a per-installation id that "does not uniquely
  identify a user or physical device". The push token row covers it; declaring Device IDs keeps us safe either
  way.
- **What's collected before you act:** the fan id (the cookie the app is given on its first request), the
  address counters, and on Android 12 and older the push token. There's no sign-up wall.
- **Account deletion** removes every row above at once (`accounts.py` delete), and daily backups are kept 14
  days: say so in the Account deletion declaration.

## When this changes

Any of these needs the form updated the same week: an analytics SDK (BHA-239 if PostHog is picked), crash
reporting in the app, Google sign-in in the app, link tags read by the app, a widget that reads location, or a
new processor. The privacy page (`web/src/pages/PrivacyPage.tsx` and its twin `mobile/src/app/privacy.tsx`)
changes with it.
