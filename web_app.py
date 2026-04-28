import json
import os
import random
import threading
import time
from datetime import datetime

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ClientThrottledError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY", "instagram-bot-secret-dev"
)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

bot_state = {
    "running": False,
    "feature_running": False,
    "cl": None,
    "username": None,
    "stats": {
        "followers_gained": 0,
        "likes_given": 0,
        "unfollowed": 0,
        "stories_viewed": 0,
        "dms_sent": 0,
        "accounts_processed": 0,
        "start_time": None,
    },
}


def load_saved_session():
    """Try to load a saved session if available"""
    try:
        import glob

        session_files = glob.glob("sessions/*.json")
        log(f"🔍 Checking for saved sessions... Found {len(session_files)} files")
        if session_files:
            latest_file = max(session_files, key=os.path.getmtime)
            log(f"📂 Found saved session: {latest_file}")

            cl = Client()
            cl.delay_range = [3, 6]
            log(f"📖 Loading session file...")
            cl.load_settings(latest_file)

            # Verify session still works
            try:
                log(f"🔐 Verifying session is still valid...")
                user_info = cl.account_info()
                username = user_info.username
                bot_state["cl"] = cl
                bot_state["username"] = username
                log(f"✅ Loaded saved session as @{username}")
                return True
            except Exception as e:
                log(f"⚠️ Saved session verification failed: {str(e)[:100]}")
                log("⚠️ Saved session expired, need to login again")
                return False
        else:
            log("ℹ️ No saved sessions found, need to login")
    except Exception as e:
        log(f"⚠️ Could not load session: {str(e)[:100]}")
        import traceback

        log(f"🔧 Traceback: {traceback.format_exc()[:200]}")
    return False


def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit("log", {"message": f"[{timestamp}] {message}"})


def update_stats():
    socketio.emit("stats", bot_state["stats"])


def human_delay(base=2.0, jitter=0.3):
    time.sleep(base * random.uniform(1 - jitter, 1 + jitter))


def try_recover_session():
    """Try to reload session from disk when login_required is hit"""
    try:
        log("🔄 Session expired — attempting auto-recovery...")
        import glob

        session_files = glob.glob("sessions/*.json")
        if not session_files:
            log("❌ No saved sessions to recover from")
            return False
        latest = max(session_files, key=os.path.getmtime)
        cl = Client()
        cl.delay_range = [4, 8]
        cl.load_settings(latest)
        # Re-login with saved credentials to refresh the session
        cl.get_timeline_feed()  # test if session still works
        user_info = cl.account_info()
        username = user_info.username
        bot_state["cl"] = cl
        bot_state["username"] = username
        cl.dump_settings(latest)  # save refreshed session
        log(f"✅ Session recovered as @{username}")
        return True
    except Exception as e:
        log(f"❌ Auto-recovery failed: {str(e)[:80]}")
        log("⚠️ Please re-login manually from the Login section")
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})
        socketio.emit("session_expired", {})
        return False


def handle_error(e, context="action"):
    """Central error handler — returns True if bot should continue, False if stop"""
    if isinstance(e, LoginRequired):
        log(f"🔑 Session expired during {context}")
        return try_recover_session()
    elif isinstance(e, (PleaseWaitFewMinutes, ClientThrottledError)):
        log(f"⏳ Rate limited — waiting 5 minutes...")
        time.sleep(300)
        return True
    elif isinstance(e, ChallengeRequired):
        log("⚠️ Instagram challenge required — please verify the account manually")
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})
        return False
    elif isinstance(e, FeedbackRequired):
        log("⚠️ Action blocked by Instagram — pausing 3 minutes...")
        time.sleep(180)
        return True
    else:
        err = str(e).lower()
        if "login_required" in err or "not authorized" in err:
            log(f"🔑 Login required during {context}")
            return try_recover_session()
        elif "wait" in err or "throttl" in err or "rate" in err or "few minutes" in err:
            log(f"⏳ Rate limited — waiting 5 minutes...")
            time.sleep(300)
            return True
        elif "feedback_required" in err:
            log("⚠️ Action blocked — pausing 3 minutes...")
            time.sleep(180)
            return True
        log(f"⚠️ {context} error: {str(e)[:100]}")
        return True  # keep going on unknown errors


