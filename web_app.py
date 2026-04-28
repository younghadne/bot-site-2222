import glob
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

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

bot_state = {
    "running": False,
    "feature_running": False,
    "cl": None,
    "username": None,
    "pending_cl": None,
    "pending_username": None,
    "pending_password": None,
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

# ── Helpers ──────────────────────────────────────────────────────────────────


def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit("log", {"message": f"[{timestamp}] {message}"})


def update_stats():
    socketio.emit("stats", bot_state["stats"])


def safe_delay(lo=3, hi=7):
    time.sleep(random.uniform(lo, hi))


# ── Session helpers ───────────────────────────────────────────────────────────


def load_saved_session():
    try:
        os.makedirs("sessions", exist_ok=True)
        files = glob.glob("sessions/*.json")
        main = [f for f in files if "backup" not in f]
        if not main:
            return False
        latest = max(main, key=os.path.getmtime)
        cl = Client()
        cl.delay_range = [3, 7]
        cl.load_settings(latest)
        user_info = cl.account_info()
        username = user_info.username
        bot_state["cl"] = cl
        bot_state["username"] = username
        log(f"✅ Loaded saved session as @{username}")
        return True
    except Exception as e:
        log(f"⚠️ Saved session expired or invalid: {str(e)[:80]}")
        return False


def try_recover_session():
    try:
        log("🔄 Session expired — attempting auto-recovery...")
        files = glob.glob("sessions/*.json")
        if not files:
            log("❌ No saved sessions to recover from")
            bot_state["running"] = False
            socketio.emit("bot_status", {"running": False})
            socketio.emit("session_expired", {})
            return False
        latest = max(files, key=os.path.getmtime)
        cl = Client()
        cl.delay_range = [3, 7]
        cl.load_settings(latest)
        cl.get_timeline_feed()
        user_info = cl.account_info()
        username = user_info.username
        bot_state["cl"] = cl
        bot_state["username"] = username
        cl.dump_settings(latest)
        log(f"✅ Session recovered as @{username}")
        return True
    except Exception as e:
        log(f"❌ Auto-recovery failed: {str(e)[:80]}")
        log("⚠️ Please re-login")
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})
        socketio.emit("session_expired", {})
        return False


def handle_error(e, context="action"):
    if isinstance(e, LoginRequired):
        log(f"🔑 Session expired during {context}")
        return try_recover_session()
    elif isinstance(e, (PleaseWaitFewMinutes, ClientThrottledError)):
        log("⏳ Rate limited — waiting 5 minutes...")
        time.sleep(300)
        return True
    elif isinstance(e, ChallengeRequired):
        log("⚠️ Instagram challenge required — please verify your account manually")
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
        elif any(w in err for w in ["wait", "throttl", "rate", "few minutes"]):
            log("⏳ Rate limited — waiting 5 minutes...")
            time.sleep(300)
            return True
        elif "feedback_required" in err:
            log("⚠️ Action blocked — pausing 3 minutes...")
            time.sleep(180)
            return True
        log(f"⚠️ {context}: {str(e)[:100]}")
        return True


# ── Bot features ──────────────────────────────────────────────────────────────


