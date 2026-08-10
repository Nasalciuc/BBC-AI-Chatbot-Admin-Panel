import { ContentSection } from '../components/content-section'
import { ProfileForm } from './profile-form'

export function SettingsProfile() {
  return (
    <ContentSection
      title='Profile'
      desc='Your name and photo, as teammates see them in the panel and clients see them in the chat widget.'
    >
      <ProfileForm />
    </ContentSection>
  )
}