# ==================== INSTAGRAM ACTIONS ====================


def do_browser_login():
    """Open Chrome so user can login themselves, then capture session"""
    driver = None
    try:
        log("🌐 Opening Chrome browser for you to login...")
        log("👉 Login to Instagram in the browser window, then come back here")

        options = Options()
        options.add_argument("--window-size=412,915")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )

        # Auto-download correct ChromeDriver version
        log(f"🔧 Setting up ChromeDriver...")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        log(f"✅ Chrome browser opened")

        # Navigate to Instagram login
        log(f"🌍 Navigating to Instagram login page...")
        driver.get("https://www.instagram.com/accounts/login/")
        log("⏳ Waiting for you to login... (take your time)")

        # Wait until user is logged in (URL changes away from login page)
        for i in range(300):  # Wait up to 5 minutes
            time.sleep(1)
            try:
                current_url = driver.current_url
                if i % 30 == 0:  # Log every 30 seconds
                    log(f"⏱️ Still waiting... ({i // 60}m {i % 60}s elapsed)")
                if "login" not in current_url and "accounts" not in current_url:
                    log(f"✅ Detected successful login! URL: {current_url}")
                    break
            except Exception as e:
                log(f"⚠️ Error checking URL: {str(e)[:50]}")
                break
        else:
            log("⚠️ Timed out waiting for login (5 minutes)")
            return False

        time.sleep(3)  # Let page fully load
        log("📄 Page loaded, extracting cookies...")

        # Extract cookies from browser
        cookies = driver.get_cookies()
        log(f"🍪 Captured {len(cookies)} cookies from browser")

        # Log cookie names for debugging
        cookie_names = [c["name"] for c in cookies]
        log(f"📋 Cookie names: {', '.join(cookie_names[:10])}")

        # Get session info from cookies
        session_data = {}
        for cookie in cookies:
            session_data[cookie["name"]] = cookie["value"]

        # Check for sessionid
        sessionid = session_data.get("sessionid", "")
        if sessionid:
            log(f"✅ Found sessionid cookie (length: {len(sessionid)})")
        else:
            log("❌ No sessionid cookie found!")
            return False

        # Get the username from the browser
        try:
            log("🔍 Fetching username from account edit page...")
            driver.get("https://www.instagram.com/accounts/edit/")
            time.sleep(3)
            # Try to find username in page source
            page_source = driver.page_source
            import re

            username_match = re.search(r'"username":"([^"]+)"', page_source)
            if username_match:
                username = username_match.group(1)
                log(f"👤 Detected username: @{username}")
            else:
                log("⚠️ Could not extract username from page source")
                username = "me"
        except Exception as e:
            log(f"⚠️ Error getting username: {str(e)[:50]}")
            username = "me"

        # Create instagrapi client with safe delays
        cl = Client()
        cl.delay_range = [3, 6]  # 3-6 seconds between actions (safe mode)

        # Use sessionid for login (most reliable method)
        sessionid = session_data.get("sessionid", "")

        if not sessionid:
            log("❌ Could not find sessionid cookie")
            return False

        try:
            log(f"🔑 Using sessionid to login...")
            cl.login_by_sessionid(sessionid=sessionid)
            username = cl.account_info().username
            log(f"✅ Logged in via session ID as @{username}")

            # Save session for reuse
            os.makedirs("sessions", exist_ok=True)
            cl.dump_settings(f"sessions/{username}.json")
            log("💾 Session saved for next time")

            bot_state["cl"] = cl
            bot_state["username"] = username
            log(f"✅ Ready! Bot is connected as @{username}")
            return True

        except Exception as e:
            log(f"❌ Session login failed: {str(e)[:100]}")
            log("� Try logging in again or check if session expired")
            return False

    except Exception as e:
        log(f"❌ Browser login error: {str(e)}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
                log("🌐 Browser closed")
            except:
                pass


def do_search_and_follow(target, max_followers, follow_limit, welcome_message=None):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    try:
        cl = bot_state["cl"]
        log(f"🔍 Starting follow process for target: @{target}")
        if welcome_message:
            log(f"💬 Welcome DM enabled: {welcome_message[:50]}...")

        user_id = cl.user_id_from_username(target)
        log(f"✅ Found user ID for @{target}: {user_id}")

        log(f"👥 Fetching up to {max_followers} followers...")
        followers = cl.user_followers(user_id, amount=max_followers)
        log(f"👥 Fetched {len(followers)} followers")

        followed = 0
        skipped = 0
        for uid, user_info in followers.items():
            if not bot_state["running"]:
                log("⏸️ Bot stopped by user")
                break
            if bot_state["stats"]["followers_gained"] >= follow_limit:
                log(f"🎯 Daily follow limit reached ({follow_limit})")
                break

            uname = user_info.username if hasattr(user_info, "username") else str(uid)

            # --- FOLLOW ---
            try:
                cl = bot_state["cl"]  # refresh ref in case session was recovered
                log(f"👤 Following @{uname}...")
                cl.user_follow(int(uid))
                bot_state["stats"]["followers_gained"] += 1
                followed += 1
                log(
                    f"✅ Followed @{uname} ({followed}/{max_followers}) | Total: {bot_state['stats']['followers_gained']}/{follow_limit}"
                )
                update_stats()
                # Safe delay after follow (8–15 sec)
                time.sleep(random.uniform(8, 15))
            except Exception as e:
                skipped += 1
                should_continue = handle_error(e, f"follow @{uname}")
                if not should_continue:
                    return
                continue  # skip DM if follow failed

            # --- WELCOME DM ---
            if welcome_message:
                try:
                    cl = bot_state["cl"]
                    log(f"💬 Sending welcome DM to @{uname}...")
                    dm_user_id = cl.user_id_from_username(uname)
                    cl.direct_send(welcome_message, user_ids=[int(dm_user_id)])
                    bot_state["stats"]["dms_sent"] += 1
                    log(f"✅ Welcome DM sent to @{uname}")
                    update_stats()
                    # Safe delay after DM (10–20 sec)
                    time.sleep(random.uniform(10, 20))
                except Exception as e:
                    should_continue = handle_error(e, f"DM @{uname}")
                    if not should_continue:
                        return

            # Extra break every 5 follows
            if followed > 0 and followed % 5 == 0:
                pause = random.uniform(45, 90)
                log(f"😴 Anti-detection pause ({int(pause)}s)...")
                time.sleep(pause)

        bot_state["stats"]["accounts_processed"] += 1
        update_stats()
        log(f"✅ Done with @{target}: {followed} followed, {skipped} skipped")

    except Exception as e:
        handle_error(e, f"search_and_follow @{target}")


def do_auto_unfollow(max_unfollows):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return

    # Stop the main bot loop and any other running feature
    if bot_state["running"]:
        log("🛑 Stopping main bot loop to start Auto Unfollow...")
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})
        time.sleep(2)  # let the loop wind down
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Auto Unfollow"})

    try:
        cl = bot_state["cl"]
        log(f"🔄 Fetching up to {max_unfollows} accounts to unfollow...")
        following = cl.user_following(cl.user_id, amount=max_unfollows)
        total = len(following)
        log(f"📊 Fetched {total} accounts to unfollow")

        unfollowed = 0
        skipped = 0
        for uid, user_info in following.items():
            if not bot_state["feature_running"]:
                log("⏹️ Auto Unfollow stopped by user")
                break
            if unfollowed >= max_unfollows:
                log(f"🎯 Unfollow limit reached ({max_unfollows})")
                break

            uname = user_info.username if hasattr(user_info, "username") else str(uid)
            try:
                cl = bot_state["cl"]
                cl.user_unfollow(int(uid))
                unfollowed += 1
                bot_state["stats"]["unfollowed"] += 1
                log(f"✅ Unfollowed @{uname} ({unfollowed}/{max_unfollows})")
                update_stats()
                # Safe delay between unfollows
                time.sleep(random.uniform(5, 10))
                if unfollowed % 10 == 0:
                    pause = random.uniform(45, 90)
                    log(f"😴 Anti-detection pause ({int(pause)}s)...")
                    time.sleep(pause)
            except Exception as e:
                skipped += 1
                should_continue = handle_error(e, f"unfollow @{uname}")
                if not should_continue:
                    break

        log(f"✅ Auto Unfollow done: {unfollowed} unfollowed, {skipped} skipped")
    except Exception as e:
        handle_error(e, "auto_unfollow")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Auto Unfollow"})


