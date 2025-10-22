"""
Telegram Anonymous Dating Bot (Interactive PRO version + Pakasir QRIS integration)
"""

import os
import uuid
import json
import random
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
PAKASIR_SLUG = os.getenv("PAKASIR_SLUG")
PAKASIR_API_KEY = os.getenv("PAKASIR_API_KEY")
PRO_PRICE = int(os.getenv("PRO_PRICE", "20000"))
PRO_DURATION_DAYS = 15
USERS_FILE = "users.json"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")

# ---------------- DATA STORAGE ----------------
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
users_waiting: Dict[int, Dict[str, Any]] = {}
active_chats: Dict[int, int] = {}

# ---------------- HELPERS ----------------
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
    ensure_user_record(uid)
    return users[str(uid)]

def is_pro_active(uid: int) -> bool:
    rec = users.get(str(uid))
    if not rec or not rec.get("is_pro"):
        return False
    exp = rec.get("pro_expiry")
    if not exp:
        return False
    try:
        return datetime.utcnow() <= datetime.fromisoformat(exp)
    except Exception:
        return False

def set_pro_for_user(uid: int, days: int = PRO_DURATION_DAYS) -> None:
    expiry = datetime.utcnow() + timedelta(days=days)
    users[str(uid)]["is_pro"] = True
    users[str(uid)]["pro_expiry"] = expiry.isoformat()
    save_users(users)

def validate_region_input(text: str) -> Optional[str]:
    valid = [
        "Aceh", "Sumatera Utara", "Sumatera Barat", "Riau", "Jambi", "Sumatera Selatan",
        "Bengkulu", "Lampung", "Kepulauan Bangka Belitung", "Kepulauan Riau", "Jakarta",
        "Jawa Barat", "Jawa Tengah", "Yogyakarta", "Jawa Timur", "Banten", "Bali",
        "Nusa Tenggara Barat", "Nusa Tenggara Timur", "Kalimantan Barat", "Kalimantan Tengah",
        "Kalimantan Selatan", "Kalimantan Timur", "Kalimantan Utara", "Sulawesi Utara",
        "Sulawesi Tengah", "Sulawesi Selatan", "Sulawesi Tenggara", "Gorontalo",
        "Sulawesi Barat", "Maluku", "Maluku Utara", "Papua", "Papua Barat"
    ]
    t = text.strip().lower()
    for r in valid:
        if t in r.lower() or r.lower().startswith(t):
            return r
    return None

# ---------------- COMMANDS ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user_record(u.id, u.username)
    await update.message.reply_text(
        "👋 Selamat datang di Anonymous Dating Bot!\n\n"
        "Isi dulu profil kamu:\n"
        "• /setgender <pria|wanita>\n"
        "• /setregion <nama provinsi>\n\n"
        "Perintah lain:\n"
        "• /find — mulai mencari pasangan\n"
        "• /stop — keluar dari chat\n"
        "• /upgrade — jadi PRO 💎\n"
        "• /pro — lihat status & keuntungan PRO\n"
    )

async def cmd_setgender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user_record(u.id)
    if not context.args:
        await update.message.reply_text("Format: /setgender <pria|wanita>")
        return
    g = context.args[0].lower()
    if g not in ("pria", "wanita"):
        await update.message.reply_text("Pilih 'pria' atau 'wanita'.")
        return
    users[str(u.id)]["gender"] = g
    save_users(users)
    await update.message.reply_text(f"✅ Gender diset: {g}")

async def cmd_setregion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user_record(u.id)
    if not context.args:
        await update.message.reply_text("Format: /setregion <provinsi>")
        return
    region = validate_region_input(" ".join(context.args))
    if not region:
        await update.message.reply_text("Provinsi tidak dikenali. Contoh: Jawa Barat, Bali, Jakarta.")
        return
    users[str(u.id)]["region"] = region
    save_users(users)
    await update.message.reply_text(f"✅ Wilayah diset: {region}")

