"""Build docs/index.html: Australian Government receipts, payments and gross debt.

Data comes from the "Historical Australian Government Data" appendix of the
latest Final Budget Outcome (FBO), published each September by Treasury and
Finance on budget.gov.au:

  Table x.1  General government sector cash receipts and payments
  Table x.5  Face value of Australian Government Securities (AGS) on issue
             ("gross debt"), end of financial year
  Table x.6  General government sector accrual revenue and expenses

Each run looks for the newest FBO appendix PDF. If it finds and parses one,
data/fiscal_history.csv is refreshed from it; otherwise the committed CSV is
used unchanged. The build exits non-zero only if it has no usable data at all,
so a broken page is never published.

Run locally:  python build.py
"""

from __future__ import annotations

import html
import io
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pdfplumber
import plotly.graph_objects as go
import requests

ROOT = Path(__file__).parent
CSV = ROOT / "data" / "fiscal_history.csv"
SOURCE_FILE = ROOT / "data" / "source.txt"
OUT = ROOT / "docs" / "index.html"

YEARS_SHOWN = 30  # most recent N financial years
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
}
FBO_URL = "https://archive.budget.gov.au/{fy}/fbo/download/06_appendix_b.pdf"

build_notes: list[str] = []


# ------------------------------------------------------------------ fetching
def fy_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def find_latest_fbo() -> tuple[str, bytes] | None:
    """Return (financial year, PDF bytes) of the newest FBO appendix found."""
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    # The FBO for year Y-(Y+1) is released around September of Y+1.
    newest_start = now.year - 1
    for start in range(newest_start, newest_start - 4, -1):
        fy = fy_label(start)
        try:
            r = requests.get(FBO_URL.format(fy=fy), headers=UA, timeout=60)
        except requests.RequestException as e:
            print(f"FBO {fy}: {e}")
            continue
        if r.ok and r.content[:4] == b"%PDF":
            print(f"Found FBO {fy} historical appendix")
            return fy, r.content
        print(f"FBO {fy}: not available (HTTP {r.status_code})")
    return None


def _table_rows(text: str, table_no: int) -> dict[str, list[str]]:
    """Rows of 'Table <letter>.<n>:' keyed by financial year (actuals only)."""
    lines = text.splitlines()
    rows: dict[str, list[str]] = {}
    title = re.compile(rf"Table [A-Z]+\.{table_no}:")
    for i, line in enumerate(lines):
        if not title.search(line):
            continue
        for row in lines[i + 1 : i + 80]:
            if re.search(r"Table [A-Z]+\.\d+:", row) and not title.search(row):
                break
            m = re.match(r"\s*(\d{4}-\d{2})\s+(.*)", row)
            if m and "(e)" not in m.group(2):  # skip estimates
                rows[m.group(1)] = m.group(2).replace(",", "").split()
    return rows


def parse_fbo(pdf_bytes: bytes) -> pd.DataFrame:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)

    def num(tokens: list[str], i: int) -> float:
        return float(tokens[i]) / 1000  # $m -> $b

    t1, t5, t6 = (_table_rows(text, n) for n in (1, 5, 6))
    df = pd.DataFrame(
        {
            "receipts": {y: num(v, 0) for y, v in t1.items()},
            "payments": {y: num(v, 2) for y, v in t1.items()},
            "revenue": {y: num(v, 0) for y, v in t6.items()},
            "expenses": {y: num(v, 2) for y, v in t6.items()},
            "gross_debt": {y: num(v, 0) for y, v in t5.items()},
        }
    ).sort_index()
    df.index.name = "financial_year"
    return df


def validate(df: pd.DataFrame, fy: str) -> None:
    """Sanity checks so a changed PDF layout can't publish garbage."""
    problems = []
    if df.index[-1] != fy:
        problems.append(f"latest year is {df.index[-1]}, expected {fy}")
    starts = [int(y[:4]) for y in df.index]
    if starts != list(range(starts[0], starts[-1] + 1)):
        problems.append("financial years are not contiguous")
    recent = df.iloc[-YEARS_SHOWN:]
    if recent[["receipts", "payments", "gross_debt"]].isna().any().any():
        problems.append("missing cash or debt values in the chart window")
    if not (recent[["receipts", "payments"]] > 50).all().all():
        problems.append("implausibly small receipts/payments")
    if CSV.exists():  # history shouldn't move much between editions
        old = pd.read_csv(CSV, index_col=0)
        common = old.index.intersection(df.index)
        drift = (df.loc[common] - old.loc[common]).abs() / old.loc[common]
        if (drift > 0.05).any().any():
            problems.append("historical values changed by more than 5%")
    if problems:
        raise ValueError("; ".join(problems))


