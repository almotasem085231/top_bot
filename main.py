import logging
import asyncio
import os
from datetime import datetime, timedelta
import pytz # Import pytz for timezone handling

# Import aiosqlite for asynchronous database operations
import aiosqlite
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# For loading environment variables (e.g., API_TOKEN, OWNER_ID)
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Get API_TOKEN and OWNER_ID from environment variables for security
API_TOKEN = os.getenv('API_TOKEN')
try:
    OWNER_ID = int(os.getenv('OWNER_ID'))
except (TypeError, ValueError):
    logging.error("OWNER_ID environment variable is missing or invalid. Please set it.")
    exit(1) # Exit if OWNER_ID is not set correctly

if not API_TOKEN:
    logging.error("API_TOKEN environment variable is missing. Please set it.")
    exit(1) # Exit if API_TOKEN is not set

DATABASE_NAME = 'top_engaged_db.sqlite' # Changed database name

# Set timezone for scheduled tasks (Saudi Arabia Time)
SAUDI_ARABIA_TIMEZONE = pytz.timezone('Asia/Riyadh')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Bot and Router
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

# Global variables for connection and cursor (will be managed by startup/shutdown hooks)
db_conn = None
db_cursor = None

# --- Database Initialization and Management ---
async def init_db():
    """Initializes the SQLite database connection asynchronously."""
    global db_conn, db_cursor
    logging.info(f"Initializing database: {DATABASE_NAME}")
    db_conn = await aiosqlite.connect(DATABASE_NAME)
    db_cursor = await db_conn.cursor()

    await db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS message_counts (
        user_id INTEGER PRIMARY KEY,
        message_count INTEGER DEFAULT 0,
        username TEXT,
        full_name TEXT
    )
    """)
    await db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS deputies (
        user_id INTEGER PRIMARY KEY
    )
    """)
    await db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS top_engaged_history (
        week_start_date TEXT PRIMARY KEY,
        top_1_user_id INTEGER,
        top_2_user_id INTEGER,
        top_3_user_id INTEGER,
        top_1_username TEXT,
        top_2_username TEXT,
        top_3_username TEXT
    )
    """)
    await db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_settings (
        setting_name TEXT PRIMARY KEY,
        setting_value TEXT
    )
    """)
    await db_conn.commit()
    logging.info("Database tables checked/created successfully.")

async def close_db():
    """Closes the SQLite database connection asynchronously."""
    global db_conn
    if db_conn:
        logging.info("Closing database connection.")
        await db_conn.close()
        db_conn = None

# Register startup and shutdown hooks for the database
dp.startup.register(init_db)
dp.shutdown.register(close_db)

# --- Helper Functions ---

def is_owner(user_id: int) -> bool:
    """Checks if the given user ID is the bot owner."""
    return user_id == OWNER_ID

async def is_deputy(user_id: int) -> bool:
    """Checks if the given user ID is a deputy."""
    await db_cursor.execute("SELECT 1 FROM deputies WHERE user_id=?", (user_id,))
    return await db_cursor.fetchone() is not None

async def get_group_chat_id():
    """Retrieves the stored group chat ID from settings."""
    await db_cursor.execute("SELECT setting_value FROM bot_settings WHERE setting_name = 'main_group_chat_id'")
    result = await db_cursor.fetchone()
    if result:
        try:
            return int(result[0])
        except ValueError:
            return None
    return None

async def set_group_chat_id(chat_id: int):
    """Stores the main group chat ID in settings."""
    await db_cursor.execute("INSERT OR REPLACE INTO bot_settings (setting_name, setting_value) VALUES (?, ?)", ('main_group_chat_id', str(chat_id)))
    await db_conn.commit()
    logging.info(f"Main group chat ID set to: {chat_id}")

# --- TOP ENGAGED Logic ---