async def cmd_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user_record(u.id)
    if is_pro_active(u.id):
        exp = users[str(u.id)]["pro_expiry"]
        await update.message.reply_text(
            f"💎 Kamu pengguna PRO aktif!\n"
            f"Berlaku sampai {exp}\n\n"
            "Fitur PRO:\n"
            "• Filter gender & wilayah\n"
            "• Match lebih cepat\n"
            "• Masa aktif 15 hari"
        )
    else:
        await update.message.reply_text(
            "🆓 Kamu user biasa.\n\n"
            f"Upgrade ke PRO cuma Rp{PRO_PRICE:,}\n"
            "• Bisa pilih gender & wilayah\n"
            "• Prioritas match\n"
            f"Ketik /upgrade untuk bayar via QRIS 💳"
        )

# ---------------- FIND / INTERAKTIF ----------------
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)

    if not user.get("gender") or not user.get("region"):
        await update.message.reply_text("Isi dulu data kamu pakai /setgender dan /setregion.")
        return

    if u.id in active_chats:
        await update.message.reply_text("Kamu masih dalam percakapan. /stop dulu kalau mau cari lagi.")
        return

    # USER BIASA → langsung cari random + tombol upgrade
    if not is_pro_active(u.id):
        keyboard = [[InlineKeyboardButton("💎 Upgrade PRO", callback_data="upgrade_now")]]
        markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🆓 Kamu user biasa, pencarian acak tanpa filter.\n"
            f"💎 Mau cari berdasarkan gender & wilayah?\n"
            f"Upgrade cuma Rp{PRO_PRICE:,} — /upgrade sekarang!",
            reply_markup=markup,
        )
        await start_search(u.id, None, None, update, context)
        return

    # USER PRO → pilih gender
    keyboard = [
        [
            InlineKeyboardButton("👨 Pria", callback_data="find_gender_pria"),
            InlineKeyboardButton("👩 Wanita", callback_data="find_gender_wanita"),
        ]
    ]
    await update.message.reply_text("Pilih gender target:", reply_markup=InlineKeyboardMarkup(keyboard))

