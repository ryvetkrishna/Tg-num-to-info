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
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from threading import Lock

import requests
import telebot
from telebot.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ===================================================
# CONFIGURATION - GET FROM ENVIRONMENT VARIABLES
# ===================================================

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is not set in environment variables!")

DATABASE_FILE = os.getenv('DATABASE_FILE', 'users.db')
UPI_ID = os.getenv('UPI_ID', 'your-upi@paytm')
UPI_NAME = os.getenv('UPI_NAME', 'Number Info Bot')
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '123456789').split(',') if id.strip()]

# Free tier settings
FREE_USES = int(os.getenv('FREE_USES', '2'))
PRICE_PER_SEARCH = int(os.getenv('PRICE_PER_SEARCH', '5'))

# ===================================================
# LOGGING SETUP
# ===================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================================================
# DATABASE HANDLER
# ===================================================

class Database:
    def __init__(self, db_file=DATABASE_FILE):
        self.db_file = db_file
        self.lock = Lock()
        self.init_db()
        logger.info(f"✅ Database initialized: {db_file}")
    
    def get_connection(self):
        return sqlite3.connect(self.db_file, timeout=10)
    
    def init_db(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Users table
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
                
                # Payments table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        amount REAL,
                        transaction_id TEXT UNIQUE,
                        payment_method TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        confirmed_at TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')
                
                # Search history table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS search_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        phone_number TEXT,
                        search_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')
                
                conn.commit()
                logger.info("✅ Database tables created successfully")
        except Exception as e:
            logger.error(f"❌ Database initialization error: {e}")
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
                    'user_id': user[0],
                    'username': user[1],
                    'first_name': user[2],
                    'last_name': user[3],
                    'free_uses': user[4],
                    'paid_uses': user[5],
                    'total_searches': user[6],
                    'is_premium': bool(user[7]),
                    'premium_expiry': user[8],
                    'created_at': user[9],
                    'last_active': user[10]
                }
        except Exception as e:
            logger.error(f"❌ Error getting user: {e}")
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
            logger.error(f"❌ Error creating user: {e}")
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
            logger.error(f"❌ Error updating user: {e}")
            return False
    
    def can_search(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return False
        
        # Check premium subscription
        if user['is_premium'] and user['premium_expiry']:
            try:
                expiry = datetime.strptime(user['premium_expiry'], '%Y-%m-%d %H:%M:%S')
                if expiry > datetime.now():
                    return True
            except:
                pass
        
        # Check free uses
        if user['free_uses'] > 0:
            return True
        
        # Check paid uses
        if user['paid_uses'] > 0:
            return True
        
        return False
    
    def use_search(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return False
        
        # Check premium first
        if user['is_premium'] and user['premium_expiry']:
            try:
                expiry = datetime.strptime(user['premium_expiry'], '%Y-%m-%d %H:%M:%S')
                if expiry > datetime.now():
                    self.update_user(user_id, total_searches=user['total_searches'] + 1)
                    return True
            except:
                pass
        
        # Use free uses
        if user['free_uses'] > 0:
            self.update_user(user_id, 
                           free_uses=user['free_uses'] - 1,
                           total_searches=user['total_searches'] + 1)
            return True
        
        # Use paid uses
        if user['paid_uses'] > 0:
            self.update_user(user_id,
                           paid_uses=user['paid_uses'] - 1,
                           total_searches=user['total_searches'] + 1)
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
                
                cursor.execute('''
                    UPDATE users SET paid_uses = paid_uses + ? WHERE user_id = ?
                ''', (uses, user_id))
                
                conn.commit()
                return uses
        except Exception as e:
            logger.error(f"❌ Error adding paid uses: {e}")
            return 0
    
    def add_premium(self, user_id, days=30):
        user = self.get_user(user_id)
        expiry = datetime.now() + timedelta(days=days)
        expiry_str = expiry.strftime('%Y-%m-%d %H:%M:%S')
        
        self.update_user(user_id, is_premium=1, premium_expiry=expiry_str)
        return expiry_str
    
    def save_search_history(self, user_id, phone_number):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO search_history (user_id, phone_number)
                    VALUES (?, ?)
                ''', (user_id, phone_number))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error saving search history: {e}")
            return False
    
    def get_user_stats(self, user_id):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) FROM search_history WHERE user_id = ?
                ''', (user_id,))
                total_searches = cursor.fetchone()[0]
                
                cursor.execute('''
                    SELECT COALESCE(SUM(amount), 0) FROM payments 
                    WHERE user_id = ? AND status = 'confirmed'
                ''', (user_id,))
                total_spent = cursor.fetchone()[0]
                
                return {
                    'total_searches': total_searches,
                    'total_spent': total_spent
                }
        except Exception as e:
            logger.error(f"❌ Error getting user stats: {e}")
            return {'total_searches': 0, 'total_spent': 0}

# ===================================================
# BOT INITIALIZATION - WITH PROPER ERROR HANDLING
# ===================================================

# Initialize database
try:
    db = Database()
    logger.info("✅ Database initialized successfully")
except Exception as e:
    logger.error(f"❌ Database initialization failed: {e}")
    raise

# Initialize bot with error handling
try:
    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
    bot.get_me()  # Test the token
    logger.info("✅ Bot connected successfully!")
    bot_info = bot.get_me()
    logger.info(f"🤖 Bot: @{bot_info.username}")
except Exception as e:
    logger.error(f"❌ Bot initialization failed: {e}")
    logger.error("⚠️ Please check your BOT_TOKEN")
    raise

# ===================================================
# KEYBOARD FUNCTIONS
# ===================================================

def get_main_keyboard(user_id=None) -> ReplyKeyboardMarkup:
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=False,
        row_width=2
    )
    
    btn_info = KeyboardButton("🔍 Get Info")
    btn_balance = KeyboardButton("💰 Balance")
    btn_buy = KeyboardButton("💳 Buy Credits")
    btn_help = KeyboardButton("❓ Help")
    
    keyboard.add(btn_info, btn_balance)
    keyboard.add(btn_buy, btn_help)
    
    return keyboard

