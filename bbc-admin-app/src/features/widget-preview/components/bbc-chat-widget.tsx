import { useRef, useCallback } from 'react'
import ChatBot, { type Flow, type Params } from 'react-chatbotify'
import './bbc-chat-widget.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export function BBCChatWidget() {
  const conversationIdRef = useRef<string | null>(null)

  const handleUserMessage = useCallback(async (params: Params) => {
    const userMessage = params.userInput

    try {
      const res = await fetch(`${API_URL}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          conversation_id: conversationIdRef.current,
          visitor: {
            name: null,
            email: null,
            phone: null,
            source: 'widget-preview',
          },
          tunnel: 'sales',
        }),
      })

      if (!res.ok) {
        return 'Sorry, I could not process your request. Please try again.'
      }

      const data = await res.json()

      if (data.conversation_id) {
        conversationIdRef.current = data.conversation_id
      }

      return data.message || 'Sorry, I did not get a response. Please try again.'
    } catch {
      return 'Connection error. Please check if the backend is running.'
    }
  }, [])

  const flow: Flow = {
    start: {
      message: 'Welcome! Where are you looking to fly in business class?',
      path: 'userMessage',
    },
    userMessage: {
      message: async (params: Params) => {
        return await handleUserMessage(params)
      },
      path: 'userMessage',
    },
  }

  return (
    <ChatBot
      flow={flow}
      settings={{
        general: {
          embedded: true,
          primaryColor: '#0B1829',
          secondaryColor: '#C9A54E',
          fontFamily: 'inherit',
        },
        header: {
          title: 'BBC Travel Assistant',
          avatar: undefined,
        },
        footer: {
          text: undefined,
        },
        chatButton: {
          icon: undefined,
        },
        notification: {
          disabled: true,
        },
        tooltip: {
          text: '',
        },
      }}
      styles={{
        headerStyle: { background: '#0B1829', color: '#fff' },
        botBubbleStyle: {
          background: '#F8F9FA',
          color: '#343A40',
          borderRadius: '12px',
          maxWidth: '80%',
        },
        userBubbleStyle: {
          background: '#0B1829',
          color: '#fff',
          borderRadius: '12px',
          maxWidth: '80%',
        },
        sendButtonStyle: { background: '#C9A54E' },
        chatWindowStyle: { background: '#fff' },
        sendButtonHoveredStyle: { background: '#b8943d' },
      }}
    />
  )
}
