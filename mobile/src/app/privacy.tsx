// Privacy, the web's PrivacyPage.tsx: what the app keeps about a fan, why, who else sees any of it, and how to
// delete it. The same policy as the site's (one service; this app is another way in).

import { router } from 'expo-router'
import { Linking, Text } from 'react-native'
import { GOOGLE_PRIVACY } from '../../../web/src/legal'
import { useAccount } from '../account'
import { t } from '../theme'
import { BackHead } from '../ui'
import { Page } from '../page'
import { P, Part } from '../policy'

export default function PrivacyScreen() {
  const { config } = useAccount()
  const contact = config?.contact_email
  return (
    <Page>
      <BackHead title="Privacy" />
      <Part title="What we keep">
        <P>Without an account: a random id in a cookie on this device, the players, teams and games you follow, how much you want to hear about each, the sports you said you watch, and games you muted.</P>
        <P>With an account: your email address (and your Google account's id if you sign in with Google), when you last signed in, and the same things as above, so they're on every device you sign in on.</P>
        <P>If you turn alerts on: the address your browser gives us to deliver them (in the Android app, the token Google's Firebase gives the app for this phone), your time zone and quiet hours, whether the app shows live scores on your lock screen, and a log of the alerts we sent (to keep sound for the big moments, and to count how many are opened).</P>
        <P>If the first link you opened here says where it was posted (a tag on it, like whatsapp or share): that tag and the page it opened, once, to count which posts bring fans.</P>
        <P>If something goes wrong in the app: a report of it (the page, the error and where in the app's code it happened), kept with our server's logs, which name you only by a code made from your random id.</P>
        <P>Your network address is counted for a day or two to limit sign-in codes and AI research, and for an hour to limit error reports, so one person can't use them up for everyone.</P>
      </Part>
      <Part title="What we don't do">
        <P>No ads, no selling or sharing your data, no tracking you on other sites. Emails are only sign-in codes.</P>
      </Part>
      <Part title="Who else handles it">
        <P>Resend sends sign-in emails. Google, if you choose to sign in with Google. Your browser's push service (Google, Apple, Mozilla or Microsoft) delivers alerts; Google's Firebase Cloud Messaging delivers the Android app's. OpenRouter, and the model it picks, reads the name you type into Research with AI.</P>
      </Part>
      <Part title="Videos from YouTube">
        <P>The Feed's videos are YouTube's: we find them and check them through the YouTube API Services, and our requests to them name no fan. A video plays in YouTube's own embedded player, which loads only once the video plays (when you tap it, or as it comes on screen if videos play as you scroll). The player, and the video's picture before it, come from YouTube's servers, and YouTube's player may set cookies and collect data on your device as Google's Privacy Policy says: <Text onPress={() => Linking.openURL(GOOGLE_PRIVACY)} style={t.link}>policies.google.com/privacy</Text>.</P>
        <P>Watching them, you agree to YouTube's Terms of Service: see <Text onPress={() => router.push('/terms')} style={t.link}>Terms</Text>.</P>
      </Part>
      <Part title="Live streams">
        <P>A game's Watch live plays its broadcast in Twitch's, Kick's or YouTube's own player, which loads only once you tap play, and may set cookies and collect data on your device as that platform's privacy policy says. Before that, the match page shows the platform's picture of it (a live Twitch channel's latest frame, a YouTube video's thumbnail), which your device loads from the platform's servers.</P>
      </Part>
      <Part title="Deleting it">
        <P>Signed in: <Text onPress={() => router.push('/account')} style={t.link}>Account</Text>, then Delete account. Everything above goes at once, and our daily backups are kept for 14 days.</P>
        <P>Without an account: unfollow your players, teams and games, untick your sports in Following, and turn alerts off in Alerts, then clear this site's data in your browser, or uninstall the Android app.</P>
      </Part>
      {contact ? (
        <Part title="Questions">
          <P>Write to <Text onPress={() => Linking.openURL(`mailto:${contact}`)} style={t.link}>{contact}</Text>.</P>
        </Part>
      ) : null}
    </Page>
  )
}