async def calculate_and_announce_top_engaged():
    """
    Calculates top engaged users, announces them, resets counts,
    and notifies the owner. Pins the new message instead of deleting old one.
    """
    logging.info("Starting TOP ENGAGED calculation and announcement.")

    main_group_id = await get_group_chat_id()
    if not main_group_id:
        logging.warning("Main group chat ID is not set. Cannot announce TOP ENGAGED.")
        try:
            await bot.send_message(OWNER_ID, "⚠️ لم يتم تحديد المجموعة الرئيسية للإعلان عن TOP ENGAGED. يرجى استخدام أمر /set_main_group **داخل المجموعة** التي تريد الإعلان فيها.")
        except TelegramForbiddenError:
            logging.error(f"Cannot send message to owner {OWNER_ID}. User blocked bot.")
        return

    # Get top 3 users by message count
    await db_cursor.execute("SELECT user_id, username, full_name, message_count FROM message_counts ORDER BY message_count DESC LIMIT 3")
    top_users_data = await db_cursor.fetchall()

    owner_and_deputy_notification_text_details = "" # Details for notification
    top_history_data = {
        'week_start_date': datetime.now(SAUDI_ARABIA_TIMEZONE).strftime('%Y-%m-%d'),
        'top_1_user_id': None, 'top_2_user_id': None, 'top_3_user_id': None,
        'top_1_username': None, 'top_2_username': None, 'top_3_username': None,
    }

    # Custom announcement message template
    announcement_text_template = (
        "التوب الأسبوعي \n\n"
        "الاول  {top1_mention}\n"
        "الثاني  {top2_mention}\n"
        "الثالث  {top3_mention}\n\n"
        "مبروك لكم لقب top engaged \n\n"
        "وشكرا لتفاعل الجميع وحظ موفق للأسبوع القادم"
    )

    placeholders = {
        'top1_mention': "غير متاح",
        'top2_mention': "غير متاح",
        'top3_mention': "غير متاح",
    }
    
    if not top_users_data:
        announcement_text = "لا يوجد بيانات تفاعل كافية لهذا الأسبوع."
        owner_and_deputy_notification_text_details = "لم يتم تسجيل أي تفاعل لهذا الأسبوع."
    else:
        for i, (user_id, username, full_name, count) in enumerate(top_users_data):
            # Define how the user will be mentioned/displayed
            if username:
                display_mention = f"@{username}" # Direct @mention if username exists
            else:
                # Use a clickable full name if no username, otherwise just ID
                display_name = full_name if full_name else f"مستخدم (ID: {user_id})"
                display_mention = f"<a href='tg://user?id={user_id}'>{display_name}</a>"

            owner_and_deputy_notification_text_details += f"\n- {display_mention} ({count} رسالة)"

            # Populate history data and announcement placeholders
            if i == 0:
                top_history_data['top_1_user_id'] = user_id
                top_history_data['top_1_username'] = username if username else full_name
                placeholders['top1_mention'] = display_mention
            elif i == 1:
                top_history_data['top_2_user_id'] = user_id
                top_history_data['top_2_username'] = username if username else full_name
                placeholders['top2_mention'] = display_mention
            elif i == 2:
                top_history_data['top_3_user_id'] = user_id
                top_history_data['top_3_username'] = username if username else full_name
                placeholders['top3_mention'] = display_mention
        
        announcement_text = announcement_text_template.format(**placeholders)

    # Announce in the main group
    try:
        sent_message = await bot.send_message(
            chat_id=main_group_id,
            text=announcement_text,
            parse_mode="HTML" # IMPORTANT: HTML parse mode is needed for clickable mentions
        )
        logging.info(f"TOP ENGAGED announced in chat {main_group_id}. Message ID: {sent_message.message_id}")

        # Pin the new message
        try:
            await bot.pin_chat_message(chat_id=main_group_id, message_id=sent_message.message_id, disable_notification=True)
            logging.info(f"TOP ENGAGED message {sent_message.message_id} pinned in chat {main_group_id}.")
        except TelegramForbiddenError:
            logging.warning(f"Bot lacks 'can_pin_messages' permission in chat {main_group_id}. Could not pin message.")
            await bot.send_message(OWNER_ID, f"⚠️ لا يمكنني تثبيت رسالة TOP ENGAGED في المجموعة {main_group_id}. تأكد أن البوت لديه صلاحية 'تثبيت الرسائل'.")
        except TelegramBadRequest as e:
            logging.warning(f"Failed to pin message {sent_message.message_id} in chat {main_group_id}: {e}")
        except Exception as e:
            logging.error(f"Error pinning TOP ENGAGED message: {e}")

    except TelegramForbiddenError as e:
        logging.error(f"Bot forbidden to send messages in chat {main_group_id}: {e}")
        try:
            await bot.send_message(OWNER_ID, f"⚠️ لا يمكنني الإعلان في المجموعة {main_group_id}. تأكد أن البوت لديه صلاحية إرسال الرسائل.")
        except TelegramForbiddenError:
            logging.error(f"Cannot send message to owner {OWNER_ID}. User blocked bot.")
    except Exception as e:
        logging.error(f"Error announcing TOP ENGAGED in chat {main_group_id}: {e}")
        try:
            await bot.send_message(OWNER_ID, f"حدث خطأ أثناء الإعلان عن TOP ENGAGED في المجموعة {main_group_id}: {e}")
        except TelegramForbiddenError:
            pass # Can't notify owner

    # Store history
    insert_sql = """
    INSERT INTO top_engaged_history (week_start_date, top_1_user_id, top_2_user_id, top_3_user_id, top_1_username, top_2_username, top_3_username)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(week_start_date) DO UPDATE SET
        top_1_user_id=excluded.top_1_user_id, top_2_user_id=excluded.top_2_user_id, top_3_user_id=excluded.top_3_user_id,
        top_1_username=excluded.top_1_username, top_2_username=excluded.top_2_username, top_3_username=excluded.top_3_username
    """
    await db_cursor.execute(insert_sql, (
        top_history_data['week_start_date'],
        top_history_data['top_1_user_id'], top_history_data['top_2_user_id'], top_history_data['top_3_user_id'],
        top_history_data['top_1_username'], top_history_data['top_2_username'], top_history_data['top_3_username']
    ))
    await db_conn.commit()
    logging.info("TOP ENGAGED history saved.")

    # Reset message counts for next week
    await db_cursor.execute("UPDATE message_counts SET message_count = 0")
    await db_conn.commit()
    logging.info("Message counts reset for the new week.")

    # Notify owner
    try:
        owner_message = f"مرحباً بك! تم تحديث قائمة TOP ENGAGED وإعلان الفائزين الجدد:{owner_and_deputy_notification_text_details}\n\nتفضل بالمراجعة."
        await bot.send_message(OWNER_ID, owner_message)
        logging.info(f"Owner {OWNER_ID} notified about TOP ENGAGED update.")
    except TelegramForbiddenError:
        logging.error(f"Cannot send message to owner {OWNER_ID}. User blocked bot.")
    except Exception as e:
        logging.error(f"Error sending owner notification: {e}")

    # Notify deputies
    await db_cursor.execute("SELECT user_id FROM deputies")
    deputy_ids = [row[0] for row in await db_cursor.fetchall()]

    for deputy_id in deputy_ids:
        try:
            deputy_info = await bot.get_chat(deputy_id)
            # Prioritize username, then full_name, then just ID
            deputy_name = deputy_info.username if deputy_info.username else deputy_info.full_name if deputy_info.full_name else f"صديقي (ID: {deputy_id})"
            deputy_notification_message = f"مرحباً {deputy_name}، تم تحديث قائمة TOP ENGAGED وإعلان الفائزين الجدد:{owner_and_deputy_notification_text_details}\n\nتفضل بالمراجعة."
            await bot.send_message(deputy_id, deputy_notification_message)
            logging.info(f"Deputy {deputy_id} notified about TOP ENGAGED update.")
        except TelegramForbiddenError:
            logging.warning(f"Cannot send message to deputy {deputy_id}. User blocked bot.")
        except Exception as e:
            logging.error(f"Error sending notification to deputy {deputy_id}: {e}")


