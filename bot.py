"""
Telegram Anonymous Dating Bot (JSON storage) + Pakasir QRIS upgrade (15 hari PRO)

Sebelum menjalankan:
- Set environment variables:
    - BOT_TOKEN (token dari @BotFather)
    - PAKASIR_SLUG (contoh: bottelegrampremium)
    - PAKASIR_API_KEY (API key dari Pakasir)
    - PRO_PRICE (opsional, default 20000 rupiah)

Contoh (Windows PowerShell):
  $env:BOT_TOKEN="123:ABC..."
  $env:PAKASIR_SLUG="bottelegrampremium"
  $env:PAKASIR_API_KEY="SECRET_API_KEY"
  $env:PRO_PRICE="20000"

Install dependency:
  pip install python-telegram-bot==20.5 requests

Jalankan:
  python bot.py
"""

import os
import uuid
import json
import time
from datetime import datetime, timedelta
import requests
from typing import Dict, Any, Optional

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------- CONFIG -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # required
PAKASIR_SLUG = os.getenv("PAKASIR_SLUG")  # example: bottelegrampremium
PAKASIR_API_KEY = os.getenv("PAKASIR_API_KEY")
PRO_PRICE = int(os.getenv("PRO_PRICE", "20000"))  # in IDR
PRO_DURATION_DAYS = 15  # PRO valid for 15 days

USERS_FILE = "users.json"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required. Set it before running.")

if not PAKASIR_SLUG or not PAKASIR_API_KEY:
    # We allow running without payments for testing, but /upgrade will warn.
    print("WARNING: PAKASIR_SLUG or PAKASIR_API_KEY not set. /upgrade will be disabled until set.")


# ----------------- DATA STORAGE -----------------
def load_users() -> Dict[str, Any]:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_users(data: Dict[str, Any]) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


users = load_users()
# Structure per user_id (string):
# users[user_id] = {
#   "gender": "pria" or "wanita",
#   "region": "Jawa Barat",
#   "is_pro": False,
#   "pro_expiry": "<iso-timestamp>" or None,
#   "pending_orders": [order_id, ...]  # optional
# }

pending_transactions: Dict[str, int] = {}  # order_id -> user_id (int)


# ----------------- VALID REGIONS (PROVINSI INDONESIA) -----------------
VALID_REGIONS = [
    "Aceh", "Sumatera Utara", "Sumatera Barat", "Riau", "Jambi", "Sumatera Selatan",
    "Bengkulu", "Lampung", "Kepulauan Bangka Belitung", "Kepulauan Riau", "Jakarta",
    "Jawa Barat", "Jawa Tengah", "Yogyakarta", "Jawa Timur", "Banten", "Bali",
    "Nusa Tenggara Barat", "Nusa Tenggara Timur", "Kalimantan Barat", "Kalimantan Tengah",
    "Kalimantan Selatan", "Kalimantan Timur", "Kalimantan Utara", "Sulawesi Utara",
    "Sulawesi Tengah", "Sulawesi Selatan", "Sulawesi Tenggara", "Gorontalo",
    "Sulawesi Barat", "Maluku", "Maluku Utara", "Papua", "Papua Barat", "Papua Tengah",
    "Papua Pegunungan", "Papua Selatan", "Papua Barat Daya"
]


# ----------------- MATCHING STATE -----------------
users_waiting: Dict[int, Dict[str, Any]] = {}  # user_id -> metadata (for quick checks)
active_chats: Dict[int, int] = {}  # user_id -> partner_id


# ----------------- HELPERS -----------------
def ensure_user_record(uid: int, username: Optional[str] = None) -> None:
    sid = str(uid)
    if sid not in users:
        users[sid] = {
            "gender": None,
            "region": None,
            "is_pro": False,
            "pro_expiry": None,
            "pending_orders": []
        }
        if username:
            users[sid]["username"] = username
        save_users(users)

def get_user(uid: int) -> Dict[str, Any]:
    """Ambil data user dari file JSON, buat baru kalau belum ada."""
    ensure_user_record(uid)
    return users[str(uid)]



def is_pro_active(uid: int) -> bool:
    sid = str(uid)
    rec = users.get(sid)
    if not rec:
        return False
    if rec.get("is_pro"):
        expiry = rec.get("pro_expiry")
        if expiry:
            try:
                exp_dt = datetime.fromisoformat(expiry)
                return datetime.utcnow() <= exp_dt
            except Exception:
                return False
    return False


def set_pro_for_user(uid: int, days: int = PRO_DURATION_DAYS) -> None:
    sid = str(uid)
    ensure_user_record(uid)
    expiry = datetime.utcnow() + timedelta(days=days)
    users[sid]["is_pro"] = True
    users[sid]["pro_expiry"] = expiry.isoformat()
    save_users(users)


def validate_region_input(text: str) -> Optional[str]:
    # try to match by title-casing, allow exact matches ignoring case
    candidate = text.strip()
    for r in VALID_REGIONS:
        if candidate.lower() == r.lower():
            return r
    # try simple fuzzy match: startswith
    candidate2 = candidate.lower()
    for r in VALID_REGIONS:
        if r.lower().startswith(candidate2) or candidate2 in r.lower():
            return r
    return None


