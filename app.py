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
        url = "https://api.coingecko.com/api/v3/simple/price?ids=sui&vs_currencies=usd&include_24h_change=true"
        response = requests.get(url, timeout=5)
        data = response.json()
        return data['sui']['usd'], data['sui']['usd_24h_change']
    except:
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

def render_user_list(title: str, users: List[sqlite3.Row]):
    st.header(title)
    if not users:
        st.info("No users found here.")
        return
    for u in users:
        with st.container(border=True):
            cols = st.columns([1, 6, 1])
            if u['profile_pic_path']:
                cols[0].image(u['profile_pic_path'], width=50)
            else:
                cols[0].markdown("👤", unsafe_allow_html=True)
            cols[1].markdown(f"**@{u['username']}** — *{u['display_name']}*")
            cols[1].caption(u['bio'])
            if cols[2].button("View", key=f"view_list_{u['id']}_{title.replace(' ', '_')}"):
                st.session_state.view = f"profile:{u['username']}"
                st.rerun()

# --- RENDER POST ---
def render_post(p, key_prefix: str = "default"):
    p = dict(p)
    st.write("\n")
    with st.container(border=True):
        header_cols = st.columns([1, 5, 1])
        with header_cols[0]:
            if p.get('profile_pic_path') and os.path.exists(p['profile_pic_path']):
                st.image(p['profile_pic_path'], width=50)
            else:
                st.markdown("<div style='font-size: 30px;'>👤</div>", unsafe_allow_html=True)
        
        username = p['username']
        display_name = p['display_name']
        created = human_time(p['created_at'])
        header_cols[1].markdown(f"**@{username}** — *{display_name}* \n<div style='font-size: 0.8em; color: gray;'>{created}</div>", unsafe_allow_html=True)
        
        if header_cols[2].button("View profile", key=f"{key_prefix}_view_profile:{p['id']}"):
            st.session_state.view = f"profile:{username}"
            st.rerun()
            
        st.markdown("---") 
        if p.get('text'): st.markdown(p['text'])
        
        if p.get('image_path'):
            abs_path = os.path.abspath(p['image_path'])
            if os.path.exists(abs_path):
                b64_str = get_image_base64(abs_path)
                if b64_str:
                    html = f"""<div style="width: 100%; margin-top: 10px; border-radius: 12px; overflow: hidden;"><img src="data:image/png;base64,{b64_str}" style="width: 100%; height: auto; display: block; object-fit: cover; border-radius: 12px;"></div>"""
                    st.markdown(html, unsafe_allow_html=True)

        st.write("") 
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
            if row[2].button(f"{bookmark_icon} Bookmark", key=f"{key_prefix}_bm:{post_id}"):
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

# --- REAL-TIME CHAT FRAGMENT ---
@st.fragment(run_every=2)
def render_realtime_chat(current_user_id, other_user_id, current_user_name, other_user_name):
    msgs = get_messages_between(current_user_id, other_user_id)
    with st.container(height=300, border=True):
        if not msgs: st.caption("No messages yet. Say hi! 👋")
        for m in msgs:
            is_me = (m['sender_id'] == current_user_id)
            who = "You" if is_me else other_user_name
            style = "color: #1d9bf0;" if is_me else "color: #536471;"
            align = "text-align: right;" if is_me else "text-align: left;"
            st.markdown(f"<div style='{align} {style}'><b>{who}</b> <span style='font-size:0.8em'>({human_time(m['created_at'])})</span><br>{m['text']}</div>", unsafe_allow_html=True)

# ----------------------------------------------------
# MAIN APP EXECUTION
# ----------------------------------------------------
init_db()
st.set_page_config(page_title="Mini Twitter (Web3 Integrated)", layout="wide")