async def schedule_top_engaged_task():
    """Schedules the TOP ENGAGED calculation to run weekly."""
    while True:
        now = datetime.now(SAUDI_ARABIA_TIMEZONE)
        
        # Calculate next Saturday 00:00:00
        # Saturday is weekday 5 (Monday is 0, Sunday is 6)
        days_until_saturday = (5 - now.weekday() + 7) % 7 
        
        next_run_time = now + timedelta(days=days_until_saturday)
        next_run_time = next_run_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # If today is Saturday and the time is already past 00:00, schedule for next Saturday
        if now.weekday() == 5 and now.hour >= 0 and now.minute >= 0 and now.second >= 0:
            next_run_time += timedelta(days=7)

        time_to_wait = (next_run_time - now).total_seconds()
        
        logging.info(f"Next TOP ENGAGED announcement scheduled for: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')} ({time_to_wait} seconds from now)")
        await asyncio.sleep(time_to_wait)
        
        # Ensure it runs exactly at the scheduled time, even if sleep was slightly off
        current_time_before_run = datetime.now(SAUDI_ARABIA_TIMEZONE)
        if current_time_before_run.weekday() == 5 and \
           current_time_before_run.hour == 0 and \
           current_time_before_run.minute == 0:
            await calculate_and_announce_top_engaged()
        else:
            logging.warning("Scheduled task woke up slightly off time. Skipping current run to avoid drift.")

        # Sleep for a bit to avoid busy-waiting or immediate re-scheduling in a loop
        # and allow the main loop to handle other events.
        await asyncio.sleep(60) # Sleep for 1 minute before re-calculating next run time

