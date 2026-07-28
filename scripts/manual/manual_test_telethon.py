import os

from dotenv import load_dotenv
from telethon import TelegramClient, events

# 1. Load cấu hình
load_dotenv()
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")

# 2. Khởi tạo client
client = TelegramClient("crypto_session", api_id, api_hash)


# ==========================================
# PHẦN 1: ĐỊNH NGHĨA BỘ LẮNG NGHE (REALTIME)
# ==========================================
@client.on(events.NewMessage(chats=["whale_alert", "CoinDesk"]))
async def my_event_handler(event):
    print("🔔 [TIN MỚI]:", event.message.text)


# ==========================================
# PHẦN 2: LUỒNG CHẠY CHÍNH CỦA CHƯƠNG TRÌNH
# ==========================================
async def main():
    # Bước A: Khởi động và đăng nhập
    await client.start()
    me = await client.get_me()
    print(f"✅ Đăng nhập thành công với tài khoản: {me.username}")
    print("-" * 30)

    # Bước B: Cào thử lịch sử (Chạy 1 lần rồi thôi)
    # Mình để limit=5 cho bạn test nhanh, tránh in ra màn hình quá dài
    print("⏳ Đang cào 5 tin nhắn gần nhất từ whale_alert...")
    async for message in client.iter_messages("whale_alert", limit=5):
        # Cắt lấy 50 ký tự đầu in ra cho gọn
        short_text = str(message.text)[:50].replace("\n", " ")
        print(f"[{message.date.strftime('%H:%M:%S')}] {short_text}...")

    print("-" * 30)

    # Bước C: Khóa chương trình lại để lắng nghe tin nhắn mới
    print("🎧 Đang chuyển sang chế độ lắng nghe realtime...")
    print(
        "👉 Hãy thử đăng 1 tin vào channel (nếu bạn có quyền) hoặc đợi tin mới. Bấm Ctrl+C để thoát."
    )
    await client.run_until_disconnected()


# ==========================================
# PHẦN 3: KÍCH HOẠT MỌI THỨ
# ==========================================
with client:
    client.loop.run_until_complete(main())
