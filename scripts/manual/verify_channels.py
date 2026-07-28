# scripts/manual/verify_channels.py
import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

CHANNELS_TO_CHECK = [
    "bitcoin_vietnam_news",
    "chatdautucrypto",
    "RIC_Capital_Channel",
    "crypto_musk1m",
    "Tradecoinspeed",
    "thichcheatair",
    "nghiencryptochannel",
    "bd_ventures",
    "dttdsignal",
    "coin369channel",
]


async def main():
    client = TelegramClient(
        "crypto_session",
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    async with client:
        for ch in CHANNELS_TO_CHECK:
            try:
                entity = await client.get_entity(ch)
                messages = await client.get_messages(ch, limit=5)
                last_date = messages[0].date if messages else None
                print(f"[OK] {ch} | title={entity.title} | last_post={last_date}")
            except Exception as e:
                print(f"[FAIL] {ch} | error={e}")


if __name__ == "__main__":
    asyncio.run(main())