# CSS THEME
# =========================================================
# CSS THEME (Mobile & Dark Mode Fixed)
# =========================================================
st.markdown(
    """
    <style>
    /* ---------------------------------------------------------
       1. CORE THEME: Pitch Black & White Text
       --------------------------------------------------------- */
    :root {
        --primary-color: #1d9bf0;
        --background-color: #000000;
        --secondary-background-color: #000000;
        --text-color: #e7e9ea;
    }
    .stApp {
        background-color: #000000 !important;
    }
    
    /* ---------------------------------------------------------
       2. MOBILE HEADER & SIDEBAR TOGGLE (CRITICAL FIX)
       --------------------------------------------------------- */
    
    /* Ensure the Header is VISIBLE and TRANSPARENT */
    header[data-testid="stHeader"] {
        background: transparent !important;
        visibility: visible !important;
        z-index: 99999 !important; /* Force it to stay on top */
    }

    /* Force all buttons in the header (including the Menu Burger) to be WHITE */
    header[data-testid="stHeader"] button {
        color: white !important;
    }
    
    /* Force the Icons (SVG) inside the buttons to be WHITE */
    header[data-testid="stHeader"] svg {
        fill: white !important;
        stroke: white !important;
    }

    /* Stop hiding the first child. This was the cause of the missing menu. 
       We accept the decoration line to ensure the button works. */
    header[data-testid="stHeader"] > div:first-child {
        display: flex !important;
        visibility: visible !important;
    }

    /* Hide ONLY the 'Deploy' button to clean it up */
    .stDeployButton {
        display: none !important;
    }

    /* Push content down so it doesn't overlap the header on mobile */
    .block-container {
        padding-top: 4rem !important; 
    }

    /* ---------------------------------------------------------
       3. SIDEBAR STYLING
       --------------------------------------------------------- */
    section[data-testid="stSidebar"] {
        background-color: #000000 !important;
        border-right: 1px solid #2f3336;
    }
    
    /* Sidebar Buttons (Twitter Style) */
    section[data-testid="stSidebar"] .stButton > button {
        background-color: transparent !important;
        border: none !important;
        color: #e7e9ea !important;
        text-align: left !important;
        font-size: 20px !important; 
        font-weight: 700 !important;
        padding: 10px 15px !important;
        margin-bottom: 4px !important;
        border-radius: 30px !important;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background-color: #181919 !important;
    }

    /* ---------------------------------------------------------
       4. UI ELEMENTS (Images, Inputs, Posts)
       --------------------------------------------------------- */
    
    /* Inputs */
    input, textarea, select, div[data-baseweb="select"] > div {
        background-color: #000000 !important;
        color: white !important;
        border: 1px solid #2f3336 !important;
    }

    /* Tweet Button (Blue) */
    button[kind="primary"] {
        background-color: #1d9bf0 !important;
        color: white !important;
        border-radius: 30px !important;
        border: none !important;
        font-weight: 800 !important;
    }

    /* Post Images */
    .post-img img {
        border-radius: 16px !important;
        border: 1px solid #2f3336 !important;
    }
    
    /* Circular Profile Pics */
    div[data-testid="stImage"] img {
        border-radius: 50% !important; 
        object-fit: cover !important;
    }
    
    </style>
    """, unsafe_allow_html=True
)

# ----------------------------------------------------
# COOKIE MANAGER (CLOUD FIX APPLIED)
# ----------------------------------------------------

# 1. Initialize with a specific key
cookie_manager = stx.CookieManager(key="auth_mgr_production_v2")

# 2. Add delay to allow the cookie component to mount on Streamlit Cloud
time.sleep(0.5)

# 3. Retrieve all cookies
cookies = cookie_manager.get_all()

# 4. Strict Stop: If cookies is None, the component hasn't loaded yet.
# Stop execution and wait for the automatic re-run from the component.
if cookies is None:
    st.spinner("Syncing login status...")
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
            # IMPORTANT: If cookie exists but user doesn't (DB wiped on Cloud),
            # we must clear the cookie to avoid infinite loops.
            cookie_manager.delete("current_user_id")
            st.warning("Session expired or database reset. Please log in again.")
    except Exception as e:
        cookie_manager.delete("current_user_id")

