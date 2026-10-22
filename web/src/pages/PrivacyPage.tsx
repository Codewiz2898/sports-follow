import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useAccount } from '../account'
import { SectionHead } from '../components/Blocks'
import { BackIcon } from '../components/Icons'
import { GOOGLE_PRIVACY } from '../legal'

/** What the app keeps about a fan, why, who else sees any of it, and how to delete it. */
export function PrivacyPage() {
  const { config } = useAccount()
  const contact = config?.contact_email
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/" className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">Privacy</h1>
      </div>

      <Part title="What we keep">
        <p>Without an account: a random id in a cookie on this device, the players, teams and games you follow, how much you want to hear about each, the sports you said you watch, and games you muted.</p>
        <p>With an account: your email address (and your Google account's id if you sign in with Google), when you last signed in, and the same things as above, so they're on every device you sign in on.</p>
        <p>If you turn alerts on: the address your browser gives us to deliver them (in the Android app, the token Google's Firebase gives the app for this phone), your time zone and quiet hours, whether the app shows live scores on your lock screen, and a log of the alerts we sent (to keep sound for the big moments, and to count how many are opened).</p>
        <p>If the first link you opened here says where it was posted (a tag on it, like whatsapp or share): that tag and the page it opened, once, to count which posts bring fans.</p>
        <p>If something goes wrong in the app: a report of it (the page, the error and where in the app's code it happened), kept with our server's logs, which name you only by a code made from your random id.</p>
        <p>Your network address is counted for a day or two to limit sign-in codes and AI research, and for an hour to limit error reports, so one person can't use them up for everyone.</p>
      </Part>

      <Part title="What we don't do">
        <p>No ads, no selling or sharing your data, no tracking you on other sites. Emails are only sign-in codes.</p>
      </Part>

      <Part title="Who else handles it">
        <p>Resend sends sign-in emails. Google, if you choose to sign in with Google. Your browser's push service (Google, Apple, Mozilla or Microsoft) delivers alerts; Google's Firebase Cloud Messaging delivers the Android app's. OpenRouter, and the model it picks, reads the name you type into Research with AI.</p>
      </Part>

      <Part title="Videos from YouTube">
        <p>The Feed's videos are YouTube's: we find them and check them through the YouTube API Services, and our requests to them name no fan. A video plays in YouTube's own embedded player, which loads only once the video plays (when you tap it, or as it comes on screen if videos play as you scroll). The player, and the video's picture before it, come from YouTube's servers, and YouTube's player may set cookies and collect data on your device as Google's Privacy Policy says: <a className="link" href={GOOGLE_PRIVACY} target="_blank" rel="noreferrer">policies.google.com/privacy</a>.</p>
        <p>Watching them, you agree to YouTube's Terms of Service: see <Link to="/terms" className="link">Terms</Link>.</p>
      </Part>

      <Part title="Deleting it">
        <p>Signed in: <Link to="/account" className="link">Account</Link>, then Delete account. Everything above goes at once, and our daily backups are kept for 14 days.</p>
        <p>Without an account: unfollow your players, teams and games, untick your sports in Following, and turn alerts off in Alerts, then clear this site's data in your browser, or uninstall the Android app.</p>
      </Part>

      {contact && (
        <Part title="Questions">
          <p>Write to <a className="link" href={`mailto:${contact}`}>{contact}</a>.</p>
        </Part>
      )}
    </>
  )
}

export function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="section" aria-label={title}>
      <SectionHead title={title} />
      <div className="card card-pad stack small" style={{ gap: 10, lineHeight: 1.5 }}>{children}</div>
    </section>
  )
}
