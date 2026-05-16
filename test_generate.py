"""
Tests for generate.py — job search dashboard generator.
Run with: pytest test_generate.py -v
"""

import json
import os
import re
import sys
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate

SCRIPT_DIR = os.path.dirname(os.path.abspath(generate.__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "template.html")


# ---------------------------------------------------------------------------
# Shared test fixture
# ---------------------------------------------------------------------------

def make_df(**overrides):
    """Minimal valid DataFrame covering all REQUIRED_COLUMNS.

    Applied months fall in the NS phase (01-2026, 02-2026) so the phase
    constants in generate.py don't need to be changed for tests to pass.
    """
    data = {
        "Company":                 ["Acme Corp",   "Globex",    "Initech"],
        "Applied":                 ["01-2026",     "01-2026",   "02-2026"],
        "In Play":                 ["x",           None,        None],
        "Recruiter":               [None,          "x",         None],
        "Hiring":                  [None,          "x",         None],
        "Test":                    [None,          None,        "x"],
        "Followups":               [None,          "x",         None],
        "Rejection":               [None,          None,        "x"],
        "Ghosted":                 [None,          None,        None],
        "Should not have applied": [None,          None,        None],
        "Not Moving forward":      [None,          None,        None],
        "Withdraw":                [None,          None,        None],
    }
    data.update(overrides)
    df = pd.DataFrame(data)
    # Replicate what load_data does: dtype=str then NaN for missing
    for col in df.columns:
        df[col] = df[col].where(df[col].notna(), other=float("nan"))
    return df


@pytest.fixture
def df():
    return make_df()


@pytest.fixture
def stats(df):
    return generate.compute_stats(df)


# ---------------------------------------------------------------------------
# fmt_month / fmt_month_long
# ---------------------------------------------------------------------------

class TestFmtMonth:
    def test_june_2025(self):
        assert generate.fmt_month("06-2025") == "Jun '25"

    def test_january(self):
        assert generate.fmt_month("01-2026") == "Jan '26"

    def test_december(self):
        assert generate.fmt_month("12-2024") == "Dec '24"


class TestFmtMonthLong:
    def test_june(self):
        assert generate.fmt_month_long("06-2025") == "June 2025"

    def test_february(self):
        assert generate.fmt_month_long("02-2026") == "February 2026"


# ---------------------------------------------------------------------------
# month_range
# ---------------------------------------------------------------------------

class TestMonthRange:
    def test_single_month(self):
        assert generate.month_range("01-2026", "01-2026") == ["01-2026"]

    def test_within_year(self):
        assert generate.month_range("01-2026", "03-2026") == [
            "01-2026", "02-2026", "03-2026"
        ]

    def test_cross_year(self):
        assert generate.month_range("11-2025", "02-2026") == [
            "11-2025", "12-2025", "01-2026", "02-2026"
        ]

    def test_length(self):
        assert len(generate.month_range("06-2025", "11-2025")) == 6


# ---------------------------------------------------------------------------
# pct_str / diff_pct / funnel_css
# ---------------------------------------------------------------------------

class TestPctStr:
    def test_zero_denominator(self):
        assert generate.pct_str(5, 0) == "0%"

    def test_basic(self):
        assert generate.pct_str(1, 10) == "10.0%"

    def test_zero_decimals(self):
        assert generate.pct_str(1, 3, decimals=0) == "33%"

    def test_100_percent(self):
        assert generate.pct_str(10, 10) == "100.0%"


class TestDiffPct:
    def test_zero_old_val(self):
        assert generate.diff_pct(5, 0) == "N/A"

    def test_doubled(self):
        assert generate.diff_pct(200, 100) == "+100%"

    def test_halved(self):
        assert generate.diff_pct(50, 100) == "-50%"

    def test_no_change(self):
        assert generate.diff_pct(100, 100) == "+0%"


class TestFunnelCss:
    def test_zero_total(self):
        assert generate.funnel_css(5, 0) == "0%"

    def test_half(self):
        assert generate.funnel_css(50, 100) == "50.00%"

    def test_full(self):
        assert generate.funnel_css(100, 100) == "100.00%"


# ---------------------------------------------------------------------------
# has_flag
# ---------------------------------------------------------------------------

class TestHasFlag:
    def test_nan_returns_false(self):
        assert generate.has_flag(float("nan")) is False

    def test_x_returns_true(self):
        assert generate.has_flag("x") is True

    def test_empty_string_returns_false(self):
        assert generate.has_flag("") is False

    def test_whitespace_returns_false(self):
        assert generate.has_flag("   ") is False

    def test_numeric_string_returns_true(self):
        assert generate.has_flag("1") is True

    def test_none_like_nan(self):
        import numpy as np
        assert generate.has_flag(np.nan) is False


# ---------------------------------------------------------------------------
# find_ods
# ---------------------------------------------------------------------------

class TestFindOds:
    def test_nonexistent_path_raises(self):
        with pytest.raises(SystemExit, match="not found"):
            generate.find_ods("/nonexistent/path/file.ods")

    def test_valid_path_returned(self, tmp_path):
        f = tmp_path / "jobs.ods"
        f.write_bytes(b"")
        assert generate.find_ods(str(f)) == str(f)

    def test_no_arg_no_ods_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit, match="No .ods file found"):
            generate.find_ods(None)

    def test_no_arg_finds_ods(self, tmp_path, monkeypatch):
        f = tmp_path / "myjobs.ods"
        f.write_bytes(b"")
        monkeypatch.chdir(tmp_path)
        result = generate.find_ods(None)
        assert result == "myjobs.ods"

    def test_example_ods_excluded(self, tmp_path, monkeypatch):
        (tmp_path / "example.ods").write_bytes(b"")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit, match="No .ods file found"):
            generate.find_ods(None)