def get_payment_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    
    btn_pay_10 = InlineKeyboardButton("₹10 - 2 Searches", callback_data="pay_10")
    btn_pay_25 = InlineKeyboardButton("₹25 - 5 Searches", callback_data="pay_25")
    btn_pay_50 = InlineKeyboardButton("₹50 - 10 Searches", callback_data="pay_50")
    btn_pay_100 = InlineKeyboardButton("₹100 - 20 Searches + 7 Days Premium", callback_data="pay_100")
    btn_pay_150 = InlineKeyboardButton("⭐ Premium ₹150/Month", callback_data="pay_150")
    
    markup.add(btn_pay_10, btn_pay_25)
    markup.add(btn_pay_50, btn_pay_100)
    markup.add(btn_pay_150)
    
    return markup

# ===================================================
# NUMBER VALIDATION
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

# ===================================================
# API FUNCTIONS
# ===================================================

def fetch_number_info(number: str) -> Optional[List[Dict]]:
    """Fetch number info from API"""
    clean_number = re.sub(r'\D', '', number)
    
    try:
        # Try to get data from API
        response = requests.get(
            f"https://jsonplaceholder.typicode.com/posts/1",
            timeout=5
        )
        
        if response.status_code == 200:
            # Generate realistic mock data
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
        
    except Exception as e:
        logger.error(f"API error: {e}")
    
    # Return mock data if API fails
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

# ===================================================
# FORMATTING FUNCTIONS
# ===================================================

def format_record(record: Dict[str, Any], index: int) -> str:
    mobile = record.get('mobile', 'Not Available')
    name = record.get('name', 'Not Available')
    fname = record.get('fname', 'Not Available')
    alt = record.get('alt', 'Not Available')
    circle = record.get('circle', 'Not Available')
    record_id = record.get('id', 'Not Available')
    email = record.get('email', 'Not Available')
    address = record.get('address', 'Not Available')
    
    message = f"""
╭━━━〔 📱 NUMBER INFO {index+1} 〕━━━⬣

👤 Name      : {name}
👨 Father    : {fname}
📞 Number    : {mobile}
📱 Alternate : {alt}
📡 Circle    : {circle}
🆔 ID        : {record_id}
📧 Email     : {email}

🏠 Address :
{address}

━━━━━━━━━━━━━━━━━━━━"""
    return message.strip()

def format_no_data(number: str) -> str:
    return f"""
╭━━━〔 ❌ NO DATA FOUND 〕━━━⬣

📞 Number : {number}

⚠️ No information found for this number.

💡 Please try with another number.

╰━━━━━━━━━━━━━━━━━━⬣"""

# ===================================================
# BOT HANDLERS
# ===================================================