# ==========================================
# AUTH SCREEN
# ==========================================
if not st.session_state.user:
    if "auth_mode" not in st.session_state: st.session_state.auth_mode = "login"
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center;'>🐦</h1>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>Sign in to Mini Twitter</h2>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #536471; font-size: 0.9em;'>SocialFi: Integrated SUI Wallet</p>", unsafe_allow_html=True)
        
        with st.container(border=True):
            if st.session_state.auth_mode == "login":
                with st.form("login_form"):
                    st.write("Enter your details")
                    username = st.text_input("Username", placeholder="@username")
                    password = st.text_input("Password", type="password", placeholder="••••••••")
                    st.write("") 
                    submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)
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
                st.markdown("<div style='text-align: center; color: #536471; font-size: 0.8em;'>Don't have an account?</div>", unsafe_allow_html=True)
                if st.button("Create an account", use_container_width=True):
                    st.session_state.auth_mode = "signup"
                    st.rerun()
            else:
                st.info("ℹ️ A SUI Blockchain Wallet will be generated for you.")
                with st.form("signup_form"):
                    su_user = st.text_input("Choose username", placeholder="e.g., tech_guru")
                    su_name = st.text_input("Display name", placeholder="e.g., Tech Guru")
                    su_pass = st.text_input("Password", type="password")
                    su_bio = st.text_area("Bio (optional)", placeholder="Tell the world about yourself...")
                    su_pic = st.file_uploader("Profile Picture", type=["png","jpg","jpeg"])
                    st.write("")
                    ok = st.form_submit_button("Sign up & Generate Wallet", type="primary", use_container_width=True)
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
                st.markdown("<div style='text-align: center; color: #536471; font-size: 0.8em;'>Have an account already?</div>", unsafe_allow_html=True)
                if st.button("Back to Login", use_container_width=True):
                    st.session_state.auth_mode = "login"
                    st.rerun()
    st.stop()

# ==========================================
# MAIN APP - LOGGED IN
# ==========================================
if "view" not in st.session_state: st.session_state.view = "home"

with st.sidebar:
    st.markdown("<h1 style='text-align: center; color: white; font-size: 45px; margin-top: -20px; margin-bottom: 10px;'>𝕏</h1>", unsafe_allow_html=True)
    if st.button("🏠   Home", use_container_width=True): st.session_state.view = "home"; st.rerun()
    if st.button("🔍   Explore", use_container_width=True): st.session_state.view = "explore"; st.rerun()
    if st.button("🔔   Notifications", use_container_width=True): st.session_state.view = "notifications"; st.rerun()
    if st.button("✉️   Messages", use_container_width=True): st.session_state.view = "messages"; st.rerun()
    if st.button("🔖   Bookmarks", use_container_width=True): st.session_state.view = "bookmarks"; st.rerun()
    if st.button("💳   Wallet", use_container_width=True): st.session_state.view = "wallet"; st.rerun()
    if st.button("👤   Profile", use_container_width=True): st.session_state.view = f"profile:{st.session_state.user['username']}"; st.rerun()
    st.write("") 
    if st.button("Tweet", type="primary", use_container_width=True): st.session_state.view = "create_post"; st.rerun()
    st.divider()
    
    usr = st.session_state.user
    if usr:
        usr = get_user_by_id(usr['id']) 
        st.session_state.user = usr
        with st.container():
            col_p1, col_p2 = st.columns([1, 3])
            with col_p1:
                if usr.get('profile_pic_path'): st.image(usr['profile_pic_path'], width=40)
                else: st.write("👤")
            with col_p2:
                st.markdown(f"<div style='line-height: 1.1; margin-top: 2px;'><b>{usr.get('display_name')}</b><br><span style='color: gray; font-size: 0.9em;'>@{usr.get('username')}</span></div>", unsafe_allow_html=True)

    addr = usr.get("wallet_address", "No Wallet")
    short_addr = f"{addr[:5]}...{addr[-5:]}"
    st.markdown(f"<div style='background-color: #e7f5fd; color: #1d9bf0; padding: 8px; border-radius: 5px; font-family: monospace; text-align: center; font-size: 0.9em; border: 1px solid #1d9bf0; margin-top: 5px;'>{short_addr}</div>", unsafe_allow_html=True)
    st.write("")
    if st.button("🚪 Sign Out", use_container_width=True):
        # 1. Clear State
        st.session_state.user = None
        st.session_state.auth_mode = "login"
        st.session_state.view = "home"
        
        # 2. Delete Cookie
        cookie_manager.delete("current_user_id")
        
        # 3. CRITICAL: Wait for browser to actually delete it
        # This prevents the "Sticky Login" issue
        time.sleep(1) 
        st.rerun()