# --- Message Handlers ---

@router.message(Command("start"))
async def start_handler(message: types.Message):
    """Handles the /start command."""
    if message.chat.type == ChatType.PRIVATE:
        me = await bot.get_me()
        invite_link = f'https://t.me/{me.username}?startgroup=true'
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="إضافة لقروب", url=invite_link)]])
        await message.answer(
            "أهلاً بك! أنا بوت Top Engaged. أضفني لقروبك لبدء تتبع التفاعل الأسبوعي وإعلان الفائزين.\n\n"
            "ملاحظة: لتعيين المجموعة الرئيسية لتتبع التفاعل، استخدم أمر /set_main_group **داخل المجموعة** التي تريدها.",
            reply_markup=keyboard
        )
    else:
        await message.answer("مرحباً! أنا جاهز للعمل في هذه المجموعة. تأكد أنني مشرف.")

@router.message(Command("help"))
async def help_handler(message: types.Message):
    """Handles the /help command."""
    help_text = (
        "أوامري بسيطة:\n"
        "• `/start`: للبدء وإضافة البوت للمجموعات.\n"
        "• `/help`: لعرض هذه القائمة.\n"
        "• `/my_messages`: لعرض عدد رسائلك لهذا الأسبوع.\n"
        "• `/top_this_week`: لعرض أعلى 3 مستخدمين تفاعلاً لهذا الأسبوع حتى الآن.\n"
        "• `/history_top`: لعرض تاريخ الفائزين بـ TOP ENGAGED (آخر أسبوع).\n\n"
        "أوامر المالك (تستخدم في الخاص مع البوت فقط):\n"
        "• `/add_deputy <user_id>`: لتعيين مستخدم نائبًا.\n"
        "• `/remove_deputy <user_id>`: لعزل نائب.\n"
        "• `/list_deputies`: لعرض قائمة النواب.\n"
        "• `/clear_deputies`: لإزالة جميع النواب.\n\n"
        "أوامر المالك في المجموعة:\n"
        "• `/set_main_group`: لتعيين المجموعة الحالية كمجموعة رئيسية لتتبع TOP ENGAGED. (يجب أن يكون البوت مشرفًا هنا)\n"
        "• `/run_top_now`: لتشغيل عملية TOP ENGAGED وإعلان الفائزين فوراً (للاختبار أو التعديل الفوري)."
    )
    if is_owner(message.from_user.id) or await is_deputy(message.from_user.id):
        await message.answer(help_text)
    else:
        await message.answer(help_text.split("أوامر المالك (تستخدم في الخاص مع البوت فقط):")[0].strip()) # Show only user commands

@router.message(Command("my_messages"))
async def my_messages_handler(message: types.Message):
    """Shows user's message count for the current week."""
    user_id = message.from_user.id
    await db_cursor.execute("SELECT message_count FROM message_counts WHERE user_id=?", (user_id,))
    row = await db_cursor.fetchone()
    count = row[0] if row else 0
    await message.reply(f"عدد رسائلك لهذا الأسبوع: {count}")

@router.message(Command("top_this_week"))
async def top_this_week_handler(message: types.Message):
    """Displays the current week's top engaged users."""
    await db_cursor.execute("SELECT user_id, username, full_name, message_count FROM message_counts ORDER BY message_count DESC LIMIT 3")
    top_users = await db_cursor.fetchall()

    if not top_users:
        await message.reply("لم يتم تسجيل أي تفاعل لهذا الأسبوع بعد، أو لم يتم الإعلان عن الفائزين بعد.")
        return

    response = "أعلى 3 مستخدمين تفاعلاً هذا الأسبوع حتى الآن:\n"
    for i, (user_id, username, full_name, count) in enumerate(top_users):
        display_name = f"@{username}" if username else full_name if full_name else f"ID: {user_id}"
        response += f"{i+1}. {display_name} ({count} رسالة)\n"
    await message.reply(response)

