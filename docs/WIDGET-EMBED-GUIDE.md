# BBC Chatbot — Widget Embed Guide

## Option A: Floating Chat Bubble (RECOMMENDED)

Add this code before `</body>` on any page of buybusinessclass.com:

```html
<script>
(function() {
  var open = false;
  var iframe = document.createElement('iframe');
  iframe.src = 'https://admin-panel-error.vercel.app/widget-embed';
  iframe.style.cssText = 'position:fixed;bottom:20px;right:20px;width:400px;height:600px;border:none;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.2);z-index:99999;display:none;';
  iframe.id = 'bbc-chatbot-frame';
  iframe.allow = 'microphone';
  document.body.appendChild(iframe);

  var btn = document.createElement('div');
  btn.id = 'bbc-chatbot-btn';
  btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
  btn.style.cssText = 'position:fixed;bottom:20px;right:20px;width:60px;height:60px;background:#0B1829;border:2px solid #C9A54E;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:100000;box-shadow:0 4px 16px rgba(0,0,0,0.25);transition:transform 0.2s;';
  btn.onmouseenter = function() { btn.style.transform = 'scale(1.1)'; };
  btn.onmouseleave = function() { btn.style.transform = 'scale(1)'; };
  btn.onclick = function() {
    open = !open;
    iframe.style.display = open ? 'block' : 'none';
    btn.innerHTML = open
      ? '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
      : '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
  };
  document.body.appendChild(btn);
})();
</script>
```

## Option B: WordPress / CMS

1. Go to your site admin panel
2. Find "Custom HTML" or "Footer Scripts" section
3. Paste the code from Option A
4. Save

## Option C: Full Page Embed

```html
<iframe src="https://admin-panel-error.vercel.app/widget-embed"
  width="100%" height="700" style="border:none;border-radius:12px;">
</iframe>
```

## Branding

- Bubble color: Navy `#0B1829` with Gold `#C9A54E` border
- Chat header: Navy with white text
- Send button: Gold
- Bot name: "BBC Travel Assistant"

## Testing

Before adding to your site, test at:
https://admin-panel-error.vercel.app/widget-embed

## Requirements

- Your site must load over HTTPS (not HTTP)
- The widget connects to our backend automatically
- No API keys needed on your side