# --- VIEW HANDLERS ---
if st.session_state.view == "create_post":
    st.header("Create a post")
    with st.form("post_form"):
        text = st.text_area("What's happening?", max_chars=280)
        img = st.file_uploader("Image (optional)", type=["png","jpg","jpeg","gif"])
        ok = st.form_submit_button("Post")
        if ok:
            img_path = None
            if img:
                fname = f"{int(time.time()*1000)}_{img.name}"
                path = os.path.join(POST_IMAGE_DIR, fname)
                with open(path, "wb") as f: f.write(img.getbuffer())
                img_path = path
            create_post(st.session_state.user['id'], text, img_path)
            st.success("Posted")
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
            txt = st.text_area("Reply", max_chars=280)
            ok = st.form_submit_button("Reply")
            if ok:
                reply_to_post(st.session_state.user['id'], pid, txt)
                st.success("Replied")
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
            submitted = st.form_submit_button("Save Changes", type="primary")
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
    st.header("Home")
    posts = get_feed(st.session_state.user['id'], limit=100)
    if not posts: st.info("Your timeline is empty. Go to 'Explore' to find people to follow!")
    for p in posts: render_post(p, "home")

elif st.session_state.view == "explore":
    st.header("Explore")
    term = st.text_input("Search users or posts", placeholder="Try searching for a username or topic...")
    if term:
        st.subheader("Users")
        for u in search_users(term):
            st.write(f"@{u['username']} — {u['display_name']}")
            if st.button("View", key=f"viewu:{u['id']}"):
                st.session_state.view = f"profile:{u['username']}"; st.rerun()
        st.subheader("Posts")
        for p in search_posts(term): render_post(p, "explore")
    else:
        st.subheader("Public posts (recent)")
        c = get_conn().cursor()
        c.execute("SELECT p.*, u.username, u.display_name, u.profile_pic_path FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC LIMIT 100")
        for p in c.fetchall(): render_post(p, "explore")

elif st.session_state.view == "bookmarks":
    st.header("Bookmarks")
    bookmarks = get_bookmarks_for_user(st.session_state.user['id'])
    if not bookmarks: st.info("No bookmarks yet.")
    for p in bookmarks: render_post(p, "bookmarks")

elif st.session_state.view == "notifications":
    st.header("Notifications")
    notes = get_notifications(st.session_state.user['id'])
    if not notes: st.info("No notifications.")
    for n in notes: st.write(f"**{human_time(n['created_at'])}** — {n['text']}")
    mark_notifications_seen(st.session_state.user['id'])

