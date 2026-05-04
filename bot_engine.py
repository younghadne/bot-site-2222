import os
import sys
import time
import json
import random
import hashlib
import shutil
import threading
import subprocess
import traceback
from datetime import datetime
from threading import Lock

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self.lock = Lock()

    def add_proxy(self, proxy_url):
        with self.lock:
            if proxy_url not in self.proxies:
                self.proxies.append(proxy_url)
                return True
        return False

    def get_proxy(self, thread_id=None):
        with self.lock:
            if not self.proxies:
                return None
            if thread_id is not None:
                return self.proxies[thread_id % len(self.proxies)]
            proxy = self.proxies[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.proxies)
            return proxy

    def remove_proxy(self, proxy_url):
        with self.lock:
            if proxy_url in self.proxies:
                self.proxies.remove(proxy_url)
                return True
        return False

    def clear_proxies(self):
        with self.lock:
            self.proxies = []
            self.current_index = 0

    def get_all_proxies(self):
        with self.lock:
            return self.proxies.copy()


class LogEntry:
    def __init__(self, message):
        self.timestamp = datetime.now().strftime("%H:%M:%S")
        self.message = message

    def to_dict(self):
        return {"timestamp": self.timestamp, "message": self.message}


class BotEngine:
    def __init__(self):
        self.is_running = False
        self.total_plays = 0
        self.target_plays = 1000000
        self.active_threads = 0
        self.thread_count = 5
        self.start_time = None
        self.plays_per_hour = 0
        self.lock = Lock()

        self.track_url = "https://open.spotify.com/track/73cD9EewduEMctfNOkE5S5"
        self.browser = "Chrome"
        self.headless = True
        self.mobile_mode = True
        self.low_cpu = True

        self.proxy_manager = ProxyManager()
        self.proxy_preset_enabled = False
        self.proxy_preset_url = "https://4vHGHxTtrXsALKk_8eI-8g@smartproxy.crawlbase.com:8013"

        self.profile_base_dir = os.path.join(os.path.expanduser("~"), ".spotify_bot_profiles")
        self.cookies_dir = os.path.join(os.path.expanduser("~"), ".spotify_bot_cookies")
        os.makedirs(self.profile_base_dir, exist_ok=True)
        os.makedirs(self.cookies_dir, exist_ok=True)
        self.active_profiles = set()
        self.cookie_files = []
        self.used_cookies = set()
        self.cookie_lock = Lock()

        self.threads = []
        self.drivers = []

        self.logs = []
        self.log_lock = Lock()

        self.user_agents = [
            "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; IN2023) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; Pixel 6 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; SM-F936B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        ]
        self.screen_resolutions = [(412, 915), (393, 851), (430, 932), (360, 800), (375, 812)]
        self.languages = ["en-US,en", "en-GB,en", "fr-FR,fr", "de-DE,de", "es-ES,es"]

        self.driver_status = "Checking..."
        self._check_drivers()
        self._check_cookies()
        self._load_proxies()

    def log(self, message):
        entry = LogEntry(message)
        with self.log_lock:
            self.logs.append(entry)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
        print(f"[{entry.timestamp}] {message}")

    def get_state(self):
        with self.lock:
            if self.start_time and self.is_running:
                elapsed = time.time() - self.start_time
                if elapsed > 0:
                    self.plays_per_hour = round(self.total_plays / (elapsed / 3600))
        with self.log_lock:
            logs = [entry.to_dict() for entry in self.logs[-200:]]
        return {
            "isRunning": self.is_running,
            "totalPlays": self.total_plays,
            "targetPlays": self.target_plays,
            "activeThreads": self.active_threads,
            "threadCount": self.thread_count,
            "startTime": self.start_time,
            "trackUrl": self.track_url,
            "browser": self.browser,
            "headless": self.headless,
            "mobileMode": self.mobile_mode,
            "lowCpu": self.low_cpu,
            "proxyPresetEnabled": self.proxy_preset_enabled,
            "proxies": self.proxy_manager.get_all_proxies(),
            "cookieCount": len(self.cookie_files),
            "driverStatus": self.driver_status,
            "logs": logs,
            "playsPerHour": self.plays_per_hour,
        }

    def update_settings(self, settings):
        if "targetPlays" in settings:
            self.target_plays = int(settings["targetPlays"])
        if "threadCount" in settings:
            self.thread_count = int(settings["threadCount"])
        if "trackUrl" in settings:
            self.track_url = settings["trackUrl"]
        if "browser" in settings:
            self.browser = settings["browser"]
        if "headless" in settings:
            self.headless = bool(settings["headless"])
        if "mobileMode" in settings:
            self.mobile_mode = bool(settings["mobileMode"])
        if "lowCpu" in settings:
            self.low_cpu = bool(settings["lowCpu"])
        if "proxyPresetEnabled" in settings:
            self.proxy_preset_enabled = bool(settings["proxyPresetEnabled"])

    def generate_stealth_profile(self, thread_id):
        random.seed(thread_id * 42)
        return {
            "user_agent": random.choice(self.user_agents),
            "screen_resolution": random.choice(self.screen_resolutions),
            "language": random.choice(self.languages),
            "platform": random.choice(["Win32", "MacIntel", "Linux x86_64"]),
            "thread_id": thread_id,
        }

    def create_unique_profile_dir(self, thread_id):
        ts = int(time.time())
        d = os.path.join(self.profile_base_dir, f"profile_{thread_id}_{ts}")
        os.makedirs(d, exist_ok=True)
        self.active_profiles.add(d)
        return d

    def cleanup_profile(self, profile_dir):
        try:
            self.active_profiles.discard(profile_dir)
        except Exception:
            pass

    def clear_all_profiles(self):
        try:
            if os.path.exists(self.profile_base_dir):
                shutil.rmtree(self.profile_base_dir)
                os.makedirs(self.profile_base_dir, exist_ok=True)
                self.log("Cleared all browser profiles")
        except Exception as e:
            self.log(f"Error clearing profiles: {e}")

    def _check_drivers(self):
        available = []
        try:
            r = subprocess.run(["chromedriver", "--version"], capture_output=True, text=True, timeout=5)
            if "ChromeDriver" in r.stdout:
                available.append("Chrome")
        except Exception:
            pass
        try:
            r = subprocess.run(["geckodriver", "--version"], capture_output=True, text=True, timeout=5)
            if "geckodriver" in (r.stdout + r.stderr):
                available.append("Firefox")
        except Exception:
            pass
        self.driver_status = (", ".join(available) + " ready") if available else "No drivers found"
        self.log(f"Driver status: {self.driver_status}")

    def _check_cookies(self):
        try:
            os.makedirs(self.cookies_dir, exist_ok=True)
            self.cookie_files = [
                f for f in os.listdir(self.cookies_dir)
                if f.endswith((".pkl", ".json", ".txt"))
            ]
            if self.cookie_files:
                self.log(f"Found {len(self.cookie_files)} cookie file(s)")
            else:
                self.log("No cookie files found. Import cookies to get started.")
        except Exception as e:
            self.log(f"Error checking cookies: {e}")

    def import_cookie_data(self, filename, data):
        try:
            dest = os.path.join(self.cookies_dir, filename)
            counter = 1
            base, ext = os.path.splitext(filename)
            while os.path.exists(dest):
                dest = os.path.join(self.cookies_dir, f"{base}_{counter}{ext}")
                counter += 1
            with open(dest, "wb") as f:
                f.write(data)
            self._check_cookies()
            self.log(f"Imported cookie: {os.path.basename(dest)}")
            return True
        except Exception as e:
            self.log(f"Error importing cookie: {e}")
            return False

    def _load_proxies(self):
        try:
            pf = os.path.join(os.path.expanduser("~"), ".spotify_bot_proxies.json")
            if os.path.exists(pf):
                with open(pf, "r") as f:
                    for p in json.load(f):
                        self.proxy_manager.add_proxy(p)
        except Exception:
            pass

    def _save_proxies(self):
        try:
            pf = os.path.join(os.path.expanduser("~"), ".spotify_bot_proxies.json")
            with open(pf, "w") as f:
                json.dump(self.proxy_manager.get_all_proxies(), f)
        except Exception:
            pass

    def add_proxy(self, proxy_url):
        if self.proxy_manager.add_proxy(proxy_url):
            self.log(f"Added proxy: {proxy_url}")
            self._save_proxies()
            return True
        return False

    def remove_proxy_by_index(self, idx):
        proxies = self.proxy_manager.get_all_proxies()
        if 0 <= idx < len(proxies):
            self.proxy_manager.remove_proxy(proxies[idx])
            self._save_proxies()

    def clear_proxies(self):
        self.proxy_manager.clear_proxies()
        self._save_proxies()
        self.log("Cleared all proxies")

    def test_proxy(self):
        self.log("Testing proxy...")
        try:
            import requests as req
            if self.proxy_preset_enabled:
                cmd = ["curl", "-k", "--proxy", self.proxy_preset_url, "https://api.ipify.org?format=json"]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if r.returncode == 0:
                    ip = json.loads(r.stdout).get("ip", "Unknown")
                    self.log(f"SmartProxy test OK! IP: {ip}")
                    return True
                self.log(f"SmartProxy test failed: {r.stderr}")
                return False
            proxies_list = self.proxy_manager.get_all_proxies()
            if not proxies_list:
                self.log("No proxies to test")
                return False
            p = proxies_list[0]
            r = req.get("https://api.ipify.org?format=json", proxies={"http": p, "https": p}, timeout=10, verify=False)
            if r.status_code == 200:
                self.log(f"Proxy test OK! IP: {r.json().get('ip', 'Unknown')}")
                return True
        except Exception as e:
            self.log(f"Proxy test failed: {e}")
        return False

    def setup_stealth_chrome(self, profile, proxy=None):
        opts = ChromeOptions()
        profile_dir = self.create_unique_profile_dir(profile["thread_id"])

        if not self.proxy_preset_enabled and proxy:
            opts.add_argument(f"--proxy-server={proxy}")

        if self.headless:
            opts.add_argument("--headless=new")

        if self.mobile_mode:
            mobile_emulation = {
                "deviceMetrics": {
                    "width": profile["screen_resolution"][0],
                    "height": profile["screen_resolution"][1],
                    "pixelRatio": 3.0,
                    "touch": True,
                },
                "userAgent": profile["user_agent"],
            }
            opts.add_experimental_option("mobileEmulation", mobile_emulation)
            opts.add_argument("--enable-touchview")
            opts.add_argument("--enable-viewport")
            opts.add_argument("--disable-desktop-notifications")

        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(f"--window-size={profile['screen_resolution'][0]},{profile['screen_resolution'][1]}")
        opts.add_argument(f"--lang={profile['language'].split(',')[0]}")
        opts.add_argument(f"--user-data-dir={profile_dir}")
        opts.add_argument("--profile-directory=Default")

        prefs = {
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.geolocation": 2,
            "profile.managed_default_content_settings.images": 1,
            "profile.managed_default_content_settings.cookies": 1,
            "profile.managed_default_content_settings.javascript": 1,
            "profile.managed_default_content_settings.popups": 0,
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        }
        opts.add_experimental_option("prefs", prefs)
        opts.add_argument("--disable-web-security")
        opts.add_argument("--allow-running-insecure-content")

        if self.low_cpu:
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-images")
            opts.add_argument("--mute-audio")
        else:
            opts.add_argument("--autoplay-policy=no-user-gesture-required")

        try:
            driver = webdriver.Chrome(service=ChromeService(), options=opts)
        except Exception:
            driver = webdriver.Chrome(options=opts)

        driver.set_window_size(profile["screen_resolution"][0], profile["screen_resolution"][1])
        try:
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception:
            pass
        return driver

    def setup_stealth_firefox(self, profile):
        opts = FirefoxOptions()
        profile_dir = self.create_unique_profile_dir(profile["thread_id"])
        if self.headless:
            opts.add_argument("--headless")

        from selenium.webdriver.firefox.firefox_profile import FirefoxProfile
        fp = FirefoxProfile(profile_dir)
        fp.set_preference("media.autoplay.default", 0)
        fp.set_preference("media.autoplay.blocking_policy", 0)
        fp.set_preference("media.autoplay.enabled.user-gestures-needed", False)
        fp.set_preference("media.block-autoplay-until-in-foreground", False)
        fp.set_preference("media.eme.enabled", True)
        fp.set_preference("network.cookie.sameSite.noneRequiresSecure", False)
        fp.set_preference("dom.webdriver.enabled", False)
        fp.set_preference("useAutomationExtension", False)
        fp.set_preference("general.useragent.override", profile["user_agent"])
        fp.set_preference("intl.accept_languages", profile["language"])
        if self.low_cpu:
            fp.set_preference("permissions.default.image", 2)
            fp.set_preference("media.volume_scale", "0.0")
        else:
            fp.set_preference("media.volume_scale", "1.0")
        fp.update_preferences()

        try:
            driver = webdriver.Firefox(service=FirefoxService(), options=opts, firefox_profile=fp)
        except Exception:
            driver = webdriver.Firefox(options=opts, firefox_profile=fp)

        driver.set_window_size(profile["screen_resolution"][0], profile["screen_resolution"][1])
        return driver

    def setup_browser(self, browser_type, thread_id):
        profile = self.generate_stealth_profile(thread_id)
        proxy = self.proxy_manager.get_proxy(thread_id) if not self.proxy_preset_enabled else None
        if browser_type == "Chrome":
            return self.setup_stealth_chrome(profile, proxy)
        elif browser_type == "Firefox":
            return self.setup_stealth_firefox(profile)
        raise ValueError(f"Unsupported browser: {browser_type}")

    def check_if_playing(self, driver, thread_id):
        try:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        "button[data-testid='control-button-playpause'], "
                        "button[data-testid='play-button']"))
                )
                html = (btn.get_attribute("innerHTML") or "").lower()
                aria = btn.get_attribute("aria-label") or ""
                if "pause" in html or "Pause" in aria:
                    return True
            except Exception:
                pass
            try:
                prog = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        "[data-testid='playback-progressbar'], .playback-bar__progress"))
                )
                p1 = prog.rect["x"]
                time.sleep(1)
                p2 = prog.rect["x"]
                if p2 > p1 + 1:
                    return True
            except Exception:
                pass
            try:
                np_widget = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR,
                        "[data-testid='now-playing-widget']"))
                )
                if np_widget.is_displayed():
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    def ensure_playback(self, driver, thread_id):
        try:
            time.sleep(2)
            if self.check_if_playing(driver, thread_id):
                self.log(f"Thread {thread_id}: Playback already active")
                return True
            try:
                btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR,
                        "button[data-testid='control-button-playpause'], "
                        "button[data-testid='play-button']"))
                )
                btn.click()
                self.log(f"Thread {thread_id}: Clicked play button")
                time.sleep(2)
                return self.check_if_playing(driver, thread_id)
            except Exception as e:
                self.log(f"Thread {thread_id}: Could not click play: {e}")
            return False
        except Exception as e:
            self.log(f"Thread {thread_id}: ensure_playback error: {e}")
            return False

    def refresh_and_play(self, driver, thread_id, track_url):
        try:
            driver.get(track_url)
            time.sleep(3)
            for selector in ["button[data-testid='web-browser-popup']", "button[data-testid='web-browser-popup-cancel']"]:
                try:
                    elems = WebDriverWait(driver, 3).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector)))
                    if elems and elems[0].is_displayed():
                        elems[0].click()
                        time.sleep(1)
                        break
                except Exception:
                    pass
            success = self.ensure_playback(driver, thread_id)
            return driver, success
        except Exception as e:
            self.log(f"Thread {thread_id}: refresh_and_play error: {e}")
            return driver, False

    def stealth_worker(self, thread_id):
        driver = None
        cookie_file = None
        profile_dir = None
        try:
            with self.lock:
                self.active_threads += 1

            profile_data = self.generate_stealth_profile(thread_id)
            self.log(f"Thread {thread_id}: Starting stealth mode ({profile_data['platform']})")

            try:
                driver = self.setup_browser(self.browser, thread_id)
            except Exception as e:
                self.log(f"Thread {thread_id}: Setup failed - {e}")
                return

            with self.cookie_lock:
                available = [f for f in self.cookie_files if f not in self.used_cookies]
                if available:
                    cookie_file = available[0]
                    self.used_cookies.add(cookie_file)
                elif self.cookie_files:
                    cookie_file = self.cookie_files[0]

                if cookie_file:
                    cookie_path = os.path.join(self.cookies_dir, cookie_file)
                    if os.path.exists(cookie_path):
                        try:
                            with open(cookie_path, "r") as f:
                                cookies = json.load(f)
                            driver.get("https://open.spotify.com")
                            time.sleep(2)
                            driver.delete_all_cookies()
                            for c in cookies:
                                try:
                                    if "expiry" in c:
                                        del c["expiry"]
                                    if c.get("sameSite") == "None" and not c.get("secure"):
                                        c["sameSite"] = "Lax"
                                    driver.add_cookie(c)
                                except Exception:
                                    pass
                            self.log(f"Thread {thread_id}: Loaded cookies: {cookie_file}")
                        except Exception as e:
                            self.log(f"Thread {thread_id}: Error loading cookies: {e}")

            track_url = self.track_url
            if not track_url or not track_url.startswith("https://open.spotify.com/"):
                track_url = "https://open.spotify.com/track/73cD9EewduEMctfNOkE5S5"

            while self.is_running and self.total_plays < self.target_plays:
                try:
                    self.log(f"Thread {thread_id}: Loading track...")
                    driver, success = self.refresh_and_play(driver, thread_id, track_url)
                    if not success:
                        self.log(f"Thread {thread_id}: Playback failed, retrying...")
                        time.sleep(2)
                        driver, success = self.refresh_and_play(driver, thread_id, track_url)
                        if not success:
                            time.sleep(5)
                            continue

                    stream_time = random.randint(30, 45)
                    self.log(f"Thread {thread_id}: Streaming for {stream_time}s...")

                    t0 = time.time()
                    while (time.time() - t0) < stream_time and self.is_running:
                        time.sleep(5)
                        if not self.check_if_playing(driver, thread_id):
                            self.log(f"Thread {thread_id}: Playback stopped, restarting...")
                            if not self.ensure_playback(driver, thread_id):
                                break

                    if (time.time() - t0) >= 30:
                        with self.lock:
                            self.total_plays += 1
                        self.log(f"Thread {thread_id}: Play registered. Total: {self.total_plays}")

                    time.sleep(random.uniform(1, 3))

                except Exception as e:
                    self.log(f"Thread {thread_id}: Error in loop: {e}")
                    time.sleep(5)
                    try:
                        driver.refresh()
                        time.sleep(3)
                    except Exception:
                        pass

        except Exception as e:
            self.log(f"Thread {thread_id}: Fatal error: {e}")
            self.log(traceback.format_exc())
        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            with self.lock:
                self.active_threads -= 1
            if cookie_file and cookie_file in self.used_cookies:
                with self.cookie_lock:
                    self.used_cookies.discard(cookie_file)
            self.log(f"Thread {thread_id}: Stopped. Active: {self.active_threads}")

    def start_bot(self):
        if self.is_running:
            self.stop_bot()

        self.threads = []
        self.is_running = True
        self.start_time = time.time()
        self.total_plays = 0
        self.active_threads = 0

        self.log(f"Starting bot with {self.thread_count} threads")
        self.log(f"Target: {self.target_plays:,} plays")
        self.log(f"Browser: {self.browser} | Mobile: {self.mobile_mode} | Headless: {self.headless}")

        for i in range(1, self.thread_count + 1):
            t = threading.Thread(target=self.stealth_worker, args=(i,), daemon=True)
            self.threads.append(t)
            t.start()
            time.sleep(random.uniform(1, 3))

        monitor = threading.Thread(target=self._monitor_threads, daemon=True)
        monitor.start()

    def _monitor_threads(self):
        while self.is_running:
            self.threads = [t for t in self.threads if t.is_alive()]
            if not self.threads and self.active_threads <= 0:
                self.log("All worker threads have stopped")
                self.is_running = False
                break
            time.sleep(5)

    def stop_bot(self):
        if not self.is_running:
            return
        self.log("Stopping all threads...")
        self.is_running = False
        for t in self.threads:
            try:
                if t.is_alive():
                    t.join(timeout=5)
            except Exception:
                pass
        self.threads = []
        with self.cookie_lock:
            self.used_cookies.clear()
        self.log("Bot stopped")

    def reset_plays(self):
        with self.lock:
            self.total_plays = 0
            self.plays_per_hour = 0
            self.start_time = None
        self.log("Play counter reset")
