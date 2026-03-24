import { useState } from 'react'
import { FloatingButtons } from './FloatingButtons'
import { TunnelForm } from './TunnelForm'
import { ChatWindow } from './ChatWindow'

type Step = 'buttons' | 'form' | 'chat'

export default function WidgetEmbed() {
  const [step, setStep] = useState<Step>('buttons')
  const [tunnel, setTunnel] = useState<'sales' | 'support'>('sales')
  const [visitor, setVisitor] = useState<{ name?: string; email?: string; phone?: string }>({})
  const [metadata, setMetadata] = useState<{ booking_id?: string }>({})

  const handleTunnelSelect = (t: 'sales' | 'support') => {
    setTunnel(t)
    setStep('form')
  }

  const handleFormSubmit = (data: { name?: string; email?: string; phone?: string; booking_id?: string }) => {
    setVisitor({ name: data.name, email: data.email, phone: data.phone })
    if (data.booking_id) setMetadata({ booking_id: data.booking_id })
    setStep('chat')
  }

  const handleBack = () => setStep('buttons')
  const handleCloseChat = () => {
    setStep('buttons')
    setVisitor({})
    setMetadata({})
  }

  return (
    <div style={{ position: 'relative', width: '100vw', height: '100vh' }}>
      {step === 'buttons' && <FloatingButtons onSelect={handleTunnelSelect} />}
      {step === 'form' && <TunnelForm tunnel={tunnel} onSubmit={handleFormSubmit} onBack={handleBack} />}
      {step === 'chat' && <ChatWindow tunnel={tunnel} visitor={visitor} metadata={metadata} onClose={handleCloseChat} />}
    </div>
  )
}
