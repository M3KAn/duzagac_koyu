import os
import sys
import uuid
import sqlite3
import time
import json
import shutil
import threading
import webbrowser
from datetime import datetime
from urllib.parse import quote, urlencode
from urllib.request import urlopen, Request

from flask import Flask, render_template_string, request, redirect, session, abort


# ---------------------------
# EXE uyumluluk: dosya yolları
# ---------------------------
def is_frozen():
    return getattr(sys, "frozen", False)

def exe_dir():
    # EXE'nin bulunduğu klasör
    return os.path.dirname(sys.executable) if is_frozen() else os.path.dirname(os.path.abspath(__file__))

def bundle_dir():
    # PyInstaller onefile/onedir iç kaynak dizini
    return getattr(sys, "_MEIPASS", exe_dir())

BASE_DIR = exe_dir()
BUNDLE_DIR = bundle_dir()

# Kullanıcının sonradan foto/video atacağı gerçek klasörler (EXE yanında)
STATIC_DIR = os.path.join(BASE_DIR, "static")
VIDEOS_DIR = os.path.join(STATIC_DIR, "videolar")
PHOTOS_DIR = os.path.join(STATIC_DIR, "fotograflar")

# TXT / DB dosyaları EXE yanında (düzenlemesi kolay)
ANNOUNCE_FILE = os.path.join(BASE_DIR, "duyurular.txt")
CONTACT_FILE = os.path.join(BASE_DIR, "iletisim.txt")
ADMIN_KEY_FILE = os.path.join(BASE_DIR, ".admin_key")
DB_PATH = os.path.join(BASE_DIR, "data.db")

# Paket içinden (ilk çalıştırmada kopyalamak için) static kaynağı
BUNDLE_STATIC = os.path.join(BUNDLE_DIR, "static")
BUNDLE_BG = os.path.join(BUNDLE_STATIC, "arkaplan.jpg")


# ---------------------------
# Ayarlar
# ---------------------------
ADMIN_LOGIN_PATH = "/yonetici"   # gizli admin giriş
PANEL_PATH = "/panel"            # sadece admin görür

SOCIALS = {
    "facebook": "https://www.facebook.com/DuzagacKoyuKozan/?locale=tr_TR",
    "instagram": "https://www.instagram.com/duzagacky/",
    "whatsapp": "https://chat.whatsapp.com/J9tfpgXd3iu8HM1FBxC2U7",
}

# Hava durumu koordinatları (ekranda görünmez)
WEATHER_LAT = 37.579171
WEATHER_LON = 35.820547


# ---------------------------
# Flask app (static klasörü EXE yanında)
# ---------------------------
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


def ensure_dirs_and_files():
    # static klasörleri
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)

    # arkaplan.jpg yoksa bundle'dan kopyala
    bg_target = os.path.join(STATIC_DIR, "arkaplan.jpg")
    if not os.path.exists(bg_target):
        # eğer bundle içinde varsa kopyala
        if os.path.exists(BUNDLE_BG):
            os.makedirs(STATIC_DIR, exist_ok=True)
            shutil.copy2(BUNDLE_BG, bg_target)

    # duyurular.txt yoksa oluştur
    if not os.path.exists(ANNOUNCE_FILE):
        with open(ANNOUNCE_FILE, "w", encoding="utf-8") as f:
            f.write("")  # boş

    # iletisim.txt yoksa oluştur
    if not os.path.exists(CONTACT_FILE):
        with open(CONTACT_FILE, "w", encoding="utf-8") as f:
            f.write("Muhtar: \nTelefon: \nAdres: Düzağaç Köyü / Kozan\n")

    # .admin_key yoksa örnek oluştur (sen sonra değiştir)
    if not os.path.exists(ADMIN_KEY_FILE):
        with open(ADMIN_KEY_FILE, "w", encoding="utf-8") as f:
            f.write("Duzagac123!\n")

ensure_dirs_and_files()


