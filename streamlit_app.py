import streamlit as st
import anthropic
import json
import base64
import math
from datetime import datetime
from fpdf import FPDF
try:
    import numpy_financial as npf
except ImportError:
    npf = None

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MF Deal Screener",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── STYLES ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  .header-bar {
    background: linear-gradient(135deg, #0f2942 0%, #1a4a7a 100%);
    padding: 1.4rem 2rem;
    border-radius: 10px;
    margin-bottom: 1.5rem;
    color: white;
  }
  .header-bar h1 { color: white !important; margin: 0; font-size: 1.6rem; font-weight: 700; }
  .header-bar p  { color: #a8c4e0 !important; margin: 0.2rem 0 0; font-size: 0.85rem; }

  .verdict-pass  { background: #d4edda; border-left: 5px solid #28a745; padding: 1rem 1.2rem; border-radius: 6px; color: #155724 !important; }
  .verdict-watch { background: #fff3cd; border-left: 5px solid #ffc107; padding: 1rem 1.2rem; border-radius: 6px; color: #333 !important; }
  .verdict-pass  { background: #d4edda; border-left: 5px solid #28a745; padding: 1rem 1.2rem; border-radius: 6px; color: #155724 !important; }
  .verdict-fail  { background: #f8d7da; border-left: 5px solid #dc3545; padding: 1rem 1.2rem; border-radius: 6px; color: #721c24 !important; }

  .kpi-card {
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 0.9rem 1rem;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }
  .kpi-label { font-size: 0.72rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; margin-bottom: 0.3rem; }
  .kpi-value { font-size: 1.35rem; font-weight: 700; color: #0f2942; }
  .kpi-sub   { font-size: 0.75rem; color: #94a3b8; margin-top: 0.15rem; }

  .flag-red   { color: #dc3545; font-weight: 600; }
  .flag-green { color: #28a745; font-weight: 600; }
  .flag-amber { color: #f59e0b; font-weight: 600; }

  .section-label {
    font-size: 0.78rem; font-weight: 700; color: #64748b;
    text-transform: uppercase; letter-spacing: 0.06em;
    border-bottom: 2px solid #e2e8f0; padding-bottom: 0.3rem;
    margin: 1.2rem 0 0.7rem;
  }
  .scenario-bar {
    display: flex; gap: 0.5rem; margin-bottom: 1rem;
  }
  .stDataFrame thead { background: #f1f5f9 !important; }

  /* sensitivity table cells */
  .sens-table td, .sens-table th {
    text-align: center; padding: 0.4rem 0.6rem; font-size: 0.82rem;
    border: 1px solid #e2e8f0;
  }
  .sens-table th { background: #f1f5f9; font-weight: 700; }
  .sens-high { background: #d4edda; }
  .sens-mid  { background: #fff3cd; }
  .sens-low  { background: #f8d7da; }
</style>
""", unsafe_allow_html=True)

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def clean(text):
    """Force any string to safe latin-1 for fpdf/Helvetica. Call on everything going into PDF."""
    if text is None: return "-"
    text = str(text)
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2015": "-",
        "\u2018": "'", "\u2019": "'", "\u201a": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"',
        "\u2022": "*", "\u2023": "*", "\u2043": "-",
        "\u2026": "...", "\u00b7": "*",
        "\u2192": "->", "\u2190": "<-", "\u2194": "<->",
        "\u2713": "OK", "\u2714": "OK", "\u2717": "X", "\u2718": "X",
        "\u25b3": "^", "\u25bc": "v", "\u25ba": ">",
        "\u00a0": " ",
    }
    for char, rep in replacements.items():
        text = text.replace(char, rep)
    return text.encode("latin-1", errors="replace").decode("latin-1")

def fmt_d(v):
    if v is None: return "-"
    try: return f"${float(v):,.0f}"
    except: return str(v)

def fmt_pct(v):
    if v is None: return "-"
    try: return f"{float(v)*100:.1f}%"
    except: return str(v)

def fmt_x(v):
    if v is None: return "-"
    try: return f"{float(v):.2f}x"
    except: return str(v)

def safe(v, default=0.0):
    try: return float(v) if v is not None else default
    except: return default

def fmt_pct_cap(v):
    """Cap rates may come back as 0.071 or 7.1 — normalize both."""
    if v is None: return "-"
    try:
        f = float(v)
        if f > 1: f = f / 100  # already in percentage form
        return f"{f*100:.1f}%"
    except: return str(v)

def irr_calc(cashflows):
    if npf is None: return None
    try:
        r = npf.irr(cashflows)
        if r is None or math.isnan(r) or math.isinf(r): return None
        return r
    except: return None

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior multifamily acquisitions analyst. Your job is to screen Offering Memoranda quickly for first-cut investment decisions.

Analyze the uploaded document(s) — which may include an OM, rent roll, and/or T-12 operating statement — and return a single valid JSON object with the structure below.

If a value cannot be found, use null. Do not add markdown, backticks, or commentary outside the JSON.

{
  "property": {
    "name": "string",
    "address": "string",
    "city_state": "string",
    "year_built": number_or_null,
    "units": number,
    "unit_mix": [{"type":"1BR/1BA","count":10,"avg_sf":750,"avg_market_rent":1200}],
    "total_sf": number_or_null,
    "stories": number_or_null,
    "asking_price": number,
    "price_per_unit": number_or_null,
    "price_per_sf": number_or_null
  },
  "income": {
    "gross_potential_rent_annual": number_or_null,
    "current_occupied_rent_annual": number_or_null,
    "physical_vacancy_pct": number_or_null,
    "economic_vacancy_pct": number_or_null,
    "other_income_annual": number_or_null,
    "t3_noi": number_or_null,
    "t6_noi": number_or_null,
    "t12_noi": number_or_null,
    "broker_projected_noi": number_or_null,
    "in_place_cap_rate": number_or_null,
    "broker_pro_forma_cap_rate": number_or_null
  },
  "expenses": {
    "t12_opex_total": number_or_null,
    "t12_opex_per_unit": number_or_null,
    "taxes": number_or_null,
    "insurance": number_or_null,
    "utilities": number_or_null,
    "repairs_maintenance": number_or_null,
    "management_fee_pct": number_or_null,
    "payroll": number_or_null,
    "admin_marketing": number_or_null,
    "capex_reserves_per_unit": number_or_null,
    "broker_expense_ratio": number_or_null,
    "analyst_normalized_opex_per_unit": number_or_null,
    "expense_notes": "any suspicious or missing line items, broker adjustments you noticed"
  },
  "debt": {
    "suggested_ltv": number_or_null,
    "estimated_loan_amount": number_or_null,
    "assumed_interest_rate": number_or_null,
    "assumed_amortization_years": number_or_null,
    "debt_constant": number_or_null,
    "dscr_in_place": number_or_null
  },
  "replacement_cost": {
    "land_value_est": number_or_null,
    "hard_cost_per_sf_est": number,
    "soft_cost_pct": number,
    "estimated_replacement_cost": number_or_null,
    "discount_to_replacement_pct": number_or_null,
    "notes": "string"
  },
  "verdict": {
    "recommendation": "PROCEED / WATCH / PASS",
    "confidence": "High / Medium / Low",
    "one_liner": "25-word max summary",
    "key_positives": ["string","string","string"],
    "key_concerns": ["string","string","string"],
    "red_flags": ["string"],
    "further_diligence": ["string"]
  }
}"""

# ─── EXTRACTION ───────────────────────────────────────────────────────────────
def extract_om(files_bytes_list):
    """files_bytes_list: list of (filename, bytes)"""
    client = anthropic.Anthropic(api_key=st.secrets.get("ANTHROPIC_API_KEY", ""))
    content = []
    for fname, fbytes in files_bytes_list:
        b64 = base64.standard_b64encode(fbytes).decode("utf-8")
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            "title": fname,
        })
    content.append({"type": "text", "text": "Analyze these documents and return the JSON as instructed."})

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)

# ─── FINANCIAL MODEL ──────────────────────────────────────────────────────────
def build_model(d, scenario, overrides=None):
    ov = overrides or {}

    # ── base assumptions by scenario ──
    rent_growth_map  = {"Bear": 0.01,  "Base": 0.03,  "Bull": 0.045}
    expense_growth   = {"Bear": 0.035, "Base": 0.025, "Bull": 0.02}
    vacancy_map      = {"Bear": 0.10,  "Base": 0.07,  "Bull": 0.05}
    exit_cap_map     = {"Bear": 0.065, "Base": 0.055, "Bull": 0.048}
    hold_years       = int(ov.get("hold_years", 10))
    purchase_price   = safe(ov.get("purchase_price") or d["property"].get("asking_price"), 0)
    ltv              = safe(ov.get("ltv", d["debt"].get("suggested_ltv", 0.65)))
    interest_rate    = safe(ov.get("interest_rate") or d["debt"].get("assumed_interest_rate", 0.065))
    amort            = int(ov.get("amort", d["debt"].get("assumed_amortization_years", 30) or 30))
    rent_growth      = safe(ov.get("rent_growth", rent_growth_map[scenario]))
    exp_growth       = safe(ov.get("exp_growth", expense_growth[scenario]))
    vacancy          = safe(ov.get("vacancy", vacancy_map[scenario]))
    exit_cap         = safe(ov.get("exit_cap", exit_cap_map[scenario]))
    capex_reserve    = safe(ov.get("capex_reserve") or d["expenses"].get("capex_reserves_per_unit", 350), 350)
    units            = safe(d["property"].get("units", 0))

    # ── starting NOI (prefer T12, fallback to broker, then GPR-based estimate) ──
    t12_noi = safe(d["income"].get("t12_noi") or d["income"].get("broker_projected_noi"))
    if t12_noi == 0:
        gpr = safe(d["income"].get("gross_potential_rent_annual"))
        t12_noi = gpr * (1 - vacancy) * 0.55  # rough 45% expense ratio fallback

    # ── debt ──
    loan = purchase_price * ltv
    equity = purchase_price - loan
    if interest_rate > 0 and amort > 0 and loan > 0:
        monthly_r = interest_rate / 12
        n_months  = amort * 12
        monthly_pmt = loan * (monthly_r * (1 + monthly_r)**n_months) / ((1 + monthly_r)**n_months - 1)
        annual_ds   = monthly_pmt * 12
    else:
        annual_ds = 0

    # ── annual cash flows ──
    cash_flows = []
    balance = loan
    annual_r = interest_rate
    for yr in range(1, hold_years + 1):
        noi     = t12_noi * ((1 + rent_growth) ** yr)
        opex    = noi * 0 # NOI already net
        capex   = capex_reserve * units
        noi_net = noi - capex

        # amortization for balance tracking
        if amort > 0 and loan > 0 and annual_ds > 0:
            interest_paid = balance * annual_r
            principal_paid = annual_ds - interest_paid
            balance = max(0, balance - principal_paid)
        else:
            interest_paid = 0
            principal_paid = 0

        cof_levered = noi_net - annual_ds
        dscr = noi_net / annual_ds if annual_ds > 0 else None
        coc  = cof_levered / equity if equity > 0 else None

        cash_flows.append({
            "Year": yr,
            "NOI": round(noi),
            "CapEx Reserve": round(capex),
            "NOI (net)": round(noi_net),
            "Debt Service": round(annual_ds),
            "CF (levered)": round(cof_levered),
            "DSCR": round(dscr, 2) if dscr else None,
            "CoC": round(coc * 100, 1) if coc else None,
            "Loan Balance": round(balance),
        })

    # ── exit ──
    exit_noi         = cash_flows[-1]["NOI (net)"]
    exit_value       = exit_noi / exit_cap if exit_cap > 0 else 0
    net_proceeds     = exit_value - balance - purchase_price * 0.015  # 1.5% selling costs

    # ── IRR ──
    cf_series = [-equity] + [r["CF (levered)"] for r in cash_flows]
    cf_series[-1] += net_proceeds + balance  # add back equity
    irr = irr_calc(cf_series)

    # ── equity multiple ──
    total_in  = equity
    total_out = sum(max(r["CF (levered)"], 0) for r in cash_flows) + max(net_proceeds + balance, 0)
    em        = total_out / total_in if total_in > 0 else None

    # ── in-place metrics ──
    in_place_cap = t12_noi / purchase_price if purchase_price > 0 else None
    dscr_yr1 = cash_flows[0]["DSCR"]

    return {
        "scenario": scenario,
        "purchase_price": purchase_price,
        "equity": equity,
        "loan": loan,
        "ltv": ltv,
        "interest_rate": interest_rate,
        "annual_ds": annual_ds,
        "t12_noi": t12_noi,
        "in_place_cap": in_place_cap,
        "exit_cap": exit_cap,
        "exit_value": exit_value,
        "irr": irr,
        "equity_multiple": em,
        "dscr_yr1": dscr_yr1,
        "cash_flows": cash_flows,
        "rent_growth": rent_growth,
        "vacancy": vacancy,
        "hold_years": hold_years,
    }

# ─── SENSITIVITY TABLES ───────────────────────────────────────────────────────
def sensitivity_irr_vs_exitcap(base_m, ov):
    """IRR sensitivity: exit cap (rows) vs purchase price (cols)"""
    pp = base_m["purchase_price"]
    price_deltas = [-0.10, -0.05, 0, 0.05, 0.10]
    cap_rates    = [0.040, 0.045, 0.050, 0.055, 0.060, 0.065, 0.070]

    prices = [pp * (1 + d) for d in price_deltas]
    rows = []
    for ec in cap_rates:
        row = {"Exit Cap →": f"{ec*100:.1f}%"}
        for pp_adj in prices:
            adj_ov = dict(ov, purchase_price=pp_adj, exit_cap=ec)
            m = build_model(_last_data, "Base", adj_ov)
            irr = m["irr"]
            row[f"${pp_adj/1e6:.1f}M"] = f"{irr*100:.1f}%" if irr else "-"
        rows.append(row)
    return rows, [f"${p/1e6:.1f}M" for p in prices]

def sensitivity_coc_vs_vacancy(base_m, ov):
    """CoC sensitivity: vacancy (rows) vs rent growth (cols)"""
    vacancies = [0.04, 0.06, 0.08, 0.10, 0.12]
    rent_growths = [0.01, 0.02, 0.03, 0.04, 0.05]
    rows = []
    for v in vacancies:
        row = {"Vacancy →": f"{v*100:.0f}%"}
        for rg in rent_growths:
            adj_ov = dict(ov, vacancy=v, rent_growth=rg)
            m = build_model(_last_data, "Base", adj_ov)
            coc = m["cash_flows"][0]["CoC"]
            row[f"Rent {rg*100:.0f}%"] = f"{coc:.1f}%" if coc else "-"
        rows.append(row)
    return rows, [f"Rent {rg*100:.0f}%" for rg in rent_growths]

def sensitivity_dscr_vs_rate(base_m, ov):
    """DSCR sensitivity: interest rate (rows) vs LTV (cols)"""
    rates = [0.055, 0.060, 0.065, 0.070, 0.075, 0.080]
    ltvs  = [0.55, 0.60, 0.65, 0.70, 0.75]
    rows = []
    for r in rates:
        row = {"Rate →": f"{r*100:.1f}%"}
        for ltv in ltvs:
            adj_ov = dict(ov, interest_rate=r, ltv=ltv)
            m = build_model(_last_data, "Base", adj_ov)
            dscr = m["dscr_yr1"]
            row[f"LTV {ltv*100:.0f}%"] = f"{dscr:.2f}" if dscr else "-"
        rows.append(row)
    return rows, [f"LTV {ltv*100:.0f}%" for ltv in ltvs]

# ─── PDF EXPORT ───────────────────────────────────────────────────────────────
def generate_pdf_memo(d, models, overrides):
    class PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(100, 100, 100)
            self.cell(0, 6, "MULTIFAMILY DEAL SCREENING MEMO - CONFIDENTIAL", align="R")
            self.ln(4)
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(150, 150, 150)
            self.cell(0, 5, f"Page {self.page_no()} | Generated {datetime.now().strftime('%B %d, %Y')}", align="C")

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(16, 16, 16)

    prop   = d.get("property", {})
    income = d.get("income", {})
    exp    = d.get("expenses", {})
    debt_d = d.get("debt", {})
    rc     = d.get("replacement_cost", {})
    verdict = d.get("verdict", {})
    base_m = models.get("Base", {})
    bear_m = models.get("Bear", {})
    bull_m = models.get("Bull", {})

    # ── COVER / HEADER ──
    pdf.add_page()
    pdf.set_fill_color(15, 41, 66)
    pdf.rect(0, 0, 210, 38, "F")
    pdf.set_y(8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, clean(prop.get("name", "Property")), align="L")
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(168, 196, 224)
    pdf.cell(0, 5, clean(f"{prop.get('address','')}  |  {prop.get('city_state','')}  |  {prop.get('units','?')} Units  |  Built {prop.get('year_built','?')}"), align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(44)

    # ── VERDICT BOX ──
    rec = str(verdict.get("recommendation", "")).upper().strip()
    color_map = {"PROCEED": (212, 237, 218, 40, 167, 69),
                 "WATCH":   (255, 243, 205, 180, 130, 0),
                 "PASS":    (248, 215, 218, 220, 53, 69)}
    bg = color_map.get(rec, (240, 240, 240, 100, 100, 100))
    pdf.set_fill_color(bg[0], bg[1], bg[2])
    pdf.rect(16, 44, 178, 28, "F")
    pdf.set_y(47)
    pdf.set_x(16)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(bg[3], bg[4], bg[5])
    one_liner = clean(verdict.get('one_liner', ''))
    pdf.multi_cell(178, 6, clean(f"VERDICT: {rec}  -  {one_liner}"), align="C")
    pdf.set_x(16)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(178, 5, clean(f"Confidence: {verdict.get('confidence','')}  |  Asking: {fmt_d(prop.get('asking_price'))}  |  {fmt_d(prop.get('price_per_unit'))}/unit"), align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(76)

    def section(title):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(15, 41, 66)
        pdf.cell(0, 5, clean(title.upper()), ln=True)
        pdf.set_draw_color(15, 41, 66)
        pdf.set_line_width(0.4)
        pdf.line(16, pdf.get_y(), 194, pdf.get_y())
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    def kv(label, value, col_w=88, indent=0):
        pdf.set_x(16 + indent)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_w - indent, 5, clean(label))
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, clean(str(value)), ln=True)

    def bullet(text, flag="+", color=(60, 60, 60)):
        pdf.set_x(20)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*color)
        pdf.multi_cell(168, 4.5, clean(f"{flag}  {text}"))
        pdf.set_text_color(0, 0, 0)

    # ── KEY METRICS ──
    section("Key Metrics at a Glance")
    col = 88
    pdf.set_font("Helvetica", "", 8)

    left_items = [
        ("Asking Price", fmt_d(prop.get("asking_price"))),
        ("Price / Unit", fmt_d(prop.get("price_per_unit"))),
        ("Price / SF", fmt_d(prop.get("price_per_sf"))),
        ("T-12 NOI", fmt_d(income.get("t12_noi"))),
        ("T-6 NOI (ann.)", fmt_d(income.get("t6_noi"))),
        ("T-3 NOI (ann.)", fmt_d(income.get("t3_noi"))),
        ("In-Place Cap Rate", fmt_pct(base_m.get("in_place_cap"))),
        ("Broker Pro Forma Cap", fmt_pct_cap(income.get("broker_pro_forma_cap_rate"))),
    ]
    right_items = [
        ("Base IRR (10yr)",  fmt_pct(base_m.get("irr"))),
        ("Bear IRR (10yr)",  fmt_pct(bear_m.get("irr"))),
        ("Bull IRR (10yr)",  fmt_pct(bull_m.get("irr"))),
        ("Equity Multiple",  fmt_x(base_m.get("equity_multiple"))),
        ("Base CoC (Yr 1)",  f"{base_m.get('cash_flows',[{}])[0].get('CoC','-')}%" if base_m.get("cash_flows") else "-"),
        ("DSCR (Yr 1)",      str(round(base_m.get("dscr_yr1") or 0, 2))),
        ("LTV",              fmt_pct(base_m.get("ltv"))),
        ("Loan Amount",      fmt_d(base_m.get("loan"))),
    ]
    y_start = pdf.get_y()
    for i, (lbl, val) in enumerate(left_items):
        pdf.set_xy(16, y_start + i * 5.5)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.cell(50, 5, lbl)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(38, 5, val)
    for i, (lbl, val) in enumerate(right_items):
        pdf.set_xy(106, y_start + i * 5.5)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.cell(50, 5, lbl)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(38, 5, val)
    pdf.set_y(y_start + len(left_items) * 5.5 + 4)

    # ── TRAILING NOI ANALYSIS ──
    section("Trailing NOI Analysis")
    t_data = [
        ("T-3 NOI (annualized)", income.get("t3_noi")),
        ("T-6 NOI (annualized)", income.get("t6_noi")),
        ("T-12 NOI",             income.get("t12_noi")),
        ("Broker Pro Forma NOI", income.get("broker_projected_noi")),
    ]
    for lbl, val in t_data:
        kv(lbl, fmt_d(val))

    if income.get("t12_noi") and income.get("broker_projected_noi"):
        uplift = (safe(income["broker_projected_noi"]) - safe(income["t12_noi"])) / safe(income["t12_noi"]) * 100
        pdf.set_x(16)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 5, clean(f"  Broker NOI uplift vs T-12: {uplift:+.1f}% - verify assumptions"), ln=True)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    # ── EXPENSE NORMALIZATION ──
    section("Expense Normalization")
    exp_items = [
        ("Real Estate Taxes",       exp.get("taxes")),
        ("Insurance",               exp.get("insurance")),
        ("Utilities",               exp.get("utilities")),
        ("Repairs & Maintenance",   exp.get("repairs_maintenance")),
        ("Payroll",                 exp.get("payroll")),
        ("Admin / Marketing",       exp.get("admin_marketing")),
        ("CapEx Reserves / Unit",   exp.get("capex_reserves_per_unit")),
        ("Mgmt Fee",                f"{fmt_pct(exp.get('management_fee_pct'))} of EGI" if exp.get("management_fee_pct") else "-"),
    ]
    for lbl, val in exp_items:
        if isinstance(val, (int, float)):
            kv(lbl, fmt_d(val))
        else:
            kv(lbl, str(val) if val else "-")
    kv("T-12 Total OpEx / Unit",       fmt_d(exp.get("t12_opex_per_unit")))
    kv("Analyst Normalized / Unit",    fmt_d(exp.get("analyst_normalized_opex_per_unit")))
    if exp.get("expense_notes"):
        pdf.set_x(16); pdf.set_font("Helvetica", "I", 7.5); pdf.set_text_color(100,100,100)
        pdf.multi_cell(178, 4.5, clean(f"Notes: {exp.get('expense_notes','')}"))
        pdf.set_text_color(0,0,0)
    pdf.ln(2)

    # ── REPLACEMENT COST ──
    section("Replacement Cost Analysis")
    kv("Hard Cost Est. / SF",       fmt_d(rc.get("hard_cost_per_sf_est")))
    kv("Soft Cost %",               fmt_pct(rc.get("soft_cost_pct")))
    kv("Land Value Est.",           fmt_d(rc.get("land_value_est")))
    kv("Est. Replacement Cost",     fmt_d(rc.get("estimated_replacement_cost")))
    kv("Discount to Replacement",   fmt_pct(rc.get("discount_to_replacement_pct")))
    if rc.get("notes"):
        pdf.set_x(16); pdf.set_font("Helvetica", "I", 7.5); pdf.set_text_color(100,100,100)
        pdf.multi_cell(178, 4.5, clean(rc.get("notes","")))
        pdf.set_text_color(0,0,0)
    pdf.ln(2)

    # ── 10-YEAR CASH FLOW (BASE) ──
    pdf.add_page()
    section("10-Year Levered Cash Flow - Base Scenario")

    headers = ["Yr", "NOI", "CapEx", "NOI Net", "Debt Svc", "CF Levered", "DSCR", "CoC%"]
    widths  = [10, 27, 22, 27, 22, 27, 16, 15]
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(241, 245, 249)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for row in base_m.get("cash_flows", []):
        vals = [
            str(row["Year"]),
            fmt_d(row["NOI"]),
            fmt_d(row["CapEx Reserve"]),
            fmt_d(row["NOI (net)"]),
            fmt_d(row["Debt Service"]),
            fmt_d(row["CF (levered)"]),
            str(row["DSCR"]) if row["DSCR"] else "-",
            f"{row['CoC']}%" if row["CoC"] else "-",
        ]
        for val, w in zip(vals, widths):
            pdf.cell(w, 5.5, clean(val), border=1, align="C")
        pdf.ln()
    pdf.ln(3)

    # ── SCENARIO COMPARISON ──
    section("Bear / Base / Bull Scenario Returns")
    s_headers = ["Metric", "Bear", "Base", "Bull"]
    s_widths  = [55, 40, 40, 40]
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_fill_color(241, 245, 249)
    for h, w in zip(s_headers, s_widths):
        pdf.cell(w, 6, clean(h), border=1, fill=True, align="C")
    pdf.ln()
    scenario_rows = [
        ("Rent Growth",     fmt_pct(bear_m.get("rent_growth")),  fmt_pct(base_m.get("rent_growth")),  fmt_pct(bull_m.get("rent_growth"))),
        ("Vacancy",         fmt_pct(bear_m.get("vacancy")),       fmt_pct(base_m.get("vacancy")),       fmt_pct(bull_m.get("vacancy"))),
        ("Exit Cap",        fmt_pct(bear_m.get("exit_cap")),      fmt_pct(base_m.get("exit_cap")),      fmt_pct(bull_m.get("exit_cap"))),
        ("Levered IRR",     fmt_pct(bear_m.get("irr")),           fmt_pct(base_m.get("irr")),           fmt_pct(bull_m.get("irr"))),
        ("Equity Multiple", fmt_x(bear_m.get("equity_multiple")), fmt_x(base_m.get("equity_multiple")), fmt_x(bull_m.get("equity_multiple"))),
        ("Exit Value",      fmt_d(bear_m.get("exit_value")),      fmt_d(base_m.get("exit_value")),      fmt_d(bull_m.get("exit_value"))),
    ]
    pdf.set_font("Helvetica", "", 7.5)
    for r in scenario_rows:
        for val, w in zip(r, s_widths):
            pdf.cell(w, 5.5, clean(val), border=1, align="C")
        pdf.ln()
    pdf.ln(3)

    # ── VERDICT / DILIGENCE ──
    section("Verdict & Next Steps")
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, clean(f"Recommendation: {verdict.get('recommendation','')}  (Confidence: {verdict.get('confidence','')})"), ln=True)
    pdf.ln(1)
    if verdict.get("key_positives"):
        pdf.set_font("Helvetica", "B", 8); pdf.cell(0, 5, "Positives:", ln=True)
        for item in verdict.get("key_positives", []):
            bullet(item, "+", (40, 167, 69))
    if verdict.get("key_concerns"):
        pdf.set_font("Helvetica", "B", 8); pdf.cell(0, 5, "Concerns:", ln=True)
        for item in verdict.get("key_concerns", []):
            bullet(item, "!", (200, 120, 0))
    if verdict.get("red_flags"):
        pdf.set_font("Helvetica", "B", 8); pdf.cell(0, 5, "Red Flags:", ln=True)
        for item in verdict.get("red_flags", []):
            bullet(item, "X", (220, 53, 69))
    if verdict.get("further_diligence"):
        pdf.set_font("Helvetica", "B", 8); pdf.cell(0, 5, "Further Diligence:", ln=True)
        for item in verdict.get("further_diligence", []):
            bullet(item, "->", (30, 100, 170))

    return pdf.output()

# ─── MAIN APP ─────────────────────────────────────────────────────────────────
_last_data = {}

st.markdown("""
<div class="header-bar">
  <h1>🏢 Multifamily Deal Screener</h1>
  <p>First-cut screening memo — upload OM + rent roll + T-12 for instant analysis</p>
</div>
""", unsafe_allow_html=True)

# session init
for key in ["data", "models", "overrides"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ──────────────────────────────────────────────────
#  SIDEBAR — Assumption Overrides
# ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Model Assumptions")

    scenario = st.radio("Scenario", ["Bear", "Base", "Bull"], index=1, horizontal=True)

    st.markdown("**Deal Structure**")
    pp_override = st.number_input("Purchase Price ($)", value=0, step=100000, format="%d",
                                   help="0 = use asking price from OM")
    ltv         = st.slider("LTV", 0.50, 0.80, 0.65, 0.01, format="%.2f")
    interest_r  = st.slider("Interest Rate", 0.04, 0.10, 0.065, 0.0025, format="%.3f")
    amort       = st.selectbox("Amortization", [25, 30], index=1)
    hold_yrs    = st.selectbox("Hold Period (yrs)", [5, 7, 10], index=2)

    st.markdown("**Operating Assumptions**")
    rent_g      = st.slider("Rent Growth (annual)", 0.00, 0.07, 0.03, 0.005, format="%.3f")
    vacancy     = st.slider("Vacancy", 0.02, 0.15, 0.07, 0.005, format="%.3f")
    exit_cap    = st.slider("Exit Cap Rate", 0.035, 0.09, 0.055, 0.0025, format="%.4f")
    capex_res   = st.number_input("CapEx Reserve / Unit / Yr ($)", value=350, step=25)

    overrides = {
        "purchase_price": pp_override if pp_override > 0 else None,
        "ltv": ltv, "interest_rate": interest_r,
        "amort": amort, "hold_years": hold_yrs,
        "rent_growth": rent_g, "vacancy": vacancy,
        "exit_cap": exit_cap, "capex_reserve": capex_res,
    }
    st.session_state.overrides = overrides

    st.markdown("---")
    if st.session_state.data:
        if st.button("🔄 Reset / New Deal", use_container_width=True):
            st.session_state.data   = None
            st.session_state.models = None
            st.rerun()

# ──────────────────────────────────────────────────
#  UPLOAD
# ──────────────────────────────────────────────────
if st.session_state.data is None:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("#### Upload Deal Documents")
        files = st.file_uploader(
            "Drop OM, Rent Roll, T-12 (PDFs)",
            type=["pdf"],
            accept_multiple_files=True,
        )
        if files and st.button("🔍 Screen This Deal", type="primary", use_container_width=True):
            with st.status("Screening deal...", expanded=True) as status:
                st.write("📤 Sending to Claude...")
                try:
                    file_list = [(f.name, f.read()) for f in files]
                    data = extract_om(file_list)
                    _last_data.update(data)
                    st.write("✅ Extracted — running models...")
                    ov = st.session_state.overrides
                    models = {
                        sc: build_model(data, sc, ov)
                        for sc in ["Bear", "Base", "Bull"]
                    }
                    st.session_state.data   = data
                    st.session_state.models = models
                    status.update(label="Done!", state="complete")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

# ──────────────────────────────────────────────────
#  RESULTS
# ──────────────────────────────────────────────────
else:
    data    = st.session_state.data
    _last_data.update(data)
    ov      = st.session_state.overrides or {}

    # recompute on assumption changes
    models = {sc: build_model(data, sc, ov) for sc in ["Bear", "Base", "Bull"]}
    m       = models[scenario]

    prop    = data.get("property", {})
    income  = data.get("income", {})
    exp     = data.get("expenses", {})
    debt_d  = data.get("debt", {})
    rc      = data.get("replacement_cost", {})
    verdict = data.get("verdict", {})
    rec     = str(verdict.get("recommendation","")).upper().strip()

    # ── VERDICT BANNER ──
    color_map = {
        "PROCEED": ("d4edda", "28a745", "155724"),
        "WATCH":   ("fff3cd", "ffc107", "333333"),
        "PASS":    ("f8d7da", "dc3545", "721c24"),
    }
    bg_hex, border_hex, text_hex = color_map.get(rec, ("f0f0f0", "aaaaaa", "333333"))
    st.markdown(f"""
    <div style="background:#{bg_hex};border-left:5px solid #{border_hex};padding:1rem 1.2rem;border-radius:6px;margin-bottom:1rem;">
      <span style="color:#{text_hex};font-weight:700;">VERDICT: {rec}</span>
      <span style="color:#{text_hex};">&nbsp;·&nbsp;{verdict.get('one_liner','')}</span>
      <span style="float:right;font-size:0.8rem;color:#{text_hex};opacity:0.7;">Confidence: {verdict.get('confidence','')}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── TOP KPI ROW ──
    st.markdown('<div class="section-label">Returns at a Glance</div>', unsafe_allow_html=True)
    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    kpi_data = [
        (k1, "Asking Price",    fmt_d(prop.get("asking_price")),      ""),
        (k2, "Price / Unit",    fmt_d(prop.get("price_per_unit")),    ""),
        (k3, "In-Place Cap",    fmt_pct(m.get("in_place_cap")),       ""),
        (k4, f"IRR ({scenario})", fmt_pct(m.get("irr")),              f"{m.get('hold_years',10)}-yr levered"),
        (k5, "Eq. Multiple",   fmt_x(m.get("equity_multiple")),      ""),
        (k6, "DSCR Yr1",       str(round(m.get("dscr_yr1") or 0, 2)), ""),
        (k7, "LTV",            fmt_pct(m.get("ltv")),                ""),
    ]
    for col, label, val, sub in kpi_data:
        with col:
            st.markdown(f"""
            <div class="kpi-card">
              <div class="kpi-label">{label}</div>
              <div class="kpi-value">{val}</div>
              <div class="kpi-sub">{sub}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── TABS ──
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 NOI Analysis", "💰 Cash Flows", "📉 Sensitivity", "📋 Deal Details", "🚩 Flags & Diligence"
    ])

    # ── TAB 1: NOI / TRAILING ANALYSIS ──
    with tab1:
        st.markdown('<div class="section-label">Trailing NOI Windows</div>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        trailing = [
            (c1, "T-3 NOI (ann.)", income.get("t3_noi")),
            (c2, "T-6 NOI (ann.)", income.get("t6_noi")),
            (c3, "T-12 NOI",       income.get("t12_noi")),
            (c4, "Broker Pro Forma NOI", income.get("broker_projected_noi")),
        ]
        for col, label, val in trailing:
            with col:
                st.metric(label, fmt_d(val))

        if income.get("t12_noi") and income.get("broker_projected_noi"):
            uplift = (safe(income["broker_projected_noi"]) - safe(income["t12_noi"])) / safe(income["t12_noi"]) * 100
            color = "🟢" if abs(uplift) < 10 else ("🟡" if abs(uplift) < 20 else "🔴")
            st.info(f"{color} Broker NOI uplift vs T-12: **{uplift:+.1f}%** — {'reasonable' if abs(uplift) < 12 else 'scrutinize closely'}")

        st.markdown('<div class="section-label">Expense Normalization</div>', unsafe_allow_html=True)
        exp_rows = {
            "Real Estate Taxes":     exp.get("taxes"),
            "Insurance":             exp.get("insurance"),
            "Utilities":             exp.get("utilities"),
            "Repairs & Maintenance": exp.get("repairs_maintenance"),
            "Payroll":               exp.get("payroll"),
            "Admin / Marketing":     exp.get("admin_marketing"),
            "Management Fee":        f"{fmt_pct(exp.get('management_fee_pct'))} of EGI" if exp.get("management_fee_pct") else None,
            "CapEx Reserve / Unit":  exp.get("capex_reserves_per_unit"),
        }
        exp_col1, exp_col2 = st.columns(2)
        items = [(k, v) for k, v in exp_rows.items() if v is not None]
        half = len(items) // 2
        with exp_col1:
            for k, v in items[:half]:
                st.metric(k, fmt_d(v) if isinstance(v, (int, float)) else str(v))
        with exp_col2:
            for k, v in items[half:]:
                st.metric(k, fmt_d(v) if isinstance(v, (int, float)) else str(v))

        if exp.get("t12_opex_per_unit") and exp.get("analyst_normalized_opex_per_unit"):
            st.markdown(f"""
            **Broker OpEx/Unit:** {fmt_d(exp.get('t12_opex_per_unit'))} &nbsp;|&nbsp;
            **Analyst Normalized:** {fmt_d(exp.get('analyst_normalized_opex_per_unit'))}
            """)
        if exp.get("expense_notes"):
            st.warning(f"📝 {exp.get('expense_notes')}")

        # Replacement Cost
        st.markdown('<div class="section-label">Replacement Cost</div>', unsafe_allow_html=True)
        rc1, rc2, rc3, rc4 = st.columns(4)
        with rc1: st.metric("Hard Cost / SF", fmt_d(rc.get("hard_cost_per_sf_est")))
        with rc2: st.metric("Land Value Est.", fmt_d(rc.get("land_value_est")))
        with rc3: st.metric("Est. Replacement Cost", fmt_d(rc.get("estimated_replacement_cost")))
        with rc4: st.metric("Discount to Replacement", fmt_pct(rc.get("discount_to_replacement_pct")))
        if rc.get("notes"):
            st.caption(rc.get("notes"))

    # ── TAB 2: CASH FLOWS + SCENARIO COMPARE ──
    with tab2:
        st.markdown(f'<div class="section-label">10-Year Cash Flow — {scenario} Scenario</div>', unsafe_allow_html=True)
        import pandas as pd
        cf_df = pd.DataFrame(m["cash_flows"])
        cf_df["NOI"]        = cf_df["NOI"].apply(fmt_d)
        cf_df["CapEx Reserve"] = cf_df["CapEx Reserve"].apply(fmt_d)
        cf_df["NOI (net)"]  = cf_df["NOI (net)"].apply(fmt_d)
        cf_df["Debt Service"] = cf_df["Debt Service"].apply(fmt_d)
        cf_df["CF (levered)"] = cf_df["CF (levered)"].apply(fmt_d)
        cf_df["DSCR"]       = cf_df["DSCR"].apply(lambda x: str(x) if x else "-")
        cf_df["CoC"]        = cf_df["CoC"].apply(lambda x: f"{x}%" if x else "-")
        cf_df["Loan Balance"] = cf_df["Loan Balance"].apply(fmt_d)
        st.dataframe(cf_df.set_index("Year"), use_container_width=True)

        st.markdown('<div class="section-label">Bear / Base / Bull Scenario Summary</div>', unsafe_allow_html=True)
        sc_df = pd.DataFrame([
            {
                "Scenario": sc,
                "Rent Growth": fmt_pct(models[sc]["rent_growth"]),
                "Vacancy": fmt_pct(models[sc]["vacancy"]),
                "Exit Cap": fmt_pct(models[sc]["exit_cap"]),
                "Levered IRR": fmt_pct(models[sc]["irr"]),
                "Equity Multiple": fmt_x(models[sc]["equity_multiple"]),
                "Exit Value": fmt_d(models[sc]["exit_value"]),
                "DSCR Yr1": str(round(models[sc]["dscr_yr1"] or 0, 2)),
            }
            for sc in ["Bear", "Base", "Bull"]
        ])
        st.dataframe(sc_df.set_index("Scenario"), use_container_width=True)

    # ── TAB 3: SENSITIVITY ──
    with tab3:
        st.markdown('<div class="section-label">Sensitivity Table 1: Levered IRR vs Exit Cap × Purchase Price</div>', unsafe_allow_html=True)
        try:
            rows, cols = sensitivity_irr_vs_exitcap(m, ov)
            import pandas as pd
            df1 = pd.DataFrame(rows).set_index("Exit Cap →")
            st.dataframe(df1, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not compute: {e}")

        st.markdown('<div class="section-label">Sensitivity Table 2: Year 1 CoC vs Vacancy × Rent Growth</div>', unsafe_allow_html=True)
        try:
            rows2, cols2 = sensitivity_coc_vs_vacancy(m, ov)
            df2 = pd.DataFrame(rows2).set_index("Vacancy →")
            st.dataframe(df2, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not compute: {e}")

        st.markdown('<div class="section-label">Sensitivity Table 3: DSCR vs Interest Rate × LTV</div>', unsafe_allow_html=True)
        try:
            rows3, cols3 = sensitivity_dscr_vs_rate(m, ov)
            df3 = pd.DataFrame(rows3).set_index("Rate →")
            st.dataframe(df3, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not compute: {e}")

    # ── TAB 4: DEAL DETAILS ──
    with tab4:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="section-label">Property</div>', unsafe_allow_html=True)
            for label, key in [
                ("Address", "address"), ("City / State", "city_state"),
                ("Year Built", "year_built"), ("Units", "units"),
                ("Total SF", "total_sf"), ("Stories", "stories"),
                ("Asking Price", "asking_price"), ("Price / Unit", "price_per_unit"),
            ]:
                val = prop.get(key)
                if val:
                    st.markdown(f"**{label}:** {fmt_d(val) if isinstance(val,(int,float)) and label not in ['Year Built','Units','Stories'] else val}")

            st.markdown('<div class="section-label">Unit Mix</div>', unsafe_allow_html=True)
            mix = prop.get("unit_mix", [])
            if mix:
                import pandas as pd
                mix_df = pd.DataFrame(mix)
                st.dataframe(mix_df, use_container_width=True)

        with c2:
            st.markdown('<div class="section-label">Income</div>', unsafe_allow_html=True)
            st.metric("GPR (Annual)", fmt_d(income.get("gross_potential_rent_annual")))
            st.metric("Physical Vacancy", fmt_pct(income.get("physical_vacancy_pct")))
            st.metric("Other Income", fmt_d(income.get("other_income_annual")))
            st.metric("In-Place Cap Rate", fmt_pct_cap(income.get("in_place_cap_rate")))

            st.markdown('<div class="section-label">Debt Assumptions</div>', unsafe_allow_html=True)
            st.metric("Loan Amount", fmt_d(m.get("loan")))
            st.metric("Interest Rate", fmt_pct(m.get("interest_rate")))
            st.metric("Annual Debt Service", fmt_d(m.get("annual_ds")))

    # ── TAB 5: FLAGS & DILIGENCE ──
    with tab5:
        st.markdown("**✅ Key Positives**")
        for item in verdict.get("key_positives", []):
            st.markdown(f'<span class="flag-green">✓</span> {item}', unsafe_allow_html=True)

        st.markdown("<br>**⚠️ Key Concerns**", unsafe_allow_html=True)
        for item in verdict.get("key_concerns", []):
            st.markdown(f'<span class="flag-amber">△</span> {item}', unsafe_allow_html=True)

        st.markdown("<br>**🔴 Red Flags**", unsafe_allow_html=True)
        flags = verdict.get("red_flags", [])
        if flags:
            for f in flags:
                st.markdown(f'<span class="flag-red">✗</span> {f}', unsafe_allow_html=True)
        else:
            st.markdown("None identified.")

        st.markdown("<br>**→ Further Diligence Needed**", unsafe_allow_html=True)
        for item in verdict.get("further_diligence", []):
            st.markdown(f"→ {item}")

    # ── PDF EXPORT ──
    st.markdown("---")
    col_pdf, col_json, _ = st.columns([1, 1, 2])
    with col_pdf:
        try:
            pdf_bytes = generate_pdf_memo(data, models, ov)
            prop_name = (prop.get("name") or "deal").replace(" ", "_")
            st.download_button(
                "📄 Download Screening Memo (PDF)",
                data=bytes(pdf_bytes),
                file_name=f"screening_memo_{prop_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
        except Exception as e:
            st.error(f"PDF error: {e}")
    with col_json:
        st.download_button(
            "📥 Export JSON (reload later)",
            data=json.dumps({"extracted": data}, indent=2, default=str),
            file_name="deal_data.json",
            mime="application/json",
            use_container_width=True,
        )
