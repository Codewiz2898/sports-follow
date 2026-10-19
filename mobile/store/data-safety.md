# Data safety form

Answers for Play Console, **Policy → App content → Data safety**, from what the app and the server do
(read on 19 Oct 2026: `mobile/src/push.ts`, `api.ts`, `account.tsx`; `sports_follow/server.py`,
`models.py`, `accounts.py`, `fcm.py`, `web_errors.py`, `logs.py`). Google's form asks per data type whether it's
collected, shared, optional, and why; "collected" means sent off the device, even to our own server.

## The short version

- **Collected:** email address (if you sign in), a user id, the players, teams and games followed with their
  alert settings, the alert log, the Android push token with its time zone and quiet hours, where the first link
  you opened was posted, and your network address for a day or two.
- **Shared with others:** nothing. Sold: nothing.
- **Encrypted in transit:** yes. **Deletable:** yes, in the app and at stann.in/account.
- **Not collected:** location, contacts, photos, files, messages, calendar, health, financial, browsing
  history, search history, crash logs, advertising id, device id, phone number, name.

## Overview questions

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all of the user data collected by your app encrypted in transit? | **Yes** (the app talks to `https://stann.in` only; `mobile/src/api.ts`) |
| Do you provide a way for users to request that their data is deleted? | **Yes**: Account → Delete account in the app, or `https://stann.in/account`; without an account, turn alerts off and unfollow, or uninstall |
| Has your app been independently reviewed against a global security standard (MASA)? | **No** |

## Data types

For every row below: **Shared: No. Ephemeral: No.** Purposes are Play's names.

| Category | Data type | Collected? | Optional? | Purposes | What it is |
|---|---|---|---|---|---|
| Personal info | **Email address** | Yes | Optional (only if you sign in) | Account management | `account.email`; sign-in codes go to it (`accounts.py`) |
| Personal info | **User IDs** | Yes | Required | App functionality, Account management | the random fan id in the cookie the app keeps from its first request (`server.py` `fan_id`), the account's id, and Google's `sub` if you sign in with Google (web only today) |
| Personal info | Name, Address, Phone number, Race, Political or religious beliefs, Sexual orientation, Other info | No | | | |
| Financial info | all | No | | | |
| Health and fitness | all | No | | | |
| Messages | all | No | | | |
| Photos and videos | all | No | | | |
| Audio files | all | No | | | |
| Files and docs | all | No | | | |
| Calendar | all | No | | | |
| Contacts | all | No | | | |
| App activity | **App interactions** | Yes | Optional (only with alerts on) | App functionality, Analytics | the log of alerts sent and whether each was opened (`notification`): the noise rules read it, and the open rate is counted from it |
| App activity | **Other user-generated content** | Yes | Required | App functionality | the players, teams and games you follow, each one's alert level, muted games, quiet hours (`follow`, `team_follow`, `match_follow`, `notification_mute`, `push_subscription`) |
| App activity | **Other actions** | Yes | Optional | Analytics | where the first link you opened was posted (`fan_source`: a share tag, utm fields, the landing page), once |
| App activity | In-app search history, Installed apps | No | | | searches are answered and not kept; request logs drop the query string (`server.py` `RequestLog`) |
| Web browsing | Web browsing history | No | | | |
| App info and performance | Crash logs, Diagnostics, Other performance data | No | | | the Android app sends no error reports (only the web app does, `web_errors.py`) and has no crash SDK |
| Device or other IDs | **Device or other IDs** | Yes | Optional (only with alerts on) | App functionality | the FCM registration token for this phone, kept with its time zone and quiet hours (`push_subscription.endpoint`); and the network address, kept up to two days as a counter to limit AI research and error reports per address (`server.py` `client_address`, `web_errors.py`) |
| Location | Approximate, Precise | No | | | the time zone is kept for quiet hours; Google doesn't count it as location |

Notes for the form's follow-up questions:

- **"Is this data collected, shared, or both?"** Collected only, for every row. Firebase Cloud Messaging
  delivers the pushes for us, which Google's form counts as a processor, not sharing. Resend sends the sign-in
  email (processor). Nothing goes to analytics or ads companies: the app has no analytics SDK.
- **"Is this data processed ephemerally?"** No for every row: all of it is stored, if briefly (the address).
- **Firebase's own collection.** Google's
  [data disclosure page for Firebase](https://firebase.google.com/docs/android/play-data-disclosure)
  says the Messaging SDK collects the app's version and a Firebase user agent (never linked to a user or
  device), and depends on the Installations SDK, which makes a per-installation id that "does not uniquely
  identify a user or physical device". The Device IDs row above covers the registration token; the installation
  id needs no row of its own by Google's description, but declaring Device IDs already keeps us safe.
- **Nothing is collected before you act**, except the fan id (the cookie the app is given on its first
  request) and the address counters. There's no sign-up wall.
- **Account deletion** removes every row above at once (`accounts.py` delete), and daily backups are kept 14
  days: say so in the Account deletion declaration.

## When this changes

Any of these needs the form updated the same week: an analytics SDK (BHA-239 if PostHog is picked), crash
reporting in the app, Google sign-in in the app, a widget that reads location, or sharing data with a new
processor. The privacy page (`web/src/pages/PrivacyPage.tsx` and its twin `mobile/src/app/privacy.tsx`) changes
with it.
