#!/usr/bin/env python3
"""
Telegram Number Info Bot - Render Deployment Ready
With 2 Free Uses and UPI Payment Integration
"""

import os
import json
import logging
import re
import time
import sqlite3
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from threading import Lock

import requests
import telebot
from telebot.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from flask import Flask, request, jsonify

# Load environment variables
load_dotenv()

# ===================================================
# CONFIGURATION
# ===================================================

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set!")

DATABASE_FILE = os.getenv('DATABASE_FILE', 'users.db')
UPI_ID = os.getenv('UPI_ID', 'your-upi@paytm')
UPI_NAME = os.getenv('UPI_NAME', 'Number Info Bot')
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '123456789').split(',') if id.strip()]
FREE_USES = int(os.getenv('FREE_USES', '2'))
PRICE_PER_SEARCH = int(os.getenv('PRICE_PER_SEARCH', '5'))

# ===================================================
# LOGGING
# ===================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================================================
# DATABASE
# ===================================================

class Database:
    def __init__(self, db_file=DATABASE_FILE):
        self.db_file = db_file
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_file, timeout=10)
    
    def init_db(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        free_uses INTEGER DEFAULT 2,
                        paid_uses INTEGER DEFAULT 0,
                        total_searches INTEGER DEFAULT 0,
                        is_premium BOOLEAN DEFAULT 0,
                        premium_expiry TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        amount REAL,
                        transaction_id TEXT UNIQUE,
                        payment_method TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        confirmed_at TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS search_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        phone_number TEXT,
                        search_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
                logger.info("✅ Database initialized")
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            raise
    
    def get_user(self, user_id):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                user = cursor.fetchone()
                if not user:
                    return self.create_user(user_id)
                return {
                    'user_id': user[0], 'username': user[1], 'first_name': user[2],
                    'last_name': user[3], 'free_uses': user[4], 'paid_uses': user[5],
                    'total_searches': user[6], 'is_premium': bool(user[7]),
                    'premium_expiry': user[8], 'created_at': user[9], 'last_active': user[10]
                }
        except Exception as e:
            logger.error(f"❌ Get user error: {e}")
            return None
    
    def create_user(self, user_id, username=None, first_name=None, last_name=None):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO users 
                    (user_id, username, first_name, last_name, free_uses)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name, FREE_USES))
                conn.commit()
                return self.get_user(user_id)
        except Exception as e:
            logger.error(f"❌ Create user error: {e}")
            return None
    
    def update_user(self, user_id, **kwargs):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                set_clause = ', '.join([f"{key} = ?" for key in kwargs.keys()])
                values = list(kwargs.values()) + [user_id]
                cursor.execute(f'UPDATE users SET {set_clause} WHERE user_id = ?', values)
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Update user error: {e}")
            return False
    
    def can_search(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return False
        if user['is_premium'] and user['premium_expiry']:
            try:
                expiry = datetime.strptime(user['premium_expiry'], '%Y-%m-%d %H:%M:%S')
                if expiry > datetime.now():
                    return True
            except:
                pass
        if user['free_uses'] > 0:
            return True
        if user['paid_uses'] > 0:
            return True
        return False
    
    def use_search(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return False
        if user['is_premium'] and user['premium_expiry']:
            try:
                expiry = datetime.strptime(user['premium_expiry'], '%Y-%m-%d %H:%M:%S')
                if expiry > datetime.now():
                    self.update_user(user_id, total_searches=user['total_searches'] + 1)
                    return True
            except:
                pass
        if user['free_uses'] > 0:
            self.update_user(user_id, free_uses=user['free_uses'] - 1, total_searches=user['total_searches'] + 1)
            return True
        if user['paid_uses'] > 0:
            self.update_user(user_id, paid_uses=user['paid_uses'] - 1, total_searches=user['total_searches'] + 1)
            return True
        return False
    
    def get_remaining(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return 0
        if user['is_premium'] and user['premium_expiry']:
            try:
                expiry = datetime.strptime(user['premium_expiry'], '%Y-%m-%d %H:%M:%S')
                if expiry > datetime.now():
                    return float('inf')
            except:
                pass
        return user['free_uses'] + user['paid_uses']
    
    def add_paid_uses(self, user_id, amount, transaction_id):
        uses = int(amount / PRICE_PER_SEARCH)
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO payments (user_id, amount, transaction_id, payment_method, status)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, amount, transaction_id, 'UPI', 'confirmed'))
                cursor.execute('UPDATE users SET paid_uses = paid_uses + ? WHERE user_id = ?', (uses, user_id))
                conn.commit()
                return uses
        except Exception as e:
            logger.error(f"❌ Add paid uses error: {e}")
            return 0
    
    def add_premium(self, user_id, days=30):
        expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        self.update_user(user_id, is_premium=1, premium_expiry=expiry)
        return expiry
    
    def save_search_history(self, user_id, phone_number):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO search_history (user_id, phone_number) VALUES (?, ?)',
                             (user_id, phone_number))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Save history error: {e}")
            return False
    
    def get_user_stats(self, user_id):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM search_history WHERE user_id = ?', (user_id,))
                total_searches = cursor.fetchone()[0]
                cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id = ? AND status = "confirmed"',
                             (user_id,))
                total_spent = cursor.fetchone()[0]
                return {'total_searches': total_searches, 'total_spent': total_spent}
        except Exception as e:
            logger.error(f"❌ Get stats error: {e}")
            return {'total_searches': 0, 'total_spent': 0}

