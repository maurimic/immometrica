import urllib.request
import json

def test_calculator_afa_endpoint():
    # 1. Standard 50-year AfA test
    payload_standard = {
        "price": 150000,
        "rent": 750,
        "living_space": 65,
        "include_afa": True,
        "building_share_percent": 80,
        "depreciation_years": 50,
        "marginal_tax_rate": 42,
        "additional_costs": 45,
        "interest_rate": 3.6,
        "repayment_rate": 2.0,
        "equity_value": 20,
        "equity_type": "percent"
    }

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/calculator/calculate",
        data=json.dumps(payload_standard).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode())
        print("=== Standard AfA 50 Jahre ===")
        print("Kaufpreis:", res["price"], "EUR")
        print("Gesamtinvestition:", res["total_investment"], "EUR")
        print("Gebäudewert (80%):", res["depreciable_base"], "EUR")
        print("AfA-Satz:", res["afa_rate_percent"], "%")
        print("Jährliche AfA:", res["annual_afa"], "EUR")
        print("Cashflow vor Steuern:", res["monthly_cashflow"], "EUR/M")
        print("Steuerliches Ergebnis V+V:", res["taxable_income_vv"], "EUR/J")
        print("Monatlicher Steuereffekt:", res["monthly_tax_effect"], "EUR/M")
        print("Effektiver Cashflow nach Steuern:", res["monthly_cashflow_after_tax"], "EUR/M")
        assert res["include_afa"] == True
        assert res["afa_rate_percent"] == 2.0
        assert res["depreciation_years"] == 50.0

    # 2. Shortened useful life appraisal test (Gutachten mit 25 Jahren = 4% AfA)
    payload_gutachten = {
        "price": 150000,
        "rent": 750,
        "living_space": 65,
        "include_afa": True,
        "building_share_percent": 80,
        "depreciation_years": 25,
        "marginal_tax_rate": 42,
        "additional_costs": 45,
        "interest_rate": 3.6,
        "repayment_rate": 2.0,
        "equity_value": 20,
        "equity_type": "percent"
    }
    req2 = urllib.request.Request(
        "http://127.0.0.1:8000/api/calculator/calculate",
        data=json.dumps(payload_gutachten).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req2) as resp2:
        res2 = json.loads(resp2.read().decode())
        print("\n=== Restnutzungsdauer-Gutachten 25 Jahre (4% AfA) ===")
        print("AfA-Satz:", res2["afa_rate_percent"], "%")
        print("Jährliche AfA:", res2["annual_afa"], "EUR (verdoppelt!)")
        print("Steuerliches Ergebnis V+V:", res2["taxable_income_vv"], "EUR/J")
        print("Monatlicher Steuereffekt:", res2["monthly_tax_effect"], "EUR/M")
        print("Effektiver Cashflow nach Steuern:", res2["monthly_cashflow_after_tax"], "EUR/M")
        assert res2["afa_rate_percent"] == 4.0
        assert res2["annual_afa"] == res["annual_afa"] * 2
        # Tax effect with 4% AfA must be substantially higher than 2% AfA
        assert res2["monthly_cashflow_after_tax"] > res["monthly_cashflow_after_tax"]
        print("\n[OK] Gutachten-Verkürzung verbessert den Cashflow nach Steuern signifikant!")

    print("\n>>> ALL CALCULATOR AFA & TAX TESTS PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    test_calculator_afa_endpoint()
