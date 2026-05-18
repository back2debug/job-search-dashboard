#!/usr/bin/env python3
"""
Job Search Dashboard Generator
Usage: python3 generate.py [path/to/Jobs.ods]
Generates dashboard.html from template.html using data from the ODS spreadsheet.
"""

import sys
import os
import json
import glob
import datetime
from collections import defaultdict

# ── Ensure odfpy / pandas / numpy are importable ──────────────────────────────
sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.9/site-packages"))

import pandas as pd

# ── Phase configuration (edit these to adjust search phases) ──────────────────
LI_START        = "06-2025"   # First LinkedIn month
LI_END          = "11-2025"   # Last LinkedIn month
TRANS_MONTH     = "12-2025"   # Transition / wind-down month
NS_START        = "01-2026"   # First new-strategy month
RESUME_MONTH    = "02-2026"   # Specialised resumes started
PRE_RESUME_MONTH = "01-2026"  # Last month before specialised resumes (used for pre/post split)

# Month display helper: "MM-YYYY" → "Mon 'YY"
_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def fmt_month(mm_yyyy: str) -> str:
    """'06-2025' → "Jun '25" """
    mm, yyyy = mm_yyyy.split("-")
    return f"{_MONTH_NAMES[int(mm)-1]} '{yyyy[2:]}"

def fmt_month_long(mm_yyyy: str) -> str:
    """'06-2025' → 'June 2025' """
    mm, yyyy = mm_yyyy.split("-")
    long_names = ["January","February","March","April","May","June",
                  "July","August","September","October","November","December"]
    return f"{long_names[int(mm)-1]} {yyyy}"

def month_range(start: str, end: str):
    """Generate list of 'MM-YYYY' strings from start to end inclusive."""
    sm, sy = int(start[:2]), int(start[3:])
    em, ey = int(end[:2]), int(end[3:])
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{m:02d}-{y}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months

def pct_str(num, denom, decimals=1):
    """Return percentage string, e.g. '11.9%'. Returns '0%' if denom is 0."""
    if not denom:
        return "0%"
    val = num / denom * 100
    if decimals == 0:
        return f"{round(val)}%"
    return f"{val:.{decimals}f}%"

def diff_pct(new_val, old_val, decimals=0):
    """Return change badge text like '+125%' or '-44%'. old_val must not be 0."""
    if not old_val:
        return "N/A"
    change = (new_val - old_val) / old_val * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{round(change)}%"

def funnel_css(count, total):
    """CSS width percentage for funnel bar (min not enforced here, CSS handles min-width)."""
    if not total:
        return "0%"
    return f"{count/total*100:.2f}%"

# ── Find ODS file ─────────────────────────────────────────────────────────────

def find_ods(cli_arg=None):
    if cli_arg:
        if not os.path.isfile(cli_arg):
            sys.exit(f"Error: ODS file not found: {cli_arg}")
        return cli_arg
    # Search cwd for non-example .ods files
    candidates = [f for f in glob.glob("*.ods") if "example" not in f.lower()]
    if not candidates:
        sys.exit("Error: No .ods file found in current directory. Pass path as argument.")
    return candidates[0]

# ── Load data ─────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = [
    "Company", "Applied", "In Play", "Recruiter", "Hiring",
    "Test", "Followups", "Rejection", "Ghosted",
    "Should not have applied", "Not Moving forward", "Withdraw"
]

def load_data(ods_path: str) -> pd.DataFrame:
    df = pd.read_excel(ods_path, engine="odf", dtype=str)
    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        sys.exit(f"Error: Missing columns in spreadsheet: {', '.join(missing)}")
    # Drop rows with no company or applied date
    df = df[df["Applied"].notna() & df["Company"].notna()].copy()
    df["Applied"] = df["Applied"].str.strip()
    return df

def has_flag(val) -> bool:
    """Return True if the cell has any non-NaN value (typically 'x')."""
    if pd.isna(val):
        return False
    return str(val).strip() != ""

# ── Outcome-reason categorization (NMF + Withdraw comments) ───────────────────
# Each category is (label, [keywords]). The first matching category wins,
# so order = priority. Keywords are matched case-insensitively as substrings.
# Categories are intentionally generic — edit the keywords below to fit
# whatever feedback patterns show up in your own data.

NMF_CATEGORIES = [
    ("Background / experience fit", [
        "client consulting", "not right background", "lack influence",
        "wrong background", "more it", "it-ish",
    ]),
    ("Role / scope mismatch", [
        "project manager", "looking for", "more strategy",
    ]),
    ("Skill / tech stack gap", [
        "js experience", "javascript", "frontend", "fronted",
        "tech stack", "wrong stack",
    ]),
    ("Lost to other candidate", [
        "further along", "accepted",
    ]),
    ("Logistics", ["location", "relocat", "remote"]),
]

WITHDRAW_CATEGORIES = [
    ("Compensation", ["salary", "low pay", "underpaid"]),
    ("Tech / stack mismatch", [
        "tech stack", "wrong stack", "adtech", "zapier", "java",
        "it-ish", "more it",
    ]),
    ("Role / scope mismatch", [
        "not technical", "not really technical", "business user",
        "more strategy", "managing", "not qualified",
    ]),
    ("Recruiter / process issues", [
        "recruiter", "weird test", "test was not", "no comms",
        "comms", "ghosted", "hackerrank",
    ]),
    ("Red flags / culture", ["red flag", "glassdoor", "changing the job"]),
    ("Work conditions", ["weekend", "on call", "schedule"]),
]


