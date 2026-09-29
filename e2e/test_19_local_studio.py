from __future__ import annotations

import time

from apphelp import Engine
from conftest import free_port


def test_studio_is_local_only_and_generation_needs_no_account(work, run_dir):
    from playwright.sync_api import expect, sync_playwright

    engine = Engine(free_port(), work / "local-studio-library", run_dir / "local-studio-engine.log")
    try:
        with engine.client() as client:
            made = client.post("/api/library/projects", json={
                "name": "Local Studio",
                "style": {"kind": "hd-cartoon"},
                "engine": "godot",
            })
            assert made.status_code == 200, made.text
            created = client.post("/api/characters", json={
                "name": "knight",
                "description": "An armoured knight with a blue tabard.",
            })
            assert created.status_code == 200, created.text
            for _ in range(200):
                if client.get("/api/characters/knight").json().get("views"):
                    break
                time.sleep(0.05)
            approved = client.post("/api/characters/knight/approve")
            assert approved.status_code == 200, approved.text

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"{engine.url()}/?token=t0k#/projects")
            expect(page).to_have_title("SpriteGuru Studio")
            expect(page.get_by_test_id("topbar-brand-mark")).to_have_attribute("alt", "SpriteGuru")
            assert page.get_by_test_id("account-chip").count() == 0
            assert page.get_by_test_id("sync-pill").count() == 0
            assert page.get_by_test_id("cloud-row").count() == 0
            assert page.get_by_test_id("nav-library").count() == 0

            page.goto(f"{engine.url()}/?token=t0k#/settings")
            expect(page.get_by_role("heading", name="Provider keys")).to_be_visible()
            assert page.get_by_text("Cloud sync").count() == 0
            assert page.get_by_text("SpritePlay account").count() == 0

            page.goto(f"{engine.url()}/?token=t0k#/characters?new=1")
            expect(page.get_by_test_id("character-form-submit")).to_be_enabled()

            page.goto(f"{engine.url()}/?token=t0k#/builder")
            expect(page.get_by_test_id("builder-plan")).to_be_enabled()
            page.get_by_test_id("builder-plan").click()
            expect(page.get_by_test_id("builder-generate")).to_be_enabled(timeout=15000)
            browser.close()
    finally:
        engine.stop()