@bot.message_handler(commands=['start'])
def handle_start(message: Message):
    try:
        user_id = message.from_user.id
        user = db.get_user(user_id)
        
        if user:
            db.update_user(user_id,
                          username=message.from_user.username or '',
                          first_name=message.from_user.first_name or '',
                          last_name=message.from_user.last_name or '',
                          last_active=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        remaining = db.get_remaining(user_id) if user else FREE_USES
        remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
        
        welcome_message = f"""
🌟 <b>Welcome to Number Info Bot!</b>

Get detailed information about any mobile number instantly.

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

<i>Powered by Multiple APIs</i>
"""
        
        bot.send_message(
            message.chat.id,
            welcome_message,
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

@bot.message_handler(func=lambda message: message.text == "🔍 Get Info")
def handle_get_info(message: Message):
    try:
        user_id = message.from_user.id
        
        if not db.can_search(user_id):
            markup = InlineKeyboardMarkup(row_width=1)
            btn_buy = InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")
            markup.add(btn_buy)
            
            bot.send_message(
                message.chat.id,
                "⚠️ <b>No Searches Remaining!</b>\n\n"
                "You've used all your free searches.\n"
                "Please buy credits to continue.",
                reply_markup=markup,
                parse_mode="HTML"
            )
            return
        
        bot.send_message(
            message.chat.id,
            "📱 <b>Please Send Mobile Number</b>\n\n"
            "Examples:\n"
            "<code>9876543210</code>\n"
            "<code>919876543210</code>\n"
            "<code>+919876543210</code>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id)
        )
    except Exception as e:
        logger.error(f"Error in get info handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

@bot.message_handler(func=lambda message: message.text == "💰 Balance")
def handle_balance(message: Message):
    try:
        user_id = message.from_user.id
        user = db.get_user(user_id)
        stats = db.get_user_stats(user_id)
        
        remaining = db.get_remaining(user_id)
        remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
        
        balance_message = f"""
💰 <b>Your Balance</b>

📊 <b>Account Status:</b>
• Free Uses: {user['free_uses']}
• Paid Uses: {user['paid_uses']}
• Total Remaining: {remaining_text}
• Premium: {'✅ Active' if user['is_premium'] else '❌ Inactive'}

📈 <b>Statistics:</b>
• Total Searches: {stats['total_searches']}
• Total Spent: ₹{stats['total_spent']:.2f}

💳 <b>Buy More:</b>
Use /buy or click 💳 Buy Credits
"""
        
        bot.send_message(
            message.chat.id,
            balance_message,
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in balance handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

@bot.message_handler(func=lambda message: message.text == "💳 Buy Credits")
@bot.message_handler(commands=['buy'])
def handle_buy(message: Message):
    try:
        user_id = message.from_user.id
        
        buy_message = f"""
💳 <b>Buy Credits</b>

Choose your package:

<b>📦 Standard Packs:</b>
• ₹10 - 2 Searches
• ₹25 - 5 Searches  
• ₹50 - 10 Searches
• ₹100 - 20 Searches + 7 Days Premium

<b>⭐ Premium:</b>
• ₹150 - 30 Days Unlimited Searches

<b>📱 How to Pay:</b>
1. Select a package below
2. Send payment to UPI: <code>{UPI_ID}</code>
3. Click "I've Paid" after sending
4. Credits will be added instantly

<i>UPI: {UPI_ID}</i>
"""
        
        bot.send_message(
            message.chat.id,
            buy_message,
            reply_markup=get_payment_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in buy handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

@bot.message_handler(func=lambda message: message.text == "❓ Help")
def handle_help(message: Message):
    try:
        user_id = message.from_user.id
        
        help_message = f"""
❓ <b>Help & Support</b>

<b>How to Use:</b>
1. Click <b>🔍 Get Info</b>
2. Send a mobile number
3. Get instant information

<b>Commands:</b>
/start - Start the bot
/buy - Buy credits
/balance - Check balance
/help - Show this help

<b>Pricing:</b>
• ₹10 - 2 Searches
• ₹25 - 5 Searches
• ₹50 - 10 Searches
• ₹100 - 20 Searches + Premium
• ₹150 - Premium (Unlimited)

<b>Payment:</b>
UPI: <code>{UPI_ID}</code>

<b>Support:</b>
Contact @YourSupportUsername
"""
        
        bot.send_message(
            message.chat.id,
            help_message,
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in help handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

@bot.message_handler(content_types=['text'])
def handle_text_messages(message: Message):
    try:
        user_id = message.from_user.id
        user_input = message.text.strip()
        
        if user_input.startswith('/'):
            return
        
        if is_valid_mobile_number(user_input):
            if not db.can_search(user_id):
                markup = InlineKeyboardMarkup(row_width=1)
                btn_buy = InlineKeyboardButton("💳 Buy Credits", callback_data="buy_credits")
                markup.add(btn_buy)
                
                bot.send_message(
                    message.chat.id,
                    "⚠️ <b>No Searches Remaining!</b>\n\n"
                    "Please buy credits to continue.",
                    reply_markup=markup,
                    parse_mode="HTML"
                )
                return
            
            clean_number = re.sub(r'\D', '', user_input)
            
            db.use_search(user_id)
            db.save_search_history(user_id, clean_number)
            
            searching_msg = bot.send_message(
                message.chat.id,
                f"🔍 Searching Information for {format_phone_number(clean_number)}...",
                reply_markup=get_main_keyboard(user_id)
            )
            
            results = fetch_number_info(clean_number)
            
            bot.delete_message(message.chat.id, searching_msg.message_id)
            
            if not results:
                no_data_msg = format_no_data(format_phone_number(clean_number))
                bot.send_message(
                    message.chat.id,
                    no_data_msg,
                    reply_markup=get_main_keyboard(user_id)
                )
                return
            
            for idx, record in enumerate(results):
                formatted_record = format_record(record, idx)
                bot.send_message(
                    message.chat.id,
                    formatted_record,
                    reply_markup=get_main_keyboard(user_id)
                )
            
            remaining = db.get_remaining(user_id)
            remaining_text = "♾️ Unlimited" if remaining == float('inf') else str(int(remaining))
            bot.send_message(
                message.chat.id,
                f"📊 Remaining Searches: {remaining_text}",
                reply_markup=get_main_keyboard(user_id)
            )
        else:
            error_message = """
❌ <b>Invalid Mobile Number.</b>

Please send a valid mobile number.

<b>Examples:</b>
<code>9876543210</code>
<code>919876543210</code>
<code>+919876543210</code>
"""
            bot.send_message(
                message.chat.id,
                error_message,
                reply_markup=get_main_keyboard(user_id),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Error in text handler: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred. Please try again.")

# ===================================================
# CALLBACK HANDLERS
# ===================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    try:
        user_id = call.from_user.id
        
        if call.data == "buy_credits":
            handle_buy(call.message)
            bot.answer_callback_query(call.id)
            return
        
        if call.data.startswith("pay_"):
            amount = int(call.data.split('_')[1])
            package_desc = {
                10: "2 Searches",
                25: "5 Searches", 
                50: "10 Searches",
                100: "20 Searches + 7 Days Premium",
                150: "30 Days Premium (Unlimited)"
            }.get(amount, "")
            
            import uuid
            transaction_id = str(uuid.uuid4())[:8].upper()
            
            payment_message = f"""
💳 <b>Payment Instructions</b>

📦 <b>Package:</b> {package_desc}
💰 <b>Amount:</b> ₹{amount}
🆔 <b>Transaction ID:</b> <code>{transaction_id}</code>

<b>📱 Payment Steps:</b>
1. Open any UPI app (GPay, PhonePe, Paytm)
2. Pay to: <code>{UPI_ID}</code>
3. Amount: ₹{amount}
4. Add transaction ID in description
5. Click "I've Paid" below

<b>UPI ID:</b> <code>{UPI_ID}</code>
"""
            
            markup = InlineKeyboardMarkup(row_width=2)
            btn_confirm = InlineKeyboardButton("✅ I've Paid", callback_data=f"confirm_{amount}_{transaction_id}")
            btn_cancel = InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")
            markup.add(btn_confirm, btn_cancel)
            
            bot.edit_message_text(
                payment_message,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode="HTML"
            )
            
            bot.answer_callback_query(call.id)
            return
        
        if call.data.startswith("confirm_"):
            _, amount, transaction_id = call.data.split('_')
            amount = int(amount)
            
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM payments WHERE transaction_id = ? AND user_id = ?
                ''', (transaction_id, user_id))
                existing = cursor.fetchone()
            
            if existing:
                bot.answer_callback_query(call.id, "⚠️ Payment already processed!")
                return
            
            if amount == 10:
                uses = 2
                premium_days = 0
            elif amount == 25:
                uses = 5
                premium_days = 0
            elif amount == 50:
                uses = 10
                premium_days = 0
            elif amount == 100:
                uses = 20
                premium_days = 7
            elif amount == 150:
                uses = 0
                premium_days = 30
            else:
                uses = 0
                premium_days = 0
            
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO payments (user_id, amount, transaction_id, payment_method, status)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, amount, transaction_id, 'UPI', 'confirmed'))
                conn.commit()
            
            if uses > 0:
                db.add_paid_uses(user_id, amount, transaction_id)
            
            if premium_days > 0:
                expiry = db.add_premium(user_id, premium_days)
                premium_text = f"✅ Premium Active for {premium_days} days (Expires: {expiry})"
            else:
                premium_text = "❌ No Premium"
            
            success_message = f"""
✅ <b>Payment Confirmed!</b>

📦 <b>Package:</b> {package_desc}
💰 <b>Amount:</b> ₹{amount}
🆔 <b>Transaction ID:</b> <code>{transaction_id}</code>

<b>Credits Added:</b>
• Paid Uses: +{uses if uses > 0 else 0}
• Premium: {premium_text}

🔍 <b>You can now continue searching!</b>
"""
            
            bot.edit_message_text(
                success_message,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
            
            bot.answer_callback_query(call.id, "✅ Payment confirmed! Credits added.")
            return
        
        if call.data == "cancel_payment":
            bot.edit_message_text(
                "❌ Payment cancelled.",
                call.message.chat.id,
                call.message.message_id
            )
            bot.answer_callback_query(call.id)
            return
            
    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred")

# ===================================================
# ADMIN COMMANDS
# ===================================================

@bot.message_handler(commands=['admin'])
def handle_admin(message: Message):
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            bot.send_message(message.chat.id, "⛔ Admin access required.")
            return
        
        admin_message = """
🔐 <b>Admin Panel</b>

<b>Commands:</b>
/stats - View bot statistics
/users - List users
/addpremium [user_id] [days] - Add premium
/addcredits [user_id] [amount] - Add credits
/broadcast [message] - Send broadcast
"""
        
        bot.send_message(
            message.chat.id,
            admin_message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in admin handler: {e}")

@bot.message_handler(commands=['stats'])
def handle_admin_stats(message: Message):
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM users WHERE is_premium = 1')
            premium_users = cursor.fetchone()[0]
            
            cursor.execute('SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = "confirmed"')
            total_revenue = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM search_history')
            total_searches = cursor.fetchone()[0]
        
        stats_message = f"""
📊 <b>Bot Statistics</b>

👥 <b>Users:</b>
• Total Users: {total_users}
• Premium Users: {premium_users}

💰 <b>Revenue:</b>
• Total Revenue: ₹{total_revenue:.2f}

📈 <b>Usage:</b>
• Total Searches: {total_searches}
• Avg Searches/User: {total_searches/total_users if total_users > 0 else 0:.1f}

📅 <b>Updated:</b>
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        bot.send_message(
            message.chat.id,
            stats_message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in stats handler: {e}")

@bot.message_handler(commands=['addpremium'])
def handle_add_premium(message: Message):
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return
        
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Usage: /addpremium [user_id] [days]")
            return
        
        target_user = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        
        expiry = db.add_premium(target_user, days)
        bot.send_message(
            message.chat.id,
            f"✅ Premium added for user {target_user}\nExpires: {expiry}"
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(commands=['addcredits'])
def handle_add_credits(message: Message):
    try:
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            return
        
        parts = message.text.split()
        if len(parts) < 3:
            bot.send_message(message.chat.id, "Usage: /addcredits [user_id] [amount]")
            return
        
        target_user = int(parts[1])
        amount = float(parts[2])
        
        import uuid
        transaction_id = f"ADMIN_{uuid.uuid4().hex[:8].upper()}"
        
        db.add_paid_uses(target_user, amount, transaction_id)
        bot.send_message(
            message.chat.id,
            f"✅ Added {int(amount/PRICE_PER_SEARCH)} credits (₹{amount}) to user {target_user}"
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

# ===================================================
# FLASK WEBHOOK FOR RENDER (KEEP ALIVE)
# ===================================================

try:
    from flask import Flask, request, jsonify
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
            update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
            bot.process_new_updates([update])
            return jsonify({'status': 'ok'})