# ----------------- COMMANDS -----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)
    await update.message.reply_text(
        "👋 Selamat datang di Anonymous Dating Bot!\n\n"
        "WAJIB: isi dulu profil dasar:\n"
        "• /setgender <pria|wanita>\n"
        "• /setregion <nama provinsi>\n\n"
        "Perintah lainnya:\n"
        "• /find — cari pasangan (PRO bisa pakai filter)\n"
        "• /stop — hentikan percakapan / pencarian\n"
        "• /upgrade — bayar PRO via Pakasir QRIS\n"
        "• /verifypro — cek pembayaran dan aktifkan PRO\n"
        "• /status — lihat status PRO\n"
    )


async def cmd_setgender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)
    if not context.args:
        await update.message.reply_text("Format: /setgender <pria|wanita>")
        return
    arg = context.args[0].lower()
    if arg not in ("pria", "wanita"):
        await update.message.reply_text("Gender tidak valid. Pilih 'pria' atau 'wanita'.")
        return
    users[str(user.id)]["gender"] = arg
    save_users(users)
    await update.message.reply_text(f"✅ Gender diset: {arg}")


async def cmd_setregion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)
    if not context.args:
        await update.message.reply_text("Format: /setregion <nama provinsi>")
        return
    text = " ".join(context.args)
    region = validate_region_input(text)
    if not region:
        await update.message.reply_text(
            "Provinsi tidak dikenali. Contoh provinsi yang valid:\n" + ", ".join(VALID_REGIONS[:8]) +
            "\n\nGunakan nama provinsi lengkap atau kata kunci (mis. 'Jawa Barat')."
        )
        return
    users[str(user.id)]["region"] = region
    save_users(users)
    await update.message.reply_text(f"✅ Wilayah diset: {region}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)
    sid = str(user.id)
    rec = users.get(sid, {})
    is_pro = is_pro_active(user.id)
    if is_pro:
        exp = rec.get("pro_expiry")
        await update.message.reply_text(f"💎 Kamu PRO sampai {exp} (UTC)")
    else:
        await update.message.reply_text("🆓 Kamu user biasa. Ketik /upgrade untuk jadi PRO.")


# ----------------- UPGRADE / VERIFY via Pakasir -----------------
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)

    if not PAKASIR_SLUG or not PAKASIR_API_KEY:
        await update.message.reply_text(
            "⚠️ Fitur upgrade belum aktif: PAKASIR_SLUG atau PAKASIR_API_KEY belum diset di environment."
        )
        return

    if is_pro_active(user.id):
        await update.message.reply_text("✅ Kamu sudah PRO.")
        return

    # create order id
    order_id = str(uuid.uuid4())
    pending_transactions[order_id] = user.id
    # also persist into user's pending_orders
    users[str(user.id)].setdefault("pending_orders", []).append(order_id)
    save_users(users)

    payment_url = f"https://app.pakasir.com/pay/{PAKASIR_SLUG}/{PRO_PRICE}?order_id={order_id}&qris_only=1"
    await update.message.reply_text(
        "💎 Upgrade ke PRO (masa aktif 15 hari)\n"
        f"Harga: Rp{PRO_PRICE:,}\n\n"
        "Silakan bayar lewat link QRIS berikut:\n"
        f"{payment_url}\n\n"
        "Setelah membayar, kembali ke chat dan kirim /verifypro untuk cek otomatis."
    )


async def cmd_verifypro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id, user.username)

    # find any pending order for this user
    sid = str(user.id)
    pending = users.get(sid, {}).get("pending_orders", [])
    if not pending:
        await update.message.reply_text("⚠️ Tidak ada transaksi pending untuk akun ini.")
        return

    # iterate pending orders and check status via Pakasir transactiondetail API
    verified_any = False
    for order_id in pending[:]:  # copy list
        params = {
            "project": PAKASIR_SLUG,
            "order_id": order_id,
            "amount": PRO_PRICE,
            "api_key": PAKASIR_API_KEY,
        }
        try:
            resp = requests.get("https://app.pakasir.com/api/transactiondetail", params=params, timeout=10)
        except requests.RequestException as e:
            await update.message.reply_text(f"❌ Gagal menghubungi server verifikasi: {e}")
            return

        if resp.status_code != 200:
            await update.message.reply_text(f"❌ Server verifikasi merespon {resp.status_code}. Coba lagi nanti.")
            return

        try:
            data = resp.json()
        except Exception:
            await update.message.reply_text("❌ Respon server verifikasi tidak valid (bukan JSON).")
            return

        # try to extract transaction object; pakasir may return in different keys
        tx = data.get("transaction") or data.get("data") or data
        status = None
        if isinstance(tx, dict):
            status = tx.get("status") or tx.get("payment_status") or tx.get("state")
        elif isinstance(tx, list) and len(tx) > 0 and isinstance(tx[0], dict):
            status = tx[0].get("status")

        if not status:
            await update.message.reply_text("⚠️ Tidak menemukan status pembayaran pada response.")
            return

        status_low = str(status).lower()
        if status_low in ("completed", "paid", "success"):
            # mark user pro for PRO_DURATION_DAYS
            set_pro_for_user(user.id, days=PRO_DURATION_DAYS)
            # remove pending order from both in-memory and persisted list
            if order_id in pending_transactions:
                del pending_transactions[order_id]
            users[sid]["pending_orders"].remove(order_id)
            save_users(users)
            verified_any = True
            await update.message.reply_text("✅ Pembayaran terverifikasi! Kamu sekarang PRO selama 15 hari.")
        else:
            # not yet paid
            await update.message.reply_text(f"🔄 Order {order_id} status: {status}. Mohon tunggu atau periksa kembali.")

    if not verified_any:
        await update.message.reply_text("🔎 Tidak ada transaksi yang terverifikasi saat ini.")


