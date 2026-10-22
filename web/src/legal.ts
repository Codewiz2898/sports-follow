// The policies the privacy and terms pages link to, shared by the web app (pages/PrivacyPage.tsx, pages/TermsPage.tsx)
// and the Android app's twins (mobile/src/app/privacy.tsx, terms.tsx). YouTube's API terms ask every app that shows its
// videos to link both (its Developer Policies, III.A.1 and III.A.2).

/** YouTube's Terms of Service, which a fan agrees to by using the app. */
export const YOUTUBE_TERMS = 'https://www.youtube.com/t/terms'

/** Google's Privacy Policy, which covers what YouTube's player collects as a video plays. */
export const GOOGLE_PRIVACY = 'https://policies.google.com/privacy'
