"""Saved hard constraints, including legacy-client preservation and real SQL."""
import json
import unittest
from pathlib import Path

from jobagent.mobile.discovery import SUPPORTED_REMOTE_COUNTRY_CODES
import test_mobile_api as api
import test_mobile_readiness_sql as packet_sql

RULES = {"remote_country_policy": "require_explicit", "remote_country_codes": ["AE"], "sponsorship_policy": "require_explicit"}
ROOT = Path(__file__).resolve().parents[1]


class PreferenceApiTests(unittest.TestCase):
    setUp = api.MobileAPITests.setUp
    request = api.MobileAPITests.request

    def test_save_roundtrip_and_legacy_update_preserve_hard_rules(self):
        saved = self.request("PUT", "preferences", json={"sponsorship_required": True, "discovery_rules": RULES})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["discovery_rules"], RULES)
        old_client = self.request("PUT", "preferences", json={"sponsorship_required": True, "target_titles": ["Finance Manager"]})
        self.assertEqual(old_client.status_code, 200)
        self.assertEqual(old_client.json()["discovery_rules"], RULES)
        self.assertNotIn("discovery_rules", json.loads(self.supabase.requests[-1].content))

    def test_legacy_client_cannot_implicitly_weaken_sponsorship(self):
        self.supabase.tables["job_preferences"][0].update(sponsorship_required=True, discovery_rules=RULES)
        response = self.request("PUT", "preferences", json={"target_titles": ["Designer"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supabase.tables["job_preferences"][0]["discovery_rules"], RULES)

    def test_invalid_rules_and_owner_injection_reject_before_write(self):
        for rules in (None, {"remote_country_codes": ["XX"]}, {"remote_country_codes": ["US", "US"]},
                      {"remote_country_codes": ["uk"]}, {"remote_country_policy": "any"},
                      {"user_id": api.USER_B}, {"sponsorship_policy": "require_explicit"}):
            with self.subTest(rules=rules):
                self.assertEqual(self.request("PUT", "preferences", json={"discovery_rules": rules}).status_code, 422)

    def test_discovery_rules_are_tenant_private(self):
        self.request("PUT", "preferences", json={"sponsorship_required": True, "discovery_rules": RULES})
        # Workspace bootstrap is the supported preference read; never inspect A through B.
        other = self.request("GET", "bootstrap", headers={"Authorization": "Bearer session-b"})
        self.assertEqual(other.status_code, 200)
        self.assertNotEqual(other.json()["preferences"].get("discovery_rules"), RULES)

    def test_country_picker_matches_supported_backend_codes(self):
        import re
        source = (ROOT / "web/src/beta/countries.ts").read_text()
        codes = re.search(r'const codes = "([A-Z ]+)"', source)[1].split()
        self.assertEqual(set(codes), SUPPORTED_REMOTE_COUNTRY_CODES)
        self.assertEqual(len(codes), len(set(codes)))


class DiscoveryRulesPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        packet_sql.ReadinessPostgresTests.setUpClass.__func__(cls)

    transaction = packet_sql.ReadinessPostgresTests.transaction

    def test_default_and_owner_update_preserve_on_omission(self):
        self.transaction("""
          do $$ begin
            assert (select discovery_rules->>'remote_country_policy'='review' from public.job_preferences where user_id=auth.uid());
            update public.job_preferences set sponsorship_required=true,
              discovery_rules='{"remote_country_policy":"require_explicit","remote_country_codes":["AE"],"sponsorship_policy":"require_explicit"}'
              where user_id=auth.uid();
            insert into public.job_preferences(user_id,target_titles) values(auth.uid(),array['Finance'])
              on conflict(user_id) do update set target_titles=excluded.target_titles;
            assert (select discovery_rules->>'sponsorship_policy'='require_explicit' from public.job_preferences where user_id=auth.uid());
            assert (select count(*)=1 from public.job_preferences);
          end $$;
        """)

    def test_malformed_and_contradictory_rules_fail_database_validation(self):
        self.transaction("""
          do $$ declare bad jsonb; begin
            for bad in select value from jsonb_array_elements('[null,[],{},42,
              {"remote_country_policy":"open","remote_country_codes":[],"sponsorship_policy":"review"},
              {"remote_country_policy":"review","remote_country_codes":["AE","AE"],"sponsorship_policy":"review"},
              {"remote_country_policy":"review","remote_country_codes":[42],"sponsorship_policy":"review"}]') loop
              assert public.mobile_valid_discovery_rules(bad)=false;
            end loop;
            begin
              update public.job_preferences set discovery_rules='{"remote_country_policy":"review","remote_country_codes":[],"sponsorship_policy":"require_explicit"}' where user_id=auth.uid();
              raise exception 'Contradictory sponsorship accepted';
            exception when check_violation then null; end;
          end $$;
        """)