def do_auto_like_feed(num_likes):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"❤️ Auto-like feed (max: {num_likes})...")
        feed = cl.get_timeline_feed()
        liked = 0
        for item in feed:
            if not bot_state["feature_running"] or liked >= num_likes:
                break
            try:
                pk = item.pk if hasattr(item, "pk") else item.id
                cl.media_like(pk)
                liked += 1
                bot_state["stats"]["likes_given"] += 1
                log(f"❤️ Liked post #{liked}/{num_likes}")
                update_stats()
                human_delay(base=2.0)
                if liked % 15 == 0:
                    log("😴 Break from liking...")
                    time.sleep(random.uniform(20, 40))
            except Exception as e:
                log(f"⚠️ Skip: {str(e)[:50]}")
        log(f"✅ Liked {liked} posts")
    except Exception as e:
        log(f"❌ Error: {str(e)}")
    finally:
        bot_state["feature_running"] = False


def do_mass_story_view(max_stories):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"👁️ Mass story view (max: {max_stories})...")
        feed = cl.get_timeline_feed()
        viewed = 0
        for item in feed:
            if not bot_state["feature_running"] or viewed >= max_stories:
                break
            try:
                user_pk = (
                    item.user.pk
                    if hasattr(item, "user") and hasattr(item.user, "pk")
                    else None
                )
                if not user_pk:
                    continue
                stories = cl.user_stories(user_pk)
                for story in stories:
                    if not bot_state["feature_running"] or viewed >= max_stories:
                        break
                    story_pk = story.pk if hasattr(story, "pk") else story.id
                    cl.story_seen([story_pk])
                    viewed += 1
                    bot_state["stats"]["stories_viewed"] += 1
                    log(f"👁️ Viewed story #{viewed}/{max_stories}")
                    update_stats()
                    human_delay(base=1.5)
            except:
                continue
        log(f"✅ Viewed {viewed} stories")
    except Exception as e:
        log(f"❌ Error: {str(e)}")
    finally:
        bot_state["feature_running"] = False