# ---------------------------------------------------------------------------
# load_data (via mocked pd.read_excel)
# ---------------------------------------------------------------------------

class TestLoadData:
    def _mock_load(self, df_in):
        with patch("generate.pd.read_excel", return_value=df_in):
            return generate.load_data("dummy.ods")

    def test_valid_data_loaded(self, df):
        result = self._mock_load(df)
        assert len(result) == 3

    def test_missing_column_raises(self):
        bad_df = pd.DataFrame({"Company": ["A"], "Applied": ["01-2026"]})
        with pytest.raises(SystemExit, match="Missing columns"):
            self._mock_load(bad_df)

    def test_drops_rows_missing_applied(self, df):
        df.loc[0, "Applied"] = float("nan")
        result = self._mock_load(df)
        assert len(result) == 2

    def test_drops_rows_missing_company(self, df):
        df.loc[1, "Company"] = float("nan")
        result = self._mock_load(df)
        assert len(result) == 2

    def test_strips_column_whitespace(self, df):
        df.columns = ["  " + c + "  " for c in df.columns]
        result = self._mock_load(df)
        assert "Company" in result.columns

    def test_strips_applied_whitespace(self, df):
        df.loc[0, "Applied"] = "  01-2026  "
        result = self._mock_load(df)
        assert result.iloc[0]["Applied"] == "01-2026"


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_total_apps(self, stats):
        assert stats["total_apps"] == 3

    def test_month_count(self, stats):
        assert stats["month_count"] == 2

    def test_in_play_total(self, stats):
        assert stats["in_play_total"] == 1

    def test_recruiter_total(self, stats):
        assert stats["recruiter_total"] == 1

    def test_hiring_total(self, stats):
        assert stats["hiring_total"] == 1

    def test_test_total(self, stats):
        assert stats["test_total"] == 1

    def test_followup_total(self, stats):
        assert stats["followup_total"] == 1

    def test_rejection_total(self, stats):
        assert stats["rejection_total"] == 1

    def test_monthly_structure(self, stats):
        assert "01-2026" in stats["monthly"]
        assert stats["monthly"]["01-2026"]["apps"] == 2
        assert stats["monthly"]["02-2026"]["apps"] == 1

    def test_date_range_contains_months(self, stats):
        assert "Jan" in stats["date_range"]
        assert "Feb" in stats["date_range"]
        assert "2026" in stats["date_range"]

    def test_month_labels(self, stats):
        assert stats["month_labels"]["01-2026"] == "Jan '26"
        assert stats["month_labels"]["02-2026"] == "Feb '26"

    def test_roles_is_list(self, stats):
        assert isinstance(stats["roles"], list)

    def test_all_months_ordered(self, stats):
        months = stats["all_months"]
        assert months == sorted(months, key=lambda m: (int(m[3:]), int(m[:2])))

    def test_in_play_desc_is_string(self, stats):
        assert isinstance(stats["in_play_desc"], str)
        assert len(stats["in_play_desc"]) > 0

    def test_pct_fields_in_range(self, stats):
        for key in ["rejection_pct", "snha_pct_full", "withdraw_pct", "in_play_pct"]:
            assert 0 <= stats[key] <= 100, f"{key} out of range"

    def test_best_ns_month_set(self, stats):
        # Both test months are in the NS phase
        assert stats["best_ns_month"] is not None

    def test_ns_months_non_empty(self, stats):
        assert len(stats["ns_months"]) >= 2

    def test_resume_start_idx_is_int(self, stats):
        assert isinstance(stats["resume_start_idx"], int)

    def test_role_shifts_list(self, stats):
        for item in stats["role_shifts"]:
            name, li, ns, pct = item
            assert isinstance(name, str)
            assert isinstance(li, int)
            assert isinstance(ns, int)
            assert isinstance(pct, float)

    def test_zero_division_safety_with_empty_li_phase(self, stats):
        # LI phase is empty for our test data — rates should be 0, not crash
        assert stats["li_response_rate"] == 0
        assert stats["li_adv_rate"] == 0
        assert stats["ns_apps_diff"] == "N/A"  # old_val=0 → diff_pct returns N/A