async def on_gender_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    g = q.data.split("_")[-1]
    context.user_data["target_gender"] = g

    regions = ["Jakarta", "Jawa Barat", "Jawa Timur", "Bali", "Sumatera Utara", "Kalimantan Timur"]
    keyboard = [[InlineKeyboardButton(r, callback_data=f"find_region_{r}")] for r in regions]
    keyboard.append([InlineKeyboardButton("🌏 Lainnya...", callback_data="find_region_more")])
    await q.edit_message_text(
        text=f"Gender target: {g}\n\nSekarang pilih wilayah:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def on_region_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.replace("find_region_", "")
    if region == "more":
        await q.edit_message_text("Ketik manual: /find <gender> <provinsi>\nContoh: /find wanita Bali")
        return
    gender = context.user_data.get("target_gender")
    await q.edit_message_text(f"🎯 Target: {gender}, 📍 {region}\n\n🔎 Mencari pasangan...")
    await start_search(q.from_user.id, gender, region, q, context)

async def start_search(user_id: int, target_gender: Optional[str], target_region: Optional[str], update, context):
    u = get_user(user_id)
    for pid, _ in users_waiting.copy().items():
        if pid == user_id:
            continue
        p = get_user(pid)
        if is_pro_active(user_id):
            if target_gender and p.get("gender") != target_gender:
                continue
            if target_region and p.get("region") != target_region:
                continue
        active_chats[user_id] = pid
        active_chats[pid] = user_id
        users_waiting.pop(pid, None)
        await context.bot.send_message(user_id, "🔗 Kamu terhubung! Ketik /stop untuk keluar.")
        await context.bot.send_message(pid, "🔗 Kamu terhubung! Ketik /stop untuk keluar.")
        print(f"[MATCH] {user_id} ↔ {pid}")
        return

    users_waiting[user_id] = {"gender": u["gender"], "region": u["region"]}
    msg = "Menunggu pasangan..."
    if target_gender:
        msg += f"\n🎯 Gender: {target_gender}"
    if target_region:
        msg += f"\n📍 Wilayah: {target_region}"
    await context.bot.send_message(user_id, msg)
    print(f"[WAIT] {user_id} waiting.")

# ---------------- STOP ----------------
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u.id in active_chats:
        p = active_chats[u.id]
        await update.message.reply_text("❌ Kamu keluar dari chat.")
        await context.bot.send_message(p, "❌ Pasanganmu keluar dari chat.")
        del active_chats[p]
        del active_chats[u.id]

        # tombol cari lagi
        keyboard = [
            [InlineKeyboardButton("🔁 Cari lagi", callback_data="find_again")],
            [InlineKeyboardButton("💎 Upgrade PRO", callback_data="upgrade_now")],
        ]
        await update.message.reply_text("Mau cari lagi atau upgrade?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if u.id in users_waiting:
        users_waiting.pop(u.id, None)
        await update.message.reply_text("🚫 Pencarian dibatalkan.")
    else:
        await update.message.reply_text("Kamu sedang tidak mencari pasangan.")

# ---------------- CALLBACKS ----------------
async def on_upgrade_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(f"💎 Upgrade ke PRO cuma Rp{PRO_PRICE:,}!\nKetik /upgrade untuk bayar via QRIS 📲")

async def on_find_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔁 Oke, mencari lagi...")
    await cmd_find(q, context)

# ---------------- RELAY ----------------
# ---------------- UPGRADE ----------------
async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user_record(u.id)

    order_id = str(uuid.uuid4())
    order_payload = {
        "order_ref_id": order_id,
        "price": PRO_PRICE,
        "description": f"Upgrade PRO untuk @{u.username or u.id}",
        "callback_url": f"https://pakasir.com/api/callback/{PAKASIR_SLUG}",
    }

    headers = {
        "Authorization": f"Bearer {PAKASIR_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(
            f"https://api.pakasir.com/v1/{PAKASIR_SLUG}/orders",
            headers=headers,
            json=order_payload,
            timeout=15,
        )
        res = r.json()
        if "data" in res and "qris_url" in res["data"]:
            qris_url = res["data"]["qris_url"]

            # simpan order pending
            user = get_user(u.id)
            user["pending_orders"].append(order_id)
            save_users(users)

            await update.message.reply_text(
                f"💎 *Upgrade PRO*\n\n"
                f"Harga: Rp{PRO_PRICE:,}\n"
                f"Klik link di bawah untuk bayar via QRIS:\n\n"
                f"{qris_url}\n\n"
                f"Setelah bayar, sistem akan otomatis mengaktifkan akun kamu dalam beberapa menit.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ Gagal membuat order QRIS. Coba lagi nanti.")

    except Exception as e:
        await update.message.reply_text(f"❌ Terjadi kesalahan: {e}")

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u.id not in active_chats:
        await update.message.reply_text("Kamu belum terhubung. Gunakan /find untuk mencari.")
        return
    pid = active_chats[u.id]
    msg = update.message
    try:
        if msg.text:
            await context.bot.send_message(pid, msg.text)
        elif msg.photo:
            await context.bot.send_photo(pid, msg.photo[-1].file_id, caption=msg.caption or "")
        elif msg.video:
            await context.bot.send_video(pid, msg.video.file_id, caption=msg.caption or "")
        elif msg.sticker:
            await context.bot.send_sticker(pid, msg.sticker.file_id)
        else:
            await update.message.reply_text("Jenis pesan ini belum didukung.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gagal mengirim: {e}")

# ---------------- RUN ----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("setgender", cmd_setgender))
    app.add_handler(CommandHandler("setregion", cmd_setregion))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("pro", cmd_pro))
    app.add_handler(CallbackQueryHandler(on_gender_chosen, pattern="^find_gender_"))
    app.add_handler(CallbackQueryHandler(on_region_chosen, pattern="^find_region_"))
    app.add_handler(CallbackQueryHandler(on_upgrade_now, pattern="^upgrade_now$"))
    app.add_handler(CallbackQueryHandler(on_find_again, pattern="^find_again$"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_message))
    print("🤖 Bot aktif dan siap jalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