def do_auto_dm(target, message):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    try:
        log(f"💬 Sending DM to @{target}...")
        user_id = cl.user_id_from_username(target)
        cl.direct_send(message, user_ids=[user_id])
        bot_state["stats"]["dms_sent"] += 1
        log(f"✅ DM sent to @{target}")
        update_stats()
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "403" in error_str or "challenge" in error_str.lower():
            log(f"❌ Instagram API error: {error_str[:100]}")
            log("⚠️ Session may be expired. Try logging in again.")
        else:
            log(f"❌ DM error: {error_str[:100]}")


def do_auto_dm_following(message, limit):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"💬 Auto-DM to following list (max: {limit})...")
        following = cl.user_following(cl.user_id)
        log(f"📊 You follow {len(following)} users")

        sent = 0
        skipped = 0
        for uid, user_info in following.items():
            if not bot_state["feature_running"] or sent >= limit:
                break
            try:
                uname = (
                    user_info.username if hasattr(user_info, "username") else str(uid)
                )
                log(f"💬 Sending DM to @{uname}...")
                cl.direct_send(message, user_ids=[uid])
                bot_state["stats"]["dms_sent"] += 1
                sent += 1
                log(f"✅ DM sent to @{uname} ({sent}/{limit})")
                update_stats()
                human_delay(base=3.0)
                if sent % 10 == 0:
                    log("😴 Break from DMing...")
                    time.sleep(random.uniform(60, 120))
            except Exception as e:
                error_str = str(e)
                if (
                    "404" in error_str
                    or "403" in error_str
                    or "challenge" in error_str.lower()
                ):
                    log(f"❌ Instagram API error for @{uname}: {error_str[:80]}")
                    log("⚠️ Session may be expired. Try logging in again.")
                    break
                elif "wait" in error_str.lower() or "rate" in error_str.lower():
                    log(f"⚠️ Rate limited for DM: {error_str[:80]}")
                    log("😴 Waiting 3 minutes before continuing...")
                    time.sleep(180)
                else:
                    skipped += 1
                    log(f"⚠️ Skip @{uname}: {error_str[:50]}")
        log(f"✅ DM sent to {sent} users, {skipped} skipped")
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "403" in error_str or "challenge" in error_str.lower():
            log(f"❌ Instagram API error: {error_str[:100]}")
            log("⚠️ Session may be expired. Try logging in again.")
        elif "wait" in error_str.lower() or "rate" in error_str.lower():
            log(f"⚠️ Rate limit error: {error_str[:100]}")
            log("😴 Waiting 5 minutes before retrying...")
            time.sleep(300)
        else:
            log(f"❌ Error: {error_str[:100]}")
    finally:
        bot_state["feature_running"] = False