# ===================================================
# BOT INITIALIZATION
# ===================================================

db = Database()
logger.info("✅ Database initialized")

try:
    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
    bot_info = bot.get_me()
    logger.info(f"✅ Bot connected: @{bot_info.username}")
except Exception as e:
    logger.error(f"❌ Bot initialization failed: {e}")
    raise

# ===================================================
# KEYBOARDS
# ===================================================

def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("🔍 Get Info"),
        KeyboardButton("💰 Balance")
    )
    keyboard.add(
        KeyboardButton("💳 Buy Credits"),
        KeyboardButton("❓ Help")
    )
    return keyboard

def get_payment_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("₹10 - 2 Searches", callback_data="pay_10"),
        InlineKeyboardButton("₹25 - 5 Searches", callback_data="pay_25")
    )
    markup.add(
        InlineKeyboardButton("₹50 - 10 Searches", callback_data="pay_50"),
        InlineKeyboardButton("₹100 - 20 Searches + Premium", callback_data="pay_100")
    )
    markup.add(
        InlineKeyboardButton("⭐ Premium ₹150/Month", callback_data="pay_150")
    )
    return markup

# ===================================================
# UTILITY FUNCTIONS
# ===================================================

def is_valid_mobile_number(number: str) -> bool:
    number = re.sub(r'[\s\-\(\)]', '', number.strip())
    return bool(re.match(r'^\+?\d{10,15}$', number))

def format_phone_number(number: str) -> str:
    number = re.sub(r'\D', '', number)
    if len(number) == 10:
        return f"+91 {number[:5]} {number[5:]}"
    elif len(number) == 12 and number.startswith('91'):
        return f"+{number[:2]} {number[2:7]} {number[7:]}"
    else:
        return f"+{number}"

def fetch_number_info(number: str) -> Optional[List[Dict]]:
    clean_number = re.sub(r'\D', '', number)
    # Return mock data
    mock_data = {
        "mobile": clean_number,
        "name": f"User_{clean_number[-4:]}",
        "fname": f"Father_{clean_number[-4:]}",
        "alt": f"Alternate_{clean_number[-4:]}",
        "circle": "DELHI",
        "id": f"ID_{clean_number[-4:]}",
        "email": f"user_{clean_number[-4:]}@example.com",
        "address": f"Test Address, New Delhi, India"
    }
    return [mock_data]

def format_record(record: Dict, index: int) -> str:
    return f"""
╭━━━〔 📱 NUMBER INFO {index+1} 〕━━━⬣

👤 Name      : {record.get('name', 'N/A')}
👨 Father    : {record.get('fname', 'N/A')}
📞 Number    : {record.get('mobile', 'N/A')}
📱 Alternate : {record.get('alt', 'N/A')}
📡 Circle    : {record.get('circle', 'N/A')}
🆔 ID        : {record.get('id', 'N/A')}
📧 Email     : {record.get('email', 'N/A')}

🏠 Address :
{record.get('address', 'N/A')}

━━━━━━━━━━━━━━━━━━━━"""

# ===================================================
# BOT HANDLERS
# ===================================================

