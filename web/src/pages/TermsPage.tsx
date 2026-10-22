import { Link } from 'react-router-dom'
import { useAccount } from '../account'
import { BackIcon } from '../components/Icons'
import { GOOGLE_PRIVACY, YOUTUBE_TERMS } from '../legal'
import { Part } from './PrivacyPage'

/** The terms of using the app: for now, what YouTube's API terms ask every app that shows its videos to say (its
 * Developer Policies, III.A.1), beside the privacy page. The Android app's terms.tsx is its twin. */
export function TermsPage() {
  const { config } = useAccount()
  const contact = config?.contact_email
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/" className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">Terms</h1>
      </div>

      <Part title="Videos from YouTube">
        <p>The Feed's videos come from YouTube, through the YouTube API Services, and play in YouTube's own player. By using this app you agree to be bound by the YouTube Terms of Service: <a className="link" href={YOUTUBE_TERMS} target="_blank" rel="noreferrer">youtube.com/t/terms</a>.</p>
        <p>What YouTube collects as a video plays is covered by Google's Privacy Policy: <a className="link" href={GOOGLE_PRIVACY} target="_blank" rel="noreferrer">policies.google.com/privacy</a>.</p>
      </Part>

      <Part title="Your data">
        <p>What we keep about you, why, and how to delete it: <Link to="/privacy" className="link">Privacy</Link>.</p>
      </Part>

      {contact && (
        <Part title="Questions">
          <p>Write to <a className="link" href={`mailto:${contact}`}>{contact}</a>.</p>
        </Part>
      )}
    </>
  )
}
