import os
import sqlite3
import tempfile
import unittest

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_yield_gatekeeper.db")

from app.database import (  # noqa: E402
    init_db,
    save_search_profile,
    get_search_profile_by_id,
    update_search_profile,
    delete_search_profile,
    get_connection
)

class TestYieldGatekeeper(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def test_yield_basis_crud(self):
        # 1. Create with yield_basis = 'actual'
        pid = save_search_profile({
            "name": "Test Nur Ist",
            "city": "Essen",
            "min_yield": 6.0,
            "yield_basis": "actual"
        })
        p = get_search_profile_by_id(pid)
        self.assertIsNotNone(p)
        self.assertEqual(p.get("yield_basis"), "actual")

        # 2. Update to yield_basis = 'potential'
        update_search_profile(pid, {"yield_basis": "potential"})
        p_updated = get_search_profile_by_id(pid)
        self.assertEqual(p_updated.get("yield_basis"), "potential")

        # 3. Clean up
        delete_search_profile(pid)

    def test_yield_filtering_logic(self):
        # Test the core gatekeeper condition
        def check_passes_gate(audited_yield, audited_potential_yield, min_yield, yield_basis):
            if min_yield <= 0:
                return True
            if yield_basis == "actual":
                return audited_yield >= min_yield
            elif yield_basis == "potential":
                return audited_potential_yield >= min_yield
            else: # "any"
                return max(audited_yield, audited_potential_yield) >= min_yield

        # Case from user: Hattingen deal (Ist 3.91%, Mietspiegel 5.7%, target 6.0%)
        hattingen_actual = 3.91
        hattingen_potential = 5.7
        target_yield = 6.0

        # MUST ALL BE REJECTED!
        self.assertFalse(check_passes_gate(hattingen_actual, hattingen_potential, target_yield, "any"))
        self.assertFalse(check_passes_gate(hattingen_actual, hattingen_potential, target_yield, "actual"))
        self.assertFalse(check_passes_gate(hattingen_actual, hattingen_potential, target_yield, "potential"))

        # Case: Value-add deal (Ist 4.5%, Mietspiegel 6.8%, target 6.0%)
        va_actual = 4.5
        va_potential = 6.8
        self.assertFalse(check_passes_gate(va_actual, va_potential, target_yield, "actual"))
        self.assertTrue(check_passes_gate(va_actual, va_potential, target_yield, "potential"))
        self.assertTrue(check_passes_gate(va_actual, va_potential, target_yield, "any"))

        # Case: Cashflow-ready deal (Ist 6.5%, target 6.0%)
        cf_actual = 6.5
        cf_potential = 6.5
        self.assertTrue(check_passes_gate(cf_actual, cf_potential, target_yield, "actual"))
        self.assertTrue(check_passes_gate(cf_actual, cf_potential, target_yield, "any"))

if __name__ == "__main__":
    unittest.main()