def read_admin_key() -> str | None:
    try:
        with open(ADMIN_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
            return key if key else None
    except Exception:
        return None

_admin_key = read_admin_key()
app.secret_key = ("duzagac_koyu_secret_" + (_admin_key or "no_admin_key")).encode("utf-8")


# ---------------------------
# DB
# ---------------------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            post_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            name_full TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (post_id, device_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            name_full TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()

init_db()


# ---------------------------
# Yardımcılar
# ---------------------------
def get_device_id():
    device_id = request.cookies.get("dz_device")
    if not device_id:
        device_id = uuid.uuid4().hex
    return device_id

def safe_filename(name: str) -> str:
    return os.path.basename(name)

def list_media(folder: str, exts: tuple[str, ...]):
    # Windows'ta kopyalama/oluşturma zamanı (en yeni üste)
    items = []
    if not os.path.isdir(folder):
        return items
    for fn in os.listdir(folder):
        p = os.path.join(folder, fn)
        if os.path.isfile(p) and fn.lower().endswith(exts):
            items.append({"filename": fn, "ts": os.path.getctime(p)})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items

def fmt_date_ddmmyy(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m.%y")
    except Exception:
        return ""

def first_name(full: str) -> str:
    full = (full or "").strip()
    return full.split()[0] if full else ""

def like_count(post_id: str) -> int:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM likes WHERE post_id=?", (post_id,))
    row = cur.fetchone()
    con.close()
    return int(row["c"] if row else 0)

def has_liked(post_id: str, device_id: str) -> bool:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM likes WHERE post_id=? AND device_id=? LIMIT 1", (post_id, device_id))
    row = cur.fetchone()
    con.close()
    return row is not None

def add_like(post_id: str, device_id: str, name_full: str) -> bool:
    con = db()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO likes (post_id, device_id, name_full, created_at) VALUES (?,?,?,?)",
            (post_id, device_id, name_full, datetime.now().isoformat(timespec="seconds"))
        )
        con.commit()
        con.close()
        return True
    except sqlite3.IntegrityError:
        con.close()
        return False

def comments_for(post_id: str):
    con = db()
    cur = con.cursor()
    cur.execute(
        "SELECT id, name_full, comment, created_at FROM comments WHERE post_id=? ORDER BY id DESC",
        (post_id,)
    )
    rows = cur.fetchall()
    con.close()
    return rows

def add_comment(post_id: str, device_id: str, name_full: str, comment: str):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO comments (post_id, device_id, name_full, comment, created_at) VALUES (?,?,?,?,?)",
        (post_id, device_id, name_full, comment, datetime.now().isoformat(timespec="seconds"))
    )
    con.commit()
    con.close()

def delete_comment(comment_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    con.commit()
    con.close()

def is_admin() -> bool:
    return bool(session.get("is_admin"))

def require_admin_or_404():
    if not is_admin():
        abort(404)

def read_lines(path: str):
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                t = line.strip()
                if t:
                    lines.append(t)
    return lines

def append_line(path: str, text: str):
    # yeni duyuru ekleyince eskisi silinmesin: dosyaya satır olarak ekle
    text = (text or "").strip()
    if not text:
        return
    with open(path, "a", encoding="utf-8") as f:
        # satır sonu garanti
        f.write(text.replace("\n", " ").strip() + "\n")

def write_lines(path: str, lines: list[str]):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + ("\n" if lines else ""))


# -------------------------
# HAVA DURUMU (Open-Meteo) + cache
# -------------------------
_weather_cache = {"ts": 0.0, "data": None}

def weather_icon_and_label(weather_code: int, wind_m_s: float):
    # Rüzgar varsa rüzgar simgesi
    if wind_m_s is not None and wind_m_s >= 9.0:
        return "🌬️", "Rüzgarlı"

    if weather_code == 0:
        return "☀️", "Güneşli"
    if weather_code in (1, 2):
        return "⛅", "Parçalı"
    if weather_code == 3:
        return "☁️", "Bulutlu"
    if weather_code in (45, 48):
        return "🌫️", "Sisli"
    if 51 <= weather_code <= 57:
        return "🌦️", "Çise"
    if 61 <= weather_code <= 67:
        return "🌧️", "Yağmurlu"
    if 71 <= weather_code <= 77:
        return "❄️", "Karlı"
    if 80 <= weather_code <= 82:
        return "🌧️", "Sağanak"
    if 95 <= weather_code <= 99:
        return "⛈️", "Fırtına"
    return "☁️", "Hava"