@router.message(Command("history_top"))
async def history_top_handler(message: types.Message):
    """Displays the history of top engaged users."""
    await db_cursor.execute("SELECT week_start_date, top_1_username, top_2_username, top_3_username FROM top_engaged_history ORDER BY week_start_date DESC LIMIT 1")
    history = await db_cursor.fetchall()

    if not history:
        await message.reply("لا يوجد سجل سابق للفائزين بـ TOP ENGAGED.")
        return

    response = "سجل الفائزين بـ TOP ENGAGED (آخر أسبوع):\n"
    for date, top1, top2, top3 in history:
        response += f"\nالأسبوع الذي بدأ في: {date}\n"
        response += f"1. {top1 if top1 else 'غير متاح'}\n"
        response += f"2. {top2 if top2 else 'غير متاح'}\n"
        response += f"3. {top3 if top3 else 'غير متاح'}\n"
    await message.reply(response)

@router.message(Command("set_main_group"))
async def set_main_group_handler(message: types.Message):
    """Sets the current chat as the main group for TOP ENGAGED."""
    # Modified condition to allow owner OR deputy
    if not (is_owner(message.from_user.id) or await is_deputy(message.from_user.id)):
        await message.reply("ليس لديك صلاحية لتعيين المجموعة الرئيسية. هذا الأمر متاح فقط للمالك والنواب.")
        return
    
    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("يمكن تعيين المجموعة الرئيسية فقط في المجموعات أو المجموعات الخارقة.")
        return

    await set_group_chat_id(message.chat.id)
    await message.reply(f"تم تعيين هذه المجموعة ({message.chat.title}) كمجموعة رئيسية لتتبع TOP ENGAGED. سيتم الإعلان هنا أسبوعياً.")

@router.message(Command("run_top_now"))
async def run_top_now_handler(message: types.Message):
    """Manually triggers the TOP ENGAGED calculation and announcement."""
    if not (is_owner(message.from_user.id) or await is_deputy(message.from_user.id)):
        await message.reply("ليس لديك صلاحية لتشغيل هذا الأمر.")
        return
    
    await message.reply("جاري حساب وإعلان TOP ENGAGED الآن...")
    await calculate_and_announce_top_engaged()
    await message.answer("تمت عملية TOP ENGAGED بنجاح.")

# --- Owner/Deputy Commands (mainly in private chat for deputies management) ---