def do_search_and_follow(target, max_followers, follow_limit, welcome_message=None):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    try:
        cl = bot_state["cl"]
        log(f"🔍 Looking up @{target}...")
        user_id = cl.user_id_from_username(target)
        log(f"👥 Fetching up to {max_followers} followers...")
        followers = cl.user_followers(user_id, amount=max_followers)
        log(f"👥 Got {len(followers)} followers")

        followed = 0
        skipped = 0
        for uid, user_info in followers.items():
            if not bot_state["running"]:
                log("⏸️ Bot stopped")
                break
            if bot_state["stats"]["followers_gained"] >= follow_limit:
                log(f"🎯 Follow limit reached ({follow_limit})")
                break

            uname = getattr(user_info, "username", str(uid))

            # Follow
            try:
                cl = bot_state["cl"]
                cl.user_follow(int(uid))
                bot_state["stats"]["followers_gained"] += 1
                followed += 1
                log(
                    f"✅ Followed @{uname} ({followed}/{max_followers}) | Total: {bot_state['stats']['followers_gained']}/{follow_limit}"
                )
                update_stats()
                time.sleep(random.uniform(8, 15))
            except Exception as e:
                skipped += 1
                if not handle_error(e, f"follow @{uname}"):
                    return
                continue

            # Welcome DM
            if welcome_message:
                try:
                    cl = bot_state["cl"]
                    dm_uid = cl.user_id_from_username(uname)
                    cl.direct_send(welcome_message, user_ids=[int(dm_uid)])
                    bot_state["stats"]["dms_sent"] += 1
                    log(f"💬 Welcome DM sent to @{uname}")
                    update_stats()
                    time.sleep(random.uniform(10, 20))
                except Exception as e:
                    if not handle_error(e, f"DM @{uname}"):
                        return

            if followed > 0 and followed % 5 == 0:
                pause = random.uniform(45, 90)
                log(f"😴 Anti-detection pause ({int(pause)}s)...")
                time.sleep(pause)

        bot_state["stats"]["accounts_processed"] += 1
        update_stats()
        log(f"✅ Done @{target}: {followed} followed, {skipped} skipped")
    except Exception as e:
        handle_error(e, f"search_and_follow @{target}")


def do_auto_unfollow(max_unfollows):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    if bot_state["running"]:
        log("🛑 Stopping main bot to start Auto Unfollow...")
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})
        time.sleep(2)
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Auto Unfollow"})
    try:
        cl = bot_state["cl"]
        log(f"🔄 Fetching up to {max_unfollows} accounts to unfollow...")
        following = cl.user_following(cl.user_id, amount=max_unfollows)
        log(f"📊 Fetched {len(following)} accounts")
        unfollowed = 0
        skipped = 0
        for uid, user_info in following.items():
            if not bot_state["feature_running"]:
                log("⏹️ Auto Unfollow stopped")
                break
            if unfollowed >= max_unfollows:
                break
            uname = getattr(user_info, "username", str(uid))
            try:
                cl = bot_state["cl"]
                cl.user_unfollow(int(uid))
                unfollowed += 1
                bot_state["stats"]["unfollowed"] += 1
                log(f"✅ Unfollowed @{uname} ({unfollowed}/{max_unfollows})")
                update_stats()
                time.sleep(random.uniform(5, 10))
                if unfollowed % 10 == 0:
                    pause = random.uniform(45, 90)
                    log(f"😴 Pause ({int(pause)}s)...")
                    time.sleep(pause)
            except Exception as e:
                skipped += 1
                if not handle_error(e, f"unfollow @{uname}"):
                    break
        log(f"✅ Auto Unfollow done: {unfollowed} unfollowed, {skipped} skipped")
    except Exception as e:
        handle_error(e, "auto_unfollow")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Auto Unfollow"})


def do_auto_like_feed(num_likes):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Auto Like Feed"})
    try:
        cl = bot_state["cl"]
        log(f"❤️ Auto-liking feed (max {num_likes})...")
        feed = cl.get_timeline_feed()
        liked = 0
        for item in feed:
            if not bot_state["feature_running"] or liked >= num_likes:
                break
            try:
                pk = getattr(item, "pk", None) or getattr(item, "id", None)
                if not pk:
                    continue
                cl.media_like(pk)
                liked += 1
                bot_state["stats"]["likes_given"] += 1
                log(f"❤️ Liked #{liked}/{num_likes}")
                update_stats()
                time.sleep(random.uniform(3, 7))
            except Exception as e:
                if not handle_error(e, "like feed"):
                    break
        log(f"✅ Liked {liked} posts")
    except Exception as e:
        handle_error(e, "auto_like_feed")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Auto Like Feed"})


def do_mass_story_view(max_stories):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Mass Story View"})
    try:
        cl = bot_state["cl"]
        log(f"👁️ Mass story view (max {max_stories})...")
        feed = cl.get_timeline_feed()
        viewed = 0
        for item in feed:
            if not bot_state["feature_running"] or viewed >= max_stories:
                break
            try:
                user_pk = getattr(getattr(item, "user", None), "pk", None)
                if not user_pk:
                    continue
                stories = cl.user_stories(user_pk)
                for story in stories:
                    if not bot_state["feature_running"] or viewed >= max_stories:
                        break
                    story_pk = getattr(story, "pk", None) or getattr(story, "id", None)
                    cl.story_seen([story_pk])
                    viewed += 1
                    bot_state["stats"]["stories_viewed"] += 1
                    log(f"👁️ Viewed story #{viewed}/{max_stories}")
                    update_stats()
                    time.sleep(random.uniform(1, 3))
            except Exception:
                continue
        log(f"✅ Viewed {viewed} stories")
    except Exception as e:
        handle_error(e, "mass_story_view")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Mass Story View"})