def do_welcome_dm_following(message, limit):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"📩 Welcome DM to following list (max: {limit})...")
        following = cl.user_following(cl.user_id)
        log(f"📊 You follow {len(following)} users")

        sent = 0
        skipped = 0
        for uid, user_info in following.items():
            if not bot_state["feature_running"] or sent >= limit:
                break
            try:
                uname = (
                    user_info.username if hasattr(user_info, "username") else str(uid)
                )
                log(f"📩 Sending welcome DM to @{uname}...")
                cl.direct_send(message, user_ids=[uid])
                bot_state["stats"]["dms_sent"] += 1
                sent += 1
                log(f"✅ Welcome DM sent to @{uname} ({sent}/{limit})")
                update_stats()
                human_delay(base=2.5)
                if sent % 10 == 0:
                    log("😴 Break from welcome DMing...")
                    time.sleep(random.uniform(60, 120))
            except Exception as e:
                error_str = str(e)
                if (
                    "404" in error_str
                    or "403" in error_str
                    or "challenge" in error_str.lower()
                ):
                    log(f"❌ Instagram API error for @{uname}: {error_str[:80]}")
                    log("⚠️ Session may be expired. Try logging in again.")
                    break
                elif "wait" in error_str.lower() or "rate" in error_str.lower():
                    log(f"⚠️ Rate limited for welcome DM: {error_str[:80]}")
                    log("😴 Waiting 3 minutes before continuing...")
                    time.sleep(180)
                else:
                    skipped += 1
                    log(f"⚠️ Skip @{uname}: {error_str[:50]}")
        log(f"✅ Welcome DM sent to {sent} users, {skipped} skipped")
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "403" in error_str or "challenge" in error_str.lower():
            log(f"❌ Instagram API error: {error_str[:100]}")
            log("⚠️ Session may be expired. Try logging in again.")
        elif "wait" in error_str.lower() or "rate" in error_str.lower():
            log(f"⚠️ Rate limit error: {error_str[:100]}")
            log("😴 Waiting 5 minutes before retrying...")
            time.sleep(300)
        else:
            log(f"❌ Error: {error_str[:100]}")
    finally:
        bot_state["feature_running"] = False