# ----------------- FIND / STOP / MATCHING -----------------
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    # Cegah double find
    if user_id in active_chats:
        await update.message.reply_text("Kamu sedang dalam percakapan. Ketik /stop untuk keluar.")
        return

    if not user.get("gender") or not user.get("region"):
        await update.message.reply_text("Kamu harus set gender dan wilayah dulu dengan /setgender dan /setregion.")
        return

    print(f"[FIND] {user_id} ({user['gender']}, {user['region']}) mulai mencari...")

    # Cari partner yang cocok
    for pid, pdata in users_waiting.copy().items():
        if pid == user_id:
            continue

        partner = get_user(pid)

        # 🟡 Kalau user PRO → filter gender & region
        if user.get("is_pro"):
            if partner["region"] != user["region"]:
                continue
            if partner["gender"] == user["gender"]:
                continue
        else:
            # 🔵 Kalau user biasa → lewati semua filter
            pass

        # Match!
        active_chats[user_id] = pid
        active_chats[pid] = user_id
        users_waiting.pop(pid, None)
        await update.message.reply_text("🔗 Kamu terhubung! Ketik /stop untuk keluar.")
        await context.bot.send_message(pid, "🔗 Kamu terhubung! Ketik /stop untuk keluar.")
        print(f"[MATCH] {user_id} terhubung dengan {pid}")
        return

    # Kalau belum dapat pasangan
    users_waiting[user_id] = {"gender": user["gender"], "region": user["region"]}
    await update.message.reply_text("Menunggu pasangan...")
    print(f"[WAIT] {user_id} belum dapat pasangan, masuk waiting pool.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # if chatting -> inform partner
    if user.id in active_chats:
        partner = active_chats[user.id]
        await update.message.reply_text("❌ Kamu keluar dari percakapan.")
        await context.bot.send_message(partner, "❌ Pasanganmu keluar dari percakapan.")
        del active_chats[partner]
        del active_chats[user.id]
        return
    # if waiting -> remove from queue
    if user.id in users_waiting:
        users_waiting.pop(user.id, None)
        await update.message.reply_text("🚫 Pencarian dibatalkan.")
        return
    await update.message.reply_text("Kamu sedang tidak dalam pencarian atau percakapan.")


# ----------------- MESSAGE RELAY (TEXT + MEDIA) -----------------
async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in active_chats:
        await update.message.reply_text("Kamu belum terhubung. Gunakan /find untuk mulai mencari.")
        return

    partner_id = active_chats.get(user.id)
    if not partner_id:
        await update.message.reply_text("⚠️ Pasangan tidak ditemukan. Gunakan /stop lalu /find lagi.")
        return

    msg = update.message

    try:
        # Text
        if msg.text:
            await context.bot.send_message(chat_id=partner_id, text=msg.text)

        # Photo (largest)
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            caption = msg.caption or ""
            await context.bot.send_photo(chat_id=partner_id, photo=file_id, caption=caption)

        # Video
        elif msg.video:
            file_id = msg.video.file_id
            caption = msg.caption or ""
            await context.bot.send_video(chat_id=partner_id, video=file_id, caption=caption)

        # Document (pdf, other)
        elif msg.document:
            file_id = msg.document.file_id
            caption = msg.caption or ""
            await context.bot.send_document(chat_id=partner_id, document=file_id, caption=caption)

        # Voice
        elif msg.voice:
            file_id = msg.voice.file_id
            await context.bot.send_voice(chat_id=partner_id, voice=file_id)

        # Audio
        elif msg.audio:
            file_id = msg.audio.file_id
            await context.bot.send_audio(chat_id=partner_id, audio=file_id)

        # Sticker
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=msg.sticker.file_id)

        else:
            # fallback: try forward (kehilangan anon), or respond not supported
            await update.message.reply_text("Tipe pesan ini belum didukung untuk diteruskan.")
    except Exception as e:
        # if sending fails, inform user
        await update.message.reply_text(f"Error saat mengirim ke pasangan: {e}")


# ----------------- APP RUN -----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setgender", cmd_setgender))
    app.add_handler(CommandHandler("setregion", cmd_setregion))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("verifypro", cmd_verifypro))

    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("stop", cmd_stop))

    # relay all non-command messages (text & media) to partner if connected
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_message))

    print("🤖 Bot berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
