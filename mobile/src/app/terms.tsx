// Terms, the web's TermsPage.tsx: for now, what YouTube's API terms ask every app that shows its videos to say (its
// Developer Policies, III.A.1), beside the privacy page. The same terms as the site's (one service; this app is
// another way in).

import { router } from 'expo-router'
import { Linking, Text } from 'react-native'
import { GOOGLE_PRIVACY, YOUTUBE_TERMS } from '../../../web/src/legal'
import { useAccount } from '../account'
import { Page } from '../page'
import { t } from '../theme'
import { BackHead } from '../ui'
import { P, Part } from '../policy'

export default function TermsScreen() {
  const { config } = useAccount()
  const contact = config?.contact_email
  return (
    <Page>
      <BackHead title="Terms" />
      <Part title="Videos from YouTube">
        <P>The Feed's videos come from YouTube, through the YouTube API Services, and play in YouTube's own player. By using this app you agree to be bound by the YouTube Terms of Service: <Text onPress={() => Linking.openURL(YOUTUBE_TERMS)} style={t.link}>youtube.com/t/terms</Text>.</P>
        <P>What YouTube collects as a video plays is covered by Google's Privacy Policy: <Text onPress={() => Linking.openURL(GOOGLE_PRIVACY)} style={t.link}>policies.google.com/privacy</Text>.</P>
      </Part>
      <Part title="Your data">
        <P>What we keep about you, why, and how to delete it: <Text onPress={() => router.push('/privacy')} style={t.link}>Privacy</Text>.</P>
      </Part>
      {contact ? (
        <Part title="Questions">
          <P>Write to <Text onPress={() => Linking.openURL(`mailto:${contact}`)} style={t.link}>{contact}</Text>.</P>
        </Part>
      ) : null}
    </Page>
  )
}
