from pyrogram import filters, Client, enums
from Backend.helper.custom_filter import CustomFilters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Backend.config import Telegram

def _is_admin(user_id: int) -> bool:
    if not user_id:
        return False
    if user_id == Telegram.OWNER_ID:
        return True
    return user_id in (Telegram.REQUESTS_ADMIN_IDS or [])


def _invite_link() -> str:
    return (Telegram.REQUIRED_INVITE_LINK or Telegram.NOTIFY_INVITE_LINK or Telegram.REQUESTS_INVITE_LINK or "").strip()


@Client.on_message(filters.command('start') & filters.private, group=10)
async def send_start_message(client: Client, message: Message):
    try:
        user = message.from_user
        uid = user.id if user else 0

        base_url = Telegram.BASE_URL
        addon_url = f"{base_url}/stremio/manifest.json"

        if _is_admin(uid):
            await message.reply_text(
                '<b>Welcome to Telegram Stremio!</b>\n\n'
                'To install the Stremio addon, copy the URL below and add it in Stremio:\n\n'
                f'<b>Your Addon URL:</b>\n<code>{addon_url}</code>',
                quote=True,
                parse_mode=enums.ParseMode.HTML
            )
            return

        text = (
            "<b>أهلًا!</b>\n\n"
            "هذا بوت استقبال طلبات الأفلام والمسلسلات.\n\n"
            "<b>طريقة الطلب:</b>\n"
            "• اكتب اسم الفيلم/المسلسل مباشرة هنا\n"
            "• أو استخدم: <code>/request</code>\n\n"
            "بعدها تختار النتيجة الصحيحة من القائمة.\n"
        )

        buttons = []
        link = _invite_link()
        if link:
            buttons.append([InlineKeyboardButton("🔗 قناة الإشعارات", url=link)])

        await message.reply_text(
            text,
            quote=True,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=True,
        )

    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /start handler: {e}")