def do_approve_requests():
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    try:
        log("✅ Approving follow requests...")
        pending = cl.user_pending_follow_requests()
        approved = 0
        for req in pending:
            try:
                uid = req.pk if hasattr(req, "pk") else req.id
                cl.approve_pending_follow_request(uid)
                approved += 1
                uname = req.username if hasattr(req, "username") else str(uid)
                log(f"✅ Approved @{uname}")
                human_delay(base=1.0)
            except Exception as e:
                log(f"⚠️ Skip: {str(e)[:50]}")
        log(f"✅ Approved {approved} requests")
    except Exception as e:
        log(f"❌ Error: {str(e)}")


def do_like_user_posts(target, num_likes):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"❤️ Liking {num_likes} posts from @{target}...")
        user_id = cl.user_id_from_username(target)
        medias = cl.user_medias(user_id, amount=num_likes)
        liked = 0
        for media in medias:
            if not bot_state["feature_running"] or liked >= num_likes:
                break
            try:
                pk = media.pk if hasattr(media, "pk") else media.id
                cl.media_like(pk)
                liked += 1
                bot_state["stats"]["likes_given"] += 1
                log(f"❤️ Liked #{liked}/{num_likes} from @{target}")
                update_stats()
                human_delay(base=2.0)
            except Exception as e:
                log(f"⚠️ Skip: {str(e)[:50]}")
        log(f"✅ Liked {liked} posts from @{target}")
    except Exception as e:
        log(f"❌ Error: {str(e)}")
    finally:
        bot_state["feature_running"] = False


def do_auto_comment(target, comments_list, limit):
    cl = bot_state["cl"]
    if not cl:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        log(f"💬 Auto-commenting on @{target} posts (max {limit})...")
        user_id = cl.user_id_from_username(target)
        medias = cl.user_medias(user_id, amount=limit)
        commented = 0
        for media in medias:
            if not bot_state["feature_running"] or commented >= limit:
                break
            try:
                pk = media.pk if hasattr(media, "pk") else media.id
                comment = random.choice(comments_list)
                cl.media_comment(pk, comment)
                commented += 1
                log(
                    f"💬 Commented '{comment}' on post #{commented}/{limit} from @{target}"
                )
                update_stats()
                human_delay(base=3.0)
            except Exception as e:
                log(f"⚠️ Skip comment: {str(e)[:60]}")
                human_delay(base=1.0)
        log(f"✅ Commented on {commented} posts from @{target}")
    except Exception as e:
        log(f"❌ Error: {str(e)}")
    finally:
        bot_state["feature_running"] = False


# ==================== MAIN BOT LOOP ====================


def run_bot_loop(
    targets, follow_limit, like_limit, followers_per_account, welcome_message=None
):
    try:
        log("🚀 Starting bot loop...")

        # Check if logged in
        if not bot_state["cl"]:
            log("⚠️ No active session found, attempting browser login...")
            if not do_browser_login():
                log("❌ Browser login failed, cannot start bot")
                bot_state["running"] = False
                socketio.emit("bot_status", {"running": False})
                return
        else:
            log(f"✅ Using existing session as @{bot_state['username']}")

        target_accounts = [t.strip() for t in targets.split(",") if t.strip()]
        if not target_accounts:
            log("❌ No target accounts specified!")
            bot_state["running"] = False
            socketio.emit("bot_status", {"running": False})
            return

        log(f"🎯 Target accounts: {', '.join(['@' + a for a in target_accounts])}")
        log(f"📊 Daily follow limit: {follow_limit}")
        log(f"📊 Followers per account: {followers_per_account}")
        log(f"📊 Current followers gained: {bot_state['stats']['followers_gained']}")
        if welcome_message:
            log(f"💬 Welcome DM: {welcome_message[:50]}...")

        loop_count = 0
        while (
            bot_state["running"]
            and bot_state["stats"]["followers_gained"] < follow_limit
        ):
            loop_count += 1
            log(f"🔄 === Loop #{loop_count} ===")
            random.shuffle(target_accounts)

            for target in target_accounts:
                if not bot_state["running"]:
                    log("⏸️ Bot stopped by user")
                    break
                if bot_state["stats"]["followers_gained"] >= follow_limit:
                    log(f"🎯 Daily follow limit reached ({follow_limit})")
                    break

                log(f"🎯 Processing target: @{target}")
                do_search_and_follow(
                    target, followers_per_account, follow_limit, welcome_message
                )
                log(f"⏸️ Delay before next target...")
                human_delay(base=5.0)

        log("🎉 Bot session completed!")
        log(
            f"📊 Final stats: {bot_state['stats']['followers_gained']} followers gained"
        )

    except Exception as e:
        log(f"❌ Bot error: {str(e)}")
        import traceback

        log(f"🔧 Traceback: {traceback.format_exc()[:300]}")
    finally:
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})