def load_data() -> tuple[pd.DataFrame, str]:
    try:
        found = find_latest_fbo()
        if found:
            fy, pdf = found
            df = parse_fbo(pdf)
            validate(df, fy)
            CSV.parent.mkdir(exist_ok=True)
            df.round(3).to_csv(CSV)
            source = f"Final Budget Outcome {fy.replace('-', '–')}, Appendix B"
            SOURCE_FILE.write_text(source + "\n" + FBO_URL.format(fy=fy) + "\n")
            return df, source
        build_notes.append("No Final Budget Outcome appendix could be downloaded on this run.")
    except Exception as e:  # keep the last good data
        print(f"Could not refresh from FBO: {e}")
        build_notes.append("The latest Final Budget Outcome could not be read on this run.")
    if not CSV.exists():
        raise RuntimeError("No data: FBO unavailable and no data/fiscal_history.csv")
    source = SOURCE_FILE.read_text().splitlines()[0] if SOURCE_FILE.exists() else "saved data"
    build_notes.append(f"Figures are from the last successfully read edition ({source}).")
    return pd.read_csv(CSV, index_col=0), source


# --------------------------------------------------------------------- chart
def make_chart(df: pd.DataFrame) -> go.Figure:
    x = list(df.index)
    debt_max = df["gross_debt"].max()
    debt_step = 200 if debt_max > 600 else 100
    debt_top = -(-debt_max * 1.05 // debt_step) * debt_step  # round up to a step
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=df["gross_debt"], name="Gross debt (AGS on issue)", yaxis="y2",
        marker=dict(color="rgba(150, 160, 175, 0.40)"),
        hovertemplate="Gross debt: $%{y:,.1f}b<extra></extra>",
    ))
    lines = [
        ("receipts", "Receipts (cash)", "#0066cc", True),
        ("payments", "Payments (cash)", "#cc0000", True),
        ("revenue", "Revenue (accrual)", "#0066cc", False),
        ("expenses", "Expenses (accrual)", "#cc0000", False),
    ]
    for col, name, colour, visible in lines:
        fig.add_trace(go.Scatter(
            x=x, y=df[col], name=name, mode="lines+markers", visible=visible,
            line=dict(color=colour, width=2.5), marker=dict(size=5),
            hovertemplate=name + ": $%{y:,.1f}b<extra></extra>",
        ))
    fig.update_layout(
        template="plotly_white",
        hovermode="x unified",
        hoverlabel=dict(namelength=-1),
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        yaxis=dict(title=dict(text="Annual flows ($ billion)"), gridcolor="#e5e5e5",
                   rangemode="tozero"),
        yaxis2=dict(title=dict(text="Gross debt, end of year ($ billion)",
                               font=dict(color="#777")),
                    tickfont=dict(color="#777"), overlaying="y", side="right",
                    showgrid=False, tickmode="linear", tick0=0, dtick=debt_step,
                    range=[0, debt_top]),
        xaxis=dict(type="category", tickangle=-45,
                   rangeslider=dict(visible=True, thickness=0.06)),
        height=640,
        margin=dict(l=60, r=60, t=70, b=40),
    )
    return fig