def get_weather():
    now = time.time()
    if _weather_cache["data"] is not None and (now - _weather_cache["ts"] < 600):
        return _weather_cache["data"]

    try:
        params = {
            "latitude": WEATHER_LAT,
            "longitude": WEATHER_LON,
            "current_weather": "true",
            "timezone": "auto",
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
        req = Request(url, headers={"User-Agent": "DuzagacKoyuApp/1.0"})
        with urlopen(req, timeout=6) as r:
            raw = r.read().decode("utf-8")
        data = json.loads(raw)
        cw = data.get("current_weather") or {}
        temp = cw.get("temperature")
        code = int(cw.get("weathercode", 3))
        wind = cw.get("windspeed")  # km/h
        wind_m_s = (wind / 3.6) if isinstance(wind, (int, float)) else 0.0

        icon, label = weather_icon_and_label(code, wind_m_s)
        out = {"ok": True, "temp": temp, "icon": icon, "label": label}
        _weather_cache["ts"] = now
        _weather_cache["data"] = out
        return out
    except Exception:
        out = {"ok": False, "temp": None, "icon": "☁️", "label": "Hava"}
        _weather_cache["ts"] = now
        _weather_cache["data"] = out
        return out


# -------------------------
# UI Template
# -------------------------
BASE = """
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DÜZAĞAÇ KÖYÜ</title>
  <style>
    :root{
      --top-h: 56px;
      --drawer-w: 280px;
      --border: rgba(255,255,255,.14);
      --glass: rgba(16,16,16,.78);
      --glass2: rgba(12,12,12,.62);
      --txt: #fff;
      --muted: rgba(255,255,255,.75);
      --active: rgba(255,255,255,.18);
      --btn: rgba(255,255,255,.10);
      --btn2: rgba(255,255,255,.16);
      --danger: rgba(255,80,80,.14);
      --dangerB: rgba(255,80,80,.35);
    }
    *{box-sizing:border-box}
    body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#0b0b0b;color:var(--txt);min-height:100vh;}

    .topbar{
      position:sticky;top:0;height:var(--top-h);
      display:flex;align-items:center;gap:12px;padding:0 12px;
      background:rgba(0,0,0,.82);backdrop-filter:blur(10px);
      border-bottom:1px solid var(--border);z-index:30;
    }
    .burger{
      width:44px;height:40px;border-radius:12px;border:1px solid var(--border);
      background:rgba(255,255,255,.06);color:var(--txt);font-size:22px;
      cursor:pointer;display:grid;place-items:center;user-select:none;
    }
    .brand{font-weight:900;letter-spacing:1px;font-size:15px;user-select:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}

    .topRight{margin-left:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
    .sLink{
      display:inline-flex;align-items:center;gap:8px;
      padding:8px 10px;border-radius:999px;
      border:1px solid rgba(255,255,255,.16);
      background:rgba(255,255,255,.06);
      color:var(--txt);text-decoration:none;font-weight:900;
      font-size:13px;white-space:nowrap;
    }
    .sLink:hover{background:rgba(255,255,255,.12);}
    .sIcon{opacity:.95}

    .overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);opacity:0;pointer-events:none;transition:opacity .18s ease;z-index:40;}
    .drawer{
      position:fixed;top:0;left:0;width:var(--drawer-w);height:100vh;
      transform:translateX(-105%);transition:transform .22s ease;z-index:50;
      background:var(--glass);backdrop-filter:blur(14px);
      border-right:1px solid var(--border);padding:14px;
    }
    .drawer.open{transform:translateX(0);}
    .overlay.open{opacity:1;pointer-events:auto;}
    .drawerHeader{display:flex;align-items:center;justify-content:space-between;padding:6px 4px 14px;border-bottom:1px solid var(--border);margin-bottom:10px;}
    .drawerTitle{font-weight:900;letter-spacing:.8px;font-size:14px;}
    .closeBtn{width:40px;height:36px;border-radius:12px;border:1px solid var(--border);background:rgba(255,255,255,.06);color:var(--txt);
      cursor:pointer;display:grid;place-items:center;font-size:18px;}
    .nav{display:flex;flex-direction:column;gap:8px;margin-top:10px;}
    .nav a{text-decoration:none;color:var(--txt);padding:12px;border-radius:14px;background:rgba(255,255,255,.04);
      display:flex;align-items:center;gap:10px;font-weight:700;border:1px solid transparent;}
    .nav a span{color:var(--muted);font-weight:800;}
    .nav a.active{background:var(--active);border-color:rgba(255,255,255,.22);}

    .page{
      min-height:calc(100vh - var(--top-h));padding:14px 14px 28px;
      background:linear-gradient(rgba(0,0,0,.62),rgba(0,0,0,.62)),url("/static/arkaplan.jpg");
      background-size:cover;background-position:center;background-repeat:no-repeat;
    }
    .container{max-width:760px;margin:0 auto;position:relative;}
    .pageTitle{font-size:18px;font-weight:900;letter-spacing:.6px;margin:4px 2px 12px;text-transform:uppercase;text-align:center;}

    /* Weather widget (ONLY home) */
    .weatherBox{
      position:fixed; right:12px; top: calc(var(--top-h) + 12px);
      z-index:35;
      border-radius:18px;
      border:1px solid rgba(255,255,255,.16);
      background:rgba(16,16,16,.68);
      backdrop-filter:blur(12px);
      padding:10px 12px;
      display:flex; align-items:center; gap:10px;
      box-shadow:0 10px 28px rgba(0,0,0,.35);
      max-width: calc(100vw - 24px);
    }
    .wIcon{font-size:20px}
    .wTemp{font-weight:900;font-size:14px}
    .wPlace{color:var(--muted);font-weight:900;font-size:12px;margin-top:2px}
    .wCol{display:flex;flex-direction:column;line-height:1.1}

    .card{background:var(--glass2);border:1px solid var(--border);border-radius:18px;overflow:hidden;backdrop-filter:blur(10px);
      box-shadow:0 10px 30px rgba(0,0,0,.25);margin-bottom:12px;}
    .cardHeader{padding:12px 12px 10px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.10);gap:10px;}
    .pill{font-size:12px;padding:6px 10px;border-radius:999px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);
      color:var(--muted);font-weight:800;white-space:nowrap;}
    .cardBody{padding:12px;}
    .muted{color:var(--muted);line-height:1.45;}
    img,video{width:100%;display:block;}

    .actions{display:flex;gap:10px;padding:10px 12px 12px;border-top:1px solid rgba(255,255,255,.10);align-items:center;flex-wrap:wrap;}
    .btn{border:1px solid rgba(255,255,255,.16);background:var(--btn);color:var(--txt);padding:9px 12px;border-radius:14px;font-weight:800;
      cursor:pointer;text-decoration:none;display:inline-flex;gap:8px;align-items:center;}
    .btn:hover{background:var(--btn2);}
    .btn[disabled]{opacity:.55;cursor:not-allowed;}
    .btnDanger{border:1px solid var(--dangerB);background:var(--danger);}
    .btnDanger:hover{background:rgba(255,80,80,.22);}

    .commentBox{padding:12px;border-top:1px solid rgba(255,255,255,.10);}
    .field{width:100%;padding:10px 12px;border-radius:14px;border:1px solid rgba(255,255,255,.16);background:rgba(0,0,0,.28);color:var(--txt);
      outline:none;font-weight:700;margin-bottom:10px;}
    textarea.field{min-height:88px;resize:vertical;font-weight:700;}
    .commentItem{padding:10px 12px;border-radius:14px;border:1px solid rgba(255,255,255,.10);background:rgba(0,0,0,.18);margin-top:8px;}
    .commentMeta{display:flex;justify-content:space-between;gap:10px;font-weight:900;margin-bottom:6px;}
    .commentMeta small{color:var(--muted);font-weight:900;}
    .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:space-between;}

    @media (max-width:520px){
      .topRight{gap:6px;}
      .sLink{font-size:12px;padding:7px 9px;}
      .weatherBox{right:10px;top: calc(var(--top-h) + 10px);}
    }
  </style>
</head>
<body>
  <div class="topbar">
    <button class="burger" id="burger" aria-label="Menü">☰</button>
    <div class="brand">DÜZAĞAÇ KÖYÜ</div>

    <div class="topRight">
      <a class="sLink" href="{{ socials.facebook }}" onclick="return openSocial(event,'facebook', this.href)">
        <span class="sIcon">📘</span> Facebook
      </a>
      <a class="sLink" href="{{ socials.instagram }}" onclick="return openSocial(event,'instagram', this.href)">
        <span class="sIcon">📸</span> Instagram
      </a>
      <a class="sLink" href="{{ socials.whatsapp }}" onclick="return openSocial(event,'whatsapp', this.href)">
        <span class="sIcon">💬</span> WhatsApp
      </a>
    </div>
  </div>

  {% if show_weather %}
  <div class="weatherBox" aria-label="Hava Durumu">
    <div class="wIcon">{{ weather.icon }}</div>
    <div class="wCol">
      <div class="wTemp">
        {% if weather.ok and weather.temp is not none %}
          {{ weather.temp|round(0)|int }}°C
        {% else %}
          --
        {% endif %}
        <span style="color:rgba(255,255,255,.72);font-weight:900;font-size:12px;margin-left:6px;">{{ weather.label }}</span>
      </div>
      <div class="wPlace">Düzağaç Köyü</div>
    </div>
  </div>
  {% endif %}

  <div class="overlay" id="overlay"></div>
  <aside class="drawer" id="drawer" aria-hidden="true">
    <div class="drawerHeader">
      <div class="drawerTitle">MENÜ</div>
      <button class="closeBtn" id="closeBtn" aria-label="Kapat">✕</button>
    </div>

    <nav class="nav">
      <a href="/" class="{{ 'active' if path=='/' else '' }}"><span>🏠</span> Ana Sayfa</a>
      <a href="/videolar" class="{{ 'active' if path=='/videolar' else '' }}"><span>📹</span> Videolar</a>
      <a href="/fotograflar" class="{{ 'active' if path=='/fotograflar' else '' }}"><span>📷</span> Fotoğraflar</a>
      <a href="/duyuru" class="{{ 'active' if path=='/duyuru' else '' }}"><span>📢</span> Duyuru</a>
      <a href="/iletisim" class="{{ 'active' if path=='/iletisim' else '' }}"><span>☎</span> İletişim</a>

      {% if admin %}
      <a href="/panel" class="{{ 'active' if path=='/panel' else '' }}"><span>⚙</span> Panel</a>
      <a href="/cikis" class=""><span>🚪</span> Çıkış</a>
      {% endif %}
    </nav>

    <div style="margin-top:14px" class="muted">
      <div class="pill" style="display:inline-block">Klasöre at → uygulamada görünür</div>
    </div>
  </aside>

  <main class="page">
    <div class="container">
      <div class="pageTitle">{{ title }}</div>
      {{ content|safe }}
    </div>
  </main>

  <script>
    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('overlay');
    const burger = document.getElementById('burger');
    const closeBtn = document.getElementById('closeBtn');

    function openDrawer(){ drawer.classList.add('open'); overlay.classList.add('open'); drawer.setAttribute('aria-hidden','false'); }
    function closeDrawer(){ drawer.classList.remove('open'); overlay.classList.remove('open'); drawer.setAttribute('aria-hidden','true'); }
    burger.addEventListener('click', openDrawer);
    closeBtn.addEventListener('click', closeDrawer);
    overlay.addEventListener('click', closeDrawer);
    drawer.addEventListener('click', (e)=>{ const a=e.target.closest('a'); if(a) closeDrawer(); });
    document.addEventListener('keydown', (e)=>{ if(e.key==='Escape') closeDrawer(); });

    async function likePost(postId){
      const full = (prompt("Adın Soyadın (zorunlu):") || "").trim();
      if(!full){ alert("Adın Soyadın zorunlu!"); return; }
      const res = await fetch("/like", {
        method:"POST",
        headers:{"Content-Type":"application/x-www-form-urlencoded"},
        body:new URLSearchParams({post_id:postId, name_full:full, next:window.location.pathname})
      });
      if(res.redirected) window.location.href = res.url; else window.location.reload();
    }
    window.likePost = likePost;

    function bindCounters(){
      document.querySelectorAll("textarea[data-maxlen='250']").forEach(t=>{
        const c=document.getElementById(t.getAttribute("data-counter"));
        const max=250;
        const upd=()=>{ if(c) c.textContent=(max - t.value.length) + " / 250"; };
        t.addEventListener("input",upd); upd();
      });
    }
    bindCounters();

    function isMobile(){
      return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "");
    }

    function tryOpenScheme(schemeUrl, fallbackUrl){
      const start = Date.now();
      window.location.href = schemeUrl;
      setTimeout(()=> {
        if(Date.now() - start < 1400){
          window.location.href = fallbackUrl;
        }
      }, 900);
    }

    function openSocial(e, kind, webUrl){
      e.preventDefault();

      if(!isMobile()){
        window.location.href = webUrl;
        return false;
      }

      if(kind === "instagram"){
        const scheme = "instagram://user?username=duzagacky";
        tryOpenScheme(scheme, webUrl);
        return false;
      }

      if(kind === "whatsapp"){
        const scheme = "whatsapp://send?text=" + encodeURIComponent(webUrl);
        tryOpenScheme(scheme, webUrl);
        return false;
      }

      if(kind === "facebook"){
        const scheme = "fb://facewebmodal/f?href=" + encodeURIComponent(webUrl);
        tryOpenScheme(scheme, webUrl);
        return false;
      }

      window.location.href = webUrl;
      return false;
    }
    window.openSocial = openSocial;
  </script>
</body>
</html>
"""


def render_page(title: str, content_html: str, show_weather: bool = False):
    weather = get_weather() if show_weather else {"ok": False, "temp": None, "icon": "☁️", "label": "Hava"}
    return render_template_string(
        BASE,
        title=title,
        content=content_html,
        path=request.path,
        admin=is_admin(),
        socials=SOCIALS,
        show_weather=show_weather,
        weather=weather
    )


def post_card(kind: str, filename: str, media_html: str):
    # sadece foto/video için kart (duyuru/iletisim burada kullanılmaz)
    post_id = f"{kind}:{filename}"
    device_id = get_device_id()

    likes = like_count(post_id)
    liked = has_liked(post_id, device_id)
    comments = comments_for(post_id)

    comment_html = ""
    for r in comments[:50]:
        nm = first_name(r["name_full"])
        dt = fmt_date_ddmmyy(r["created_at"])
        cmt = (r["comment"]).replace("<","&lt;").replace(">","&gt;")
        comment_html += f"""
        <div class="commentItem">
          <div class="commentMeta">
            <div>{nm} <small>• {dt}</small></div>
          </div>
          <div class="muted">{cmt}</div>
        </div>
        """

    like_btn = f"""
      <button class="btn" {'disabled' if liked else ''} onclick="likePost('{post_id}')">
        ❤️ Beğen ({likes})
      </button>
    """

    return f"""
    <div class="card">
      <div class="cardHeader"><b>{filename}</b><div class="pill">{'Video' if kind=='video' else 'Foto'}</div></div>
      {media_html}
      <div class="actions">
        {like_btn}
        <div class="btn" style="cursor:default;">💬 Yorumlar ({len(comments)})</div>
      </div>

      <div class="commentBox">
        <form method="POST" action="/comment">
          <input class="field" name="name_full" placeholder="Adın Soyadın (zorunlu)" required>
          <textarea class="field" name="comment" maxlength="250" data-maxlen="250" data-counter="c_{post_id.replace(':','_')}"
            placeholder="Yorum yaz (max 250 karakter)..." required></textarea>
          <div class="row">
            <div class="muted" id="c_{post_id.replace(':','_')}"></div>
            <button class="btn" type="submit">Gönder</button>
          </div>
          <input type="hidden" name="post_id" value="{post_id}">
          <input type="hidden" name="next" value="{request.path}">
        </form>

        {comment_html if comment_html else '<div class="muted" style="margin-top:10px;">Henüz yorum yok.</div>'}
      </div>
    </div>
    """


@app.after_request
def set_device_cookie(resp):
    if not request.cookies.get("dz_device"):
        resp.set_cookie("dz_device", get_device_id(), max_age=60*60*24*365*5, httponly=True, samesite="Lax")
    return resp


# -------------------------
# ANA SAYFA: TOP 3 FOTO (en çok beğeni) + HAVA
# -------------------------
def top3_photos_by_likes():
    photos = list_media(PHOTOS_DIR, (".jpg", ".jpeg", ".png", ".webp"))
    items = []
    for p in photos:
        fn = safe_filename(p["filename"])
        post_id = f"foto:{fn}"
        lc = like_count(post_id)
        items.append((fn, lc, p["ts"]))
    items.sort(key=lambda x: (x[1], x[2]), reverse=True)  # beğeni, eşitlikte yeni
    return items[:3]

@app.get("/")
def home():
    top3 = top3_photos_by_likes()

    if not top3:
        html = """
        <div class="card">
          <div class="cardHeader"><b>Hoş geldiniz</b><div class="pill">Ana Sayfa</div></div>
          <div class="cardBody"><div class="muted">Henüz fotoğraf yok. <b>static/fotograflar</b> içine foto at.</div></div>
        </div>
        """
        return render_page("Ana Sayfa", html, show_weather=True)

    html = ""
    for fn, _, _ in top3:
        url = f"/static/fotograflar/{quote(fn)}"
        media = f"<img src='{url}' alt='{fn}'>"
        html += post_card("foto", fn, media)

    return render_page("Ana Sayfa", html, show_weather=True)


# -------------------------
# VİDEOLAR / FOTOĞRAFLAR
# -------------------------
@app.get("/videolar")
def videolar():
    vids = list_media(VIDEOS_DIR, (".mp4", ".webm", ".mov"))
    html = ""
    if not vids:
        html += """
        <div class="card">
          <div class="cardHeader"><b>Videolar</b><div class="pill">Boş</div></div>
          <div class="cardBody"><div class="muted">static/videolar klasörüne video atınca burada çıkar.</div></div>
        </div>
        """
    for v in vids[:50]:
        fn = safe_filename(v["filename"])
        url = f"/static/videolar/{quote(fn)}"
        media = f"<video controls preload='metadata'><source src='{url}'></video>"
        html += post_card("video", fn, media)
    return render_page("Videolar", html, show_weather=False)

@app.get("/fotograflar")
def fotograflar():
    photos = list_media(PHOTOS_DIR, (".jpg", ".jpeg", ".png", ".webp"))
    html = ""
    if not photos:
        html += """
        <div class="card">
          <div class="cardHeader"><b>Fotoğraflar</b><div class="pill">Boş</div></div>
          <div class="cardBody"><div class="muted">static/fotograflar klasörüne foto atınca burada çıkar.</div></div>
        </div>
        """
    for p in photos[:80]:
        fn = safe_filename(p["filename"])
        url = f"/static/fotograflar/{quote(fn)}"
        media = f"<img src='{url}' alt='{fn}'>"
        html += post_card("foto", fn, media)
    return render_page("Fotoğraflar", html, show_weather=False)


# -------------------------
# DUYURU (beğeni/yorum YOK) + TXT satır satır
# -------------------------
@app.get("/duyuru")
def duyuru():
    lines = read_lines(ANNOUNCE_FILE)
    items = ""
    if lines:
        # en yeni en üst: dosyada sona ekliyoruz -> gösterirken ters çevir
        for t in lines[::-1]:
            safe = t.replace("<","&lt;").replace(">","&gt;")
            items += f"<div class='commentItem'><div class='muted'>{safe}</div></div>"
    else:
        items = "<div class='muted'>Henüz duyuru yok. Panelden duyuru ekleyebilirsin.</div>"

    html = f"""
    <div class="card">
      <div class="cardHeader"><b>Duyurular</b><div class="pill">Liste</div></div>
      <div class="cardBody">{items}</div>
    </div>
    """
    return render_page("Duyuru", html, show_weather=False)


# -------------------------
# İLETİŞİM (beğeni/yorum YOK) + iletisim.txt satır satır
# -------------------------
@app.get("/iletisim")
def iletisim():
    lines = read_lines(CONTACT_FILE)
    items = ""
    if lines:
        for t in lines:
            safe = t.replace("<","&lt;").replace(">","&gt;")
            items += f"<div class='commentItem'><div class='muted'>{safe}</div></div>"
    else:
        items = "<div class='muted'>Henüz iletişim bilgisi yok. iletisim.txt dosyasına her satıra 1 bilgi yaz.</div>"

    html = f"""
    <div class="card">
      <div class="cardHeader"><b>İletişim</b><div class="pill">Bilgi</div></div>
      <div class="cardBody">{items}</div>
    </div>
    """
    return render_page("İletişim", html, show_weather=False)


# -------------------------
# LIKE / COMMENT (sadece foto/video)
# -------------------------
@app.post("/like")
def like():
    post_id = (request.form.get("post_id") or "").strip()
    name_full = (request.form.get("name_full") or "").strip()
    next_url = (request.form.get("next") or "/").strip()
    if not post_id or not name_full:
        return redirect(next_url)
    add_like(post_id, get_device_id(), name_full)
    return redirect(next_url)

@app.post("/comment")
def comment():
    post_id = (request.form.get("post_id") or "").strip()
    name_full = (request.form.get("name_full") or "").strip()
    text = (request.form.get("comment") or "").strip()
    next_url = (request.form.get("next") or "/").strip()

    if not post_id or not name_full or not text:
        return redirect(next_url)

    if len(text) > 250:
        text = text[:250]

    add_comment(post_id, get_device_id(), name_full, text)
    return redirect(next_url)


# -------------------------
# GİZLİ ADMIN GİRİŞ / ÇIKIŞ
# -------------------------
@app.route(ADMIN_LOGIN_PATH, methods=["GET", "POST"])
def admin_login():
    admin_key = read_admin_key()
    if not admin_key:
        return render_page("Hata", """
        <div class="card"><div class="cardHeader"><b>Admin</b><div class="pill">Hata</div></div>
        <div class="cardBody"><div class="muted">.admin_key bulunamadı. EXE yanında olmalı.</div></div></div>
        """)

    if request.method == "POST":
        pwd = (request.form.get("admin_password") or "").strip()
        if pwd == admin_key:
            session["is_admin"] = True
            return redirect(PANEL_PATH)
        return render_page("Admin", """
        <div class="card"><div class="cardHeader"><b>Yönetici Girişi</b><div class="pill">Hatalı</div></div>
        <div class="cardBody">
          <div class="muted" style="margin-bottom:10px;">Şifre yanlış.</div>
          <form method="POST">
            <input class="field" type="password" name="admin_password" placeholder="Admin şifresi" required>
            <button class="btn" type="submit">Giriş</button>
          </form>
        </div></div>
        """)

    return render_page("Admin", """
    <div class="card"><div class="cardHeader"><b>Yönetici Girişi</b><div class="pill">Gizli</div></div>
    <div class="cardBody">
      <form method="POST">
        <input class="field" type="password" name="admin_password" placeholder="Admin şifresi" required>
        <button class="btn" type="submit">Giriş</button>
      </form>
      <div class="muted" style="margin-top:10px;">Bu sayfayı sadece sen kullan. (Bookmark)</div>
    </div></div>
    """)

@app.get("/cikis")
def admin_logout():
    session["is_admin"] = False
    return redirect("/")


# -------------------------
# PANEL (SADECE ADMIN) + duyuru ekleme + silme
# -------------------------
@app.route(PANEL_PATH, methods=["GET"])
def panel():
    require_admin_or_404()

    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, post_id, name_full, comment, created_at FROM comments ORDER BY id DESC LIMIT 200")
    comments = cur.fetchall()
    con.close()

    vids = list_media(VIDEOS_DIR, (".mp4", ".webm", ".mov"))
    photos = list_media(PHOTOS_DIR, (".jpg", ".jpeg", ".png", ".webp"))
    anns = read_lines(ANNOUNCE_FILE)

    # Yorumlar
    com_html = ""
    for r in comments:
        nm = first_name(r["name_full"])
        dt = fmt_date_ddmmyy(r["created_at"])
        cmt = r["comment"].replace("<","&lt;").replace(">","&gt;")
        com_html += f"""
        <div class="commentItem">
          <div class="commentMeta">
            <div>{nm} <small>• {dt}</small></div>
            <form method="POST" action="/admin/delete_comment" style="margin:0;">
              <input type="hidden" name="comment_id" value="{r['id']}">
              <button class="btn btnDanger" type="submit">Sil</button>
            </form>
          </div>
          <div class="muted">{cmt}</div>
          <div class="muted"><small>Post: {r["post_id"]}</small></div>
        </div>
        """

    # Video sil
    vid_html = ""
    for v in vids:
        fn = safe_filename(v["filename"])
        vid_html += f"""
        <div class="commentItem">
          <div class="commentMeta">
            <div>{fn}</div>
            <form method="POST" action="/admin/delete_video" style="margin:0;">
              <input type="hidden" name="filename" value="{fn}">
              <button class="btn btnDanger" type="submit">Sil</button>
            </form>
          </div>
        </div>
        """

    # Foto sil
    photo_html = ""
    for p in photos:
        fn = safe_filename(p["filename"])
        photo_html += f"""
        <div class="commentItem">
          <div class="commentMeta">
            <div>{fn}</div>
            <form method="POST" action="/admin/delete_photo" style="margin:0;">
              <input type="hidden" name="filename" value="{fn}">
              <button class="btn btnDanger" type="submit">Sil</button>
            </form>
          </div>
        </div>
        """

    # Duyuru sil (dosyada son satır en yeni → panelde en yeni üstte gösteriyoruz)
    ann_html = ""
    if anns:
        for idx, t in enumerate(anns[::-1]):
            safe = t.replace("<","&lt;").replace(">","&gt;")
            ann_html += f"""
            <div class="commentItem">
              <div class="commentMeta">
                <div>{safe}</div>
                <form method="POST" action="/admin/delete_announcement" style="margin:0;">
                  <input type="hidden" name="reverse_index" value="{idx}">
                  <button class="btn btnDanger" type="submit">Sil</button>
                </form>
              </div>
            </div>
            """
    else:
        ann_html = "<div class='muted'>Duyuru yok.</div>"

    html = f"""
    <div class="card">
      <div class="cardHeader"><b>Panel</b><div class="pill">Yönetim</div></div>
      <div class="cardBody"><div class="muted">Yorum / Video / Foto / Duyuru yönetimi.</div></div>
    </div>

    <div class="card">
      <div class="cardHeader"><b>Duyuru Ekle</b><div class="pill">TXT</div></div>
      <div class="cardBody">
        <form method="POST" action="/admin/add_announcement">
          <input class="field" name="text" placeholder="Yeni duyuru yaz..." required>
          <button class="btn" type="submit">Ekle</button>
        </form>
        <div class="muted" style="margin-top:10px;">Yeni duyuru eklenince eskileri silmez, dosyaya satır ekler.</div>
      </div>
    </div>

    <div class="card">
      <div class="cardHeader"><b>Duyuru Sil</b><div class="pill">Liste</div></div>
      <div class="cardBody">{ann_html}</div>
    </div>

    <div class="card">
      <div class="cardHeader"><b>Videoları Sil</b><div class="pill">Dosya</div></div>
      <div class="cardBody">{vid_html if vid_html else "<div class='muted'>Video yok.</div>"}</div>
    </div>

    <div class="card">
      <div class="cardHeader"><b>Fotoğrafları Sil</b><div class="pill">Dosya</div></div>
      <div class="cardBody">{photo_html if photo_html else "<div class='muted'>Foto yok.</div>"}</div>
    </div>

    <div class="card">
      <div class="cardHeader"><b>Yorumları Sil</b><div class="pill">DB</div></div>
      <div class="cardBody">{com_html if com_html else "<div class='muted'>Henüz yorum yok.</div>"}</div>
    </div>
    """
    return render_page("Panel", html, show_weather=False)


@app.post("/admin/add_announcement")
def admin_add_announcement():
    require_admin_or_404()
    text = (request.form.get("text") or "").strip()
    if text:
        append_line(ANNOUNCE_FILE, text)  # ESKİYİ SİLMEZ
    return redirect(PANEL_PATH)


@app.post("/admin/delete_comment")
def admin_delete_comment():
    require_admin_or_404()
    try:
        cid = int(request.form.get("comment_id", "0"))
    except:
        return redirect(PANEL_PATH)
    delete_comment(cid)
    return redirect(PANEL_PATH)

@app.post("/admin/delete_video")
def admin_delete_video():
    require_admin_or_404()
    fn = safe_filename(request.form.get("filename", ""))
    if not fn.lower().endswith((".mp4", ".webm", ".mov")):
        return redirect(PANEL_PATH)
    path = os.path.join(VIDEOS_DIR, fn)
    if os.path.isfile(path):
        os.remove(path)
    return redirect(PANEL_PATH)

@app.post("/admin/delete_photo")
def admin_delete_photo():
    require_admin_or_404()
    fn = safe_filename(request.form.get("filename", ""))
    if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return redirect(PANEL_PATH)
    path = os.path.join(PHOTOS_DIR, fn)
    if os.path.isfile(path):
        os.remove(path)
    return redirect(PANEL_PATH)

@app.post("/admin/delete_announcement")
def admin_delete_announcement():
    require_admin_or_404()
    lines = read_lines(ANNOUNCE_FILE)
    try:
        rev_idx = int(request.form.get("reverse_index", "-1"))
    except:
        return redirect(PANEL_PATH)
    # ekranda ters gösteriyoruz → gerçek index
    real_index = (len(lines) - 1) - rev_idx
    if 0 <= real_index < len(lines):
        del lines[real_index]
        write_lines(ANNOUNCE_FILE, lines)
    return redirect(PANEL_PATH)


# -------------------------
# EXE çift tık → tarayıcı aç
# -------------------------
def open_browser():
    webbrowser.open("http://127.0.0.1:5050")

if __name__ == "__main__":
    # EXE açılınca tarayıcı otomatik açılsın
    threading.Timer(0.8, open_browser).start()
app.run(host="127.0.0.1", port=5050, debug=False)


