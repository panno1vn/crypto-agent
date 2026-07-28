import asyncio
import os

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# 1. Load cấu hình
load_dotenv()
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")


# 2. Đọc file YAML
def load_channels():
    with open("config/channels.yaml", "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
        # Gộp cả 2 list Anh và Việt lại thành 1 mảng duy nhất
        return data["channels"]["english"] + data["channels"]["vietnamese"]


client = TelegramClient("crypto_session", api_id, api_hash)


async def main():
    await client.start()
    channels = load_channels()

    print(f"📦 Đã load được {len(channels)} channels từ file config.")
    print("⏳ Bắt đầu test đọc tin (An toàn: nghỉ 3 giây giữa mỗi channel)...\n")

    for channel in channels:
        try:
            # Lấy đúng 1 tin nhắn mới nhất từ channel
            async for message in client.iter_messages(channel, limit=1):
                # Rút gọn text để in ra cho đỡ rối
                text = (
                    str(message.text)[:60].replace("\n", " ")
                    if message.text
                    else "[Media/No Text]"
                )
                print(f"✅ {channel}: {text}...")

            # RULE AN TOÀN SỐ 1: Bắt buộc nghỉ ngơi trước khi sang channel tiếp theo
            await asyncio.sleep(3)

        except FloodWaitError as e:
            # RULE AN TOÀN SỐ 2: Bắt lỗi nếu Telegram yêu cầu dừng
            print(f"⚠️ BỊ RATE LIMIT ở channel {channel}! Cần đợi {e.seconds} giây.")
            await asyncio.sleep(e.seconds)  # Ngủ đúng số giây Telegram bắt phạt
        except Exception as e:
            print(f"❌ Lỗi khi đọc channel {channel}: {e}")


with client:
    client.loop.run_until_complete(main())
