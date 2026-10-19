// Privacy, the web's PrivacyPage.tsx: what the app keeps about a fan, why, who else sees any of it, and how to
// delete it. The same policy as the site's (one service; this app is another way in).

import { router } from 'expo-router'
import type { ReactNode } from 'react'
import { Linking, Text } from 'react-native'
import { useAccount } from '../account'
import { t } from '../theme'
import { BackHead, Card, Section } from '../ui'
import { Page } from '../page'

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Section title={title}>
      <Card pad style={{ gap: 10 }}>{children}</Card>
    </Section>
  )
}

const P = ({ children }: { children: ReactNode }) => <Text style={[t.small, { lineHeight: 19.5 }]}>{children}</Text>

export default function PrivacyScreen() {
  const { config } = useAccount()
  const contact = config?.contact_email
  return (
    <Page>
      <BackHead title="Privacy" />
      <Part title="What we keep">
        <P>Without an account: a random id in a cookie on this device, the players you follow, how much you want to hear about each, and games you muted.</P>
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
        <P>Resend sends sign-in emails. Google, if you choose to sign in with Google. Your browser's push service (Google, Apple, Mozilla or Microsoft) delivers alerts; Google's Firebase Cloud Messaging delivers the Android app's.</P>
      </Part>
      <Part title="Deleting it">
        <P>Signed in: <Text onPress={() => router.push('/account')} style={t.link}>Account</Text>, then Delete account. Everything above goes at once, and our daily backups are kept for 14 days.</P>
        <P>Without an account: unfollow your players and turn alerts off in Alerts, then clear this site's data in your browser, or uninstall the Android app.</P>
      </Part>
      {contact ? (
        <Part title="Questions">
          <P>Write to <Text onPress={() => Linking.openURL(`mailto:${contact}`)} style={t.link}>{contact}</Text>.</P>
        </Part>
      ) : null}
    </Page>
  )
}
