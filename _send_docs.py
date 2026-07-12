"""Standalone script to send ARCHITECTURE.md to Telegram via SOCKS5 proxy."""
import asyncio
import sys
import re

# Read the file
with open('G:/bot/crypto-screener-v2/ARCHITECTURE.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Get token and chat_id from .env
with open('G:/bot/crypto-screener-v2/.env', 'r', encoding='utf-8') as f:
    env = f.read()

token = re.search(r'CS_TELEGRAM_TOKEN=(.+)', env).group(1).strip().strip('"').strip("'")
chat_id = re.search(r'CS_TELEGRAM_CHAT_ID=(.+)', env).group(1).strip().strip('"').strip("'")

MAX_CHARS = 3800

# Split into sections by ## headers
header = "📘 Crypto Screener v2 — Архитектура\n\n"
chunks = []

if len(content) <= MAX_CHARS:
    chunks.append(header + content)
else:
    # Split by double newlines on header boundaries
    parts = content.split('\n## ')
    current = header + parts[0]
    for part in parts[1:]:
        if len(current) + len('\n## ') + len(part) > MAX_CHARS:
            chunks.append(current)
            current = header + '\n## ' + part
        else:
            current += '\n## ' + part
    if current:
        chunks.append(current)

async def send_via_http(text, idx, total):
    """Send via direct HTTP API with SOCKS5 proxy."""
    from aiohttp_socks import ProxyConnector
    import aiohttp

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    connector = ProxyConnector.from_url("socks5://127.0.0.1:10808")
    
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }) as resp:
            if resp.ok:
                print(f"✅ Part {idx}/{total} sent OK")
            else:
                body = await resp.text()
                print(f"❌ Part {idx}/{total} failed: {resp.status} {body[:200]}")

async def main():
    print(f"Sending {len(chunks)} part(s) to {chat_id}...")
    for i, chunk in enumerate(chunks, 1):
        part_label = f" (📄 часть {i}/{len(chunks)})" if len(chunks) > 1 else ""
        full = f"📘 Crypto Screener v2{part_label}\n\n{chunk}"
        await send_via_http(full, i, len(chunks))
        await asyncio.sleep(0.5)

asyncio.run(main())
print("Done!")