# ---------------------------------------------------------------------- page
def build_page(df: pd.DataFrame, source: str) -> str:
    chart = make_chart(df).to_html(
        full_html=False, include_plotlyjs="cdn",
        config={"responsive": True, "displaylogo": False},
    )
    built = datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%-d %B %Y")
    first, last = df.index[0], df.index[-1]
    fy = lambda y: y.replace("-", "–")  # noqa: E731
    rows = []
    for y in reversed(df.index):
        r = df.loc[y]
        bal = r.receipts - r.payments
        rows.append(
            f"<tr><td>{fy(y)}</td><td>{r.receipts:,.1f}</td><td>{r.payments:,.1f}</td>"
            f'<td class="{"neg" if bal < 0 else "pos"}">{bal:+,.1f}</td>'
            f"<td>{r.gross_debt:,.1f}</td></tr>"
        )
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in build_notes)
    title = "Australian Government receipts, payments and gross debt"
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="Australian Government general government sector receipts, payments and gross debt, {fy(first)} to {fy(last)}, from the Final Budget Outcome historical tables.">
<style>
  :root {{ --ink:#1d2330; --muted:#5b6475; --line:#e3e6ec; --bg:#fafbfc; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
  main {{ max-width:1100px; margin:0 auto; padding:32px 20px 56px; }}
  h1 {{ font-size:1.6rem; line-height:1.25; margin:0 0 6px; }}
  h2 {{ font-size:1.15rem; margin:36px 0 10px; }}
  .sub {{ color:var(--muted); margin:0 0 20px; }}
  .toggle {{ display:flex; gap:0; margin:0 0 10px; }}
  .toggle button {{ font:inherit; font-size:.9rem; padding:6px 14px; border:1px solid #c8ced8; background:#fff; cursor:pointer; color:var(--ink); }}
  .toggle button:first-child {{ border-radius:6px 0 0 6px; }}
  .toggle button:last-child {{ border-radius:0 6px 6px 0; border-left:none; }}
  .toggle button[aria-pressed="true"] {{ background:#1d2330; color:#fff; border-color:#1d2330; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:.92rem; font-variant-numeric:tabular-nums; }}
  th, td {{ padding:7px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ background:#f3f5f8; font-weight:600; position:sticky; top:0; }}
  .neg {{ color:#b00020; }} .pos {{ color:#1b7a3a; }}
  .table-wrap {{ max-height:420px; overflow:auto; border:1px solid var(--line); border-radius:10px; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; }}
  a {{ color:#1f5fbf; }}
  footer {{ margin-top:36px; color:var(--muted); font-size:.85rem; }}
  @media (max-width:600px) {{
    main {{ padding:20px 12px 40px; }} h1 {{ font-size:1.3rem; }}
    table {{ font-size:.8rem; }} th, td {{ padding:6px 6px; }} .card {{ padding:4px; }}
  }}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
  <p class="sub">General government sector, {fy(first)} to {fy(last)}, in nominal dollars.
  Lines show annual flows (left axis); bars show gross debt at the end of each year (right axis).
  Source: {html.escape(source)}. Page last rebuilt {built}.</p>

  <div class="toggle" role="group" aria-label="Accounting basis">
    <button type="button" data-basis="cash" aria-pressed="true">Cash: receipts and payments</button>
    <button type="button" data-basis="accrual" aria-pressed="false">Accrual: revenue and expenses</button>
  </div>
  <div class="card">{chart}</div>
  <script>
    document.querySelectorAll(".toggle button").forEach(function (btn) {{
      btn.addEventListener("click", function () {{
        var cash = btn.dataset.basis === "cash";
        var gd = document.querySelector(".plotly-graph-div");
        Plotly.restyle(gd, {{visible: [true, cash, cash, !cash, !cash]}});
        document.querySelectorAll(".toggle button").forEach(function (b) {{
          b.setAttribute("aria-pressed", String(b === btn));
        }});
      }});
    }});
  </script>

  <h2>The figures ($ billion)</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Year</th><th>Receipts</th><th>Payments</th><th>Balance</th><th>Gross debt</th></tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
  </div>

  <h2>Notes on the data</h2>
  <ul>
    <li><strong>Cash view (default):</strong> receipts and payments of the Australian
    Government general government sector, the basis of the headline underlying cash
    balance. The table's balance column is receipts less payments, which equals the underlying cash balance except between
    2005–06 and 2019–20, when net Future Fund earnings are also deducted.</li>
    <li><strong>Accrual view:</strong> revenue and expenses, recorded when earned or
    incurred rather than when cash changes hands. The difference is the net operating
    balance. Accrual data begin in 1996–97.</li>
    <li><strong>Gross debt</strong> is the face value of Australian Government Securities
    (AGS) on issue at 30 June, including a small amount held on behalf of the states and
    the Northern Territory. Net debt, which deducts financial assets, is lower and is not
    shown.</li>
    <li>Treasury revises the historical series in each edition to improve comparability
    over time (for example for accounting classification changes), so figures can differ
    slightly from those first published for a year.</li>
    <li>All values are nominal, not adjusted for inflation or the size of the economy.</li>
    {notes}
  </ul>

  <h2>Sources</h2>
  <ul>
    <li>Australian Government, <a href="https://budget.gov.au/">budget.gov.au</a>:
    Final Budget Outcome, Appendix B, Historical Australian Government Data
    (Tables B.1, B.5 and B.6), and Budget Paper No. 1, Historical Australian Government Data.</li>
  </ul>

  <footer>Data © Commonwealth of Australia, used under CC BY 4.0. Charting and
  calculations are our own and are not endorsed by the Australian Government.
  Checked monthly for a new Final Budget Outcome.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    full, source = load_data()
    df = full.dropna(subset=["receipts", "payments", "gross_debt"]).iloc[-YEARS_SHOWN:]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build_page(df, source), encoding="utf-8")
    print(f"{df.index[0]} to {df.index[-1]} from {source}")
    print(df.tail(3).round(1).to_string())
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        sys.exit(1)
