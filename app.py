import streamlit as st
import sqlite3
import os
import hashlib
import time
import warnings
from datetime import datetime
from typing import List, Optional
from PIL import Image
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import base64
import requests

# --- HELPER: IMAGE TO BASE64 ---
def get_image_base64(path):
    """Converts an image file to a base64 string for HTML rendering"""
    try:
        with open(path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode('utf-8')
    except:
        return None

# --- PYSUI IMPORTS (BLOCKCHAIN LAYER) ---
warnings.filterwarnings("ignore", category=DeprecationWarning)
from pysui import SuiConfig, SyncClient
from pysui.sui.sui_txn import SyncTransaction
from pysui.sui.sui_types import SuiString, SuiInteger, SuiAddress
from pysui.sui.sui_crypto import gen_mnemonic_phrase, recover_key_and_address
from pysui.abstracts.client_keypair import SignatureScheme

# --- CONFIGURATION ---
DB_PATH = "twitter_clone.db"
UPLOAD_DIR = "uploads"
PROFILE_PIC_DIR = os.path.join(UPLOAD_DIR, "profiles")
POST_IMAGE_DIR = os.path.join(UPLOAD_DIR, "posts")
SUI_RPC_URL = "https://fullnode.mainnet.sui.io:443"

os.makedirs(PROFILE_PIC_DIR, exist_ok=True)
os.makedirs(POST_IMAGE_DIR, exist_ok=True)

# -----------------------
# DATABASE HELPERS
# -----------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        display_name TEXT,
        password_hash TEXT,
        bio TEXT,
        profile_pic_path TEXT,
        created_at REAL,
        wallet_address TEXT,
        private_key TEXT,
        mnemonic TEXT
    )
    """)
    c.execute("""CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, image_path TEXT, created_at REAL, orig_post_id INTEGER DEFAULT NULL, FOREIGN KEY(user_id) REFERENCES users(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS follows (follower_id INTEGER, followed_id INTEGER, created_at REAL, PRIMARY KEY (follower_id, followed_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS likes (user_id INTEGER, post_id INTEGER, created_at REAL, PRIMARY KEY (user_id, post_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bookmarks (user_id INTEGER, post_id INTEGER, created_at REAL, PRIMARY KEY (user_id, post_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY, post_id INTEGER, user_id INTEGER, text TEXT, created_at REAL, FOREIGN KEY(post_id) REFERENCES posts(id), FOREIGN KEY(user_id) REFERENCES users(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, sender_id INTEGER, receiver_id INTEGER, text TEXT, created_at REAL, FOREIGN KEY(sender_id) REFERENCES users(id), FOREIGN KEY(receiver_id) REFERENCES users(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, seen INTEGER DEFAULT 0, created_at REAL, FOREIGN KEY(user_id) REFERENCES users(id))""")
    conn.commit()
    return conn

# -----------------------
# WEB3 / CRYPTO FUNCTIONS
# -----------------------
def generate_new_wallet():
    mnemonic = gen_mnemonic_phrase(12)
    derivation_path = "m/44'/784'/0'/0'/0'"
    mnem, keypair, address = recover_key_and_address(
        SignatureScheme.ED25519,
        mnemonic,
        derivation_path
    )
    return str(address), keypair.serialize(), mnemonic

def get_sui_balance(address: str):
    try:
        cfg = SuiConfig.user_config(prv_keys=[], rpc_url=SUI_RPC_URL)
        client = SyncClient(cfg)
        result = client.get_gas(SuiAddress(address))
        if result.is_ok():
            total_mist = sum(int(obj.balance) for obj in result.result_data.data)
            return total_mist / 1_000_000_000
        return 0.0
    except Exception as e:
        return 0.0

def send_sui_payment(sender_priv_key: str, recipient_addr: str, amount_sui: float):
    amount_mist = int(amount_sui * 1_000_000_000)
    try:
        cfg = SuiConfig.user_config(prv_keys=[sender_priv_key], rpc_url=SUI_RPC_URL)
        client = SyncClient(cfg)
        txn = SyncTransaction(client=client)
        split_coin = txn.split_coin(coin=txn.gas, amounts=[SuiInteger(amount_mist)])
        txn.transfer_objects(transfers=[split_coin], recipient=SuiAddress(recipient_addr))
        result = txn.execute(gas_budget="5000000")
        if result.is_ok():
            digest = result.result_data.digest if hasattr(result.result_data, 'digest') else "Unknown Digest"
            return True, digest
        else:
            return False, result.result_string
    except Exception as e:
        return False, str(e)
    
def get_sui_market_data():

        try:
            url = "https://api.binance.com/api/v3/ticker/24hr?symbol=SUIUSDT"
            response = requests.get(url, timeout=5)
            data = response.json()
            return float(data['lastPrice']), float(data['priceChangePercent'])
        except:
            # Final Fallback if everything fails
            return 1.56, 2.22

# -----------------------
# UTILITY
# -----------------------
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def now_ts() -> float:
    return time.time()

def human_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")

# -----------------------
# DATA API
# -----------------------
def create_user(username: str, display_name: str, password: str, bio: str = "", profile_pic_path: Optional[str] = None) -> Optional[int]:
    conn = get_conn()
    c = conn.cursor()
    wallet_addr, priv_key, mnemonic = generate_new_wallet()
    try:
        c.execute(
            """INSERT INTO users (username, display_name, password_hash, bio, profile_pic_path, created_at, wallet_address, private_key, mnemonic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, display_name, hash_password(password), bio, profile_pic_path, now_ts(), wallet_addr, priv_key, mnemonic),
        )
        conn.commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None

def update_user_details(user_id: int, display_name: str, bio: str, new_pic_path: Optional[str] = None):
    conn = get_conn()
    c = conn.cursor()
    if new_pic_path:
        c.execute("UPDATE users SET display_name = ?, bio = ?, profile_pic_path = ? WHERE id = ?", (display_name, bio, new_pic_path, user_id))
    else:
        c.execute("UPDATE users SET display_name = ?, bio = ? WHERE id = ?", (display_name, bio, user_id))
    conn.commit()
    return get_user_by_id(user_id)

def authenticate(username: str, password: str) -> Optional[dict]:
    c = get_conn().cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    if not row: return None
    if row["password_hash"] == hash_password(password): return dict(row)
    return None

def get_user_by_id(user_id: int) -> Optional[dict]:
    c = get_conn().cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    return dict(row) if row else None

def get_user_by_username(username: str) -> Optional[dict]:
    c = get_conn().cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    return dict(row) if row else None

def create_post(user_id: int, text: str, image_path: Optional[str] = None, orig_post_id: Optional[int] = None) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO posts (user_id, text, image_path, created_at, orig_post_id) VALUES (?, ?, ?, ?, ?)", (user_id, text, image_path, now_ts(), orig_post_id))
    post_id = c.lastrowid
    conn.commit()
    return post_id

def follow_user(follower_id: int, followed_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO follows (follower_id, followed_id, created_at) VALUES (?, ?, ?)", (follower_id, followed_id, now_ts()))
        conn.commit()
        create_notification(followed_id, f"@{get_user_by_id(follower_id)['username']} followed you")
        return True
    except sqlite3.IntegrityError:
        return False

def unfollow_user(follower_id: int, followed_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM follows WHERE follower_id = ? AND followed_id = ?", (follower_id, followed_id))
    conn.commit()

def is_following(follower_id: int, followed_id: int) -> bool:
    c = get_conn().cursor()
    c.execute("SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?", (follower_id, followed_id))
    return c.fetchone() is not None

def like_post(user_id: int, post_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO likes (user_id, post_id, created_at) VALUES (?, ?, ?)", (user_id, post_id, now_ts()))
        conn.commit()
        post = get_post(post_id)
        if post: create_notification(post['user_id'], f"@{get_user_by_id(user_id)['username']} liked your post")
        return True
    except sqlite3.IntegrityError:
        return False

def unlike_post(user_id: int, post_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id))
    conn.commit()

def bookmark_post(user_id: int, post_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO bookmarks (user_id, post_id, created_at) VALUES (?, ?, ?)", (user_id, post_id, now_ts()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def unbookmark_post(user_id: int, post_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM bookmarks WHERE user_id = ? AND post_id = ?", (user_id, post_id))
    conn.commit()

def reply_to_post(user_id: int, post_id: int, text: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO replies (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?)", (post_id, user_id, text, now_ts()))
    conn.commit()
    post = get_post(post_id)
    if post: create_notification(post['user_id'], f"@{get_user_by_id(user_id)['username']} replied to your post")

def send_message(sender_id: int, receiver_id: int, text: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender_id, receiver_id, text, created_at) VALUES (?, ?, ?, ?)", (sender_id, receiver_id, text, now_ts()))
    conn.commit()
    create_notification(receiver_id, f"New message from @{get_user_by_id(sender_id)['username']}")

def get_post(post_id: int) -> Optional[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id WHERE p.id = ?", (post_id,))
    return c.fetchone()

def get_posts_for_user(user_id: int, limit=50) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id = ? ORDER BY p.created_at DESC LIMIT ?", (user_id, limit))
    return c.fetchall()

def get_liked_posts_for_user(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id JOIN likes l ON l.post_id = p.id WHERE l.user_id = ? ORDER BY l.created_at DESC", (user_id,))
    return c.fetchall()

def get_replies_for_user(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT r.id as reply_id, r.text as reply_text, r.created_at as reply_created_at, p.id as orig_post_id, p.text as orig_text, p.image_path as orig_image, p.created_at as orig_created, u.username as orig_username, u.display_name as orig_display, u.profile_pic_path as orig_pic FROM replies r JOIN posts p ON r.post_id = p.id JOIN users u ON p.user_id = u.id WHERE r.user_id = ? ORDER BY r.created_at DESC", (user_id,))
    return c.fetchall()

def get_feed(user_id: int, limit=50) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?) OR p.user_id = ? ORDER BY p.created_at DESC LIMIT ?", (user_id, user_id, limit))
    return c.fetchall()

def get_likes_for_post(post_id: int) -> int:
    c = get_conn().cursor()
    c.execute("SELECT COUNT(*) as cnt FROM likes WHERE post_id = ?", (post_id,))
    return c.fetchone()["cnt"]

def get_following_count(user_id: int) -> int:
    c = get_conn().cursor()
    c.execute("SELECT COUNT(followed_id) as cnt FROM follows WHERE follower_id = ?", (user_id,))
    return c.fetchone()["cnt"]

def get_follower_count(user_id: int) -> int:
    c = get_conn().cursor()
    c.execute("SELECT COUNT(follower_id) as cnt FROM follows WHERE followed_id = ?", (user_id,))
    return c.fetchone()["cnt"]
    
def get_following_list(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT u.id, u.username, u.display_name, u.bio, u.profile_pic_path FROM users u JOIN follows f ON u.id = f.followed_id WHERE f.follower_id = ?", (user_id,))
    return c.fetchall()

def get_followers_list(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT u.id, u.username, u.display_name, u.bio, u.profile_pic_path FROM users u JOIN follows f ON u.id = f.follower_id WHERE f.followed_id = ?", (user_id,))
    return c.fetchall()
def get_common_followers(my_id: int, target_id: int) -> List[sqlite3.Row]:
    """Returns list of users who follow 'target_id' AND are followed by 'my_id'"""
    c = get_conn().cursor()
    c.execute("""
        SELECT u.username, u.profile_pic_path
        FROM users u
        JOIN follows f_target ON u.id = f_target.follower_id
        JOIN follows f_me ON u.id = f_me.followed_id
        WHERE f_target.followed_id = ? AND f_me.follower_id = ?
        LIMIT 3
    """, (target_id, my_id))
    return c.fetchall()

def get_replies_for_post(post_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT r.*, u.username, u.display_name FROM replies r JOIN users u ON r.user_id = u.id WHERE r.post_id = ? ORDER BY r.created_at", (post_id,))
    return c.fetchall()

def get_bookmarks_for_user(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM bookmarks b JOIN posts p ON b.post_id = p.id JOIN users u ON p.user_id = u.id WHERE b.user_id = ? ORDER BY b.created_at DESC", (user_id,))
    return c.fetchall()

def get_messages_between(a: int, b: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT m.*, su.username as sender_name, ru.username as receiver_name FROM messages m JOIN users su ON m.sender_id = su.id JOIN users ru ON m.receiver_id = ru.id WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?) ORDER BY m.created_at", (a, b, b, a))
    return c.fetchall()

def search_users(term: str) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    q = f"%{term}%"
    c.execute("SELECT * FROM users WHERE username LIKE ? OR display_name LIKE ? LIMIT 50", (q, q))
    return c.fetchall()

def search_posts(term: str) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    q = f"%{term}%"
    c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id WHERE p.text LIKE ? ORDER BY p.created_at DESC LIMIT 100", (q,))
    return c.fetchall()

def create_notification(user_id: int, text: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO notifications (user_id, text, seen, created_at) VALUES (?, ?, 0, ?)", (user_id, text, now_ts()))
    conn.commit()

def get_notifications(user_id: int) -> List[sqlite3.Row]:
    c = get_conn().cursor()
    c.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 200", (user_id,))
    return c.fetchall()

def mark_notifications_seen(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE notifications SET seen = 1 WHERE user_id = ?", (user_id,))
    conn.commit()

# --- RENDER POST (Updated: Divider Between Posts) ---
def render_post(p, key_prefix: str = "default"):
    p = dict(p)
    st.write("\n")
    
    with st.container(border=True):
        header_cols = st.columns([1, 5, 2]) 
        
        # 1. Profile Picture Column
        with header_cols[0]:
            img_src = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
            if p.get('profile_pic_path') and os.path.exists(p['profile_pic_path']):
                b64 = get_image_base64(p['profile_pic_path'])
                if b64: img_src = f"data:image/png;base64,{b64}"
            
            # Circular Avatar
            st.markdown(f"""
                <div style="width: 55px; height: 55px; border-radius: 50%; overflow: hidden; display: flex; justify-content: center; align-items: center;">
                    <img src="{img_src}" style="width: 100%; height: 100%; object-fit: cover; border: none !important;">
                </div>
            """, unsafe_allow_html=True)
        
        # 2. Name & Date Column
        username = p['username']
        display_name = p['display_name']
        created = human_time(p['created_at'])
        
        header_cols[1].markdown(f"**@{username}** — *{display_name}* \n<div style='font-size: 0.8em; color: #555;'>{created}</div>", unsafe_allow_html=True)
        
        # 3. View Profile Button Column
        if header_cols[2].button("View Profile", key=f"{key_prefix}_view_profile:{p['id']}"):
            st.session_state.view = f"profile:{username}"
            st.rerun()
            
        # --- MESSAGE TEXT SIZE INCREASED (1.4em) ---
        if p.get('text'): 
            st.markdown(f"<div style='margin-top: 10px; font-size: 1.4em; line-height: 1.4; color: #000;'>{p['text']}</div>", unsafe_allow_html=True)
        
        # Post Image (if any)
        if p.get('image_path'):
            abs_path = os.path.abspath(p['image_path'])
            if os.path.exists(abs_path):
                b64_str = get_image_base64(abs_path)
                if b64_str:
                    html = f"""<div style="width: 100%; margin-top: 10px; border: 3px solid black; box-shadow: 4px 4px 0px 0px black; overflow: hidden;"><img src="data:image/png;base64,{b64_str}" style="width: 100%; height: auto; display: block; object-fit: cover;"></div>"""
                    st.markdown(html, unsafe_allow_html=True)

        st.write("") 
        st.write("") 
        
        # Action Buttons (Like, Reply, Save)
        row = st.columns([1,1,1]) 
        post_id = p['id']
        user = st.session_state.user
        
        liked = False
        bookmarked = False
        if user:
            liked = get_conn().cursor().execute("SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?", (user['id'], post_id)).fetchone() is not None
            bookmarked = get_conn().cursor().execute("SELECT 1 FROM bookmarks WHERE user_id = ? AND post_id = ?", (user['id'], post_id)).fetchone() is not None
        
        like_icon = "❤️" if liked else "🤍"
        bookmark_icon = "✅" if bookmarked else "🔖"

        if user:
            if row[0].button(f"{like_icon} {get_likes_for_post(post_id)}", key=f"{key_prefix}_like:{post_id}"):
                if liked: unlike_post(user['id'], post_id)
                else: like_post(user['id'], post_id)
                st.rerun()
            if row[1].button("💬 Reply", key=f"{key_prefix}_reply:{post_id}"):
                st.session_state.view = f"reply:{post_id}"
                st.rerun()
            if row[2].button(f"{bookmark_icon} Save", key=f"{key_prefix}_bm:{post_id}"):
                if bookmarked: unbookmark_post(user['id'], post_id)
                else: bookmark_post(user['id'], post_id)
                st.rerun()
        else:
            row[0].write(f"❤️ {get_likes_for_post(post_id)}")
            row[1].write("💬")
            row[2].write("🔖")
            
        if key_prefix != "reply_ctx":
            replies = get_replies_for_post(post_id)
            if replies:
                with st.expander(f"{len(replies)} replies"):
                    for r in replies:
                        st.markdown(f"**@{r['username']}** {human_time(r['created_at'])}")
                        st.write(r['text'])

    st.markdown("---")
# --- REAL-TIME CHAT FRAGMENT ---
@st.fragment(run_every=2)
# --- REAL-TIME CHAT FRAGMENT ---

def render_realtime_chat(current_user_id, other_user_id, current_user_name, other_user_name):
    msgs = get_messages_between(current_user_id, other_user_id)
    # Container for chat messages
    with st.container(height=400, border=True):
        if not msgs: 
            st.caption("No messages yet. Say hi! 👋")
        
        for m in msgs:
            is_me = (m['sender_id'] == current_user_id)
            
            if is_me:
                # BLUE BUBBLE (Right Aligned)
                # Note: The HTML below is flush-left to prevent Markdown code-block errors
                st.markdown(f"""
<div style="display: flex; justify-content: flex-end; margin-bottom: 10px; padding-right: 5px;">
<div style="background-color: #1D9BF0; color: white; padding: 10px 15px; border-radius: 20px 20px 2px 20px; max-width: 70%; font-family: sans-serif; font-size: 16px; box-shadow: 1px 1px 2px rgba(0,0,0,0.1);">
{m['text']}
</div>
</div>
""", unsafe_allow_html=True)
            else:
                # GRAY BUBBLE (Left Aligned)
                st.markdown(f"""
<div style="display: flex; justify-content: flex-start; margin-bottom: 10px; padding-left: 5px;">
<div style="background-color: #EFF3F4; color: black; padding: 10px 15px; border-radius: 20px 20px 20px 2px; max-width: 70%; font-family: sans-serif; font-size: 16px; border: 1px solid #e1e8ed;">
{m['text']}
</div>
</div>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# MAIN APP EXECUTION
# ----------------------------------------------------
init_db()
st.set_page_config(page_title="Sketchy Twitter", layout="wide", page_icon="📝")

# CSS THEME - SKETCHY / WIREFRAME STYLE (LIGHT BLUE BUTTONS VERSION)
# =========================================================
# CSS THEME - SKETCHY / WIREFRAME STYLE (TRUE BLUE FIX)
# =========================================================
st.markdown(
    """
    <style>
    /* IMPORT SKETCHY FONT */
    @import url('https://fonts.googleapis.com/css2?family=Patrick+Hand&display=swap');

    /* 1. CORE THEME */
    html, body, [class*="css"] {
        font-family: 'Patrick Hand', cursive, sans-serif;
        color: #000000;
    }
    
    .stApp {
        background-color: #F3FFC6 !important; /* Light Parrot Green Background */
    }
    
    /* 2. HEADERS */
    h1, h2, h3 {
        font-weight: 900 !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        background: #4FC3F7; /* BRIGHTER BLUE HEADER */
        display: inline-block;
        padding: 2px 10px;
        border: 2px solid black;
        box-shadow: 3px 3px 0px 0px black;
        transform: rotate(-1deg);
        color: black !important;
    }

    /* Remove background from the X Logo header specifically */
    section[data-testid="stSidebar"] h1 {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        transform: rotate(0deg) !important;
    }
    
    p, div, span, label {
        color: #000000 !important;
    }

    /* 3. CARDS / CONTAINERS */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 3px solid #000000 !important;
        box-shadow: 6px 6px 0px 0px #000000 !important;
        border-radius: 5px !important;
        background-color: #ffffff !important; 
        margin-bottom: 25px !important;
        padding: 15px !important;
    }

    /* 4. SEPARATOR LINE */
    hr {
        margin-top: 20px !important;
        margin-bottom: 20px !important;
        border-width: 0px !important;
        border-top: 4px solid #000000 !important;
        opacity: 1 !important;
        border-radius: 50% !important;
    }

    /* 5. BUTTONS - THE MAIN FIX */
    
    /* Primary Button (Write Post) - Darker Cyan/Blue */
    button[kind="primary"] {
        background-color: #29B6F6 !important; 
        color: black !important;
        border: 3px solid black !important;
        box-shadow: 4px 4px 0px 0px black !important;
        font-weight: 900 !important;
        text-transform: uppercase;
    }
    
    /* Secondary Buttons (Menu Items) - Lighter Blue */
    button[kind="secondary"] {
        background-color: #B3E5FC !important; /* <--- THIS IS THE BLUEISH COLOR */
        color: black !important;
        border: 3px solid black !important;
        box-shadow: 4px 4px 0px 0px black !important;
        font-weight: 900 !important;
    }
    
    button:active {
        box-shadow: 0px 0px 0px 0px black !important;
        transform: translate(4px, 4px) !important;
    }

    /* 6. INPUTS & TEXT AREAS */
    input, textarea {
        background-color: #ffffff !important;
        border: 3px solid black !important;
        color: black !important;
        box-shadow: 3px 3px 0px 0px #ccc !important; 
    }

    /* 7. DISABLED INPUTS */
    input:disabled, textarea:disabled,
    input[disabled], textarea[disabled] {
        background-color: #f4f4f4 !important;
        color: #000000 !important;
        opacity: 1 !important;
        -webkit-text-fill-color: #000000 !important;
        border-color: #000000 !important;
        font-weight: bold !important;
    }
    
    /* 8. SIDEBAR FIXES */
    section[data-testid="stSidebar"] {
        background-color: #F3FFC6 !important;
        border-right: 3px solid black !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        text-align: left !important;
        width: 100%;
    }
    
    /* REMOVED THE GRAYSCALE FILTER HERE SO BUTTONS STAY BLUE */
    section[data-testid="stSidebar"] button[kind="secondary"] {
        border-color: black !important;
        /* filter: grayscale(100%);  <-- DELETED THIS LINE */
    }
    
    section[data-testid="stSidebar"] button[kind="secondary"]:hover {
        background-color: #81D4FA !important; /* Slightly darker blue on hover */
        transform: none !important;
        box-shadow: 4px 4px 0px 0px black !important;
    }

    /* 9. IMAGES & TOASTS */
    img { border: 2px solid black; }
    div[data-baseweb="toast"] {
        background-color: #F3FFC6 !important;
        border: 3px solid black !important;
    }
    
    /* 10. HEADER ICONS */
    header { visibility: visible !important; background: transparent !important; }
    header button, header svg { color: black !important; fill: black !important; }

    /* 11. DROPDOWNS & MENUS */
    div[data-baseweb="popover"],
    div[data-baseweb="menu"],
    div[role="dialog"],
    ul[data-baseweb="menu"] {
        background-color: #F3FFC6 !important;
        border: 3px solid black !important;
        box-shadow: 5px 5px 0px 0px black !important;
    }
    div[data-baseweb="popover"] *,
    div[data-baseweb="menu"] *,
    div[role="dialog"] *,
    ul[data-baseweb="menu"] * {
        color: #000000 !important;
        background-color: transparent !important;
    }
    li[role="option"] {
        background-color: #F3FFC6 !important;
        color: black !important;
        border-bottom: 1px solid #000000 !important;
    }
    li[role="option"]:hover,
    li[role="option"][aria-selected="true"] {
        background-color: #4FC3F7 !important; /* Blue hover for dropdowns too */
        color: black !important;
    }
    div[data-baseweb="select"] > div {
        background-color: #ffffff !important;
        border: 3px solid black !important;
        color: black !important;
    }
    div[data-testid="stSelectbox"] div {
        color: black !important;
    }

    /* 12. FILE UPLOADER */
    div[data-testid="stFileUploaderDropzone"] {
        background-color: #F3FFC6 !important;
        border: 3px solid black !important;
        border-radius: 0px !important;
    }
    div[data-testid="stFileUploaderDropzone"] div,
    div[data-testid="stFileUploaderDropzone"] span,
    div[data-testid="stFileUploaderDropzone"] small {
        color: black !important;
    }
    div[data-testid="stFileUploaderDropzone"] svg {
        fill: black !important;
        stroke: black !important;
    }
    section[data-testid="stFileUploader"] button {
        background-color: #29B6F6 !important; /* Blue button */
        color: black !important;
        border: 2px solid black !important;
        box-shadow: 3px 3px 0px 0px black !important;
        font-weight: 900 !important;
        text-transform: uppercase;
    }
    </style>
    """, unsafe_allow_html=True
)

# ----------------------------------------------------
# COOKIE MANAGER
# ----------------------------------------------------

# 1. Initialize with a specific key
cookie_manager = stx.CookieManager(key="auth_mgr_production_v2")

# 2. Add delay to allow the cookie component to mount on Streamlit Cloud
time.sleep(0.5)

# 3. Retrieve all cookies
cookies = cookie_manager.get_all()

# 4. Strict Stop: If cookies is None, the component hasn't loaded yet.
if cookies is None:
    st.spinner("Loading Sketchy UI...")
    st.stop()

# 5. Extract User ID
cookie_user_id = cookies.get("current_user_id")

# 6. Session Logic
if "user" not in st.session_state:
    st.session_state.user = None

# 7. Check Login
if not st.session_state.user and cookie_user_id:
    try:
        user_data = get_user_by_id(int(cookie_user_id))
        if user_data:
            st.session_state.user = user_data
            if "auth_mode" not in st.session_state: st.session_state.auth_mode = "home"
        else:
            cookie_manager.delete("current_user_id")
            st.warning("Session expired. Please log in again.")
    except Exception as e:
        cookie_manager.delete("current_user_id")

# ==========================================
# AUTH SCREEN
# ==========================================
if not st.session_state.user:
    if "auth_mode" not in st.session_state: st.session_state.auth_mode = "login"
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; transform: rotate(-3deg);'>📝 SKETCHY TWITTER</h1>", unsafe_allow_html=True)
        
        with st.container(border=True):
            if st.session_state.auth_mode == "login":
                with st.form("login_form"):
                    st.write("### Login")
                    username = st.text_input("Username", placeholder="@username")
                    password = st.text_input("Password", type="password")
                    st.write("") 
                    submitted = st.form_submit_button("ENTER", type="primary", use_container_width=True)
                    if submitted:
                        row = authenticate(username.strip(), password)
                        if row:
                            st.session_state.user = row
                            cookie_manager.set("current_user_id", row['id'], expires_at=datetime.now() + timedelta(days=7))
                            st.toast("Welcome back!", icon="👋")
                            time.sleep(1) # Wait for cookie to write
                            st.rerun()
                        else:
                            st.error("Invalid username or password")
                if st.button("Create an account", use_container_width=True):
                    st.session_state.auth_mode = "signup"
                    st.rerun()
            else:
                st.info("ℹ️ A SUI Blockchain Wallet will be generated for you.")
                with st.form("signup_form"):
                    st.write("### New Account")
                    su_user = st.text_input("Choose username")
                    su_name = st.text_input("Display name")
                    su_pass = st.text_input("Password", type="password")
                    su_bio = st.text_area("Bio (optional)")
                    su_pic = st.file_uploader("Profile Picture", type=["png","jpg","jpeg"])
                    st.write("")
                    ok = st.form_submit_button("CREATE & GENERATE WALLET", type="primary", use_container_width=True)
                    if ok:
                        if not su_user or not su_name or not su_pass: st.error("Please fill required fields")
                        else:
                            pic_path = None
                            if su_pic:
                                fname = f"{su_user}_{int(time.time()*1000)}_{su_pic.name}"
                                path = os.path.join(PROFILE_PIC_DIR, fname)
                                with open(path, "wb") as f: f.write(su_pic.getbuffer())
                                pic_path = path
                            with st.spinner("Generating Keys on Blockchain..."):
                                new_id = create_user(su_user.strip(), su_name.strip(), su_pass, su_bio.strip(), pic_path)
                            if new_id:
                                st.success("Account created! Please log in.")
                                time.sleep(1.5)
                                st.session_state.auth_mode = "login"
                                st.rerun()
                            else: st.error("Username already exists")
                if st.button("Back to Login", use_container_width=True):
                    st.session_state.auth_mode = "login"
                    st.rerun()
    st.stop()

# ==========================================
# MAIN APP - LOGGED IN
# ==========================================
if "view" not in st.session_state: st.session_state.view = "home"

with st.sidebar:
    # 1. THE X LOGO (Replaces Pencil)
    st.markdown("<h1 style='text-align: center; margin-bottom: 20px; font-size: 60px; font-family: sans-serif;'>𝕏</h1>", unsafe_allow_html=True)
    
    # 2. RENAMED BUTTONS (Matches Real Twitter)
    if st.button("   Home", use_container_width=True): st.session_state.view = "home"; st.rerun()
    if st.button("   Explore", use_container_width=True): st.session_state.view = "explore"; st.rerun()
    if st.button("   Notifications", use_container_width=True): st.session_state.view = "notifications"; st.rerun()
    if st.button("   Messages", use_container_width=True): st.session_state.view = "messages"; st.rerun()
    if st.button("   Bookmarks", use_container_width=True): st.session_state.view = "bookmarks"; st.rerun()
    if st.button("   Wallet", use_container_width=True): st.session_state.view = "wallet"; st.rerun()
    if st.button("   Profile", use_container_width=True): st.session_state.view = f"profile:{st.session_state.user['username']}"; st.rerun()
    
    st.write("") 
    if st.button("WRITE POST", type="primary", use_container_width=True): st.session_state.view = "create_post"; st.rerun()
    
    st.markdown("---")
    
    usr = st.session_state.user
    if usr:
        usr = get_user_by_id(usr['id']) 
        st.session_state.user = usr
        with st.container():
            col_p1, col_p2 = st.columns([1, 3])
            with col_p1:
                img_src = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
                if usr.get('profile_pic_path') and os.path.exists(usr['profile_pic_path']):
                    b64 = get_image_base64(usr['profile_pic_path'])
                    if b64: img_src = f"data:image/png;base64,{b64}"
                
                # --- UPDATED: CLEAN CIRCLE (NO BORDER) ---
                st.markdown(f"""
                <div style="width: 50px; height: 50px; border-radius: 50%; overflow: hidden;">
                    <img src="{img_src}" style="width: 100%; height: 100%; object-fit: cover; border: none !important;">
                </div>
                """, unsafe_allow_html=True)
                # -----------------------------------------
            with col_p2:
                st.markdown(f"<div style='line-height: 1.1; margin-top: 2px;'><b>{usr.get('display_name')}</b><br><span style='color: #666; font-size: 0.9em;'>@{usr.get('username')}</span></div>", unsafe_allow_html=True)

    addr = usr.get("wallet_address", "No Wallet")
    short_addr = f"{addr[:5]}...{addr[-5:]}"
    st.markdown(f"<div style='background-color: #fff; color: #000; padding: 8px; border: 2px solid black; box-shadow: 2px 2px 0px black; font-family: monospace; text-align: center; font-size: 0.9em; margin-top: 5px;'>{short_addr}</div>", unsafe_allow_html=True)
    st.write("")
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.user = None
        st.session_state.auth_mode = "login"
        st.session_state.view = "home"
        cookie_manager.delete("current_user_id")
        time.sleep(1) 
        st.rerun()

def render_user_list(title: str, user_list: List[sqlite3.Row]):
    st.header(title)
    if not user_list:
        st.info("No users found.")
        return
    for u in user_list:
        with st.container(border=True):
            cols = st.columns([1, 4, 2])
            with cols[0]:
                img_src = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
                if u['profile_pic_path'] and os.path.exists(u['profile_pic_path']):
                    b64 = get_image_base64(u['profile_pic_path'])
                    if b64: img_src = f"data:image/png;base64,{b64}"
                st.image(img_src, width=50)
            with cols[1]:
                st.write(f"**{u['display_name']}**")
                st.caption(f"@{u['username']}")
            with cols[2]:
                if st.button("View", key=f"list_view_{u['id']}"):
                    st.session_state.view = f"profile:{u['username']}"
                    st.rerun()

# --- VIEW HANDLERS ---
if st.session_state.view == "create_post":
    st.header("New Post")
    with st.container(border=True):
        with st.form("post_form"):
            text = st.text_area("What's on your mind?", max_chars=280)
            img = st.file_uploader("Attach Image", type=["png","jpg","jpeg","gif"])
            ok = st.form_submit_button("PUBLISH", type="primary")
            if ok:
                img_path = None
                if img:
                    fname = f"{int(time.time()*1000)}_{img.name}"
                    path = os.path.join(POST_IMAGE_DIR, fname)
                    with open(path, "wb") as f: f.write(img.getbuffer())
                    img_path = path
                create_post(st.session_state.user['id'], text, img_path)
                st.success("Posted!")
                st.session_state.view = "home"
                st.rerun()

elif st.session_state.view.startswith("reply:"):
    _, pid = st.session_state.view.split(":")
    pid = int(pid)
    p = get_post(pid)
    if not p: st.error("Post not found")
    else:
        render_post(p, "reply_view")
        with st.form("reply_form"):
            txt = st.text_area("Write a reply...", max_chars=280)
            ok = st.form_submit_button("REPLY", type="primary")
            if ok:
                reply_to_post(st.session_state.user['id'], pid, txt)
                st.success("Replied!")
                st.session_state.view = "home"
                st.rerun()

elif st.session_state.view == "edit_profile":
    st.header("Edit Profile")
    curr = st.session_state.user
    with st.container(border=True):
        with st.form("edit_profile_form"):
            new_name = st.text_input("Display Name", value=curr['display_name'])
            new_bio = st.text_area("Bio", value=curr['bio'] if curr['bio'] else "", max_chars=160)
            st.write("Profile Picture")
            col_preview, col_upload = st.columns([1, 3])
            with col_preview:
                if curr.get('profile_pic_path') and os.path.exists(curr['profile_pic_path']): st.image(curr['profile_pic_path'], width=80)
                else: st.markdown("👤")
            with col_upload: new_pic = st.file_uploader("Upload new image", type=["png", "jpg", "jpeg"])
            st.write("")
            submitted = st.form_submit_button("SAVE CHANGES", type="primary")
            if submitted:
                if not new_name.strip(): st.error("Display Name cannot be empty")
                else:
                    final_path = None
                    if new_pic:
                        fname = f"updated_{curr['id']}_{int(time.time())}_{new_pic.name}"
                        path = os.path.join(PROFILE_PIC_DIR, fname)
                        with open(path, "wb") as f: f.write(new_pic.getbuffer())
                        final_path = path
                    updated_user = update_user_details(curr['id'], new_name.strip(), new_bio.strip(), final_path)
                    st.session_state.user = updated_user
                    st.success("Profile updated successfully!")
                    time.sleep(1)
                    st.session_state.view = f"profile:{curr['username']}"
                    st.rerun()
    if st.button("Cancel"):
        st.session_state.view = f"profile:{curr['username']}"
        st.rerun()

elif st.session_state.view == "home":
    st.header("TODAY")
    posts = get_feed(st.session_state.user['id'], limit=100)
    if not posts: st.info("Timeline empty. Go to Explore!")
    for p in posts: render_post(p, "home")

elif st.session_state.view == "explore":
    st.header("EXPLORE")
    term = st.text_input("Search...", placeholder="Find users or posts...")
    if term:
        st.subheader("Users")
        for u in search_users(term):
            st.write(f"@{u['username']} — {u['display_name']}")
            if st.button("View", key=f"viewu:{u['id']}"):
                st.session_state.view = f"profile:{u['username']}"; st.rerun()
        st.subheader("Posts")
        for p in search_posts(term): render_post(p, "explore")
    else:
        st.subheader("Recent Activity")
        c = get_conn().cursor()
        c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC LIMIT 100")
        for p in c.fetchall(): render_post(p, "explore")

elif st.session_state.view == "bookmarks":
    st.header("SAVED")
    bookmarks = get_bookmarks_for_user(st.session_state.user['id'])
    if not bookmarks: st.info("No bookmarks yet.")
    for p in bookmarks: render_post(p, "bookmarks")

elif st.session_state.view == "notifications":
    st.header("ALERTS")
    with st.container(border=True):
        notes = get_notifications(st.session_state.user['id'])
        if not notes: st.info("No notifications.")
        for n in notes: st.write(f"**{human_time(n['created_at'])}** — {n['text']}")
        mark_notifications_seen(st.session_state.user['id'])

elif st.session_state.view == "messages":
    st.header("CHAT")
    user = st.session_state.user
    rows = get_conn().cursor().execute("SELECT username FROM users WHERE id != ?", (user['id'],)).fetchall()
    options = [r['username'] for r in rows]
    other = st.selectbox("Select User", options=options)
    if other:
        other_row = get_user_by_username(other)
        st.subheader(f"Chat with @{other_row['username']}")
        render_realtime_chat(user['id'], other_row['id'], user['username'], other_row['username'])
        with st.form("send_msg", clear_on_submit=True):
            txt = st.text_area("Message")
            ok = st.form_submit_button("SEND", type="primary")
            if ok and txt.strip():
                send_message(user['id'], other_row['id'], txt)
                st.toast("Message sent!")

elif st.session_state.view == "wallet":
    curr = st.session_state.user
    with st.spinner("Syncing with Blockchain..."):
        balance = get_sui_balance(curr['wallet_address'])
        sui_price, price_change_pct = get_sui_market_data()
    holdings_value = balance * sui_price
    change_color = "#00ba7c" if price_change_pct >= 0 else "#f91880"
    change_sign = "+" if price_change_pct >= 0 else ""

    st.header("WALLET")
    st.markdown(f"""
    <div style="background-color: #ffffff; border: 3px solid black; box-shadow: 6px 6px 0px black; padding: 20px; margin-bottom: 30px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; align-items: center; gap: 15px;">
                <img src="https://s2.coinmarketcap.com/static/img/coins/64x64/20947.png" width="48" height="48" style="border-radius: 50%;">
                <div>
                    <div style="font-weight: 800; font-size: 19px; color: black; display: flex; align-items: center; gap: 4px;">SUI COIN</div>
                    <div style="font-size: 15px; color: #555; margin-top: 2px;">${sui_price:,.2f} <span style="color: {change_color}; font-weight: 900;">{change_sign}{price_change_pct:.2f}%</span></div>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-weight: 800; font-size: 24px; color: black;">${holdings_value:,.2f}</div>
                <div style="font-size: 15px; color: #555; margin-top: 2px;">{balance:.4f} SUI</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.subheader("Transfer Funds")
    with st.container(border=True):
        with st.form("withdraw_form"):
            dest_addr = st.text_input("Destination Address (0x...)")
            amount = st.number_input("Amount to Send", min_value=0.0, max_value=balance, step=0.1)
            if st.form_submit_button("SEND TRANSACTION", type="primary", use_container_width=True):
                if amount <= 0: st.error("Amount must be positive.")
                elif not dest_addr.startswith("0x"): st.error("Invalid SUI address.")
                else:
                    with st.spinner("Processing on Blockchain..."):
                        success, msg = send_sui_payment(curr['private_key'], dest_addr, amount)
                        if success:
                            st.success(f"Transaction Sent! Digest: {msg}")
                            st.balloons()
                            time.sleep(2)
                            st.rerun()
                        else: st.error(f"Failed: {msg}")
    st.divider()
    with st.expander("🔐 View Keys"):
        st.warning("These are your keys. Never share them.")
        st.text_input("Private Key", curr['private_key'], type="password", disabled=True)
        st.text_area("Mnemonic Phrase", curr['mnemonic'], disabled=True)

elif st.session_state.view.startswith("profile:"):
    _, uname = st.session_state.view.split(":")
    u_row = get_user_by_username(uname)
    if not u_row: st.error("User not found")
    else:
        u = dict(u_row)
        user_id = u['id']
        current_user_id = st.session_state.user['id']
        is_me = (current_user_id == user_id)
        
        # Main Profile Card
        with st.container(border=True):
            # Layout: Avatar | Info (Name, Bio, Stats) | Edit/Follow Button
            header_cols = st.columns([1.2, 4, 1.2])
            
            # --- COLUMN 1: AVATAR ---
            with header_cols[0]:
                img_src = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
                if u.get('profile_pic_path') and os.path.exists(u['profile_pic_path']):
                    b64 = get_image_base64(u['profile_pic_path'])
                    if b64: img_src = f"data:image/png;base64,{b64}"
                
                # CSS: Added box-shadow to mask jagged edges and ensure a clean circle
                st.markdown(f"""
                    <div style="
                        width: 110px; 
                        height: 110px; 
                        border-radius: 50%; 
                        overflow: hidden; 
                        display: flex; justify-content: center; align-items: center;
                        box-shadow: 0px 0px 0px 3px white; 
                        margin-bottom: 10px;
                    ">
                        <img src="{img_src}" style="width: 100%; height: 100%; object-fit: cover;">
                    </div>
                """, unsafe_allow_html=True)

            # --- COLUMN 2: INFO (Name, Bio, Stats, "Followed By") ---
            with header_cols[1]:
                # 1. Name and Handle
                st.markdown(f"""
                    <div style="line-height: 1.2;">
                        <span style="font-size: 1.8rem; font-weight: 900; text-transform: uppercase;">{u['display_name']}</span>
                        <br>
                        <span style="color: #555; font-size: 1.1rem;">@{u['username']}</span>
                    </div>
                """, unsafe_allow_html=True)
                
                # 2. Bio
                if u.get('bio'): 
                    st.markdown(f"<div style='margin-top: 8px; font-size: 1rem;'>{u['bio']}</div>", unsafe_allow_html=True)
                
                # 3. Join Date
                st.markdown(f"""<div style="color: #666; font-size: 0.9rem; margin-top: 8px; margin-bottom: 10px;">📅 Joined {human_time(u.get('created_at')).split(' ')[0]}</div>""", unsafe_allow_html=True)
                
                # 4. Stats (Following / Followers) - Nested columns to keep them tight
                stat_row = st.columns([1.2, 1.2, 3])
                with stat_row[0]:
                    if st.button(f"{get_following_count(user_id)} Following", key=f"ing_{user_id}"): 
                        st.session_state.view = f"following_list:{user_id}:{uname}"
                        st.rerun()
                with stat_row[1]:
                    if st.button(f"{get_follower_count(user_id)} Followers", key=f"ers_{user_id}"): 
                        st.session_state.view = f"followers_list:{user_id}:{uname}"
                        st.rerun()

                # 5. "Followed By" Section (Clickable Buttons + Bigger Text)
                if not is_me:
                    mutuals = get_common_followers(current_user_id, user_id)
                    if mutuals:
                        # 1. Prepare Avatar HTML
                        avatar_html = ""
                        for m in mutuals:
                            m_src = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
                            if m['profile_pic_path'] and os.path.exists(m['profile_pic_path']):
                                b64 = get_image_base64(m['profile_pic_path'])
                                if b64: m_src = f"data:image/png;base64,{b64}"
                            avatar_html += f"""<img src="{m_src}" style="width: 24px; height: 24px; border-radius: 50%; border: 1px solid white; margin-right: -8px;">"""
                        
                        # 2. Render Layout using Columns to allow Buttons
                        st.write("") # Spacer
                        
                        # Calculate columns based on how many mutuals we have
                        # Layout: [Avatars] [Text "Followed by"] [Button Name 1] [Text "&"] [Button Name 2]
                        
                        if len(mutuals) == 1:
                            # Single Mutual
                            c_av, c_txt, c_btn, c_rest = st.columns([0.5, 1.3, 1.5, 4])
                            with c_av: 
                                st.markdown(f"<div style='display:flex;'>{avatar_html}</div>", unsafe_allow_html=True)
                            with c_txt:
                                st.markdown("<div style='font-size: 1.1rem; color: #555; padding-top: 5px;'>Followed by</div>", unsafe_allow_html=True)
                            with c_btn:
                                if st.button(f"@{mutuals[0]['username']}", key=f"mbtn_{mutuals[0]['username']}"):
                                    st.session_state.view = f"profile:{mutuals[0]['username']}"
                                    st.rerun()
                                    
                        elif len(mutuals) >= 2:
                            # Two or more Mutuals
                            c_av, c_txt, c_btn1, c_and, c_btn2, c_rest = st.columns([0.6, 1.3, 1.5, 0.4, 1.5, 3])
                            
                            with c_av:
                                st.markdown(f"<div style='display:flex;'>{avatar_html}</div>", unsafe_allow_html=True)
                            with c_txt:
                                st.markdown("<div style='font-size: 1.1rem; color: #555; padding-top: 5px;'>Followed by</div>", unsafe_allow_html=True)
                            with c_btn1:
                                if st.button(f"@{mutuals[0]['username']}", key=f"mbtn_{mutuals[0]['username']}"):
                                    st.session_state.view = f"profile:{mutuals[0]['username']}"
                                    st.rerun()
                            with c_and:
                                st.markdown("<div style='font-size: 1.1rem; color: #555; padding-top: 5px;'>&</div>", unsafe_allow_html=True)
                            with c_btn2:
                                if st.button(f"@{mutuals[1]['username']}", key=f"mbtn_{mutuals[1]['username']}"):
                                    st.session_state.view = f"profile:{mutuals[1]['username']}"
                                    st.rerun()

            # --- COLUMN 3: EDIT / FOLLOW BUTTON ---
            with header_cols[2]:
                if not is_me:
                    if is_following(st.session_state.user['id'], user_id):
                        if st.button("Unfollow", key=f"unfol_{user_id}", use_container_width=True): 
                            unfollow_user(st.session_state.user['id'], user_id)
                            st.rerun()
                    else:
                        if st.button("Follow", type="primary", key=f"fol_{user_id}", use_container_width=True): 
                            follow_user(st.session_state.user['id'], user_id)
                            st.rerun()
                    
                    # Tip Button (Popver)
                    st.write("")
                    with st.popover("💸 Tip SUI", use_container_width=True):
                        tip_val = st.number_input("Amount", 0.1, step=0.1, key=f"tip_{user_id}")
                        if st.button("Send Tip", key=f"pay_{user_id}"):
                            with st.spinner("..."):
                                s, m = send_sui_payment(st.session_state.user['private_key'], u['wallet_address'], tip_val)
                                if s: st.success("Sent!"); create_notification(user_id, f"Tip from @{st.session_state.user['username']}")
                                else: st.error(m)
                else:
                    if st.button("Edit Profile", key="edit_profile_btn", use_container_width=True): 
                        st.session_state.view = "edit_profile"
                        st.rerun()

        st.markdown("---")

        # TABS
        tab_posts, tab_replies, tab_likes = st.tabs(["POSTS", "REPLIES", "LIKES"])
        with tab_posts:
            user_posts = get_posts_for_user(user_id, limit=100)
            if not user_posts: st.info("No posts yet.")
            for p in user_posts: render_post(p, "prof_posts")
        with tab_replies:
            replies_list = get_replies_for_user(user_id)
            if not replies_list: st.info("No replies yet.")
            for r in replies_list:
                with st.container(border=True):
                    col_icon, col_txt = st.columns([1, 20])
                    with col_icon: st.write("💬")
                    with col_txt:
                        st.caption(f"Replying to @{r['orig_username']}")
                        st.markdown(f"**{r['reply_text']}**")
                        with st.expander("Original Post Context"):
                            fake_post_row = { "id": r["orig_post_id"], "username": r["orig_username"], "display_name": r["orig_display"], "profile_pic_path": r["orig_pic"], "text": r["orig_text"], "image_path": r["orig_image"], "created_at": r["orig_created"] }
                            render_post(fake_post_row, key_prefix=f"reply_ctx_{r['reply_id']}")
        with tab_likes:
             liked_posts = get_liked_posts_for_user(user_id)
             if not liked_posts: st.info("No liked posts yet.")
             for p in liked_posts: render_post(p, "prof_likes")


elif st.session_state.view.startswith("following_list:"):
    _, user_id_str, uname = st.session_state.view.split(":")
    render_user_list(f"@{uname} IS FOLLOWING", get_following_list(int(user_id_str)))
    if st.button("← Back"): st.session_state.view = f"profile:{uname}"; st.rerun()

elif st.session_state.view.startswith("followers_list:"):
    _, user_id_str, uname = st.session_state.view.split(":")
    render_user_list(f"FOLLOWERS OF @{uname}", get_followers_list(int(user_id_str)))
    if st.button("← Back"): st.session_state.view = f"profile:{uname}"; st.rerun()

else: st.write("Unknown view")
st.markdown("---")
