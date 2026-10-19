# Closed testing: the 12 testers, 14 days rule

Checked against [Google's help page](https://support.google.com/googleplay/android-developer/answer/14151465)
on 19 Oct 2026.

**The rule.** A personal developer account created after 13 November 2023 can't publish to production until
it has run a closed test with **at least 12 testers opted in continuously for 14 days**. Then the Dashboard
offers **Apply for production**: three short sections (the closed test, the app, production readiness), and
Google's review of the application "usually takes seven days or less". A tester who leaves before 14 days
doesn't count; one who opts out and back in starts their 14 days again. Google also looks at engagement: if
the testers didn't install and use the app, it can ask for more testing.

Bharath's account is a personal one made today, so this applies.

## What it means for the launch

| Day | What | Earliest |
|---|---|---|
| 0 | Account verified (Google says up to a few days; identity checks can take longer) | 20–22 Oct |
| 0 | First `.aab` uploaded to a closed track, listing and declarations filled, testers added | same day |
| 0–3 | Google reviews the closed-testing release (it's reviewed like any release) | 21–25 Oct |
| 1 | Testers opt in and install; the 14 days start when the twelfth is in | 22–26 Oct |
| 15 | Apply for production | 6–10 Nov |
| 15–22 | Google's review of the application | 13–17 Nov |
| 22 | First production release reviewed and live | 14–19 Nov |

So Play's production listing is **touch and go for Sat 14 Nov** (the LoL Worlds mini launch) and **fine for
24 Nov** (Gukesh v Sindarov, game 1), as long as the twelfth tester is in by about 26 Oct. The marketing sheet
already assumes the Play Store isn't on the path for 14 Nov: the campaign pages send Android fans to install
the web app. The closed track itself is useful before that: a tester installs from Play like any app, with
Play App Signing and App Links working, which is the dress rehearsal (BHA-244) on real phones.

## Where the 12 testers come from

Testers are Google accounts. Give Play either a list of email addresses or a Google Group; a **Google Group**
is easier (`stann-testers@googlegroups.com`: add a person, they're a tester; Google counts opted-in members).

- **Family and friends with Android phones**: the surest dozen. Ask for 15, since a few won't finish the opt-in.
- **The people already testing the vivo builds** and anyone who has installed stann.in from Chrome.
- **Colleagues** who follow cricket or football.
- **Chess and esports contacts** from the communities on the marketing sheet's list (BHA-243), as a first ask
  before the public posts: "be one of the first 12 on the Android app".
- Not from the accounts on stann.in: the privacy page says emails are sign-in codes only, so no invitation
  mail goes to signed-in fans.

Each tester has to: (1) be in the group or list, (2) open the opt-in link Play gives for the track and tap
**Become a tester** with the same Google account their phone uses, (3) install the app from the Play link, and
(4) stay opted in and keep it installed for the 14 days. Opening it and following someone is what Google
counts as engagement.

## The message to send (WhatsApp)

```
Hi! I've built an app called stann: follow any athlete, in any sport, and get an alert the moment they do something (a goal, a wicket, Gukesh winning). It's on stann.in today, and the Android app is going on the Play Store.

Google needs 12 people to test it for two weeks before it can go public. Could you be one?

It takes two minutes:
1. Join the testers group: <group link>
2. Open <opt-in link> and tap "Become a tester" (same Google account as your phone).
3. Install stann from the Play link on that page.
4. Keep it installed for two weeks, follow a player or two, and tell me anything that looks wrong.

No ads, no selling data, and you can delete everything from Account at any time. Thanks!
```

Fill in the two links from Play Console, **Testing → Closed testing → Testers**, once the release is out.

## Keep the count up

Play Console's Dashboard shows the opted-in count. Check it on day 1 and day 7; if someone drops below, ask
the next person on the list the same day, since the 14 days are continuous. Keep the testers after
production too: the closed track is where every later build goes first.
