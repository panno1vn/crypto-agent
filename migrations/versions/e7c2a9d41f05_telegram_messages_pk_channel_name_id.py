"""telegram_messages: khóa chính (channel_name, id)

Revision ID: e7c2a9d41f05
Revises: d4a9f21b8e37
Create Date: 2026-10-08 00:00:00.000000

Nợ #15
======
`id` là msg_id của Telegram, đánh số RIÊNG theo từng kênh. Khóa cũ
PRIMARY KEY (id) cùng `ON CONFLICT (id) DO NOTHING` của DatabaseWriter làm
tin của kênh B trùng id với tin đã có của kênh A bị bỏ im lặng. Đo read-only
2026-10-08: ước tính ~2.300 tin mất, tập trung ở nghiencryptochannel và
thichcheatair (docs/nhat-ky/2026-10-08_bug_khoa-chinh-telegram-messages-...).

Đồng thời bỏ DEFAULT nextval('telegram_messages_id_seq'): id luôn do
Telegram cấp. Default tự sinh là bẫy im lặng — một INSERT quên cột id sẽ
nhận 1, 2, 3... và va chạm với msg_id thật. Sequence chưa từng được gọi
(last_value=1, is_called=false, kiểm 2026-10-08).

Downgrade sẽ FAIL nếu đã có 2 kênh cùng id — đúng ý: không thể quay về
khóa (id) mà không xóa dữ liệu, và việc đó phải do người quyết.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c2a9d41f05"
down_revision: Union[str, Sequence[str], None] = "d4a9f21b8e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("telegram_messages_pkey", "telegram_messages", type_="primary")
    op.create_primary_key(
        "telegram_messages_pkey", "telegram_messages", ["channel_name", "id"]
    )
    op.execute("ALTER TABLE telegram_messages ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS telegram_messages_id_seq")


def downgrade() -> None:
    op.drop_constraint("telegram_messages_pkey", "telegram_messages", type_="primary")
    op.create_primary_key("telegram_messages_pkey", "telegram_messages", ["id"])
    op.execute("CREATE SEQUENCE IF NOT EXISTS telegram_messages_id_seq")
    op.execute("ALTER SEQUENCE telegram_messages_id_seq OWNED BY telegram_messages.id")
    op.execute(
        "ALTER TABLE telegram_messages "
        "ALTER COLUMN id SET DEFAULT nextval('telegram_messages_id_seq')"
    )
