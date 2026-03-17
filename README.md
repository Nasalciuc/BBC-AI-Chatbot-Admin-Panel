# BBC AI Chatbot — Admin Panel + Backend

Full-stack AI chatbot system for [BuyBusinessClass.com](https://buybusinessclass.com).
Captures leads through intelligent conversation, manages them via admin panel.

## Live URLs
- **Admin Panel:** https://admin-panel-error.vercel.app
- **Backend API:** https://admin-panel-error-production.up.railway.app
- **Chat Endpoint:** POST /api/chat (public, no auth)
- **Widget:** /widget-preview (admin) | /widget-embed (iframe)

## Architecture
```
buybusinessclass.com → iframe /widget-embed → POST /api/chat → Railway (FastAPI)
                                                                    ↓
                                                              8-Step AI Pipeline
                                                          Intent → KB (Qdrant) → Template/AI
                                                                    ↓
                                                            Supabase (9 tables)
```

## Stack
| Layer | Technology | Cost |
|-------|-----------|------|
| Frontend | React 19 + TanStack Router + shadcn/ui | Vercel $0 |
| Backend | FastAPI + Python 3.11 | Railway $5/mo |
| Database | Supabase PostgreSQL (9 tables, RLS) | Free $0 |
| Vector Search | Qdrant Cloud (MiniLM 384d, server-side) | Free $0 |
| AI | Claude Haiku + Sonnet, 33 templates | ~$3-8/mo |
| **Total** | | **$8-13/mo** |

## Features
- 8-step AI pipeline: receive → intent → entity → KB → template/AI → validate → lead → save
- 13 intent categories (regex + Haiku classifier)
- 33 template responses (~85% of messages at $0 cost)
- Qdrant semantic search with server-side MiniLM embedding
- Automatic lead scoring: Gold (80+) / Silver (50-79) / Bronze (0-49)
- Lead detail drawer with full conversation history
- Dual tunnel: Sales (capture leads) + Support (resolve issues)
- Auto-summarization every 5 messages
- Budget guards: $0.50/conversation, $50/day
- Rate limiting: 15 burst, 100/hr, 300/day per IP

## Repository Structure
```
BBC-AI-Chatbot-Admin-Panel/       (monorepo)
├── bbc-admin-app/                React 19 frontend (Vercel)
├── bbc-chatbot-api/              FastAPI backend (Railway)
├── docs/                         Documentation
└── README.md                     This file
```

## Quick Start
```bash
# Frontend
cd bbc-admin-app && npm install && npm run dev
# → http://localhost:5173

# Backend
cd bbc-chatbot-api
pip install -r requirements.txt
cp .env.example .env  # fill in API keys
uvicorn app.main:app --reload
# → http://localhost:8000
```

## Deployment
Push to master → Railway + Vercel auto-deploy (2 minutes).

## Documentation
- [Widget Embed Guide](docs/WIDGET-EMBED-GUIDE.md) — how to add chatbot to your site
- [User Guide](docs/USER-GUIDE.md) — daily workflow for Dan & Maria

## Team
Built by Scaler + Nasalciuc for BBC Sky Data.