def classify_reason(val, categories):
    """Classify a comment cell into a category label.

    Returns "Unknown" for empty/NaN/just-'x'/no-reason placeholders.
    Returns "Other" when none of the category keywords match.
    """
    if pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None
    low = text.lower()
    if low in ("x", "no reason provided", "no reason given", "no reason", "n/a", "none"):
        return "Unknown"
    for label, keywords in categories:
        if any(kw in low for kw in keywords):
            return label
    return "Other"


def categorize_reasons(df, column, categories):
    """Categorize all non-empty comments in `column`.

    Returns dict with:
      - ordered: list of (label, count) in category-definition order,
                 then Other, then Unknown. Empty categories are omitted.
      - top_quotes: list of (quote, count) for the most-recurring verbatim
                    quotes (lowercased+stripped grouping). Top 3 with count >= 1.
      - with_comment: int — entries whose comment was more than just 'x'.
    """
    series = df[column].dropna()
    labels = [classify_reason(v, categories) for v in series]

    # Count per category, preserving definition order
    counts = {}
    for lab in labels:
        if lab is None:
            continue
        counts[lab] = counts.get(lab, 0) + 1

    ordered = []
    for label, _ in categories:
        if counts.get(label, 0) > 0:
            ordered.append((label, counts[label]))
    for tail in ("Other", "Unknown"):
        if counts.get(tail, 0) > 0:
            ordered.append((tail, counts[tail]))

    # Group recurring verbatim quotes (substantive comments only)
    quote_counts = {}
    quote_originals = {}
    for v in series:
        text = str(v).strip()
        if not text:
            continue
        low = text.lower().rstrip(".! ")
        if low in ("x", "no reason provided", "no reason given", "no reason", "n/a", "none"):
            continue
        quote_counts[low] = quote_counts.get(low, 0) + 1
        # Keep the first-seen original casing
        quote_originals.setdefault(low, text)

    top_quotes = sorted(quote_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    top_quotes = [(quote_originals[low], n) for low, n in top_quotes]

    with_comment = sum(1 for v in series if str(v).strip().lower() not in ("", "x"))

    return {
        "ordered":      ordered,
        "top_quotes":   top_quotes,
        "with_comment": with_comment,
    }


# ── Compute stats ─────────────────────────────────────────────────────────────

def compute_stats(df: pd.DataFrame) -> dict:
    s = {}

    # ── Phase month lists ──────────────────────────────────────────────────────
    # Sort MM-YYYY strings chronologically (year first, then month)
    chron_key = lambda m: (int(m[3:]), int(m[:2]))
    all_applied_sorted = sorted(df["Applied"].unique(), key=chron_key)
    earliest = all_applied_sorted[0]
    latest   = all_applied_sorted[-1]
    s["li_months"]    = month_range(LI_START, LI_END)
    s["trans_months"] = [TRANS_MONTH]
    s["ns_months"]    = month_range(NS_START, latest)
    s["resume_month"] = RESUME_MONTH

    all_months = sorted(set(df["Applied"].tolist()), key=chron_key)
    s["all_months"] = all_months

    # ── Monthly data dict ──────────────────────────────────────────────────────
    monthly = {}
    for m in all_months:
        sub = df[df["Applied"] == m]
        monthly[m] = {
            "apps":      len(sub),
            "recruiter": int(sub["Recruiter"].apply(has_flag).sum()),
            "hiring":    int(sub["Hiring"].apply(has_flag).sum()),
            "test":      int(sub["Test"].apply(has_flag).sum()),
            "inplay":    int(sub["In Play"].apply(has_flag).sum()),
            "rejection": int(sub["Rejection"].apply(has_flag).sum()),
            "snha":      int(sub["Should not have applied"].apply(has_flag).sum()),
            "followup":  int(sub["Followups"].apply(has_flag).sum()),
            "withdraw":  int(sub["Withdraw"].apply(has_flag).sum()),
            "nmf":       int(sub["Not Moving forward"].apply(has_flag).sum()),
            "ghosted":   int(sub["Ghosted"].apply(has_flag).sum()),
        }
    s["monthly"] = monthly

    # ── Totals ─────────────────────────────────────────────────────────────────
    s["total_apps"]      = len(df)
    s["month_count"]     = len(all_months)
    s["recruiter_total"] = int(df["Recruiter"].apply(has_flag).sum())
    s["hiring_total"]    = int(df["Hiring"].apply(has_flag).sum())
    s["test_total"]      = int(df["Test"].apply(has_flag).sum())
    s["followup_total"]  = int(df["Followups"].apply(has_flag).sum())
    s["rejection_total"] = int(df["Rejection"].apply(has_flag).sum())
    s["snha_total"]      = int(df["Should not have applied"].apply(has_flag).sum())
    s["withdraw_total"]  = int(df["Withdraw"].apply(has_flag).sum())
    s["nmf_total"]       = int(df["Not Moving forward"].apply(has_flag).sum())
    s["ghosted_total"]   = int(df["Ghosted"].apply(has_flag).sum())
    s["in_play_total"]   = int(df["In Play"].apply(has_flag).sum())

    # ── Date range ─────────────────────────────────────────────────────────────
    first_m = all_months[0]
    last_m  = all_months[-1]
    # Build "Jun 2025 – May 2026" style
    def range_str(m):
        mm, yyyy = m.split("-")
        return f"{_MONTH_NAMES[int(mm)-1]} {yyyy}"
    s["date_range"] = f"{range_str(first_m)} – {range_str(last_m)}"

    # ── In-play description ────────────────────────────────────────────────────
    ip_by_month = {m: monthly[m]["inplay"] for m in all_months if monthly[m]["inplay"] > 0}
    ip_parts = [f"{v} from {fmt_month_long(m)}" for m, v in sorted(ip_by_month.items(), key=lambda kv: chron_key(kv[0]))]
    # Phase attribution note
    ip_all_ns = all(m in s["ns_months"] for m in ip_by_month)
    ip_note = " · All from the new strategy era" if ip_all_ns else ""
    s["in_play_desc"] = " · ".join(ip_parts) + ip_note

    # ── Phase subsets ──────────────────────────────────────────────────────────
    li_df  = df[df["Applied"].isin(s["li_months"])]
    ns_df  = df[df["Applied"].isin(s["ns_months"])]
    pre_df = df[df["Applied"] == PRE_RESUME_MONTH]
    post_df = df[df["Applied"].isin(month_range(RESUME_MONTH, latest))]

    def phase_stats(sub):
        n = len(sub)
        rec  = int(sub["Recruiter"].apply(has_flag).sum())
        hire = int(sub["Hiring"].apply(has_flag).sum())
        test = int(sub["Test"].apply(has_flag).sum())
        snha = int(sub["Should not have applied"].apply(has_flag).sum())
        wdraw = int(sub["Withdraw"].apply(has_flag).sum())
        resp  = rec + hire + test
        adv   = hire + test
        return dict(n=n, rec=rec, hire=hire, test=test, snha=snha, wdraw=wdraw,
                    resp=resp, adv=adv)

    li_s  = phase_stats(li_df)
    ns_s  = phase_stats(ns_df)
    pre_s = phase_stats(pre_df)
    post_s= phase_stats(post_df)

    s["li_apps"]     = li_s["n"]
    s["li_recruiter"]= li_s["rec"]
    s["li_test"]     = li_s["test"]
    s["li_withdraw"] = li_s["wdraw"]
    s["li_snha_pct"] = li_s["snha"] / li_s["n"] * 100 if li_s["n"] else 0

    s["ns_apps"]     = ns_s["n"]
    s["ns_recruiter"]= ns_s["rec"]
    s["ns_test"]     = ns_s["test"]
    s["ns_withdraw"] = ns_s["wdraw"]
    s["ns_snha_pct"] = ns_s["snha"] / ns_s["n"] * 100 if ns_s["n"] else 0

    # Response / advancement rates
    def rate(resp, n):
        return resp / n * 100 if n else 0

    s["li_response_rate"] = rate(li_s["resp"], li_s["n"])
    s["ns_response_rate"] = rate(ns_s["resp"], ns_s["n"])
    s["li_adv_rate"]      = rate(li_s["adv"],  li_s["n"])
    s["ns_adv_rate"]      = rate(ns_s["adv"],  ns_s["n"])

    # Recruiter → hiring, hiring → followup, hiring → test
    s["recruiter_to_hiring_pct"] = rate(s["hiring_total"], s["recruiter_total"])
    s["hiring_to_followup_pct"]  = rate(s["followup_total"], s["hiring_total"])
    s["hiring_to_test_pct"]      = rate(s["test_total"], s["hiring_total"])

    # Overall %
    t = s["total_apps"]
    s["rejection_pct"]  = rate(s["rejection_total"], t)
    s["snha_pct_full"]  = rate(s["snha_total"], t)
    s["withdraw_pct"]   = rate(s["withdraw_total"], t)
    s["nmf_ghosted_pct"]= rate(s["nmf_total"] + s["ghosted_total"], t)
    s["in_play_pct"]    = rate(s["in_play_total"], t)

    # Pre/post resume stats
    s["pre_apps"]          = pre_s["n"]
    s["pre_response_rate"] = rate(pre_s["resp"], pre_s["n"])
    s["pre_adv_rate"]      = rate(pre_s["adv"],  pre_s["n"])
    s["post_apps"]         = post_s["n"]
    s["post_response_rate"]= rate(post_s["resp"], post_s["n"])
    s["post_adv_rate"]     = rate(post_s["adv"],  post_s["n"])

    # Trans apps
    trans_df = df[df["Applied"].isin(s["trans_months"])]
    s["trans_apps"] = len(trans_df)

    # LI date range display (e.g. "Jun – Nov 2025")
    li_first_mm = int(LI_START[:2])
    li_last_mm  = int(LI_END[:2])
    li_yyyy     = LI_START[3:]
    s["li_date_range"] = f"{_MONTH_NAMES[li_first_mm-1]} – {_MONTH_NAMES[li_last_mm-1]} {li_yyyy}"

    # NS date range display (e.g. "Jan 2026 – present")
    ns_mm   = int(NS_START[:2])
    ns_yyyy = NS_START[3:]
    s["ns_date_range"] = f"{_MONTH_NAMES[ns_mm-1]} {ns_yyyy} – present"

    # ── Role data ──────────────────────────────────────────────────────────────
    # Aggregate role counts by phase using Company column (best-effort: parse title from company)
    # Since Jobs.ods has no dedicated Title column, we approximate by grouping company name keywords.
    # The roles are defined as keyword groups to classify the "Company" column entries.
    ROLE_KEYWORDS = [
        ("Support Eng",     ["support engineer", "support eng", "customer support engineer",
                             "technical support engineer", "technical support", "support specialist"]),
        ("Solutions Eng",   ["solutions engineer", "solutions eng", "solutions specialist",
                             "solutions consultant", "pre-sales"]),
        ("Impl Eng",        ["implementation engineer", "implementation eng", "impl engineer",
                             "implementations engineer", "implementation specialist"]),
        ("Impl Manager",    ["implementation manager", "impl manager", "implementations manager",
                             "implementation project manager"]),
        ("CS Eng",          ["customer success engineer", "cs engineer", "client success engineer",
                             "customer success specialist"]),
        ("QA Eng",          ["qa engineer", "quality assurance", "quality engineer", "test engineer",
                             "sdet", "qa analyst"]),
        ("TPM",             ["technical program manager", "tpm", "technical project manager"]),
        ("Integration Eng", ["integration engineer", "integration specialist", "integrations engineer"]),
        ("Dev Rel",         ["developer relations", "developer advocate", "devrel", "dev advocate",
                             "developer experience"]),
        ("PM",              ["product manager", " pm ", "product management"]),
    ]

    def classify_role(company_str):
        low = str(company_str).lower()
        for role_name, keywords in ROLE_KEYWORDS:
            if any(kw in low for kw in keywords):
                return role_name
        return None

    # Try to use a "Title" or "Role" column if it exists, otherwise fall back to Company
    title_col = None
    for candidate in ["Title", "Role", "Position", "Job Title"]:
        if candidate in df.columns:
            title_col = candidate
            break

    if title_col:
        classify_series = df[title_col]
    else:
        classify_series = df["Company"]

    li_roles  = defaultdict(int)
    ns_roles  = defaultdict(int)

    for idx, row in df.iterrows():
        role = classify_role(classify_series.loc[idx])
        if role:
            if row["Applied"] in s["li_months"]:
                li_roles[role] += 1
            elif row["Applied"] in s["ns_months"]:
                ns_roles[role] += 1

    # Build roles list ordered by ROLE_KEYWORDS definition
    roles = []
    for role_name, _ in ROLE_KEYWORDS:
        li_count = li_roles.get(role_name, 0)
        ns_count = ns_roles.get(role_name, 0)
        if li_count > 0 or ns_count > 0:
            roles.append({"name": role_name, "li": li_count, "ns": ns_count})

    # If no roles matched (no parseable title data), use empty list
    s["roles"] = roles

    # ── Outcome reasons (NMF / Withdraw comment analysis) ──────────────────────
    nmf_categorized = categorize_reasons(df, "Not Moving forward", NMF_CATEGORIES)
    wd_categorized  = categorize_reasons(df, "Withdraw",           WITHDRAW_CATEGORIES)
    s["nmf_reasons"]      = nmf_categorized["ordered"]
    s["nmf_top_quotes"]   = nmf_categorized["top_quotes"]
    s["nmf_with_comment"] = nmf_categorized["with_comment"]
    s["wd_reasons"]       = wd_categorized["ordered"]
    s["wd_top_quotes"]    = wd_categorized["top_quotes"]
    s["wd_with_comment"]  = wd_categorized["with_comment"]

    # ── Month labels ───────────────────────────────────────────────────────────
    s["month_labels"] = {m: fmt_month(m) for m in all_months}

    # ── Resume start index (for JS table phase labeling) ──────────────────────
    resume_months_in_data = [m for m in all_months if m >= RESUME_MONTH and m not in s["li_months"] and m != TRANS_MONTH]
    s["resume_start_idx"] = all_months.index(RESUME_MONTH) if RESUME_MONTH in all_months else len(all_months)

    # ── Best engagement month in new strategy era ──────────────────────────────
    best_month = None
    best_rate  = -1.0
    for m in s["ns_months"]:
        if m not in monthly:
            continue
        d = monthly[m]
        if d["apps"] == 0:
            continue
        r = (d["recruiter"] + d["hiring"] + d["test"]) / d["apps"] * 100
        if r > best_rate:
            best_rate  = r
            best_month = m
    s["best_ns_month"]      = best_month
    s["best_ns_month_rate"] = best_rate

    # Best month detail
    if best_month and best_month in monthly:
        bd = monthly[best_month]
        s["best_ns_month_apps"]      = bd["apps"]
        s["best_ns_month_recruiter"] = bd["recruiter"]
        s["best_ns_month_hiring"]    = bd["hiring"]
        s["best_ns_month_test"]      = bd["test"]
    else:
        s["best_ns_month_apps"]      = 0
        s["best_ns_month_recruiter"] = 0
        s["best_ns_month_hiring"]    = 0
        s["best_ns_month_test"]      = 0

    # Busiest NS month (most apps)
    busiest_month = None
    busiest_apps  = -1
    for m in s["ns_months"]:
        if m not in monthly:
            continue
        if monthly[m]["apps"] > busiest_apps:
            busiest_apps  = monthly[m]["apps"]
            busiest_month = m
    s["busiest_ns_month"]      = busiest_month
    s["busiest_ns_month_apps"] = busiest_apps
    if busiest_month and busiest_month in monthly:
        bd2 = monthly[busiest_month]
        s["busiest_ns_month_rate"] = (bd2["recruiter"] + bd2["hiring"] + bd2["test"]) / bd2["apps"] * 100 if bd2["apps"] else 0
    else:
        s["busiest_ns_month_rate"] = 0

    # ── Zero-response month near end of LinkedIn era ───────────────────────────
    zero_resp_months = [m for m in s["li_months"] if m in monthly and
                        (monthly[m]["recruiter"] + monthly[m]["hiring"] + monthly[m]["test"]) == 0]
    s["li_zero_resp_months"] = zero_resp_months

    # ── Role shifts between phases ─────────────────────────────────────────────
    role_shifts = []
    for r in roles:
        if r["li"] > 0:
            chg_pct = (r["ns"] - r["li"]) / r["li"] * 100
        else:
            chg_pct = 100.0 if r["ns"] > 0 else 0.0
        role_shifts.append((r["name"], r["li"], r["ns"], chg_pct))

    role_shifts.sort(key=lambda x: x[3], reverse=True)
    s["role_shifts"] = role_shifts  # list of (name, li, ns, pct_change)

    # ── NS phase change badges ─────────────────────────────────────────────────
    s["ns_apps_diff"]  = diff_pct(ns_s["n"],    li_s["n"])
    s["ns_resp_diff"]  = diff_pct(s["ns_response_rate"], s["li_response_rate"])
    s["ns_adv_diff"]   = diff_pct(s["ns_adv_rate"],      s["li_adv_rate"])
    s["ns_test_diff"]  = diff_pct(ns_s["test"],  li_s["test"])
    s["ns_snha_diff"]  = diff_pct(s["ns_snha_pct"], s["li_snha_pct"])

    return s


# ── Build outcome-reason HTML (NMF / Withdraw) ─────────────────────────────────

def _html_escape(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_reasons_html(ordered, total, fill_color):
    """Render category bars for an outcome-reason card."""
    if not ordered:
        return '<div style="color:var(--text-dim); font-size:12px; padding:6px 0;">No categorized reasons yet.</div>'
    parts = []
    for label, count in ordered:
        pct = (count / total * 100) if total else 0
        bar_color = "var(--text-dim)" if label in ("Unknown", "Other") else fill_color
        parts.append(
            '<div class="reason-row">'
            f'<div class="reason-label">{_html_escape(label)}</div>'
            f'<div class="reason-track"><div class="reason-fill" '
            f'style="width:{pct:.1f}%; background:{bar_color};"></div></div>'
            f'<div class="reason-count">{count}</div>'
            f'<div class="reason-pct">{pct:.0f}%</div>'
            '</div>'
        )
    return "\n      ".join(parts)


def build_quotes_html(top_quotes):
    """Render the 'recurring feedback' list under each reason card."""
    if not top_quotes:
        return ('<div style="color:var(--text-dim); font-size:12px; font-style:italic;">'
                'No recurring verbatim feedback.</div>')
    parts = []
    for quote, count in top_quotes:
        # Truncate long quotes for readability
        display = quote if len(quote) <= 110 else quote[:107] + "…"
        count_badge = (f'<span class="reason-quote-count">×{count}</span>'
                       if count > 1 else '')
        parts.append(
            f'<div class="reason-quote">{count_badge}"{_html_escape(display)}"</div>'
        )
    return "\n      ".join(parts)


# ── Build insights HTML ────────────────────────────────────────────────────────

def build_insights_html(s: dict) -> str:
    parts = []

    def card(cls, icon, title, text):
        parts.append(
            f'<div class="insight {cls}">\n'
            f'  <div class="insight-icon">{icon}</div>\n'
            f'  <div class="insight-body">\n'
            f'    <div class="insight-title">{title}</div>\n'
            f'    <div class="insight-text">{text}</div>\n'
            f'  </div>\n'
            f'</div>'
        )

    # 1. Strategy switch impact
    ns_rr  = f"{s['ns_response_rate']:.1f}%"
    li_rr  = f"{s['li_response_rate']:.1f}%"
    ns_adv = f"{s['ns_adv_rate']:.1f}%"
    li_adv = f"{s['li_adv_rate']:.1f}%"
    app_drop = abs(round((s["ns_apps"] - s["li_apps"]) / s["li_apps"] * 100)) if s["li_apps"] else 0
    card("highlight", "🚀",
         "Strategy switch more than doubled your response rate",
         f"Switching from LinkedIn to Builtin + targeted searches pushed response rate from "
         f"<strong>{li_rr} → {ns_rr}</strong> and advancement rate from "
         f"<strong>{li_adv} → {ns_adv}</strong> — with {app_drop}% fewer applications. "
         f"Less volume, far better results.")

    # 2. LinkedIn targeting problem
    li_snha = f"{s['li_snha_pct']:.1f}%"
    ns_snha = f"{s['ns_snha_pct']:.1f}%"
    card("alert", "⚠️",
         "LinkedIn had a targeting problem — 1 in 2 apps was a poor fit",
         f"During the LinkedIn era, <strong>{li_snha}</strong> of applications were self-flagged as "
         f"\"shouldn't have applied.\" That dropped to <strong>{ns_snha}</strong> with targeted search "
         f"lists — the algorithm was feeding you noise, not relevant jobs.")

    # 3. Best engagement month (dynamic)
    bm = s.get("best_ns_month")
    if bm:
        bm_label = fmt_month_long(bm)
        bm_apps  = s["best_ns_month_apps"]
        bm_rec   = s["best_ns_month_recruiter"]
        bm_hire  = s["best_ns_month_hiring"]
        bm_test  = s["best_ns_month_test"]
        bm_rate  = f"{s['best_ns_month_rate']:.1f}%"
        card("positive", "📈",
             f"{bm_label} was your best engagement month",
             f"{bm_apps} applications yielded {bm_rec} recruiter contact{'s' if bm_rec != 1 else ''}, "
             f"{bm_hire} hiring response{'s' if bm_hire != 1 else ''}, and "
             f"<strong>{bm_test} test{'s' if bm_test != 1 else ''}</strong> — "
             f"a <strong>{bm_rate}</strong> response rate. "
             f"The combination of new sourcing strategy and specialised resumes hit full stride that month.")
    else:
        card("positive", "📈",
             "Best engagement month (new strategy era)",
             "No new-strategy months found in the data yet.")

    # 4. Conversion strength
    r2h  = f"{s['recruiter_to_hiring_pct']:.1f}%"
    h2f  = f"{s['hiring_to_followup_pct']:.1f}%"
    h2t  = f"{s['hiring_to_test_pct']:.1f}%"
    rec_pct = pct_str(s["recruiter_total"], s["total_apps"])
    card("positive", "🎯",
         "Once you're in the door, you advance strongly",
         f"Recruiter → Hiring: <strong>{r2h}</strong>. "
         f"Hiring → Follow-up round (peer/team): <strong>{h2f}</strong>. "
         f"Hiring → Skills test: <strong>{h2t}</strong>. "
         f"The bottleneck is the first contact (only {rec_pct} of apps), not interview performance. "
         f"Resume/title keyword matching is the main lever to pull.")

    # 5. Volume vs focus (busiest ns month vs best ns month)
    bsm = s.get("busiest_ns_month")
    if bsm and bm and bsm != bm:
        bsm_label = fmt_month_long(bsm)
        bsm_apps  = s["busiest_ns_month_apps"]
        bsm_rate  = f"{s['busiest_ns_month_rate']:.1f}%"
        card("caution", "📊",
             f"{bsm_label}'s volume spike didn't outperform {fmt_month_long(bm)}'s focus",
             f"{bsm_label} had {bsm_apps} applications (your busiest new-strategy month) but only a "
             f"<strong>{bsm_rate}</strong> response rate vs {fmt_month_long(bm)}'s "
             f"<strong>{s['best_ns_month_rate']:.1f}%</strong>. "
             f"Focused months appear to outperform high-volume months in your new strategy.")
    elif bm:
        card("caution", "📊",
             "Volume vs focus in the new strategy era",
             f"Your best engagement month, {fmt_month_long(bm)}, achieved a "
             f"<strong>{s['best_ns_month_rate']:.1f}%</strong> response rate with "
             f"<strong>{s['best_ns_month_apps']}</strong> applications — quality over quantity.")
    else:
        card("caution", "📊", "Volume vs focus", "Insufficient new-strategy data.")

    # 6. Follow-up rounds reached
    followup = s["followup_total"]
    hiring   = s["hiring_total"]
    h2f_pct  = round(s["hiring_to_followup_pct"])
    card("positive", "🤝",
         f"You've advanced to peer/team interview rounds {followup} times",
         f"Follow-up rounds (peer/team interviews) represent one of the deepest pipeline stages — "
         f"you reached them in <strong>{h2f_pct}% of hiring-stage contacts</strong> "
         f"({followup} of {hiring}). This is a strong signal that you're performing well in "
         f"initial screens and hiring manager conversations.")

    # 7. Role shift
    shifts = s.get("role_shifts", [])
    if shifts:
        top_up   = [(n, li, ns, p) for n, li, ns, p in shifts if p > 5][:2]
        top_down = [(n, li, ns, p) for n, li, ns, p in reversed(shifts) if p < -5][:2]
        up_parts   = [f"{n} ({li}→{ns}, <strong>+{round(p)}%</strong>)" for n, li, ns, p in top_up]
        down_parts = [f"{n} ({li}→{ns}, <strong>{round(p)}%</strong>)" for n, li, ns, p in top_down]
        if up_parts or down_parts:
            text = ""
            if up_parts:
                text += "Roles growing: " + ", ".join(up_parts) + ". "
            if down_parts:
                text += "Roles shrinking: " + ", ".join(down_parts) + ". "
            text += "The pivot toward roles with stronger new-strategy fit is reflected in the data."
            card("highlight", "📋", "Role targeting shift between phases", text)
        else:
            card("highlight", "📋", "Role targeting relatively stable",
                 "No dramatic role shifts detected between the LinkedIn and new-strategy phases.")
    else:
        card("highlight", "📋",
             "Role targeting shift between phases",
             "Role classification data not available — add a Title/Role column to the spreadsheet for this insight.")

    # 8. Self-filter vs employer-filter balance
    wd_total  = s["withdraw_total"]
    nmf_total = s["nmf_total"]
    wd_reasons  = s.get("wd_reasons", [])
    nmf_reasons = s.get("nmf_reasons", [])
    if wd_total or nmf_total:
        top_wd  = wd_reasons[0][0]  if wd_reasons  else None
        top_nmf = nmf_reasons[0][0] if nmf_reasons else None
        if wd_total > nmf_total:
            headline = (f"You walked away from <strong>{wd_total}</strong> opportunities "
                        f"vs. <strong>{nmf_total}</strong> employer rejections — "
                        f"you're self-filtering more than you're being filtered out.")
        elif wd_total == nmf_total:
            headline = (f"Withdrawals (<strong>{wd_total}</strong>) and not-moving-forward "
                        f"(<strong>{nmf_total}</strong>) are roughly balanced.")
        else:
            headline = (f"Employer rejections (<strong>{nmf_total}</strong>) outpace "
                        f"your withdrawals (<strong>{wd_total}</strong>) — "
                        f"the bottleneck is screening, not selectivity.")
        bits = [headline]
        if top_wd:
            bits.append(f"Top withdraw trigger: <strong>{top_wd}</strong>.")
        if top_nmf:
            bits.append(f"Top employer-side gap: <strong>{top_nmf}</strong>.")
        card("highlight", "🎚️",
             "Self-filter vs. employer-filter balance",
             " ".join(bits))

    # 9. Platform exit validation
    zero_months = s.get("li_zero_resp_months", [])
    li_snha_str = f"{s['li_snha_pct']:.1f}%"
    if zero_months:
        zm_label = fmt_month_long(zero_months[-1])   # last zero-response LI month
        zm_apps  = s["monthly"][zero_months[-1]]["apps"]
        card("positive", "✅",
             f"{zm_label} exit from LinkedIn was validated by the data",
             f"{zm_label} was {'a' if len(zero_months) == 1 else 'one of the'} "
             f"zero-response month{'s' if len(zero_months) > 1 else ''} in the search — "
             f"<strong>{zm_apps} applications, 0 recruiter contacts</strong>. "
             f"That signal, combined with a {li_snha_str} poor-fit rate across the LinkedIn era, "
             f"confirms the platform switch was the right call at the right time.")
    else:
        card("positive", "✅",
             "LinkedIn exit timing",
             f"With a {li_snha_str} poor-fit rate across the LinkedIn era, "
             f"transitioning to a targeted search strategy was well-timed.")

    return "\n    ".join(parts)


# ── Render template ────────────────────────────────────────────────────────────

def render(s: dict, template_path: str) -> str:
    with open(template_path, "r", encoding="utf-8") as fh:
        html = fh.read()

    t = s["total_apps"]

    # Funnel percentages
    funnel_rec_pct    = f"{s['recruiter_total']/t*100:.1f}%" if t else "0%"
    funnel_hire_pct   = f"{s['hiring_total']/t*100:.1f}%"   if t else "0%"
    funnel_fol_pct    = f"{s['followup_total']/t*100:.2f}%" if t else "0%"
    funnel_test_pct   = f"{s['test_total']/t*100:.2f}%"     if t else "0%"
    funnel_ip_pct     = f"{s['in_play_total']/t*100:.1f}%"  if t else "0%"

    # Outcome meter percentages (as CSS width values and display labels)
    rej_pct_str   = f"{s['rejection_pct']:.1f}%"
    snha_pct_str  = f"{s['snha_pct_full']:.1f}%"
    wdraw_pct_str = f"{s['withdraw_pct']:.1f}%"
    nmf_pct_str   = f"{s['nmf_ghosted_pct']:.1f}%"
    ip_pct_str    = f"{s['in_play_pct']:.1f}%"

    # Generated date
    today = datetime.date.today()
    generated = today.strftime("Generated %B %-d, %Y")

    substitutions = {
        "__DATE_RANGE__":           s["date_range"],
        "__TOTAL_APPS__":           str(t),
        "__MONTH_COUNT__":          str(s["month_count"]),
        "__GENERATED_DATE__":       generated,
        "__LI_DATE_RANGE__":        s["li_date_range"],
        "__LI_APPS__":              str(s["li_apps"]),
        "__TRANS_DATE__":           fmt_month_long(TRANS_MONTH),
        "__TRANS_APPS__":           str(s["trans_apps"]),
        "__NS_DATE_RANGE__":        s["ns_date_range"],
        "__NS_APPS__":              str(s["ns_apps"]),
        "__RESUME_MONTH__":         fmt_month_long(RESUME_MONTH),
        "__IN_PLAY_TOTAL__":        str(s["in_play_total"]),
        "__IN_PLAY_DESC__":         s["in_play_desc"],
        "__RECRUITER_TOTAL__":      str(s["recruiter_total"]),
        "__HIRING_TOTAL__":         str(s["hiring_total"]),
        "__TEST_TOTAL__":           str(s["test_total"]),
        "__FOLLOWUP_TOTAL__":       str(s["followup_total"]),
        "__NS_RESPONSE_RATE__":     f"{s['ns_response_rate']:.1f}%",
        "__LI_RESPONSE_RATE__":     f"{s['li_response_rate']:.1f}%",
        "__NS_ADV_RATE__":          f"{s['ns_adv_rate']:.1f}%",
        "__LI_ADV_RATE__":          f"{s['li_adv_rate']:.1f}%",
        "__RECRUITER_TO_HIRING__":  f"{s['recruiter_to_hiring_pct']:.0f}%",
        "__HIRING_TO_FOLLOWUP__":   f"{s['hiring_to_followup_pct']:.0f}%",
        "__SNHA_TOTAL__":           str(s["snha_total"]),
        "__SNHA_PCT__":             f"{s['snha_pct_full']:.0f}%",
        "__LI_SNHA_PCT_ROUND__":    f"{s['li_snha_pct']:.0f}%",
        "__REJECTION_TOTAL__":      str(s["rejection_total"]),
        "__REJECTION_PCT__":        f"{s['rejection_pct']:.1f}%",
        # Funnel CSS widths
        "__FUNNEL_RECRUITER_CSS__": funnel_css(s["recruiter_total"], t),
        "__FUNNEL_HIRING_CSS__":    funnel_css(s["hiring_total"], t),
        "__FUNNEL_FOLLOWUP_CSS__":  funnel_css(s["followup_total"], t),
        "__FUNNEL_TEST_CSS__":      funnel_css(s["test_total"], t),
        "__FUNNEL_INPLAY_CSS__":    funnel_css(s["in_play_total"], t),
        # Funnel bar labels
        "__FUNNEL_RECRUITER_LABEL__": f"{s['recruiter_total']} contacts",
        "__FUNNEL_HIRING_LABEL__":    f"{s['hiring_total']} reached",
        "__FUNNEL_FOLLOWUP_LABEL__":  f"{s['followup_total']} peer/team",
        "__FUNNEL_TEST_LABEL__":      f"{s['test_total']} tests",
        "__FUNNEL_INPLAY_LABEL__":    f"{s['in_play_total']} active",
        # Funnel percentage labels
        "__OUTCOME_RECRUITER_PCT__":  funnel_rec_pct,
        "__FUNNEL_HIRING_PCT__":      funnel_hire_pct,
        "__FUNNEL_FOLLOWUP_PCT__":    f"{s['followup_total']/t*100:.1f}%" if t else "0%",
        "__FUNNEL_TEST_PCT__":        f"{s['test_total']/t*100:.1f}%"     if t else "0%",
        "__FUNNEL_INPLAY_PCT__":      funnel_ip_pct,
        # Outcome meters
        "__OUTCOME_REJECTION_PCT__":  rej_pct_str,
        "__OUTCOME_SNHA_PCT__":       snha_pct_str,
        "__OUTCOME_WITHDRAW_PCT__":   wdraw_pct_str,
        "__OUTCOME_NMF_PCT__":        nmf_pct_str,
        "__OUTCOME_INPLAY_PCT__":     ip_pct_str,
        # Phase comparison – LinkedIn
        "__LI_RECRUITER__":           str(s["li_recruiter"]),
        "__LI_TEST__":                str(s["li_test"]),
        "__LI_SNHA_PCT_VAL__":        f"{s['li_snha_pct']:.1f}%",
        "__LI_WITHDRAW__":            str(s["li_withdraw"]),
        # Phase comparison – New Strategy
        "__NS_APPS_DIFF__":           s["ns_apps_diff"],
        "__NS_RESP_DIFF__":           s["ns_resp_diff"],
        "__NS_ADV_DIFF__":            s["ns_adv_diff"],
        "__NS_RECRUITER__":           str(s["ns_recruiter"]),
        "__NS_TEST__":                str(s["ns_test"]),
        "__NS_TEST_DIFF__":           s["ns_test_diff"],
        "__NS_SNHA_PCT_VAL__":        f"{s['ns_snha_pct']:.1f}%",
        "__NS_SNHA_DIFF__":           s["ns_snha_diff"],
        "__NS_WITHDRAW__":            str(s["ns_withdraw"]),
        # Resume chart data values
        "__PRE_APPS__":               str(s["pre_apps"]),
        "__PRE_RESPONSE_RATE__":      f"{s['pre_response_rate']:.1f}",
        "__PRE_ADV_RATE__":           f"{s['pre_adv_rate']:.1f}",
        "__POST_APPS__":              str(s["post_apps"]),
        "__POST_RESPONSE_RATE__":     f"{s['post_response_rate']:.1f}",
        "__POST_ADV_RATE__":          f"{s['post_adv_rate']:.1f}",
        # Outcome reasons (NMF / Withdraw)
        "__NMF_TOTAL__":              str(s["nmf_total"]),
        "__WITHDRAW_TOTAL_OUT__":     str(s["withdraw_total"]),
        "__NMF_REASONS_HTML__":       build_reasons_html(s["nmf_reasons"],   s["nmf_total"],      "var(--danger)"),
        "__WITHDRAW_REASONS_HTML__":  build_reasons_html(s["wd_reasons"],    s["withdraw_total"], "var(--warning)"),
        "__NMF_QUOTES_HTML__":        build_quotes_html(s["nmf_top_quotes"]),
        "__WITHDRAW_QUOTES_HTML__":   build_quotes_html(s["wd_top_quotes"]),
        # Insights HTML
        "__INSIGHTS_HTML__":          build_insights_html(s),
        # Footer
        "__FOOTER__":                 f"{t} applications · {s['date_range']} · Source: Jobs.ods",
        # JS data objects
        "__MONTHLY_DATA_JSON__":      json.dumps(s["monthly"], ensure_ascii=False),
        "__ROLES_JSON__":             json.dumps(s["roles"],   ensure_ascii=False),
        "__LABELS_JSON__":            json.dumps([fmt_month(m) for m in s["all_months"]], ensure_ascii=False),
        "__LI_MONTHS_JSON__":         json.dumps(s["li_months"], ensure_ascii=False),
        "__MONTH_LABELS_JSON__":      json.dumps(s["month_labels"], ensure_ascii=False),
        # Resume start index for JS phase labeling
        "__RESUME_START_IDX__":       str(s["resume_start_idx"]),
    }

    for marker, value in substitutions.items():
        html = html.replace(marker, value)

    return html


# ── Main ───────────────────────────────────────────────────────────────────────

def main(ods_path: str = None):
    if ods_path is None and len(sys.argv) > 1:
        ods_path = sys.argv[1]
    ods_path = find_ods(ods_path)

    print(f"Reading: {ods_path}")
    df = load_data(ods_path)
    print(f"  {len(df)} rows loaded")

    stats = compute_stats(df)
    print(f"  {stats['total_apps']} applications · "
          f"{stats['month_count']} months · "
          f"{stats['in_play_total']} in play")

    # Template is in the same directory as this script
    script_dir    = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "template.html")
    output_path   = os.path.join(script_dir, "dashboard.html")

    if not os.path.isfile(template_path):
        sys.exit(f"Error: template.html not found at {template_path}")

    html = render(stats, template_path)

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"✓ Dashboard generated → {output_path}")


if __name__ == "__main__":
    main()
