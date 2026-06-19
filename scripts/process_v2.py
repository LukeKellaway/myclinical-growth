#!/usr/bin/env python3
"""
UK National Data Processor v2
==============================
Extracts meaningful KPIs, time series trends, and RAG ratings
from all downloaded datasets. Outputs dashboard_data.json.

Usage: python3 process_v2.py
"""

import os
import sys
import json
import csv
import zipfile
import io
from pathlib import Path
from datetime import datetime
from collections import defaultdict

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# Paths overridable via env so the same script runs locally (Mac UK_National_Data)
# and in CI (temp dir from fetch_observatory.py, writing into the published site).
BASE_DIR = Path(os.environ.get("OBS_DATA_DIR") or (Path(__file__).parent.parent))
OUTPUT = Path(os.environ.get("OBS_OUTPUT") or (Path(__file__).parent / "dashboard_data.json"))

# ─── NHS TARGETS for RAG rating ────────────────────────────────
TARGETS = {
    "RTT_18wk": {"target": 92.0, "amber": 85.0, "unit": "%", "label": "RTT 18-Week Performance"},
    "DM01_6wk": {"target": 99.0, "amber": 95.0, "unit": "%", "label": "DM01 <6 Week Performance"},
    "DM01_13wk": {"target": 0, "amber": 5000, "unit": "patients", "label": "DM01 13+ Week Breaches", "inverse": True},
    "AE_4hr": {"target": 95.0, "amber": 80.0, "unit": "%", "label": "A&E 4-Hour Performance"},
    "Cancer_62day": {"target": 85.0, "amber": 75.0, "unit": "%", "label": "Cancer 62-Day Standard"},
    "Cancer_FDS": {"target": 75.0, "amber": 65.0, "unit": "%", "label": "Cancer Faster Diagnosis"},
}


def rag(value, target_key):
    """Return RAG status: green/amber/red."""
    t = TARGETS.get(target_key)
    if not t or value is None:
        return "grey"
    inverse = t.get("inverse", False)
    if inverse:
        # Lower is better (e.g. breaches)
        if value <= t["target"]:
            return "green"
        elif value <= t["amber"]:
            return "amber"
        return "red"
    else:
        if value >= t["target"]:
            return "green"
        elif value >= t["amber"]:
            return "amber"
        return "red"


def safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


