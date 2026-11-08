import type { ComponentChildren } from 'preact'
import brand from './config'
import { IconClose, IconPhone } from './icons'

interface Props {
  /** The line under the company name. */
  subtitle: string
  /** Hidden in the CRM iframe, where the panel is always open. */
  onClose?: () => void
  closeLabel?: string
  /** The form's intro (title and one sentence), drawn on the same navy as the header. */
  children?: ComponentChildren
  titleId?: string
}

/** The panel's top: the site header's navy gradient with the mark, the name, a call button and close. */
export function PanelTop({ subtitle, onClose, closeLabel = 'Close', children, titleId }: Props) {
  const tel = `tel:+${brand.contactPhone.replace(/\D/g, '')}`
  return (
    <div class="bw-top">
      <div class="bw-head">
        <img class="bw-head__mark" src={brand.logoUrl} alt="" width={40} height={40} draggable={false} />
        <div class="bw-head__text">
          <div class="bw-head__name" id={titleId}>{brand.name}</div>
          <div class="bw-head__sub">{subtitle}</div>
        </div>
        <div class="bw-head__actions">
          <a class="bw-iconbtn bw-iconbtn--call" href={tel} aria-label={`Call ${brand.contactPhone}`} title={`Call ${brand.contactPhone}`}>
            <span class="bw-call"><IconPhone /></span>
          </a>
          {onClose && (
            <button type="button" class="bw-iconbtn" onClick={onClose} aria-label={closeLabel} title={closeLabel}>
              <IconClose />
            </button>
          )}
        </div>
      </div>
      {children}
    </div>
  )
}
