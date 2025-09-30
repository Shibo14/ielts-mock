
import os, json, sqlite3, datetime, functools
from pathlib import Path
from slugify import slugify
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_from_directory, g
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "ielts.db"
AUDIO_DIR = BASE_DIR / "data" / "audio"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS centers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT,
            email TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT 'student',
            center_id INTEGER REFERENCES centers(id)
        );
        CREATE TABLE IF NOT EXISTS tests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            slug TEXT UNIQUE,
            section TEXT,
            level TEXT DEFAULT 'general',
            duration_minutes INTEGER DEFAULT 60,
            center_id INTEGER REFERENCES centers(id),
            audio_filename TEXT
        );
        CREATE TABLE IF NOT EXISTS questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER REFERENCES tests(id) ON DELETE CASCADE,
            qtype TEXT,
            prompt TEXT,
            options_json TEXT,
            answer_key TEXT,
            order_index INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            test_id INTEGER REFERENCES tests(id),
            started_at TEXT,
            finished_at TEXT,
            raw_score INTEGER DEFAULT 0,
            band_score REAL DEFAULT 0.0
        );
        CREATE TABLE IF NOT EXISTS answers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
            question_id INTEGER REFERENCES questions(id),
            response TEXT,
            is_correct INTEGER DEFAULT 0
        );
        """
    )
    db.commit()

login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.full_name = row["full_name"]
        self.email = row["email"]
        self.role = row["role"]
        self.center_id = row["center_id"]

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row) if row else None

def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            flash("Faqat admin", "warning")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped

@app.template_filter("loadjson")
def loadjson_filter(s):
    try:
        return json.loads(s or "[]")
    except Exception:
        return []

def band_from_raw(raw, total):
    if not total:
        return 0.0
    pct = raw / total
    if pct >= 0.95: return 9.0
    if pct >= 0.9: return 8.5
    if pct >= 0.85: return 8.0
    if pct >= 0.75: return 7.5
    if pct >= 0.7: return 7.0
    if pct >= 0.65: return 6.5
    if pct >= 0.6: return 6.0
    if pct >= 0.55: return 5.5
    if pct >= 0.5: return 5.0
    if pct >= 0.45: return 4.5
    return 4.0

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","")
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row))
            return redirect(url_for("dashboard"))
        flash("Email yoki parol noto'g'ri", "danger")
    return render_template("auth_login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    tests = db.execute("SELECT * FROM tests ORDER BY section, title").fetchall()
    return render_template("dashboard.html", tests=tests)

@app.route("/test/<slug>")
@login_required
def test_select(slug):
    db = get_db()
    test = db.execute("SELECT * FROM tests WHERE slug = ?", (slug,)).fetchone()
    if not test:
        flash("Test topilmadi", "warning")
        return redirect(url_for("dashboard"))
    return render_template("test_select.html", test=test)

@app.route("/test/<slug>/start", methods=["POST"])
@login_required
def test_start(slug):
    db = get_db()
    test = db.execute("SELECT * FROM tests WHERE slug = ?", (slug,)).fetchone()
    if not test:
        flash("Test topilmadi", "warning")
        return redirect(url_for("dashboard"))
    now = datetime.datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO submissions (user_id, test_id, started_at) VALUES (?, ?, ?)",
        (current_user.id, test["id"], now),
    )
    sub_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    db.commit()
    return redirect(url_for("test_take", slug=slug, submission_id=sub_id))

@app.route("/test/<slug>/take/<int:submission_id>")
@login_required
def test_take(slug, submission_id):
    db = get_db()
    test = db.execute("SELECT * FROM tests WHERE slug = ?", (slug,)).fetchone()
    sub = db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not test or not sub or sub["user_id"] != current_user.id:
        flash("Ruxsat yo'q", "danger")
        return redirect(url_for("dashboard"))
    questions = db.execute(
        "SELECT * FROM questions WHERE test_id = ? ORDER BY order_index", (test["id"],)
    ).fetchall()
    return render_template("test_take.html", test=test, submission=sub, questions=questions)

@app.route("/api/answer", methods=["POST"])
@login_required
def api_answer():
    data = request.get_json() or {}
    submission_id = int(data.get("submission_id", 0))
    question_id = int(data.get("question_id", 0))
    response = (data.get("response") or "").strip()

    db = get_db()
    sub = db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub or sub["user_id"] != current_user.id:
        return jsonify({"ok": False}), 400
    q = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not q:
        return jsonify({"ok": False}), 400

    correct = 0
    if q["qtype"] == "mcq":
        correct = 1 if response == q["answer_key"] else 0
    else:
        correct = 1 if response.lower() == (q["answer_key"] or "").lower() else 0

    existing = db.execute(
        "SELECT id FROM answers WHERE submission_id = ? AND question_id = ?",
        (submission_id, question_id),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE answers SET response=?, is_correct=? WHERE id=?",
            (response, correct, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO answers (submission_id, question_id, response, is_correct) VALUES (?, ?, ?, ?)",
            (submission_id, question_id, response, correct),
        )
    db.commit()
    return jsonify({"ok": True, "is_correct": bool(correct)})

@app.route("/test/<slug>/finish/<int:submission_id>", methods=["POST"])
@login_required
def test_finish(slug, submission_id):
    db = get_db()
    test = db.execute("SELECT * FROM tests WHERE slug = ?", (slug,)).fetchone()
    sub = db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not test or not sub or sub["user_id"] != current_user.id:
        flash("Xatolik", "danger")
        return redirect(url_for("dashboard"))
    total = db.execute("SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (test["id"],)).fetchone()["c"]
    correct = db.execute(
        "SELECT COUNT(*) AS c FROM answers WHERE submission_id=? AND is_correct=1",
        (submission_id,),
    ).fetchone()["c"]
    band = band_from_raw(correct, total)
    now = datetime.datetime.utcnow().isoformat()
    db.execute(
        "UPDATE submissions SET raw_score=?, band_score=?, finished_at=? WHERE id=?",
        (correct, band, now, submission_id),
    )
    db.commit()
    return redirect(url_for("results_view", submission_id=submission_id))

@app.route("/results/<int:submission_id>")
@login_required
def results_view(submission_id):
    db = get_db()
    sub = db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not sub or (sub["user_id"] != current_user.id and current_user.role != "admin"):
        flash("Ruxsat yo'q", "danger")
        return redirect(url_for("dashboard"))
    test = db.execute("SELECT * FROM tests WHERE id = ?", (sub["test_id"],)).fetchone()
    total = db.execute("SELECT COUNT(*) AS c FROM questions WHERE test_id=?", (test["id"],)).fetchone()["c"]
    return render_template("results_view.html", sub=sub, test=test, total=total)

@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    return render_template("admin_panel.html")

@app.route("/admin/tests")
@login_required
@admin_required
def admin_tests():
    db = get_db()
    tests = db.execute("SELECT * FROM tests ORDER BY section, title").fetchall()
    return render_template("admin_tests.html", tests=tests)

@app.route("/admin/tests/create", methods=["POST"])
@login_required
@admin_required
def admin_tests_create():
    title = request.form.get("title","").strip()
    section = request.form.get("section","listening")
    level = request.form.get("level","academic")
    duration = int(request.form.get("duration") or 60)
    audio = request.files.get("audio")

    slug = slugify(title) or f"test-{int(datetime.datetime.utcnow().timestamp())}"
    audio_filename = None
    if audio and section == "listening":
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = audio.filename.replace("/", "_").replace("\\", "_")
        path = AUDIO_DIR / safe_name
        audio.save(path)
        audio_filename = safe_name

    db = get_db()
    db.execute(
        "INSERT INTO tests (title, slug, section, level, duration_minutes, audio_filename) VALUES (?, ?, ?, ?, ?, ?)",
        (title, slug, section, level, duration, audio_filename),
    )
    db.commit()
    flash("Test yaratildi", "success")
    return redirect(url_for("admin_tests"))

@app.route("/admin/tests/<int:test_id>/import", methods=["POST"])
@login_required
@admin_required
def admin_tests_import(test_id):
    file = request.files.get("file")
    if not file:
        flash("Fayl tanlanmadi", "warning")
        return redirect(url_for("admin_tests"))
    try:
        data = json.load(file)
    except Exception:
        flash("JSON faylda xato", "danger")
        return redirect(url_for("admin_tests"))
    db = get_db()
    for item in data:
        qtype = item.get("qtype","mcq")
        prompt = item.get("prompt","")
        options = item.get("options", [])
        answer = item.get("answer_key","")
        order = int(item.get("order", 0))
        db.execute(
            "INSERT INTO questions (test_id, qtype, prompt, options_json, answer_key, order_index) VALUES (?, ?, ?, ?, ?, ?)",
            (test_id, qtype, prompt, json.dumps(options, ensure_ascii=False), answer, order),
        )
    db.commit()
    flash("Savollar import qilindi", "success")
    return redirect(url_for("admin_tests"))

@app.route("/admin/results")
@login_required
@admin_required
def admin_results():
    db = get_db()
    subs = db.execute("""
      SELECT s.*, u.full_name AS user_name, t.title AS test_title,
             (SELECT COUNT(*) FROM questions WHERE test_id=t.id) AS total_q
      FROM submissions s
      JOIN users u ON u.id=s.user_id
      JOIN tests t ON t.id=s.test_id
      ORDER BY s.started_at DESC
    """).fetchall()
    return render_template("admin_results.html", submissions=subs)

@app.route("/audio/<path:filename>")
@login_required
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.cli.command("seed")
def seed_cmd():
    init_db()
    db = get_db()
    c = db.execute("SELECT id FROM centers WHERE name = ?", ("Default Center",)).fetchone()
    if not c:
        db.execute("INSERT INTO centers (name) VALUES (?)", ("Default Center",))
        db.commit()
        c = db.execute("SELECT id FROM centers WHERE name = ?", ("Default Center",)).fetchone()
    center_id = c["id"]
    a = db.execute("SELECT id FROM users WHERE email=?", ("admin@example.com",)).fetchone()
    if not a:
        db.execute("INSERT INTO users (full_name,email,password_hash,role,center_id) VALUES (?,?,?,?,?)",
                   ("Admin","admin@example.com", generate_password_hash("admin123"), "admin", center_id))
    s = db.execute("SELECT id FROM users WHERE email=?", ("student@example.com",)).fetchone()
    if not s:
        db.execute("INSERT INTO users (full_name,email,password_hash,role,center_id) VALUES (?,?,?,?,?)",
                   ("Student","student@example.com", generate_password_hash("student123"), "student", center_id))
    db.commit()
    t1 = db.execute("SELECT id FROM tests WHERE slug='listening-sample-1'").fetchone()
    if not t1:
        db.execute("INSERT INTO tests (title,slug,section,duration_minutes,center_id,audio_filename) VALUES (?,?,?,?,?,?)",
                   ("Listening Sample 1","listening-sample-1","listening",30,center_id,"listening_sample_1.mp3"))
        db.commit()
        t1 = db.execute("SELECT id FROM tests WHERE slug='listening-sample-1'").fetchone()
        with open(BASE_DIR / "data" / "seeds" / "sample_listening.json", "r", encoding="utf-8") as f:
            items = json.load(f)
        for i in items:
            db.execute("INSERT INTO questions (test_id,qtype,prompt,options_json,answer_key,order_index) VALUES (?,?,?,?,?,?)",
                       (t1["id"], i["qtype"], i["prompt"], json.dumps(i.get("options", []), ensure_ascii=False), i["answer_key"], i.get("order",0)))
        db.commit()
    t2 = db.execute("SELECT id FROM tests WHERE slug='reading-sample-1'").fetchone()
    if not t2:
        db.execute("INSERT INTO tests (title,slug,section,duration_minutes,center_id) VALUES (?,?,?,?,?)",
                   ("Reading Sample 1","reading-sample-1","reading",60,center_id))
        db.commit()
        t2 = db.execute("SELECT id FROM tests WHERE slug='reading-sample-1'").fetchone()
        with open(BASE_DIR / "data" / "seeds" / "sample_reading.json", "r", encoding="utf-8") as f:
            items = json.load(f)
        for i in items:
            db.execute("INSERT INTO questions (test_id,qtype,prompt,options_json,answer_key,order_index) VALUES (?,?,?,?,?,?)",
                       (t2["id"], i["qtype"], i["prompt"], json.dumps(i.get("options", []), ensure_ascii=False), i["answer_key"], i.get("order",0)))
        db.commit()
    print("Seed tugadi: admin=admin@example.com / admin123 | student=student@example.com / student123")

if __name__ == "__main__":
    init_db()
    app.run()