# ---------------------------------------------------------------------------
# build_insights_html
# ---------------------------------------------------------------------------

class TestBuildInsightsHtml:
    def test_returns_non_empty_string(self, stats):
        html = generate.build_insights_html(stats)
        assert isinstance(html, str)
        assert len(html) > 0

    def test_contains_insight_divs(self, stats):
        html = generate.build_insights_html(stats)
        assert 'class="insight' in html

    def test_no_raw_marker_tokens(self, stats):
        html = generate.build_insights_html(stats)
        assert "__" not in html.replace("__init__", "")  # no leftover markers


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

class TestRender:
    def test_output_is_html(self, stats):
        html = generate.render(stats, TEMPLATE_PATH)
        assert html.startswith("<!DOCTYPE html>")

    def test_all_markers_replaced(self, stats):
        html = generate.render(stats, TEMPLATE_PATH)
        remaining = re.findall(r"__[A-Z_]+__", html)
        assert remaining == [], f"Unreplaced markers: {remaining}"

    def test_total_apps_present(self, stats):
        html = generate.render(stats, TEMPLATE_PATH)
        assert ">3<" in html or '"3"' in html or ">3 " in html or " 3<" in html

    def test_monthly_data_is_valid_json(self, stats):
        html = generate.render(stats, TEMPLATE_PATH)
        # Extract the JS variable value — should parse as JSON
        match = re.search(r"const monthlyData\s*=\s*(\{.*?\});", html, re.DOTALL)
        if match:
            parsed = json.loads(match.group(1))
            assert "01-2026" in parsed

    def test_roles_json_is_valid(self, stats):
        html = generate.render(stats, TEMPLATE_PATH)
        match = re.search(r"const rolesData\s*=\s*(\[.*?\]);", html, re.DOTALL)
        if match:
            json.loads(match.group(1))  # must not raise


# ---------------------------------------------------------------------------
# main() signature fix
# ---------------------------------------------------------------------------

class TestMainSignature:
    def test_accepts_ods_path_positional(self, tmp_path):
        """Before the fix this raised TypeError; now it raises SystemExit."""
        with pytest.raises(SystemExit):
            generate.main(str(tmp_path / "nonexistent.ods"))

    def test_accepts_no_args_falls_back_to_argv(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(sys, "argv", ["generate.py"]):
            with pytest.raises(SystemExit, match="No .ods file found"):
                generate.main()

    def test_path_arg_takes_priority_over_argv(self, tmp_path, monkeypatch):
        """Explicit ods_path arg should be used, not sys.argv."""
        with pytest.raises(SystemExit, match="not found"):
            generate.main("/definitely/does/not/exist.ods")