def do_auto_dm(target, message):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        cl = bot_state["cl"]
        log(f"💬 Sending DM to @{target}...")
        uid = cl.user_id_from_username(target)
        cl.direct_send(message, user_ids=[int(uid)])
        bot_state["stats"]["dms_sent"] += 1
        log(f"✅ DM sent to @{target}")
        update_stats()
    except Exception as e:
        handle_error(e, f"DM @{target}")
    finally:
        bot_state["feature_running"] = False


def do_auto_dm_following(message, limit):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Auto DM"})
    try:
        cl = bot_state["cl"]
        log(f"💬 Auto-DM to following list (max {limit})...")
        following = cl.user_following(cl.user_id, amount=limit)
        log(f"📊 Got {len(following)} accounts")
        sent = 0
        for uid, user_info in following.items():
            if not bot_state["feature_running"] or sent >= limit:
                break
            uname = getattr(user_info, "username", str(uid))
            try:
                cl = bot_state["cl"]
                cl.direct_send(message, user_ids=[int(uid)])
                bot_state["stats"]["dms_sent"] += 1
                sent += 1
                log(f"✅ DM sent to @{uname} ({sent}/{limit})")
                update_stats()
                time.sleep(random.uniform(8, 15))
                if sent % 10 == 0:
                    pause = random.uniform(60, 120)
                    log(f"😴 Pause ({int(pause)}s)...")
                    time.sleep(pause)
            except Exception as e:
                if not handle_error(e, f"DM @{uname}"):
                    break
        log(f"✅ Auto DM done: {sent} sent")
    except Exception as e:
        handle_error(e, "auto_dm_following")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Auto DM"})


def do_approve_requests():
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    try:
        cl = bot_state["cl"]
        log("✅ Approving follow requests...")
        pending = cl.user_pending_follow_requests()
        approved = 0
        for req in pending:
            uid = getattr(req, "pk", None) or getattr(req, "id", None)
            uname = getattr(req, "username", str(uid))
            try:
                cl.approve_pending_follow_request(uid)
                approved += 1
                log(f"✅ Approved @{uname}")
                time.sleep(random.uniform(1, 3))
            except Exception as e:
                log(f"⚠️ Skip @{uname}: {str(e)[:50]}")
        log(f"✅ Approved {approved} requests")
    except Exception as e:
        handle_error(e, "approve_requests")
    finally:
        bot_state["feature_running"] = False


def do_like_user_posts(target, num_likes):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Like User Posts"})
    try:
        cl = bot_state["cl"]
        log(f"❤️ Liking {num_likes} posts from @{target}...")
        user_id = cl.user_id_from_username(target)
        medias = cl.user_medias(user_id, amount=num_likes)
        liked = 0
        for media in medias:
            if not bot_state["feature_running"] or liked >= num_likes:
                break
            try:
                pk = getattr(media, "pk", None) or getattr(media, "id", None)
                cl.media_like(pk)
                liked += 1
                bot_state["stats"]["likes_given"] += 1
                log(f"❤️ Liked #{liked}/{num_likes} from @{target}")
                update_stats()
                time.sleep(random.uniform(3, 7))
            except Exception as e:
                if not handle_error(e, f"like post from @{target}"):
                    break
        log(f"✅ Liked {liked} posts from @{target}")
    except Exception as e:
        handle_error(e, f"like_user_posts @{target}")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Like User Posts"})


