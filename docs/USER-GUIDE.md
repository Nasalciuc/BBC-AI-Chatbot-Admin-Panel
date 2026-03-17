# BBC AI Chatbot — User Guide for Dan & Maria

## 1. Opening the Admin Panel
- Go to: https://admin-panel-error.vercel.app
- Login with your credentials
- You'll see the Dashboard with real-time KPIs

## 2. Dashboard
- **Conversations Today:** number of chat sessions
- **Leads Total:** all captured leads
- **Cost This Month:** AI spending (budget: $50/day)
- **Lead Pipeline:** funnel from New → Contacted → Qualified → Converted

## 3. Managing Leads (Maria's daily workflow)
- Click "Leads" in sidebar
- See all leads with Score badges (0-100) and Tier (Gold/Silver/Bronze)
- **Gold (80+):** Call IMMEDIATELY — high intent, all data collected
- **Silver (50-79):** Call within 2 hours — partial data
- **Bronze (0-49):** Follow up when convenient — browsing only
- Click any lead → detail drawer opens on the right
- See: route, dates, contact info, full conversation, status
- Change status: New → Contacted → Qualified → Converted/Lost

## 4. Knowledge Base
- Click "Knowledge Base" in sidebar
- 5 categories: Routes, Pricing, Booking, Changes, Baggage
- To add a new route: click "Add Entry" → fill title + content → save
- Chatbot uses these entries to answer customer questions
- After adding, run the embed script to update Qdrant search

## 5. Testing the Chatbot
- Click "Widget Preview" in sidebar
- Type a message like: "I want to fly business class from NYC to London"
- Chatbot responds with route info, prices, and asks for your details
- Test different scenarios: pricing, baggage, booking changes

## 6. Cost & Budget
- 80% of messages cost $0 (templates)
- AI messages cost $0.003 (Haiku) or $0.015 (Sonnet)
- Daily budget: $50 (hard limit)
- Typical monthly cost: $3-8

## Need Help?
Contact Scaler for technical issues.
