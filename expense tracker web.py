from flask import Flask, g, render_template_string, request, redirect, url_for, send_file, flash, jsonify
import sqlite3
from pathlib import Path
from datetime import date, datetime
import csv
import io

APP = Flask(__name__)
APP.secret_key = "dev-secret-change-this"  # local-only; change if you plan to expose it
DB_PATH = Path("expenses_web.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,        -- YYYY-MM-DD
    amount REAL NOT NULL,      -- positive for income, negative for expense
    type TEXT NOT NULL,        -- 'income' or 'expense'
    category TEXT,
    tags TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
"""


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        g._db = db
    return db

@APP.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()

def add_transaction(tx_date, amount, tx_type, category=None, tags=None, notes=None):
    created = datetime.utcnow().isoformat()
    # Normalize sign: expense stored negative, income positive
    try:
        amt = float(amount)
    except:
        raise ValueError("Invalid amount")
    if tx_type == "expense" and amt > 0:
        amt = -abs(amt)
    if tx_type == "income" and amt < 0:
        amt = abs(amt)
    db = get_db()
    db.execute("INSERT INTO transactions (date, amount, type, category, tags, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (tx_date, amt, tx_type, category, tags, notes, created))
    db.commit()

def update_transaction(tx_id, tx_date, amount, tx_type, category, tags, notes):
    amt = float(amount)
    if tx_type == "expense" and amt > 0:
        amt = -abs(amt)
    if tx_type == "income" and amt < 0:
        amt = abs(amt)
    db = get_db()
    db.execute("UPDATE transactions SET date=?, amount=?, type=?, category=?, tags=?, notes=? WHERE id=?",
               (tx_date, amt, tx_type, category, tags, notes, tx_id))
    db.commit()

def delete_transaction(tx_id):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    db.commit()

def query_transactions(limit=1000, start_date=None, end_date=None, category=None):
    q = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if start_date:
        q += " AND date >= ?"
        params.append(start_date)
    if end_date:
        q += " AND date <= ?"
        params.append(end_date)
    if category:
        q += " AND category = ?"
        params.append(category)
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)
    cur = get_db().execute(q, params)
    return cur.fetchall()

def summary_by_month(year, month):
    start = date(year, month, 1).isoformat()
    if month == 12:
        end = date(year+1, 1, 1).isoformat()
    else:
        end = date(year, month+1, 1).isoformat()
    cur = get_db().execute(
        "SELECT SUM(CASE WHEN type='income' THEN amount ELSE 0 END) as income, "
        "SUM(CASE WHEN type='expense' THEN -amount ELSE 0 END) as expense "
        "FROM transactions WHERE date >= ? AND date < ?",
        (start, end)
    )
    row = cur.fetchone()
    income = row["income"] or 0.0
    expense = row["expense"] or 0.0
    net = income - expense
    return {"income": income, "expense": expense, "net": net}

BASE_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Expenses & Savings Tracker — Web</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css" rel="stylesheet">
<style>
  .small { font-size: 0.9rem; color: #444;}
  .nowrap { white-space: nowrap; }
  .flex-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .top-actions { display:flex; gap:8px; align-items:center; margin-bottom:8px; }
  table td.notes { max-width: 320px; overflow:hidden; text-overflow:ellipsis; }
</style>
</head>
<body>
<header>
  <h1>Expenses & Savings Tracker — Web</h1>
  <p class="small">Local-only, single-user. Database: <code>{{ db_name }}</code></p>
</header>
<main>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <section>
        {% for m in messages %}
          <div style="padding:8px;border-left:3px solid #5a5;">{{ m }}</div>
        {% endfor %}
      </section>
    {% endif %}
  {% endwith %}

  {% block content %}{% endblock %}
</main>
<footer class="small">
  <p>Made with practical tools. Keep it local, keep it simple.</p>
</footer>
</body>
</html>
"""

INDEX_HTML = """
{% extends "base" %}
{% block content %}
<section>
  <form method="post" action="{{ url_for('add') }}" style="display:grid; grid-template-columns:repeat(6,1fr); gap:8px; align-items:end;">
    <div>
      <label>Date</label>
      <input name="date" type="date" value="{{ today }}">
    </div>
    <div>
      <label>Amount</label>
      <input name="amount" type="number" step="0.01" required>
    </div>
    <div>
      <label>Type</label>
      <select name="type">
        <option value="expense" selected>expense</option>
        <option value="income">income</option>
      </select>
    </div>
    <div>
      <label>Category</label>
      <input name="category" placeholder="e.g., Food, Rent">
    </div>
    <div>
      <label>Tags (comma)</label>
      <input name="tags">
    </div>
    <div>
      <label>Notes</label>
      <input name="notes">
    </div>
    <div style="grid-column: 1 / -1;">
      <div class="top-actions">
        <button type="submit">Add transaction</button>
        <a href="{{ url_for('index') }}">Refresh</a>
        <a href="{{ url_for('export_csv') }}">Export CSV</a>
        <a href="{{ url_for('dashboard') }}">Dashboard</a>
      </div>
    </div>
  </form>
</section>

<section style="margin-top:12px;">
  <form method="get" action="{{ url_for('index') }}" class="flex-row">
    <label>From <input name="from" type="date" value="{{ request.args.get('from','') }}"></label>
    <label>To <input name="to" type="date" value="{{ request.args.get('to','') }}"></label>
    <label>Category <input name="category" value="{{ request.args.get('category','') }}"></label>
    <button type="submit">Apply filters</button>
    <a href="{{ url_for('index') }}">Clear</a>
  </form>

  <h3>Transactions</h3>
  <table>
    <thead>
      <tr>
        <th>Date</th><th class="nowrap">Amount</th><th>Type</th><th>Category</th><th>Tags</th><th>Notes</th><th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
        <tr>
          <td>{{ r.date }}</td>
          <td class="nowrap">{{ "%.2f"|format(r.amount) }}</td>
          <td>{{ r.type }}</td>
          <td>{{ r.category or "" }}</td>
          <td>{{ r.tags or "" }}</td>
          <td class="notes">{{ r.notes or "" }}</td>
          <td class="nowrap">
            <a href="{{ url_for('edit', tx_id=r.id) }}">Edit</a> |
            <a href="{{ url_for('delete', tx_id=r.id) }}" onclick="return confirm('Delete?')">Delete</a>
          </td>
        </tr>
      {% else %}
        <tr><td colspan="7">No transactions found.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
"""

EDIT_HTML = """
{% extends "base" %}
{% block content %}
<section>
  <h3>Edit Transaction</h3>
  <form method="post" action="{{ url_for('edit', tx_id=tx.id) }}" style="display:grid; grid-template-columns:repeat(6,1fr); gap:8px; align-items:end;">
    <div>
      <label>Date</label>
      <input name="date" type="date" value="{{ tx.date }}">
    </div>
    <div>
      <label>Amount</label>
      <input name="amount" type="number" step="0.01" value="{{ "%.2f"|format(tx.amount|abs) }}" required>
    </div>
    <div>
      <label>Type</label>
      <select name="type">
        <option value="expense" {% if tx.type=='expense' %}selected{% endif %}>expense</option>
        <option value="income" {% if tx.type=='income' %}selected{% endif %}>income</option>
      </select>
    </div>
    <div>
      <label>Category</label>
      <input name="category" value="{{ tx.category }}">
    </div>
    <div>
      <label>Tags (comma)</label>
      <input name="tags" value="{{ tx.tags }}">
    </div>
    <div>
      <label>Notes</label>
      <input name="notes" value="{{ tx.notes }}">
    </div>
    <div style="grid-column: 1 / -1;">
      <button type="submit">Save</button>
      <a href="{{ url_for('index') }}">Cancel</a>
    </div>
  </form>
</section>
{% endblock %}
"""

DASH_HTML = """
{% extends "base" %}
{% block content %}
<section>
  <h2>Dashboard</h2>
  <div style="display:flex; gap:16px; align-items:center;">
    <div>
      <h3>This month</h3>
      <p>Income: <strong>{{ month_summary.income|round(2) }}</strong><br>
      Expense: <strong>{{ month_summary.expense|round(2) }}</strong><br>
      Net: <strong>{{ month_summary.net|round(2) }}</strong></p>
    </div>
    <div>
      <h3>Last 12 months</h3>
      <canvas id="chart" width="800" height="320"></canvas>
    </div>
  </div>
</section>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
  const labels = {{ months|tojson }};
  const incomes = {{ incs|tojson }};
  const expenses = {{ exps|tojson }};
  const ctx = document.getElementById('chart').getContext('2d');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: 'Income', data: incomes },
        { label: 'Expense', data: expenses }
      ]
    },
    options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true } } }
  });
</script>
{% endblock %}
"""


@APP.context_processor
def templates():
    return dict()

from jinja2 import DictLoader
APP.jinja_loader = DictLoader({
    "base": BASE_HTML,
    "index.html": INDEX_HTML,
    "edit.html": EDIT_HTML,
    "dash.html": DASH_HTML
})


@APP.before_request
def ensure_db():
    if not DB_PATH.exists():
        init_db()

@APP.route("/")
def index():
    start = request.args.get("from") or None
    end = request.args.get("to") or None
    cat = request.args.get("category") or None
    rows = query_transactions(limit=1000, start_date=start, end_date=end, category=cat)
    return render_template_string(APP.jinja_env.get_template("index.html").render(
        rows=rows, today=date.today().isoformat(), db_name=str(DB_PATH)
    ))

@APP.route("/add", methods=["POST"])
def add():
    try:
        tx_date = request.form.get("date") or date.today().isoformat()
        # validate
        datetime.strptime(tx_date, "%Y-%m-%d")
        amount = request.form["amount"]
        tx_type = request.form.get("type", "expense")
        category = request.form.get("category") or None
        tags = request.form.get("tags") or None
        notes = request.form.get("notes") or None
        add_transaction(tx_date, amount, tx_type, category, tags, notes)
        flash("Transaction added.")
    except Exception as e:
        flash(f"Error adding transaction: {e}")
    return redirect(url_for("index"))

@APP.route("/edit/<int:tx_id>", methods=["GET", "POST"])
def edit(tx_id):
    db = get_db()
    cur = db.execute("SELECT * FROM transactions WHERE id=?", (tx_id,))
    tx = cur.fetchone()
    if not tx:
        flash("Transaction not found.")
        return redirect(url_for("index"))
    if request.method == "POST":
        try:
            tx_date = request.form.get("date") or date.today().isoformat()
            datetime.strptime(tx_date, "%Y-%m-%d")
            amount = request.form["amount"]
            tx_type = request.form.get("type", "expense")
            category = request.form.get("category") or None
            tags = request.form.get("tags") or None
            notes = request.form.get("notes") or None
            update_transaction(tx_id, tx_date, amount, tx_type, category, tags, notes)
            flash("Transaction updated.")
            return redirect(url_for("index"))
        except Exception as e:
            flash(f"Error updating: {e}")
            return redirect(url_for("edit", tx_id=tx_id))

    return render_template_string(APP.jinja_env.get_template("edit.html").render(tx=tx, db_name=str(DB_PATH)))

@APP.route("/delete/<int:tx_id>")
def delete(tx_id):
    try:
        delete_transaction(tx_id)
        flash("Deleted.")
    except Exception as e:
        flash(f"Delete failed: {e}")
    return redirect(url_for("index"))

@APP.route("/export")
def export_csv():
    start = request.args.get("from") or None
    end = request.args.get("to") or None
    cat = request.args.get("category") or None
    rows = query_transactions(limit=10000, start_date=start, end_date=end, category=cat)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","date","amount","type","category","tags","notes","created_at"])
    for r in rows:
        writer.writerow([r["id"], r["date"], r["amount"], r["type"], r["category"], r["tags"], r["notes"], r["created_at"]])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="transactions.csv")

@APP.route("/dashboard")
def dashboard():

    today = date.today()
    s = summary_by_month(today.year, today.month)
    # last 12 months
    labels = []
    incs = []
    exps = []
    y = today.year
    m = today.month
    for i in range(12):

        yy = y - ((12 - m + i) // 12)
        mm = ((m - 1 + i) % 12) + 1
        labels.append(f"{yy}-{mm:02d}")
        row = summary_by_month(yy, mm)
        incs.append(row["income"])
        exps.append(row["expense"])
    return render_template_string(APP.jinja_env.get_template("dash.html").render(
        month_summary=s, months=labels, incs=incs, exps=exps, db_name=str(DB_PATH)
    ))

@APP.route("/api/summary_month/<int:year>/<int:month>")
def api_summary_month(year, month):
    return jsonify(summary_by_month(year, month))


if __name__ == "__main__":
    # ensure DB and tables
    if not DB_PATH.exists():
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.executescript(SCHEMA)
    APP.run(debug=True)