def do_auto_comment(target, comments_list, limit):
    if not bot_state["cl"]:
        log("❌ Not logged in!")
        return
    bot_state["feature_running"] = True
    socketio.emit("feature_status", {"running": True, "name": "Auto Comment"})
    try:
        cl = bot_state["cl"]
        log(f"💬 Auto-commenting on @{target} (max {limit})...")
        user_id = cl.user_id_from_username(target)
        medias = cl.user_medias(user_id, amount=limit)
        commented = 0
        for media in medias:
            if not bot_state["feature_running"] or commented >= limit:
                break
            try:
                pk = getattr(media, "pk", None) or getattr(media, "id", None)
                comment = random.choice(comments_list)
                cl.media_comment(pk, comment)
                commented += 1
                log(f"💬 Commented '{comment}' on post #{commented}/{limit}")
                update_stats()
                time.sleep(random.uniform(5, 12))
            except Exception as e:
                if not handle_error(e, f"comment on @{target}"):
                    break
        log(f"✅ Commented on {commented} posts from @{target}")
    except Exception as e:
        handle_error(e, f"auto_comment @{target}")
    finally:
        bot_state["feature_running"] = False
        socketio.emit("feature_status", {"running": False, "name": "Auto Comment"})


# ── Main bot loop ─────────────────────────────────────────────────────────────


def run_bot_loop(targets, follow_limit, followers_per_account, welcome_message=None):
    try:
        log("🚀 Starting bot loop...")
        if not bot_state["cl"]:
            log("❌ Not logged in!")
            bot_state["running"] = False
            socketio.emit("bot_status", {"running": False})
            return

        log(f"✅ Running as @{bot_state['username']}")
        target_accounts = [t.strip() for t in targets.split(",") if t.strip()]
        if not target_accounts:
            log("❌ No target accounts specified!")
            bot_state["running"] = False
            socketio.emit("bot_status", {"running": False})
            return

        log(f"🎯 Targets: {', '.join('@' + t for t in target_accounts)}")
        log(f"📊 Follow limit: {follow_limit} | Per account: {followers_per_account}")
        if welcome_message:
            log(f"💬 Welcome DM enabled")

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
                    break
                if bot_state["stats"]["followers_gained"] >= follow_limit:
                    break
                log(
                    f"📊 Progress: {bot_state['stats']['followers_gained']}/{follow_limit}"
                )
                do_search_and_follow(
                    target, followers_per_account, follow_limit, welcome_message
                )
                safe_delay(3, 8)

        log("🎉 Bot session completed!")
        log(f"📊 Final: {bot_state['stats']['followers_gained']} followers gained")
    except Exception as e:
        log(f"❌ Bot error: {str(e)}")
    finally:
        bot_state["running"] = False
        socketio.emit("bot_status", {"running": False})


# ── Flask routes ──────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


# ── Socket events ─────────────────────────────────────────────────────────────


@socketio.on("connect")
def on_connect():
    emit("bot_status", {"running": bot_state["running"]})
    emit("stats", bot_state["stats"])
    if not bot_state["cl"]:
        if load_saved_session():
            emit(
                "login_status",
                {"success": True, "username": bot_state.get("username", "")},
            )
        else:
            emit(
                "login_status",
                {"success": False, "error": "No saved session — please log in"},
            )


@socketio.on("login")
def on_login(data):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        emit("login_status", {"success": False, "error": "Enter username and password"})
        return

    def _login():
        try:
            cl = Client()
            cl.delay_range = [3, 7]
            bot_state["pending_cl"] = cl
            bot_state["pending_username"] = username
            bot_state["pending_password"] = password
            try:
                cl.login(username, password)
            except Exception as e:
                err = str(e).lower()
                if any(
                    w in err
                    for w in ["two", "factor", "challenge", "verification", "code"]
                ):
                    log(f"🔐 2FA required for @{username}")
                    socketio.emit("two_fa_required", {"required": True})
                    return
                raise
            os.makedirs("sessions", exist_ok=True)
            cl.dump_settings(f"sessions/{username}.json")
            bot_state["cl"] = cl
            bot_state["username"] = username
            log(f"✅ Logged in as @{username}")
            socketio.emit("login_status", {"success": True, "username": username})
        except Exception as e:
            log(f"❌ Login failed: {str(e)[:100]}")
            socketio.emit("login_status", {"success": False, "error": str(e)[:80]})

    threading.Thread(target=_login, daemon=True).start()


