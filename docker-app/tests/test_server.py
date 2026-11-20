"""Focused tests for the standalone data, scoring and configuration layer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from zoneinfo import ZoneInfo
import os
import sys

TEST_DATA = TemporaryDirectory()
os.environ["DATA_DIR"] = TEST_DATA.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class SolarAndScoreTests(TestCase):
    def test_preference_weights_normalize_and_change_photo_score(self):
        prefs = server.normalize_preferences({"photo_light": 100, "photo_sky": 0, "photo_weather": 0})
        self.assertEqual(sum(prefs[key] for key in ("photo_light", "photo_sky", "photo_weather")), 100)
        day = {"cloud_cover_mean": 20, "precipitation_probability_max": 10, "wind_speed_kmh": 0}
        light = {"score": 80}
        clear = server.photo_breakdown(day, light, {"cloud_cover": 20, "precipitation_probability": 10}, False, prefs)
        self.assertEqual(clear["score"], 80)
        dramatic = server.photo_breakdown(day, light, {"cloud_cover": 20, "precipitation_probability": 10}, False,
            {"photo_light": 0, "photo_sky": 100, "photo_weather": 0, "sky_style": "dramatic"})
        self.assertNotEqual(clear["score"], dramatic["score"])

    def test_solar_elevation_changes_by_time_and_location(self):
        instant = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        london_noon = server.solar_elevation(instant, 51.5, -0.12)
        london_midnight = server.solar_elevation(instant - timedelta(hours=12), 51.5, -0.12)
        new_york_noon = server.solar_elevation(instant, 40.7, -74.0)
        self.assertGreater(london_noon, 50)
        self.assertLess(london_midnight, -10)
        self.assertNotEqual(round(london_noon), round(new_york_noon))

    def test_moon_phase_estimate(self):
        self.assertEqual(server.moon_illumination(0.0), 0)
        self.assertEqual(server.moon_illumination(0.5), 100)
        self.assertEqual(server.moon_illumination(None), None)

    def test_explanations_include_score_inputs_and_night_gate(self):
        day = {"cloud_cover_mean": 35, "precipitation_probability_max": 15,
               "wind_speed_kmh": 8}
        conditions = {"cloud_cover": 28, "cloud_cover_low": 15, "cloud_cover_mid": 20,
                      "cloud_cover_high": 25, "precipitation_probability": 10,
                      "wind_speed_kmh": 6}
        light = server.light_breakdown(day, "sunset", conditions)
        weather = server.weather_breakdown(day, conditions)
        photo = server.photo_breakdown(day, light, conditions, night=False)
        night_photo = server.photo_breakdown(day, light, conditions, night=True)
        self.assertGreater(photo["score"], 0)
        self.assertEqual(round(sum(photo["contributions"].values())), photo["score"])
        self.assertEqual(night_photo["score"], 0)
        self.assertIn("below -6°", night_photo["night_gate"])
        self.assertEqual(weather["score"], round(sum(weather["contributions"].values())))

    def test_night_opportunity_is_astro_only(self):
        tomorrow = datetime.now(ZoneInfo("Europe/London")).date() + timedelta(days=1)
        day_text = tomorrow.isoformat()
        hourly = {"time": [f"{day_text}T{hour:02d}:00" for hour in range(24)]}
        for field, value in {
            "cloud_cover": 20, "cloud_cover_low": 10, "cloud_cover_mid": 15,
            "cloud_cover_high": 20, "precipitation_probability": 5, "precipitation": 0,
            "wind_speed_10m": 5,
        }.items():
            hourly[field] = [value] * 24
        weather = {"timezone": "Europe/London", "hourly": hourly, "days": [{
            "date": day_text, "condition": "Clear", "temperature_max_c": 12,
            "temperature_min_c": 5, "precipitation_probability_max": 5,
            "precipitation_mm": 0, "wind_speed_kmh": 5, "cloud_cover_mean": 20,
            "uv_index_max": 1, "sunrise": f"{day_text}T07:30",
            "sunset": f"{day_text}T16:30", "moon_phase": 0.5,
        }]}
        birds = {"species": [{"taxon_key": "1"}], "species_count": 30, "years_with_records": 4}
        opportunities = server.build_opportunities(weather, birds, {"latitude": 53.27, "longitude": -3.22})
        astro = [item for item in opportunities if item["astro"]]
        self.assertTrue(astro)
        for item in astro:
            self.assertEqual(item["photogenic"]["score"], 0)
            self.assertIsNone(item["birding"]["score"])
        self.assertTrue(any(item["photogenic"]["score"] > 0 for item in opportunities))

    def test_polar_night_without_sunrise_or_sunset_still_gets_astro_window(self):
        tomorrow = datetime.now(ZoneInfo("Europe/London")).date() + timedelta(days=1)
        day_text = tomorrow.isoformat()
        hourly = {"time": [f"{day_text}T{hour:02d}:00" for hour in range(24)]}
        for field in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
                      "precipitation_probability", "precipitation", "wind_speed_10m"):
            hourly[field] = [10] * 24
        weather = {"timezone": "Europe/London", "hourly": hourly, "days": [{
            "date": day_text, "condition": "Clear", "temperature_max_c": -10,
            "temperature_min_c": -20, "precipitation_probability_max": 0,
            "precipitation_mm": 0, "wind_speed_kmh": 3, "cloud_cover_mean": 10,
            "uv_index_max": 0, "sunrise": None, "sunset": None, "moon_phase": 0,
        }]}
        opportunities = server.build_opportunities(weather, None, {"latitude": 69.65, "longitude": 18.96})
        self.assertEqual(len(opportunities), 1)
        self.assertIsNotNone(opportunities[0]["astro"])
        self.assertEqual(opportunities[0]["photogenic"]["score"], 0)

    def test_tides_group_in_station_local_dates(self):
        tides = {"events": [
            {"time": "2026-11-14T23:30:00Z", "type": "high", "height_m": 7.3},
            {"time": "2026-11-15T01:20:00Z", "type": "low", "height_m": 2.1},
        ]}
        grouped = server.tide_calendar(tides, "Europe/London")
        self.assertEqual([entry["date"] for entry in grouped], ["2026-11-14", "2026-11-15"])
        self.assertEqual(grouped[0]["events"][0]["type"], "high")


class LocationStoreTests(TestCase):
    def test_create_update_delete_location(self):
        with TemporaryDirectory() as directory:
            store = server.LocationStore(Path(directory) / "locations.json")
            first = store.save({"name": "Coast", "latitude": 53.27, "longitude": -3.22,
                                "lookahead_days": 14, "radius_km": 25, "shoreline": True})
            self.assertEqual(store.get(first["id"])["name"], "Coast")
            self.assertEqual(sum(store.get(first["id"])["preferences"][key] for key in
                ("photo_light", "photo_sky", "photo_weather")), 100)
            updated = store.save({"name": "Cliff", "latitude": 53.30, "longitude": -3.20,
                                 "lookahead_days": 7, "radius_km": 10, "shoreline": False}, first["id"])
            self.assertEqual(updated["name"], "Cliff")
            self.assertTrue(store.delete(first["id"]))
            self.assertFalse(store.delete(first["id"]))

    def test_reject_invalid_location_options(self):
        with TemporaryDirectory() as directory:
            store = server.LocationStore(Path(directory) / "locations.json")
            with self.assertRaises(ValueError):
                store.save({"name": "Bad", "latitude": 91, "longitude": 0})
            with self.assertRaises(ValueError):
                store.save({"name": "Bad", "latitude": 0, "longitude": 0, "lookahead_days": 30})


class StaticAssetTests(TestCase):
    def test_html_contains_all_javascript_element_references(self):
        from html.parser import HTMLParser
        import re

        class IdReader(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for key, value in attrs if key == "id")

        html = (server.STATIC / "index.html").read_text(encoding="utf-8")
        js = (server.STATIC / "app.js").read_text(encoding="utf-8")
        reader = IdReader()
        reader.feed(html)
        references = set(re.findall(r'\$\("([^"]+)"\)', js))
        self.assertEqual(len(reader.ids), len(set(reader.ids)), "HTML IDs must be unique")
        self.assertEqual(references - set(reader.ids), set(), "JavaScript references an absent HTML element")

    def test_stylesheet_braces_are_balanced_and_no_external_assets(self):
        css = (server.STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("{"), css.count("}"))
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)


class HttpApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_static_page_are_served(self):
        import urllib.request
        health = json_load(urllib.request.urlopen(self.base + "/api/health"))
        self.assertEqual(health["status"], "ok")
        page = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertIn("Find the light.", page)
        self.assertIn("30-day tide calendar", page)

    def test_optional_basic_auth_protects_ui_and_api_but_not_health(self):
        import base64
        import urllib.error
        import urllib.request
        from unittest.mock import patch
        with patch.dict(os.environ, {"APP_USERNAME": "tester", "APP_PASSWORD": "secret"}):
            health = urllib.request.urlopen(self.base + "/api/health")
            self.assertEqual(health.status, 200)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(self.base + "/api/locations")
            self.assertEqual(raised.exception.code, 401)
            token = base64.b64encode(b"tester:secret").decode()
            request = urllib.request.Request(self.base + "/api/locations", headers={"Authorization": f"Basic {token}"})
            self.assertEqual(urllib.request.urlopen(request).status, 200)


def json_load(response):
    import json
    return json.loads(response.read().decode())


if __name__ == "__main__":
    main()