@bot.message_handler(commands=['start'])
def handle_start(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    if user:
        db.update_user(user_id,
                      username=message.from_user.username or '',
                      first_name=message.from_user.first_name or '',
                      last_name=message.from_user.last_name or '',
                      last_active=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    remaining = db.get_remaining(user_id)
    remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
    
    welcome = f"""
🌟 <b>Welcome to Number Info Bot!</b>

Get detailed information about any mobile number.

━━━━━━━━━━━━━━━━━━━
<b>Your Status:</b>
• Free Uses: {user['free_uses'] if user else FREE_USES}
• Paid Uses: {user['paid_uses'] if user else 0}
• Premium: {'✅ Yes' if user and user['is_premium'] else '❌ No'}
• Remaining: {remaining_text}

<b>💰 Pricing:</b>
• ₹10 - 2 Searches
• ₹25 - 5 Searches
• ₹50 - 10 Searches
• ₹100 - 20 Searches + 7 Days Premium
• ₹150 - 30 Days Premium (Unlimited)

<b>📌 How to use:</b>
1️⃣ Press <code>🔍 Get Info</code>
2️⃣ Send any valid mobile number
3️⃣ Get detailed information
"""
    bot.send_message(message.chat.id, welcome, reply_markup=get_main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🔍 Get Info")
def handle_get_info(message: Message):
    user_id = message.from_user.id
    if not db.can_search(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits"))
        bot.send_message(message.chat.id, 
            "⚠️ <b>No Searches Remaining!</b>\n\nPlease buy credits to continue.",
            reply_markup=markup, parse_mode="HTML")
        return
    
    bot.send_message(message.chat.id,
        "📱 <b>Please Send Mobile Number</b>\n\nExamples:\n<code>9876543210</code>\n<code>919876543210</code>",
        parse_mode="HTML", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def handle_balance(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    stats = db.get_user_stats(user_id)
    remaining = db.get_remaining(user_id)
    remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
    
    msg = f"""
💰 <b>Your Balance</b>

📊 <b>Account Status:</b>
• Free Uses: {user['free_uses']}
• Paid Uses: {user['paid_uses']}
• Total Remaining: {remaining_text}
• Premium: {'✅ Active' if user['is_premium'] else '❌ Inactive'}

📈 <b>Statistics:</b>
• Total Searches: {stats['total_searches']}
• Total Spent: ₹{stats['total_spent']:.2f}
"""
    bot.send_message(message.chat.id, msg, reply_markup=get_main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "💳 Buy Credits")
@bot.message_handler(commands=['buy'])
def handle_buy(message: Message):
    msg = f"""
💳 <b>Buy Credits</b>

<b>📦 Packages:</b>
• ₹10 - 2 Searches
• ₹25 - 5 Searches  
• ₹50 - 10 Searches
• ₹100 - 20 Searches + 7 Days Premium

<b>⭐ Premium:</b>
• ₹150 - 30 Days Unlimited

<b>📱 Pay to UPI:</b> <code>{UPI_ID}</code>
"""
    bot.send_message(message.chat.id, msg, reply_markup=get_payment_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "❓ Help")
def handle_help(message: Message):
    help_msg = f"""
❓ <b>Help & Support</b>

<b>Commands:</b>
/start - Start the bot
/buy - Buy credits
/balance - Check balance
/help - Show this help

<b>Payment:</b>
UPI: <code>{UPI_ID}</code>

<b>Support:</b>
Contact @YourSupportUsername
"""
    bot.send_message(message.chat.id, help_msg, reply_markup=get_main_keyboard(), parse_mode="HTML")

@bot.message_handler(content_types=['text'])
def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text.startswith('/'):
        return
    
    if not is_valid_mobile_number(text):
        bot.send_message(message.chat.id,
            "❌ <b>Invalid Number.</b>\nSend 10-digit number like <code>9876543210</code>",
            parse_mode="HTML", reply_markup=get_main_keyboard())
        return
    
    if not db.can_search(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits"))
        bot.send_message(message.chat.id, "⚠️ No searches left! Buy credits.",
            reply_markup=markup, parse_mode="HTML")
        return
    
    clean = re.sub(r'\D', '', text)
    db.use_search(user_id)
    db.save_search_history(user_id, clean)
    
    searching = bot.send_message(message.chat.id, f"🔍 Searching for {format_phone_number(clean)}...")
    
    results = fetch_number_info(clean)
    bot.delete_message(message.chat.id, searching.message_id)
    
    if not results:
        bot.send_message(message.chat.id, f"❌ No data found for {format_phone_number(clean)}",
            reply_markup=get_main_keyboard())
        return
    
    for idx, record in enumerate(results):
        bot.send_message(message.chat.id, format_record(record, idx), reply_markup=get_main_keyboard())
    
    remaining = db.get_remaining(user_id)
    remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
    bot.send_message(message.chat.id, f"📊 Remaining: {remaining_text}", reply_markup=get_main_keyboard())

# ===================================================
# CALLBACKS
# ===================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    
    if call.data == "buy_credits":
        handle_buy(call.message)
        bot.answer_callback_query(call.id)
        return
    
    if call.data.startswith("pay_"):
        amount = int(call.data.split('_')[1])
        package_map = {10: "2 Searches", 25: "5 Searches", 50: "10 Searches", 
                      100: "20 Searches + 7 Days Premium", 150: "30 Days Premium"}
        
        tid = str(uuid.uuid4())[:8].upper()
        
        msg = f"""
💳 <b>Payment Instructions</b>

📦 <b>Package:</b> {package_map.get(amount, '')}
💰 <b>Amount:</b> ₹{amount}
🆔 <b>ID:</b> <code>{tid}</code>

<b>📱 Steps:</b>
1. Pay ₹{amount} to UPI: <code>{UPI_ID}</code>
2. Click "I've Paid" below
"""
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ I've Paid", callback_data=f"confirm_{amount}_{tid}"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")
        )
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id,
                             reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
    
    if call.data.startswith("confirm_"):
        _, amount, tid = call.data.split('_')
        amount = int(amount)
        
        # Check if already processed
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM payments WHERE transaction_id = ? AND user_id = ?', (tid, user_id))
                if cursor.fetchone():
                    bot.answer_callback_query(call.id, "⚠️ Already processed!")
                    return
        except:
            pass
        
        # Process payment
        uses = {10: 2, 25: 5, 50: 10, 100: 20, 150: 0}.get(amount, 0)
        premium_days = {100: 7, 150: 30}.get(amount, 0)
        
        db.add_paid_uses(user_id, amount, tid)
        
        if premium_days > 0:
            expiry = db.add_premium(user_id, premium_days)
            premium_text = f"✅ Premium Active for {premium_days} days (Expires: {expiry})"
        else:
            premium_text = "❌ No Premium"
        
        success = f"""
✅ <b>Payment Confirmed!</b>

💰 <b>Amount:</b> ₹{amount}
🆔 <b>ID:</b> <code>{tid}</code>

<b>Credits Added:</b>
• Paid Uses: +{uses}
• Premium: {premium_text}

🔍 <b>You can now continue searching!</b>
"""
        bot.edit_message_text(success, call.message.chat.id, call.message.message_id, parse_mode="HTML")
        bot.answer_callback_query(call.id, "✅ Payment confirmed!")
        return
    
    if call.data == "cancel_payment":
        bot.edit_message_text("❌ Payment cancelled.", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

# ===================================================
# ADMIN COMMANDS
# ===================================================

@bot.message_handler(commands=['admin'])
def admin_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "🔐 Admin Panel\n\n/stats - Statistics\n/addpremium [user] [days]\n/addcredits [user] [amount]")

@bot.message_handler(commands=['stats'])
def stats_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        with db.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM users')
            total_users = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM users WHERE is_premium = 1')
            premium_users = c.fetchone()[0]
            c.execute('SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = "confirmed"')
            revenue = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM search_history')
            searches = c.fetchone()[0]
        
        msg = f"""
📊 <b>Bot Statistics</b>

👥 Users: {total_users}
⭐ Premium: {premium_users}
💰 Revenue: ₹{revenue:.2f}
🔍 Searches: {searches}
"""
        bot.send_message(message.chat.id, msg, parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(commands=['addpremium'])
def add_premium_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        target = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        expiry = db.add_premium(target, days)
        bot.send_message(message.chat.id, f"✅ Premium added for {target}\nExpires: {expiry}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(commands=['addcredits'])
def add_credits_cmd(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        target = int(parts[1])
        amount = float(parts[2])
        tid = f"ADMIN_{uuid.uuid4().hex[:8].upper()}"
        uses = db.add_paid_uses(target, amount, tid)
        bot.send_message(message.chat.id, f"✅ Added {uses} credits (₹{amount}) to {target}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

# ===================================================
# FLASK WEBHOOK (For Render)
# ===================================================

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        'status': 'running',
        'bot': bot_info.username,
        'time': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ===================================================
# RUN
# ===================================================

def run_bot():
    logger.info(f"🚀 Starting bot @{bot_info.username}")
    logger.info(f"💳 UPI: {UPI_ID}")
    logger.info(f"🎁 Free uses: {FREE_USES}")
    logger.info(f"💰 Price: ₹{PRICE_PER_SEARCH}")
    
    # Start bot polling in background for Render
    import threading
    def poll():
        while True:
            try:
                bot.polling(none_stop=True, interval=1, timeout=60)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)
    
    threading.Thread(target=poll, daemon=True).start()
    
    # Run Flask app
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    run_bot()