@router.message(Command("add_deputy"))
async def add_deputy(message: types.Message):
    """Handles making a user a deputy using /add_deputy <user_id>."""
    if not is_owner(message.from_user.id):
        await message.reply("فقط المالك يمكنه تعيين النواب.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("يجب استخدام هذا الأمر في المحادثة الخاصة مع البوت.")
        return
        
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await message.reply("استخدام خاطئ. يجب أن يكون الأمر: /add_deputy <user_id>.")
        return

    try:
        deputy_id = int(args[1])
        await db_cursor.execute("INSERT OR IGNORE INTO deputies (user_id) VALUES (?)", (deputy_id,))
        await db_conn.commit()
        
        # Try to get user info for a more friendly message
        try:
            deputy_user_info = await bot.get_chat(deputy_id) # Use get_chat for private chat
            username = deputy_user_info.username if deputy_user_info.username else deputy_user_info.full_name
            await message.reply(f"تم تعيين {username} (ID: {deputy_id}) نائبًا.")
        except Exception:
            await message.reply(f"تم تعيين المستخدم بمعرف {deputy_id} نائبًا. (تعذر جلب اسمه).")

    except ValueError:
        await message.reply("معرف المستخدم (user_id) يجب أن يكون رقمًا.")
    except Exception as e:
        logging.error(f"Error making deputy: {e}")
        await message.reply("حدث خطأ أثناء تعيين النائب.")

@router.message(Command("remove_deputy"))
async def remove_deputy(message: types.Message):
    """Handles removing a user from deputy status using /remove_deputy <user_id>."""
    if not is_owner(message.from_user.id):
        await message.reply("فقط المالك يمكنه عزل النواب.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("يجب استخدام هذا الأمر في المحادثة الخاصة مع البوت.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await message.reply("استخدام خاطئ. يجب أن يكون الأمر: /remove_deputy <user_id>.")
        return

    try:
        deputy_id = int(args[1])
        await db_cursor.execute("DELETE FROM deputies WHERE user_id=?", (deputy_id,))
        await db_conn.commit()
        
        # Try to get user info for a more friendly message
        try:
            deputy_user_info = await bot.get_chat(deputy_id)
            username = deputy_user_info.username if deputy_user_info.username else deputy_user_info.full_name
            await message.reply(f"تم عزل {username} (ID: {deputy_id}) من منصب النائب.")
        except Exception:
            await message.reply(f"تم عزل المستخدم بمعرف {deputy_id} من منصب النائب. (تعذر جلب اسمه).")

    except ValueError:
        await message.reply("معرف المستخدم (user_id) يجب أن يكون رقمًا.")
    except Exception as e:
        logging.error(f"Error removing deputy: {e}")
        await message.reply("حدث خطأ أثناء عزل النائب.")

@router.message(Command("list_deputies"))
async def list_deputies(message: types.Message):
    """Handles displaying the list of deputies using /list_deputies command."""
    if not (is_owner(message.from_user.id) or await is_deputy(message.from_user.id)):
        await message.reply("ليس لديك صلاحية لعرض النواب.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("يجب استخدام هذا الأمر في المحادثة الخاصة مع البوت.")
        return

    await db_cursor.execute("SELECT user_id FROM deputies")
    deputy_ids = [row[0] for row in await db_cursor.fetchall()]

    if not deputy_ids:
        await message.reply("لا يوجد نواب حاليًا.")
        return

    deputy_list = []
    for deputy_id in deputy_ids:
        try:
            member = await bot.get_chat(deputy_id) # Use get_chat
            username = member.username
            full_name = member.full_name
            deputy_list.append(f"- {full_name} (@{username})" if username else f"- {full_name} (ID: {deputy_id})")
        except Exception as e:
            logging.warning(f"Could not fetch deputy info for ID: {deputy_id} - {e}")
            deputy_list.append(f"- (معرف غير معروف) (ID: {deputy_id})")

    await message.reply("قائمة النواب:\n" + "\n".join(deputy_list))

@router.message(Command("clear_deputies"))
async def clear_deputies(message: types.Message):
    """Handles clearing all deputies using /clear_deputies command."""
    if not is_owner(message.from_user.id):
        await message.reply("فقط المالك يمكنه مسح النواب.")
        return
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("يجب استخدام هذا الأمر في المحادثة الخاصة مع البوت.")
        return

    await db_cursor.execute("DELETE FROM deputies")
    await db_conn.commit()
    await message.reply("تم إزالة جميع النواب بنجاح.")

# --- Message Counter (Listens to all messages in groups) ---
@router.message(F.chat.type.in_([ChatType.GROUP, ChatType.SUPERGROUP]))
async def message_counter(message: types.Message):
    """Increments message count for users in tracked groups."""
    user = message.from_user
    user_id = user.id
    username = user.username if user.username else None
    full_name = user.full_name

    # Only count messages if the group ID matches the set main group ID
    main_group_id = await get_group_chat_id()
    if main_group_id and message.chat.id == main_group_id:
        await db_cursor.execute(
            "INSERT OR IGNORE INTO message_counts (user_id, username, full_name, message_count) VALUES (?, ?, ?, 0)",
            (user_id, username, full_name)
        )
        # Update username and full_name in case they changed
        await db_cursor.execute(
            "UPDATE message_counts SET message_count = message_count + 1, username = ?, full_name = ? WHERE user_id = ?",
            (username, full_name, user_id)
        )
        await db_conn.commit()
        # logging.info(f"Message from {full_name} ({user_id}) counted. Current count: {count + 1 if count else 1}")
    else:
        # logging.info(f"Message from {full_name} ({user_id}) in chat {message.chat.id} not counted. Not main group.")
        pass # Do not log every message to avoid cluttering logs

# --- Main function to run the bot ---
async def main():
    """Main function to start the bot and its background tasks."""
    dp.include_router(router)
    
    # Start the background task for scheduling TOP ENGAGED
    asyncio.create_task(schedule_top_engaged_task())

    # Start polling for updates
    logging.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())