# ═══════════════════════════════════════════════════════════════
# DM01 — Diagnostics
# ═══════════════════════════════════════════════════════════════
def process_dm01():
    print("Processing DM01...")
    path = BASE_DIR / "Performance_Waiting_Times" / "DM01_latest.zip"
    if not path.exists():
        return None

    rows = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith('.csv'):
                with z.open(name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                    for row in reader:
                        rows.append(row)

    # National totals by diagnostic test
    totals_by_test = defaultdict(lambda: {"total_wl": 0, "over_13wk": 0, "over_6wk": 0})
    national = {"total_wl": 0, "over_13wk": 0, "over_6wk": 0}

    # Provider-level totals
    provider_totals = defaultdict(lambda: {"total_wl": 0, "over_13wk": 0, "name": ""})

    for row in rows:
        test = row.get("Diagnostic Tests", "")
        total_wl = safe_int(row.get("Total WL", 0))
        over_13 = safe_int(row.get("13+ Weeks", 0))
        provider = row.get("Provider Org Name", "")
        provider_code = row.get("Provider Org Code", "")

        # Sum weeks 7-13+ for over 6 weeks
        over_6 = sum(safe_int(row.get(f"{i:02d} < {i+1:02d} Weeks", 0)) for i in range(6, 13)) + over_13

        if test and total_wl > 0:
            totals_by_test[test]["total_wl"] += total_wl
            totals_by_test[test]["over_13wk"] += over_13
            totals_by_test[test]["over_6wk"] += over_6
            national["total_wl"] += total_wl
            national["over_13wk"] += over_13
            national["over_6wk"] += over_6

        if provider and total_wl > 0:
            provider_totals[provider_code]["total_wl"] += total_wl
            provider_totals[provider_code]["over_13wk"] += over_13
            provider_totals[provider_code]["name"] = provider

    # Calculate percentages
    pct_within_6 = round((1 - national["over_6wk"] / national["total_wl"]) * 100, 1) if national["total_wl"] else 0
    pct_within_13 = round((1 - national["over_13wk"] / national["total_wl"]) * 100, 1) if national["total_wl"] else 0

    # Top breaching tests
    test_breakdown = []
    for test, data in sorted(totals_by_test.items(), key=lambda x: x[1]["over_13wk"], reverse=True):
        if data["total_wl"] > 0:
            test_breakdown.append({
                "test": test,
                "total_wl": data["total_wl"],
                "over_13wk": data["over_13wk"],
                "over_6wk": data["over_6wk"],
                "pct_within_6wk": round((1 - data["over_6wk"] / data["total_wl"]) * 100, 1),
            })

    # ALL providers for chart (not just worst 20)
    all_providers = sorted(
        [{"code": k, "name": v["name"], "total_wl": v["total_wl"], "over_13wk": v["over_13wk"],
          "pct_within_6wk": round((1 - v["over_6wk"] / v["total_wl"]) * 100, 1) if v.get("over_6wk") and v["total_wl"] else round((1 - v["over_13wk"] / v["total_wl"]) * 100, 1) if v["total_wl"] else 0,
          "pct_over_13wk": round(v["over_13wk"] / v["total_wl"] * 100, 1) if v["total_wl"] else 0}
         for k, v in provider_totals.items() if v["total_wl"] > 500],
        key=lambda x: x["over_13wk"], reverse=True
    )
    # Add over_6wk to provider totals
    for row in rows:
        test = row.get("Diagnostic Tests", "")
        provider_code = row.get("Provider Org Code", "")
        total_wl = safe_int(row.get("Total WL", 0))
        over_13 = safe_int(row.get("13+ Weeks", 0))
        over_6 = sum(safe_int(row.get(f"{i:02d} < {i+1:02d} Weeks", 0)) for i in range(6, 13)) + over_13
        if provider_code in provider_totals and total_wl > 0:
            if "over_6wk" not in provider_totals[provider_code]:
                provider_totals[provider_code]["over_6wk"] = 0
            provider_totals[provider_code]["over_6wk"] += over_6

    # Rebuild all_providers with correct pct_within_6wk
    all_providers = sorted(
        [{"code": k, "name": v["name"], "total_wl": v["total_wl"], "over_13wk": v["over_13wk"],
          "pct_within_6wk": round((1 - v.get("over_6wk", 0) / v["total_wl"]) * 100, 1) if v["total_wl"] else 0,
          "pct_over_13wk": round(v["over_13wk"] / v["total_wl"] * 100, 1) if v["total_wl"] else 0}
         for k, v in provider_totals.items() if v["total_wl"] > 500],
        key=lambda x: x["pct_within_6wk"]
    )

    period = rows[0].get("Period", "") if rows else ""

    return {
        "period": period,
        "headline": {
            "total_waiting_list": national["total_wl"],
            "over_13_weeks": national["over_13wk"],
            "over_6_weeks": national["over_6wk"],
            "pct_within_6_weeks": pct_within_6,
            "pct_within_13_weeks": pct_within_13,
            "rag_6wk": rag(pct_within_6, "DM01_6wk"),
            "rag_13wk": rag(national["over_13wk"], "DM01_13wk"),
        },
        "by_test": test_breakdown[:20],
        "worst_providers": all_providers[:20],
        "all_providers": all_providers,
    }


# ═══════════════════════════════════════════════════════════════
# RTT — Referral to Treatment
# ═══════════════════════════════════════════════════════════════
def process_rtt():
    print("Processing RTT...")
    path = BASE_DIR / "Performance_Waiting_Times" / "RTT_latest.zip"
    if not path.exists():
        return None

    rows = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith('.csv'):
                with z.open(name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                    for row in reader:
                        rows.append(row)

    # RTT has individual week-band columns: "Gt 00 To 01 Weeks SUM 1" through "Gt 104 Weeks SUM 1"
    # plus "Total All" as the grand total. We sum week bands to derive within 18 / over 52 etc.
    national = {"total_wl": 0, "within_18": 0, "over_52": 0, "over_65": 0}
    provider_totals = defaultdict(lambda: {"total_wl": 0, "within_18": 0, "over_52": 0, "name": ""})
    specialty_totals = defaultdict(lambda: {"total_wl": 0, "within_18": 0, "over_52": 0})

    for row in rows:
        rtt_type = row.get("RTT Part Description", "")
        if "incomplete" not in rtt_type.lower():
            continue

        # Sum all week bands for total
        total = safe_int(row.get("Total All", 0))

        # Within 18 weeks = sum of bands 0-1 through 17-18
        within_18 = 0
        for w in range(18):
            col = f"Gt {w:02d} To {w+1:02d} Weeks SUM 1"
            within_18 += safe_int(row.get(col, 0))

        # Over 52 weeks = sum of bands 52+ through 104+
        over_52 = 0
        for w in range(52, 104):
            col = f"Gt {w:02d} To {w+1:02d} Weeks SUM 1" if w < 100 else f"Gt {w} To {w+1} Weeks SUM 1"
            over_52 += safe_int(row.get(col, 0))
        over_52 += safe_int(row.get("Gt 104 Weeks SUM 1", 0))

        # Over 65 weeks
        over_65 = 0
        for w in range(65, 104):
            col = f"Gt {w:02d} To {w+1:02d} Weeks SUM 1" if w < 100 else f"Gt {w} To {w+1} Weeks SUM 1"
            over_65 += safe_int(row.get(col, 0))
        over_65 += safe_int(row.get("Gt 104 Weeks SUM 1", 0))

        provider = row.get("Provider Org Name", "")
        provider_code = row.get("Provider Org Code", "")
        specialty = row.get("Treatment Function Name", "")

        if total > 0:
            national["total_wl"] += total
            national["within_18"] += within_18
            national["over_52"] += over_52
            national["over_65"] += over_65

            provider_totals[provider_code]["total_wl"] += total
            provider_totals[provider_code]["within_18"] += within_18
            provider_totals[provider_code]["over_52"] += over_52
            provider_totals[provider_code]["name"] = provider

            specialty_totals[specialty]["total_wl"] += total
            specialty_totals[specialty]["within_18"] += within_18
            specialty_totals[specialty]["over_52"] += over_52

    pct_18wk = round(national["within_18"] / national["total_wl"] * 100, 1) if national["total_wl"] else 0

    # Top specialties by waiting list size
    spec_list = sorted(
        [{"specialty": k, "total_wl": v["total_wl"],
          "pct_18wk": round(v["within_18"]/v["total_wl"]*100, 1) if v["total_wl"] else 0,
          "over_52wk": v["over_52"]}
         for k, v in specialty_totals.items() if v["total_wl"] > 0 and k],
        key=lambda x: x["total_wl"], reverse=True
    )[:20]

    all_prov = sorted(
        [{"code": k, "name": v["name"], "total_wl": v["total_wl"],
          "pct_18wk": round(v["within_18"]/v["total_wl"]*100, 1) if v["total_wl"] else 0,
          "over_52wk": v["over_52"]}
         for k, v in provider_totals.items() if v["total_wl"] > 500],
        key=lambda x: x["pct_18wk"]
    )
    worst = all_prov[:20]

    period = rows[0].get("Period", "") if rows else ""

    return {
        "period": period,
        "headline": {
            "total_waiting_list": national["total_wl"],
            "within_18_weeks": national["within_18"],
            "pct_18_weeks": pct_18wk,
            "over_52_weeks": national["over_52"],
            "over_65_weeks": national["over_65"],
            "rag": rag(pct_18wk, "RTT_18wk"),
        },
        "by_specialty": spec_list,
        "worst_providers": worst,
        "all_providers": all_prov,
    }


# ═══════════════════════════════════════════════════════════════
# A&E
# ═══════════════════════════════════════════════════════════════
def process_ae():
    print("Processing A&E...")
    path = BASE_DIR / "Performance_Waiting_Times" / "AE_latest.csv"
    if not path.exists():
        return None

    rows = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # Get column names for attendances and 4hr breaches
    national_attend = 0
    national_over4 = 0
    providers = []

    for row in rows:
        # Sum Type 1 + Type 2 + Other
        attend = (safe_int(row.get("A&E attendances Type 1", 0)) +
                  safe_int(row.get("A&E attendances Type 2", 0)) +
                  safe_int(row.get("A&E attendances Other A&E Department", 0)))
        over4 = (safe_int(row.get("Attendances over 4hrs Type 1", 0)) +
                 safe_int(row.get("Attendances over 4hrs Type 2", 0)) +
                 safe_int(row.get("Attendances over 4hrs Other Department", 0)))

        national_attend += attend
        national_over4 += over4

        if attend > 100:
            name = row.get("Org name", row.get("Org Name", ""))
            pct = round((1 - over4 / attend) * 100, 1) if attend else 0
            providers.append({"name": name, "attendances": attend, "over_4hr": over4, "pct_within_4hr": pct})

    pct_4hr = round((1 - national_over4 / national_attend) * 100, 1) if national_attend else 0
    all_prov = sorted(providers, key=lambda x: x["pct_within_4hr"])
    worst = all_prov[:20]

    period = rows[0].get("Period", "") if rows else ""

    return {
        "period": period,
        "headline": {
            "total_attendances": national_attend,
            "over_4_hours": national_over4,
            "pct_within_4_hours": pct_4hr,
            "rag": rag(pct_4hr, "AE_4hr"),
        },
        "worst_providers": worst,
        "all_providers": all_prov,
    }


# ═══════════════════════════════════════════════════════════════
# Cancer
# ═══════════════════════════════════════════════════════════════
def process_cancer():
    print("Processing Cancer...")
    path = BASE_DIR / "Performance_Waiting_Times" / "Cancer_Waits_latest.csv"
    if not path.exists():
        return None

    rows = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # Find England totals for key standards
    standards = {}
    for row in rows:
        org = row.get("Org_Code", "")
        if org != "Total":
            continue
        standard = row.get("Standard_or_Item", "")
        total = safe_int(row.get("Total", 0))
        within = safe_int(row.get("Within", 0))
        perf = safe_float(row.get("Performance", None))

        if standard and total > 0:
            if perf is not None:
                perf = round(perf * 100, 1) if perf < 1 else round(perf, 1)
            standards[standard] = {
                "total": total,
                "within": within,
                "performance": perf
            }

    fds_perf = standards.get("FDS", {}).get("performance")
    d62_perf = standards.get("62 Day", {}).get("performance")

    return {
        "headline": {
            "fds_performance": fds_perf,
            "fds_rag": rag(fds_perf, "Cancer_FDS") if fds_perf else "grey",
            "62day_performance": d62_perf,
            "62day_rag": rag(d62_perf, "Cancer_62day") if d62_perf else "grey",
        },
        "standards": standards,
    }


# ═══════════════════════════════════════════════════════════════
# POLARITY — is higher good or bad for each indicator?
# "high_is_bad" = prevalence, mortality, deprivation, crime etc.
# "high_is_good" = screening, life expectancy, employment, breastfeeding etc.
# ═══════════════════════════════════════════════════════════════
POLARITY = {
    # --- High is GOOD ---
    "Life_Expectancy_Male": "good", "Life_Expectancy_Female": "good",
    "Healthy_Life_Expectancy": "good",
    "Employment_Rate": "good",
    "Breastfeeding_Initiation": "good", "Breastfeeding_6to8wks": "good",
    "Cervical_Screening_Coverage": "good", "Breast_Screening_Coverage": "good",
    "Bowel_Screening_Coverage": "good", "NHS_Health_Check_Uptake": "good",
    "Flu_Vaccination_Over65": "good", "Flu_Vaccination_At_Risk": "good",
    "Flu_Vacc_65plus": "good",
    "Early_Cancer_Diagnosis": "good",
    "School_Readiness": "good", "GCSE_Attainment": "good",
    "Drug_Treatment_Completion": "good",
    "IAPT_Recovery_Rate": "good",
    "MH_Crisis_Team_Gatekeeping": "good",
    "Reablement_Effectiveness": "good",
    "Access_Green_Space": "good",
    "Gap_Employment_Rate_MH": "good",
    "Dementia_Diagnosis_Rate": "good",

    # --- High is BAD (default assumption for most health indicators) ---
    # Prevalence
    "Diabetes_Prevalence": "bad", "CHD_Prevalence": "bad", "COPD_Prevalence": "bad",
    "Depression_Prevalence": "bad", "Obesity_Prevalence": "bad", "Smoking_Prevalence": "bad",
    "Hypertension_Prevalence": "bad", "Asthma_Prevalence": "bad", "Dementia_Prevalence": "bad",
    "CKD_Prevalence": "bad", "Cancer_Prevalence": "bad", "Epilepsy_Prevalence": "bad",
    "Heart_Failure_Prevalence": "bad", "Atrial_Fibrillation_Prevalence": "bad",
    "Mental_Health_Prevalence": "bad", "Learning_Disability_Prevalence": "bad",
    "Palliative_Care_Prevalence": "bad", "Depression_Recorded_Prevalence": "bad",
    "Dementia_65plus": "bad",
    # Mortality
    "Under75_CVD_Mortality": "bad", "Under75_Cancer_Mortality": "bad",
    "Under75_Respiratory_Mortality": "bad", "Under75_Liver_Disease_Mortality": "bad",
    "Infant_Mortality": "bad", "Suicide_Rate": "bad", "Drug_Misuse_Deaths": "bad",
    "Excess_Winter_Deaths": "bad", "Preventable_Mortality": "bad", "Avoidable_Mortality": "bad",
    "Alcohol_Mortality": "bad",
    # Risk factors / lifestyle
    "Alcohol_Admissions": "bad", "Physical_Inactivity": "bad",
    "Excess_Weight_Adults": "bad", "Excess_Weight_Children_Yr6": "bad",
    "Excess_Weight_Children_Reception": "bad", "Smoking_At_Delivery": "bad",
    # Children
    "Low_Birth_Weight": "bad", "Teenage_Pregnancy_U18": "bad", "Child_Poverty": "bad",
    "Children_Social_Care_Rate": "bad", "Looked_After_Children_Rate": "bad",
    "NEET_16to17": "bad", "Pupil_Absence": "bad", "First_Time_Entrants_Youth_Justice": "bad",
    # Social / inequality
    "Unemployment_Rate": "bad", "Long_Term_Unemployment": "bad",
    "Fuel_Poverty": "bad", "Overcrowded_Households": "bad",
    "Homelessness_Statutory": "bad", "Rough_Sleeping": "bad",
    "Violent_Crime": "bad", "Domestic_Abuse_Incidents": "bad", "Reoffending_Rate": "bad",
    "Social_Isolation_Adult": "bad", "Loneliness": "bad", "Food_Insecurity": "bad",
    "Deprivation_Score_IMD2019": "bad",
    # Inequality
    "Slope_Index_Inequality_LE_Male": "bad", "Slope_Index_Inequality_LE_Female": "bad",
    "Inequality_Life_Expectancy": "bad",
    # Healthcare (bad = more emergencies)
    "Emergency_Admissions": "bad", "Unplanned_Hospitalisation_Chronic": "bad",
    "Emergency_Readmissions_30day": "bad", "Hip_Fracture_Emergency": "bad",
    "Antibiotic_Prescribing": "bad",
    # Mental health
    "Self_Harm_Admissions": "bad", "Perinatal_MH_Rate": "bad",
    # Environment
    "Air_Pollution_PM25": "bad", "Noise_Complaints": "bad",
    "Road_Casualties_KSI": "bad", "Excess_Cold_Hazards": "bad",
    # Ageing
    "Falls_Admissions_65plus": "bad", "Hip_Fractures_65plus": "bad",
    "Delayed_Transfers_Care": "bad", "Permanent_Admissions_Care_Homes": "bad",
    "Social_Care_Users_65plus": "bad",
    # Digital
    "Internet_Non_Users": "bad", "Digital_Exclusion_Composite": "bad",
}

def get_polarity(name):
    """Return 'good' or 'bad' — whether higher values are good or bad."""
    return POLARITY.get(name, "bad")  # Default: high is bad (conservative)


# ═══════════════════════════════════════════════════════════════
# FINGERTIPS — Full time series + trends + RAG
# ═══════════════════════════════════════════════════════════════
def process_fingertips():
    print("Processing Fingertips (101 indicators)...")
    ft_dir = BASE_DIR / "Fingertips_Extracts"
    if not ft_dir.exists():
        return {}

    indicators = {}
    for fp in sorted(ft_dir.glob("Fingertips_*.csv")):
        name = fp.stem.replace("Fingertips_", "")
        try:
            rows = []
            with open(fp, 'r', encoding='utf-8-sig', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)

            if not rows:
                continue

            indicator_name = rows[0].get("Indicator Name", name)
            indicator_id = rows[0].get("Indicator ID", "")

            # England time series
            england_ts = {}
            for row in rows:
                if "england" in row.get("Area Name", "").lower() and row.get("Sex", "") in ("Persons", "Male", "Female", ""):
                    tp = row.get("Time period", "")
                    val = safe_float(row.get("Value"))
                    sex = row.get("Sex", "Persons")
                    if val is not None and tp:
                        key = f"{tp}"
                        if sex == "Persons" or key not in england_ts:
                            england_ts[key] = val

            # Sort time series
            ts_sorted = sorted(england_ts.items())
            latest_val = ts_sorted[-1][1] if ts_sorted else None
            latest_period = ts_sorted[-1][0] if ts_sorted else ""

            # Trend direction from time series
            if len(ts_sorted) >= 3:
                recent = [v for _, v in ts_sorted[-3:]]
                if recent[-1] > recent[0] * 1.02:
                    calc_trend = "increasing"
                elif recent[-1] < recent[0] * 0.98:
                    calc_trend = "decreasing"
                else:
                    calc_trend = "stable"
            else:
                calc_trend = "unknown"

            # Area-level latest data
            # Get the latest time period
            latest_tp = ts_sorted[-1][0] if ts_sorted else ""
            area_data = []
            area_trends = defaultdict(str)
            area_comparisons = defaultdict(str)

            for row in rows:
                if row.get("Time period") == latest_tp and row.get("Sex", "") in ("Persons", ""):
                    area_name = row.get("Area Name", "")
                    area_code = row.get("Area Code", "")
                    val = safe_float(row.get("Value"))
                    trend = row.get("Recent Trend", "")
                    compared = row.get("Compared to England value or percentiles", "")

                    if val is not None and area_code and "england" not in area_name.lower():
                        area_data.append({
                            "name": area_name,
                            "code": area_code,
                            "value": round(val, 2),
                            "trend": trend,
                            "vs_england": compared,
                        })

            # Sort by value descending
            area_data.sort(key=lambda x: x["value"], reverse=True)

            # Count RAG
            higher = sum(1 for a in area_data if "Higher" in a.get("vs_england", ""))
            lower = sum(1 for a in area_data if "Lower" in a.get("vs_england", ""))
            similar = sum(1 for a in area_data if a.get("vs_england") == "Similar")
            increasing = sum(1 for a in area_data if a.get("trend") == "Increasing")
            decreasing = sum(1 for a in area_data if a.get("trend") == "Decreasing")

            polarity = get_polarity(name)
            # Determine if trend is good or bad based on polarity
            if calc_trend == "increasing":
                trend_quality = "bad" if polarity == "bad" else "good"
            elif calc_trend == "decreasing":
                trend_quality = "good" if polarity == "bad" else "bad"
            else:
                trend_quality = "neutral"

            indicators[name] = {
                "indicator_name": indicator_name,
                "indicator_id": indicator_id,
                "england_latest": latest_val,
                "latest_period": latest_period,
                "trend": calc_trend,
                "polarity": polarity,
                "trend_quality": trend_quality,
                "time_series": [{"period": p, "value": round(v, 2)} for p, v in ts_sorted],
                "area_count": len(area_data),
                "top_10": area_data[:10],
                "bottom_10": area_data[-10:] if len(area_data) > 10 else [],
                "distribution": {
                    "higher_than_england": higher,
                    "similar_to_england": similar,
                    "lower_than_england": lower,
                    "trend_increasing": increasing,
                    "trend_decreasing": decreasing,
                },
            }
        except Exception as e:
            print(f"  Warning: {fp.name}: {e}")

    return indicators


# ═══════════════════════════════════════════════════════════════
# ONS / DEMOGRAPHICS / ECONOMY
# ═══════════════════════════════════════════════════════════════
def process_ons_csv(filepath, value_col="obs_value"):
    """Process an ONS/Nomis CSV into area-level data with deduplication."""
    if not filepath.exists():
        return None
    rows = []
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i > 50000:
                break
            rows.append(row)

    if not rows:
        return None

    # Find the right columns
    cols = list(rows[0].keys())
    geo_col = next((c for c in cols if 'geography_name' in c.lower()), None)
    code_col = next((c for c in cols if 'geography_code' in c.lower()), None)
    val_col = next((c for c in cols if c.lower() in ('obs_value', 'value', 'obs value')), None)
    date_col = next((c for c in cols if 'date' in c.lower() or 'time' in c.lower() or 'period' in c.lower()), None)

    if not geo_col or not val_col:
        return {"raw_columns": cols, "row_count": len(rows)}

    # Filter to a single variable/measure type if multiple exist
    var_col = next((c for c in cols if 'variable' in c.lower() and 'name' in c.lower()), None)
    meas_col = next((c for c in cols if 'measures' in c.lower() and 'name' in c.lower()), None)

    # If there are multiple variables, pick the first one; if measures exist, prefer "Variable" over "Numerator"/"Denominator"
    if meas_col:
        rows = [r for r in rows if r.get(meas_col, "").strip() in ("Variable", "Value", "")]
    if var_col:
        var_names = list(dict.fromkeys(r.get(var_col, "").strip() for r in rows if r.get(var_col, "").strip()))
        if len(var_names) > 1:
            # Pick the most useful variable: prefer "rate" or "%" over raw counts
            preferred = next((v for v in var_names if 'rate' in v.lower() and '16-64' in v), var_names[0])
            rows = [r for r in rows if r.get(var_col, "").strip() == preferred]

    # Deduplicate: keep latest period per area (by code or name)
    area_map = {}  # code -> {name, code, value, date}
    for row in rows:
        name = row.get(geo_col, "").strip()
        code = row.get(code_col, "").strip()
        val = safe_float(row.get(val_col))
        date = row.get(date_col, "") if date_col else ""
        if not name or val is None:
            continue
        # Only include England-level areas (E-codes) or areas without codes
        if code and not code.startswith("E"):
            continue
        key = code or name
        existing = area_map.get(key)
        # Keep the latest date, or if no dates, keep the last occurrence
        if not existing or (date and date > existing.get("date", "")):
            area_map[key] = {"name": name, "code": code, "value": round(val, 2), "date": date}

    areas = sorted(area_map.values(), key=lambda x: x["value"], reverse=True)
    # Remove date field from output
    for a in areas:
        a.pop("date", None)

    vals = [a["value"] for a in areas]
    mean_val = round(sum(vals) / len(vals), 2) if vals else 0
    min_val = min(vals) if vals else 0
    max_val = max(vals) if vals else 0

    return {
        "area_count": len(areas),
        "mean": mean_val,
        "min": {"value": min_val, "area": areas[-1]["name"] if areas else ""},
        "max": {"value": max_val, "area": areas[0]["name"] if areas else ""},
        "top_10": areas[:10],
        "bottom_10": areas[-10:] if len(areas) > 10 else [],
        "all_areas": areas,
    }


def process_demographics():
    print("Processing Demographics...")
    d = BASE_DIR / "Demographics"
    return {
        "population": process_ons_csv(d / "ONS_Population_MYE_LA.csv"),
        "births": process_ons_csv(d / "ONS_Births_LA.csv"),
        "deaths": process_ons_csv(d / "ONS_Deaths_LA.csv"),
        "ethnicity": process_ons_csv(d / "ONS_Population_Ethnicity_LA.csv"),
    }


def process_economy():
    print("Processing Economy...")
    d = BASE_DIR / "Economy_Employment"
    return {
        "employment": process_ons_csv(d / "ONS_Employment_APS_LA.csv"),
        "claimant_count": process_ons_csv(d / "ONS_Claimant_Count_LA.csv"),
        "median_earnings": process_ons_csv(d / "ONS_Median_Earnings_ASHE_LA.csv"),
        "gva_per_head": process_ons_csv(d / "ONS_GVA_Per_Head_LA.csv"),
        "business_counts": process_ons_csv(d / "ONS_Business_Counts_LA.csv"),
        "dwp_benefits": process_ons_csv(d / "DWP_Benefits_LA.csv"),
    }


def process_environment():
    print("Processing Environment...")
    d = BASE_DIR / "Environment"
    return {
        "air_quality_pm25": process_ons_csv(d / "DEFRA_Air_Quality_PM25.csv"),
        "air_quality_no2": process_ons_csv(d / "DEFRA_Air_Quality_NO2.csv"),
    }


# ═══════════════════════════════════════════════════════════════
# WORKFORCE
# ═══════════════════════════════════════════════════════════════
def process_workforce():
    print("Processing Workforce...")
    result = {}

    # Sickness absence CSV
    path = BASE_DIR / "Workforce" / "Sickness_Absence_latest.csv"
    if path.exists():
        rows = []
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        if rows:
            result["sickness_absence"] = {
                "columns": list(rows[0].keys()),
                "row_count": len(rows),
                "preview": rows[:20]
            }

    return result


# ═══════════════════════════════════════════════════════════════
# MAP DATA — ICB-level and LA-level metrics for choropleth map
# ═══════════════════════════════════════════════════════════════

# Q-code (NHS) to E54 (ONS) ICB mapping
Q_TO_E54 = {
    "QE1": "E54000048", "QF7": "E54000061", "QGH": "E54000019", "QH8": "E54000026",
    "QHG": "E54000024", "QHL": "E54000055", "QHM": "E54000050", "QJ2": "E54000058",
    "QJG": "E54000023", "QJK": "E54000037", "QJM": "E54000013", "QK1": "E54000015",
    "QKK": "E54000030", "QKS": "E54000032", "QM7": "E54000025", "QMF": "E54000029",
    "QMJ": "E54000028", "QMM": "E54000022", "QNC": "E54000010", "QNQ": "E54000034",
    "QNX": "E54000064", "QOC": "E54000011", "QOP": "E54000057", "QOQ": "E54000051",
    "QOX": "E54000040", "QPM": "E54000059", "QR1": "E54000043", "QRL": "E54000042",
    "QRV": "E54000027", "QSL": "E54000038", "QT1": "E54000060", "QT6": "E54000036",
    "QU9": "E54000044", "QUA": "E54000062", "QUE": "E54000056", "QUY": "E54000039",
    "QVV": "E54000041", "QWE": "E54000031", "QWO": "E54000054", "QWU": "E54000018",
    "QXU": "E54000063", "QYG": "E54000008",
}


def process_map_data():
    """Generate ICB-level and LA-level metrics for the choropleth map."""
    print("Processing Map Data...")

    icb_data = {}   # keyed by E54 code
    la_data = {}    # keyed by E06/E10 code

    # ── 1. Aggregate NHS performance metrics to ICB level ──

    # RTT: aggregate by Provider Parent Org Code
    rtt_path = BASE_DIR / "Performance_Waiting_Times" / "RTT_latest.zip"
    if rtt_path.exists():
        icb_rtt = defaultdict(lambda: {"total_wl": 0, "within_18": 0, "over_52": 0, "name": ""})
        with zipfile.ZipFile(rtt_path) as z:
            for name in z.namelist():
                if name.endswith('.csv'):
                    with z.open(name) as f:
                        for row in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig')):
                            if "incomplete" not in row.get("RTT Part Description", "").lower():
                                continue
                            q_code = row.get("Provider Parent Org Code", "")
                            e54 = Q_TO_E54.get(q_code)
                            if not e54:
                                continue
                            total = safe_int(row.get("Total All", 0))
                            within_18 = sum(safe_int(row.get(f"Gt {w:02d} To {w+1:02d} Weeks SUM 1", 0)) for w in range(18))
                            over_52 = sum(safe_int(row.get(f"Gt {w:02d} To {w+1:02d} Weeks SUM 1" if w < 100 else f"Gt {w} To {w+1} Weeks SUM 1", 0)) for w in range(52, 104))
                            over_52 += safe_int(row.get("Gt 104 Weeks SUM 1", 0))
                            icb_rtt[e54]["total_wl"] += total
                            icb_rtt[e54]["within_18"] += within_18
                            icb_rtt[e54]["over_52"] += over_52
                            icb_rtt[e54]["name"] = row.get("Provider Parent Name", "").strip()

        for e54, v in icb_rtt.items():
            if e54 not in icb_data:
                icb_data[e54] = {"name": v["name"]}
            icb_data[e54]["rtt_18wk_pct"] = round(v["within_18"] / v["total_wl"] * 100, 1) if v["total_wl"] else None
            icb_data[e54]["rtt_total_wl"] = v["total_wl"]
            icb_data[e54]["rtt_over_52wk"] = v["over_52"]

    # DM01: aggregate by Provider Parent Org Code
    dm01_path = BASE_DIR / "Performance_Waiting_Times" / "DM01_latest.zip"
    if dm01_path.exists():
        icb_dm01 = defaultdict(lambda: {"total_wl": 0, "over_6wk": 0, "over_13wk": 0, "name": ""})
        with zipfile.ZipFile(dm01_path) as z:
            for name in z.namelist():
                if name.endswith('.csv'):
                    with z.open(name) as f:
                        for row in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig')):
                            q_code = row.get("Provider Parent Org Code", "")
                            e54 = Q_TO_E54.get(q_code)
                            if not e54:
                                continue
                            total_wl = safe_int(row.get("Total WL", 0))
                            over_13 = safe_int(row.get("13+ Weeks", 0))
                            over_6 = sum(safe_int(row.get(f"{i:02d} < {i+1:02d} Weeks", 0)) for i in range(6, 13)) + over_13
                            icb_dm01[e54]["total_wl"] += total_wl
                            icb_dm01[e54]["over_6wk"] += over_6
                            icb_dm01[e54]["over_13wk"] += over_13
                            icb_dm01[e54]["name"] = row.get("Provider Parent Name", "").strip()

        for e54, v in icb_dm01.items():
            if e54 not in icb_data:
                icb_data[e54] = {"name": v["name"]}
            icb_data[e54]["dm01_6wk_pct"] = round((1 - v["over_6wk"] / v["total_wl"]) * 100, 1) if v["total_wl"] else None
            icb_data[e54]["dm01_total_wl"] = v["total_wl"]
            icb_data[e54]["dm01_13wk_breaches"] = v["over_13wk"]
            icb_data[e54]["dm01_13wk_pct"] = round(v["over_13wk"] / v["total_wl"] * 100, 1) if v["total_wl"] else None

    # A&E: aggregate by parent org — A&E doesn't have Q-code parent, so match via org lookup
    # For A&E we'll just skip ICB aggregation as the data doesn't have parent Q-codes

    # ── 2. Extract LA-level data from Fingertips ──
    ft_dir = BASE_DIR / "Fingertips_Extracts"
    # Key indicators for the map
    map_indicators = {
        "Life_Expectancy_Male": {"label": "Life Expectancy (Male)", "unit": "years"},
        "Life_Expectancy_Female": {"label": "Life Expectancy (Female)", "unit": "years"},
        "Obesity_Prevalence": {"label": "Obesity Prevalence", "unit": "%"},
        "Smoking_Prevalence": {"label": "Smoking Prevalence", "unit": "%"},
        "Diabetes_Prevalence": {"label": "Diabetes Prevalence", "unit": "%"},
        "Depression_Prevalence": {"label": "Depression Prevalence", "unit": "%"},
        "Child_Poverty": {"label": "Child Poverty", "unit": "%"},
        "Suicide_Rate": {"label": "Suicide Rate", "unit": "per 100k"},
        "Deprivation_Score_IMD2019": {"label": "Deprivation (IMD 2019)", "unit": "score"},
        "Alcohol_Admissions": {"label": "Alcohol Admissions", "unit": "per 100k"},
        "Under75_CVD_Mortality": {"label": "Under-75 CVD Mortality", "unit": "per 100k"},
        "Under75_Cancer_Mortality": {"label": "Under-75 Cancer Mortality", "unit": "per 100k"},
        "Physical_Inactivity": {"label": "Physical Inactivity", "unit": "%"},
        "Fuel_Poverty": {"label": "Fuel Poverty", "unit": "%"},
    }

    la_metrics = {}  # metric_name -> {area_code: value}
    icb_metrics = {}  # metric_name -> {area_code: value}  (for fingertips ICB data)

    if ft_dir.exists():
        for ind_name, meta in map_indicators.items():
            fp = ft_dir / f"Fingertips_{ind_name}.csv"
            if not fp.exists():
                continue

            area_vals = {}
            icb_vals = {}
            latest_period = ""
            try:
                with open(fp, 'r', encoding='utf-8-sig', errors='replace') as f:
                    rows = list(csv.DictReader(f))

                # Find latest period for England
                eng_periods = set()
                for row in rows:
                    if row.get("Area Type") == "England" and row.get("Sex", "") in ("Persons", "Male", "Female", ""):
                        tp = row.get("Time period Sortable", row.get("Time period", ""))
                        if tp:
                            eng_periods.add(tp)
                latest_sortable = max(eng_periods) if eng_periods else ""

                for row in rows:
                    tp = row.get("Time period Sortable", row.get("Time period", ""))
                    if tp != latest_sortable:
                        continue
                    sex = row.get("Sex", "")
                    if sex not in ("Persons", "Male", "Female", ""):
                        continue
                    # Prefer "Persons", skip sex-specific unless it's the only option
                    area_code = row.get("Area Code", "")
                    area_type = row.get("Area Type", "")
                    val = safe_float(row.get("Value"))
                    if val is None or not area_code:
                        continue

                    if area_type in ("UA", "County"):
                        # Only store if not already stored, or if this is "Persons"
                        if area_code not in area_vals or sex == "Persons":
                            area_vals[area_code] = round(val, 2)
                    elif area_type == "ICBs":
                        if area_code not in icb_vals or sex == "Persons":
                            # Area code for ICBs in fingertips includes " - QXX" suffix
                            clean_code = area_code.split(" ")[0] if " " in area_code else area_code
                            icb_vals[clean_code] = round(val, 2)

            except Exception as e:
                print(f"  Map warning ({ind_name}): {e}")
                continue

            if area_vals:
                la_metrics[ind_name] = area_vals
            if icb_vals:
                icb_metrics[ind_name] = icb_vals

    # Build LA-level map data
    for metric_name, area_vals in la_metrics.items():
        for code, val in area_vals.items():
            if code not in la_data:
                la_data[code] = {}
            la_data[code][metric_name] = val

    # Add fingertips ICB metrics to icb_data
    for metric_name, area_vals in icb_metrics.items():
        for code, val in area_vals.items():
            if code not in icb_data:
                icb_data[code] = {"name": ""}
            icb_data[code][metric_name] = val

    # ── 3. Extract LA-level data from Demographics/Economy ──
    demo_files = {
        "population": (BASE_DIR / "Demographics" / "ONS_Population_MYE_LA.csv", "Population"),
    }
    econ_files = {
        "median_earnings": (BASE_DIR / "Economy_Employment" / "ONS_Median_Earnings_ASHE_LA.csv", "Median Earnings (£)"),
        "employment_rate": (BASE_DIR / "Economy_Employment" / "ONS_Employment_APS_LA.csv", "Employment Rate"),
    }

    for key, (filepath, label) in {**demo_files, **econ_files}.items():
        if not filepath.exists():
            continue
        try:
            with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
                reader = csv.DictReader(f)
                cols = None
                for i, row in enumerate(reader):
                    if cols is None:
                        cols = list(row.keys())
                    if i > 50000:
                        break
                    code_col = next((c for c in cols if 'geography_code' in c.lower()), None)
                    val_col = next((c for c in cols if c.lower() in ('obs_value', 'value', 'obs value')), None)
                    if not code_col or not val_col:
                        break
                    code = row.get(code_col, "")
                    val = safe_float(row.get(val_col))
                    if code and val is not None and (code.startswith("E06") or code.startswith("E10")):
                        if code not in la_data:
                            la_data[code] = {}
                        la_data[code][key] = round(val, 2)
        except Exception as e:
            print(f"  Map warning ({key}): {e}")

    # Build the metric definitions for the frontend
    metric_defs = {
        "icb": {
            "rtt_18wk_pct": {"label": "RTT 18-Week %", "unit": "%", "target": 92, "colorScale": "green_red_below", "description": "% patients seen within 18 weeks"},
            "dm01_6wk_pct": {"label": "DM01 Within 6 Weeks %", "unit": "%", "target": 99, "colorScale": "green_red_below", "description": "% diagnostics completed within 6 weeks"},
            "dm01_13wk_breaches": {"label": "DM01 13-Week Breaches", "unit": "", "colorScale": "red_green_below", "description": "Number of patients waiting 13+ weeks for diagnostics"},
            "dm01_13wk_pct": {"label": "DM01 13-Week Breach %", "unit": "%", "target": 0, "colorScale": "red_green_below", "description": "% of waiting list breaching 13 weeks"},
        },
        "la": {}
    }
    for ind_name, meta in map_indicators.items():
        polarity = get_polarity(ind_name)
        metric_defs["la"][ind_name] = {
            "label": meta["label"],
            "unit": meta["unit"],
            "colorScale": "red_green_below" if polarity == "bad" else "green_red_below",
            "polarity": polarity,
        }

    # Also add fingertips ICB metrics to icb metric defs
    for ind_name, meta in map_indicators.items():
        if ind_name in icb_metrics and icb_metrics[ind_name]:
            polarity = get_polarity(ind_name)
            metric_defs["icb"][ind_name] = {
                "label": meta["label"],
                "unit": meta["unit"],
                "colorScale": "red_green_below" if polarity == "bad" else "green_red_below",
                "polarity": polarity,
            }

    print(f"  ICB areas: {len(icb_data)}, LA areas: {len(la_data)}")
    print(f"  ICB metrics: {len(metric_defs['icb'])}, LA metrics: {len(metric_defs['la'])}")

    return {
        "icb": icb_data,
        "la": la_data,
        "metric_defs": metric_defs,
    }


# ═══════════════════════════════════════════════════════════════
# MAIN BUILD
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("UK NATIONAL DATA PROCESSOR v2 — KPIs, Trends & RAG")
    print(f"Source: {BASE_DIR}")
    print("=" * 60)

    dashboard = {
        "generated_at": datetime.now().isoformat(),
        "targets": TARGETS,
        "performance": {
            "dm01": process_dm01(),
            "rtt": process_rtt(),
            "ae": process_ae(),
            "cancer": process_cancer(),
        },
        "fingertips": process_fingertips(),
        "demographics": process_demographics(),
        "economy": process_economy(),
        "environment": process_environment(),
        "workforce": process_workforce(),
        "map_data": process_map_data(),
    }

    # Summary stats
    ft = dashboard["fingertips"]
    perf = dashboard["performance"]

    dashboard["summary"] = {
        "total_indicators": len(ft),
        "indicators_worsening": sum(1 for v in ft.values() if v.get("trend_quality") == "bad"),
        "indicators_improving": sum(1 for v in ft.values() if v.get("trend_quality") == "good"),
        "indicators_stable": sum(1 for v in ft.values() if v.get("trend_quality") == "neutral"),
        "performance_rag": {
            "rtt": perf["rtt"]["headline"]["rag"] if perf.get("rtt") else "grey",
            "dm01_6wk": perf["dm01"]["headline"]["rag_6wk"] if perf.get("dm01") else "grey",
            "ae": perf["ae"]["headline"]["rag"] if perf.get("ae") else "grey",
            "cancer_fds": perf["cancer"]["headline"]["fds_rag"] if perf.get("cancer") else "grey",
        }
    }

    with open(OUTPUT, "w") as f:
        json.dump(dashboard, f, indent=2, default=str)

    size = os.path.getsize(OUTPUT)
    print(f"\n{'=' * 60}")
    print(f"DONE: {OUTPUT}")
    print(f"Size: {size/1024/1024:.1f} MB")
    print(f"Fingertips indicators: {len(ft)}")
    print(f"  Worsening: {dashboard['summary']['indicators_worsening']}")
    print(f"  Improving: {dashboard['summary']['indicators_improving']}")
    print(f"  Stable: {dashboard['summary']['indicators_stable']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