@socketio.on("two_fa_code")
def on_two_fa(data):
    code = data.get("code", "").strip()
    if not code:
        emit("login_status", {"success": False, "error": "Enter the 2FA code"})
        return

    def _submit():
        try:
            cl = bot_state.get("pending_cl")
            username = bot_state.get("pending_username")
            password = bot_state.get("pending_password")
            if not cl or not username:
                socketio.emit(
                    "login_status",
                    {"success": False, "error": "Session lost — please log in again"},
                )
                return
            cl.login(username, password, verification_code=code)
            os.makedirs("sessions", exist_ok=True)
            cl.dump_settings(f"sessions/{username}.json")
            bot_state["cl"] = cl
            bot_state["username"] = username
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
        emit("bot_status", {"running": True})
        return
    if not bot_state["cl"]:
        emit("login_status", {"success": False, "error": "Please log in first"})
        return
    bot_state["running"] = True
    bot_state["feature_running"] = False
    bot_state["stats"] = {
        "followers_gained": 0,
        "likes_given": 0,
        "unfollowed": 0,
        "stories_viewed": 0,
        "dms_sent": 0,
        "accounts_processed": 0,
        "start_time": time.time(),
    }
    dm_enabled = data.get("dm_enabled", False)
    welcome_msg = data.get("welcome_message", "").strip() if dm_enabled else ""
    threading.Thread(
        target=run_bot_loop,
        args=(
            data.get("targets", ""),
            int(data.get("follow_limit", 120)),
            int(data.get("followers_per_account", 10)),
            welcome_msg or None,
        ),
        daemon=True,
    ).start()
    emit("bot_status", {"running": True})


@socketio.on("stop_bot")
def on_stop():
    bot_state["running"] = False
    emit("bot_status", {"running": False})
    log("🛑 Bot stopped")


@socketio.on("stop_feature")
def on_stop_feature():
    bot_state["feature_running"] = False
    log("🛑 Feature stopped")


@socketio.on("auto_unfollow")
def on_unfollow(data):
    if not bot_state["cl"]:
        emit("login_status", {"success": False, "error": "Please log in first"})
        return
    if bot_state["feature_running"]:
        bot_state["feature_running"] = False
        time.sleep(1)
    limit = int(data.get("limit", 50))
    threading.Thread(target=do_auto_unfollow, args=(limit,), daemon=True).start()


@socketio.on("auto_like_feed")
def on_like_feed(data):
    if not bot_state["cl"]:
        return
    threading.Thread(
        target=do_auto_like_feed, args=(int(data.get("limit", 20)),), daemon=True
    ).start()


@socketio.on("mass_story_view")
def on_story(data):
    if not bot_state["cl"]:
        return
    threading.Thread(
        target=do_mass_story_view, args=(int(data.get("limit", 50)),), daemon=True
    ).start()


@socketio.on("auto_dm")
def on_dm(data):
    if not bot_state["cl"]:
        return
    msg = data.get("message", "") or "Hey! Thanks for following 🙏"
    target = data.get("target", "").strip()
    limit = int(data.get("limit", 20))
    if target:
        threading.Thread(target=do_auto_dm, args=(target, msg), daemon=True).start()
    else:
        threading.Thread(
            target=do_auto_dm_following, args=(msg, limit), daemon=True
        ).start()


@socketio.on("approve_requests")
def on_approve():
    if not bot_state["cl"]:
        return
    threading.Thread(target=do_approve_requests, daemon=True).start()


@socketio.on("like_user_posts")
def on_like_user(data):
    if not bot_state["cl"]:
        return
    target = data.get("target", "").strip()
    if not target:
        log("❌ Enter a username in Like Target")
        return
    threading.Thread(
        target=do_like_user_posts, args=(target, int(data.get("limit", 5))), daemon=True
    ).start()


@socketio.on("auto_comment")
def on_comment(data):
    if not bot_state["cl"]:
        return
    target = data.get("target", "").strip()
    if not target:
        log("❌ Enter a target username for Auto Comment")
        return
    raw = data.get("comments", "Great post!, 🔥, Amazing!")
    comments_list = [c.strip() for c in raw.split(",") if c.strip()] or ["🔥"]
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
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    socketio.run(
        app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True
    )