# ==================== FLASK ROUTES ====================


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    emit("bot_status", {"running": bot_state["running"]})
    emit("stats", bot_state["stats"])

    # Try to load saved session automatically
    if not bot_state["cl"]:
        if load_saved_session():
            emit(
                "login_status",
                {"success": True, "username": bot_state.get("username", "")},
            )


@socketio.on("browser_login")
def on_browser_login(data):
    log("📡 Browser login requested...")

    def login_thread():
        if do_browser_login():
            socketio.emit(
                "login_status",
                {"success": True, "username": bot_state.get("username", "")},
            )
        else:
            socketio.emit(
                "login_status", {"success": False, "error": "Browser login failed"}
            )

    threading.Thread(target=login_thread, daemon=True).start()


@socketio.on("password_login")
def on_password_login(data):
    username = data.get("username", "")
    password = data.get("password", "")
    log(f"📡 Password login requested for @{username}...")

    def login_thread():
        try:
            cl = Client()
            cl.delay_range = [3, 6]
            bot_state["pending_cl"] = cl
            bot_state["pending_username"] = username
            bot_state["pending_password"] = password
            try:
                cl.login(username, password)
            except Exception as e:
                error_str = str(e).lower()
                if (
                    "two" in error_str
                    or "factor" in error_str
                    or "challenge" in error_str
                    or "verification" in error_str
                    or "code" in error_str
                ):
                    log(f"🔐 2FA required for @{username} — enter your code")
                    socketio.emit("two_fa_required", {"required": True})
                    return
                raise
            bot_state["cl"] = cl
            bot_state["username"] = username
            os.makedirs("sessions", exist_ok=True)
            cl.dump_settings(f"sessions/{username}.json")
            log(f"💾 Session saved for @{username}")
            log(f"✅ Logged in as @{username}")
            socketio.emit("login_status", {"success": True, "username": username})
        except Exception as e:
            log(f"❌ Password login failed: {str(e)[:100]}")
            socketio.emit("login_status", {"success": False, "error": str(e)[:80]})

    threading.Thread(target=login_thread, daemon=True).start()


@socketio.on("login")
def on_login(data):
    # Legacy handler - redirect to browser login
    on_browser_login(data)


@socketio.on("two_fa_code")
def on_two_fa_code(data):
    code = data.get("code", "").strip()
    if not code:
        emit("login_status", {"success": False, "error": "Please enter the 2FA code"})
        return

    def _submit():
        try:
            cl = bot_state.get("pending_cl")
            username = bot_state.get("pending_username")
            password = bot_state.get("pending_password")
            if not cl or not username or not password:
                socketio.emit(
                    "login_status",
                    {"success": False, "error": "Session expired, please login again"},
                )
                return
            cl.login(username, password, verification_code=code)
            bot_state["cl"] = cl
            bot_state["username"] = username
            os.makedirs("sessions", exist_ok=True)
            cl.dump_settings(f"sessions/{username}.json")
            log(f"✅ 2FA login successful as @{username}")
            socketio.emit("login_status", {"success": True, "username": username})
            socketio.emit("two_fa_required", {"required": False})
        except Exception as e:
            log(f"❌ 2FA failed: {str(e)[:100]}")
            socketio.emit(
                "login_status", {"success": False, "error": "Wrong code — try again"}
            )

    threading.Thread(target=_submit, daemon=True).start()