elif st.session_state.view == "messages":
    st.header("Direct Messages")
    user = st.session_state.user
    rows = get_conn().cursor().execute("SELECT username FROM users WHERE id != ?", (user['id'],)).fetchall()
    options = [r['username'] for r in rows]
    other = st.selectbox("Select user to message", options=options)
    if other:
        other_row = get_user_by_username(other)
        st.subheader(f"Chat with @{other_row['username']}")
        render_realtime_chat(user['id'], other_row['id'], user['username'], other_row['username'])
        with st.form("send_msg", clear_on_submit=True):
            txt = st.text_area("Message")
            ok = st.form_submit_button("Send")
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

    st.markdown("<h1 style='margin-bottom: 20px;'>Your Coins</h1>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background-color: #000000; border-radius: 20px; border: 1px solid #2f3336; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.02); margin-bottom: 30px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; align-items: center; gap: 15px;">
                <img src="https://s2.coinmarketcap.com/static/img/coins/64x64/20947.png" width="48" height="48" style="border-radius: 50%;">
                <div>
                    <div style="font-weight: 800; font-size: 19px; color: #e7e9ea; display: flex; align-items: center; gap: 4px;">Sui <img src="https://upload.wikimedia.org/wikipedia/commons/e/e4/Twitter_Verified_Badge.svg" width="18" height="18"></div>
                    <div style="font-size: 15px; color: #71767b; margin-top: 2px;">${sui_price:,.2f} <span style="color: {change_color}; font-weight: 500;">{change_sign}{price_change_pct:.2f}%</span></div>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-weight: 800; font-size: 19px; color: #e7e9ea;">${holdings_value:,.2f}</div>
                <div style="font-size: 15px; color: #71767b; margin-top: 2px;">{balance:.4f} SUI</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.subheader("📤 Withdraw Funds")
    st.write("Send SUI to an external address.")
    with st.form("withdraw_form"):
        dest_addr = st.text_input("Destination Address (0x...)")
        amount = st.number_input("Amount to Send", min_value=0.0, max_value=balance, step=0.1)
        if st.form_submit_button("Send Transaction", width="stretch"):
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
    with st.expander("🔐 View Private Keys (Security Risk!)"):
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
        is_me = (st.session_state.user['id'] == user_id)
        st.markdown("""<div style="background-color: transparent; height: 120px; width: 100%; margin-bottom: -60px;"></div>""", unsafe_allow_html=True)
        header_cols = st.columns([1, 2, 1])
        with header_cols[0]:
            st.markdown('<div class="profile-pic">', unsafe_allow_html=True)
            if u.get('profile_pic_path') and os.path.exists(u['profile_pic_path']): st.image(u['profile_pic_path'], width=130)
            else: st.image("https://cdn-icons-png.flaticon.com/512/149/149071.png", width=130)
            st.markdown('</div>', unsafe_allow_html=True)
        with header_cols[2]:
            st.write(""); st.write("") 
            if not is_me:
                btn_cols = st.columns([1, 1])
                with btn_cols[0]:
                    with st.popover("💸 Send SUI"):
                        st.write(f"Tip @{u['username']}")
                        tip_val = st.number_input("SUI", 0.1, step=0.1, key=f"tip_{user_id}")
                        if st.button("Send", key=f"pay_{user_id}"):
                            with st.spinner("..."):
                                s, m = send_sui_payment(st.session_state.user['private_key'], u['wallet_address'], tip_val)
                                if s: st.success("Sent!"); create_notification(user_id, f"Tip from @{st.session_state.user['username']}")
                                else: st.error(m)
                with btn_cols[1]:
                    if is_following(st.session_state.user['id'], user_id):
                        if st.button("Unfollow", key=f"unfol_{user_id}"): unfollow_user(st.session_state.user['id'], user_id); st.rerun()
                    else:
                        if st.button("Follow", type="primary", key=f"fol_{user_id}"): follow_user(st.session_state.user['id'], user_id); st.rerun()
            else:
                if st.button("Edit Profile", key="edit_profile_btn"): st.session_state.view = "edit_profile"; st.rerun()

        st.markdown(f"""<div style="margin-top: 10px;"><div style="font-size: 1.5rem; font-weight: 800; line-height: 1.2; color: white;">{u['display_name']}</div><div style="color: #71767b; font-size: 1rem;">@{u['username']}</div></div>""", unsafe_allow_html=True)
        if u.get('bio'): st.markdown(f"<div style='margin-top: 10px; font-size: 1rem; color: #e7e9ea;'>{u['bio']}</div>", unsafe_allow_html=True)
        st.markdown(f"""<div style="color: #71767b; font-size: 0.9rem; margin-top: 10px; display: flex; align-items: center;">📅 Joined {human_time(u.get('created_at')).split(' ')[0]}</div>""", unsafe_allow_html=True)

        st.write("")
        stat_cols = st.columns([1, 1, 4])
        with stat_cols[0]:
            if st.button(f"{get_following_count(user_id)} Following", key=f"ing_{user_id}"): st.session_state.view = f"following_list:{user_id}:{uname}"; st.rerun()
        with stat_cols[1]:
            if st.button(f"{get_follower_count(user_id)} Followers", key=f"ers_{user_id}"): st.session_state.view = f"followers_list:{user_id}:{uname}"; st.rerun()

        st.write(""); st.write("")
        tab_posts, tab_replies, tab_likes = st.tabs(["Posts", "Replies", "Likes"])
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
    render_user_list(f"@{uname} is Following", get_following_list(int(user_id_str)))
    if st.button("← Back to Profile"): st.session_state.view = f"profile:{uname}"; st.rerun()

elif st.session_state.view.startswith("followers_list:"):
    _, user_id_str, uname = st.session_state.view.split(":")
    render_user_list(f"Followers of @{uname}", get_followers_list(int(user_id_str)))
    if st.button("← Back to Profile"): st.session_state.view = f"profile:{uname}"; st.rerun()

else: st.write("Unknown view")
st.markdown("---")
st.caption("Mini Twitter Clone (Web3 Enabled)")