@socketio.on("start_bot")
def on_start(data):
    if bot_state["running"]:
        log("⚠️ Bot already running")
        return
    bot_state["running"] = True
    bot_state["stats"] = {
        "followers_gained": 0,
        "likes_given": 0,
        "unfollowed": 0,
        "stories_viewed": 0,
        "dms_sent": 0,
        "accounts_processed": 0,
        "start_time": time.time(),
    }
    t = threading.Thread(
        target=run_bot_loop,
        args=(
            data.get("targets", ""),
            int(data.get("follow_limit", 120)),
            int(data.get("like_limit", 50)),
            int(data.get("followers_per_account", 10)),
            data.get("welcome_message", ""),
        ),
        daemon=True,
    )
    bot_state["thread"] = t
    t.start()
    emit("bot_status", {"running": True})


@socketio.on("stop_bot")
def on_stop():
    bot_state["running"] = False
    emit("bot_status", {"running": False})
    log("🛑 Bot stopped by user")


@socketio.on("auto_unfollow")
def on_unfollow(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    if bot_state["feature_running"]:
        log("⚠️ Stopping current feature to start Auto Unfollow...")
        bot_state["feature_running"] = False
        time.sleep(1)
    limit = int(data.get("limit", 50))
    log(f"🔄 Auto Unfollow requested — limit: {limit}")
    threading.Thread(target=do_auto_unfollow, args=(limit,), daemon=True).start()


@socketio.on("auto_like_feed")
def on_like_feed(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    threading.Thread(
        target=do_auto_like_feed, args=(int(data.get("limit", 20)),), daemon=True
    ).start()


@socketio.on("mass_story_view")
def on_mass_story(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    threading.Thread(
        target=do_mass_story_view, args=(int(data.get("limit", 50)),), daemon=True
    ).start()


@socketio.on("auto_dm")
def on_dm(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    msg = data.get("message", "") or "Hey! Thanks for following me 🙏"
    target = data.get("target", "")

    if target:
        # DM specific target
        threading.Thread(target=do_auto_dm, args=(target, msg), daemon=True).start()
    else:
        # DM to following list
        limit = int(data.get("limit", 50))
        threading.Thread(
            target=do_auto_dm_following, args=(msg, limit), daemon=True
        ).start()


@socketio.on("approve_requests")
def on_approve():
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    threading.Thread(target=do_approve_requests, daemon=True).start()


@socketio.on("like_user_posts")
def on_like_user(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    threading.Thread(
        target=do_like_user_posts,
        args=(data.get("target", ""), int(data.get("limit", 3))),
        daemon=True,
    ).start()


@socketio.on("welcome_dm")
def on_welcome_dm(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    msg = data.get("message", "") or "Hey! Thanks for following me 🙏"
    limit = int(data.get("limit", 50))
    threading.Thread(
        target=do_welcome_dm_following, args=(msg, limit), daemon=True
    ).start()


@socketio.on("stop_feature")
def on_stop_feature():
    bot_state["feature_running"] = False
    log("🛑 Feature stopped")


@socketio.on("auto_comment")
def on_auto_comment(data):
    if not bot_state["cl"]:
        log("❌ Login first!")
        return
    target = data.get("target", "")
    if not target:
        log("❌ Enter a target username for Auto Comment")
        return
    comments_raw = data.get("comments", "Great post!, 🔥, Amazing!, Nice!")
    comments_list = [c.strip() for c in comments_raw.split(",") if c.strip()]
    if not comments_list:
        comments_list = ["Great post!", "🔥", "Amazing!"]
    limit = int(data.get("limit", 10))
    threading.Thread(
        target=do_auto_comment, args=(target, comments_list, limit), daemon=True
    ).start()


@socketio.on("reset_stats")
def on_reset():
    bot_state["stats"] = {
        "followers_gained": 0,
        "likes_given": 0,
        "unfollowed": 0,
        "stories_viewed": 0,
        "dms_sent": 0,
        "accounts_processed": 0,
        "start_time": None,
    }
    emit("stats", bot_state["stats"])


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    socketio.run(
        app, debug=debug, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True
    )
