"""Opt-in REAL local browser/Qwen/BGE/Qdrant check; start server with the smoke config first."""
import json
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from conftest import make_scanned_pdf


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    folder = ROOT / "data" / "web-smoke"
    folder.mkdir(parents=True, exist_ok=True)
    fixture = folder / "synthetic-browser-test.pdf"
    make_scanned_pdf(fixture)
    evidence = {"fixture": "SYNTHETIC TEST ONLY, one scanned page", "base_url": base, "browser_errors": [], "http_errors": [], "states": [], "sse_requested": False}
    started = time.perf_counter()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1512, "height": 1050}, device_scale_factor=1)
        page.on("pageerror", lambda error: evidence["browser_errors"].append(str(error)))

        def response(res):
            if res.status >= 400:
                evidence["http_errors"].append({"url": res.url, "status": res.status})
            if re.search(r"/api/runs/[a-f0-9]+$", res.url) and res.status == 200:
                try:
                    evidence["states"].append(res.json()["status"])
                except PlaywrightError as exc:
                    if "navigated away" not in str(exc):
                        raise
                    evidence["discarded_navigation_responses"] = evidence.get("discarded_navigation_responses", 0) + 1

        page.on("response", response)
        page.on("request", lambda req: evidence.update(sse_requested=True) if req.url.endswith("/events") else None)
        page.goto(base)
        page.locator("#files").set_input_files(str(fixture))
        expect(page.locator("#pending-files")).to_contain_text(fixture.name)
        old_upload = page.locator('#uploaded input[name="upload_ids"]')
        old_ids = old_upload.input_value() if old_upload.count() else ""
        with page.expect_response(re.compile(r"/ui/uploads$")):
            page.locator("#upload-form button[type=submit]").click()
        expect(page.locator('#uploaded input[name="upload_ids"]')).to_be_attached()
        expect(page.locator('#uploaded input[name="upload_ids"]')).not_to_have_value(old_ids)
        previous_id = page.locator("#run-select").input_value()
        with page.expect_response(re.compile(r"/ui/runs$")):
            page.locator("#start-form button").click()
        expect(page.locator("#run-select")).not_to_have_value(previous_id, timeout=20000)
        expect(page.locator("#run-status")).to_have_text(re.compile("RUNNING|COMPLETED"), timeout=30000)
        first_id = page.locator("#run-select").input_value()
        page.reload()
        expect(page.locator("#run-select")).to_have_value(first_id)
        expect(page.locator("#run-status")).to_have_text("COMPLETED", timeout=240000)
        expect(page.locator("#page-select option")).to_have_count(2)
        page.locator("#page-select").select_option(index=1)
        expect(page.locator("#viewer .normalized")).to_contain_text("оплаты", timeout=20000)
        expect(page.locator("#viewer .page-image img")).to_be_visible()
        assert page.locator("#viewer .page-image img").evaluate("image => image.complete && image.naturalWidth > 0")
        expect(page.locator("#chunk-content .chunk-card").first).to_be_attached()
        page.locator("#recognition").screenshot(path=str(folder / "recognition.png"))
        page.locator("#question").fill("Какой срок оплаты по договору?")
        page.locator(".search-form button").click()
        expect(page.locator("#search-results .source-page").first).to_be_visible(timeout=180000)
        page.locator("#search-results .source-page").first.click()
        expect(page.locator("#viewer .normalized")).to_be_visible()
        page.locator("#viewer .raw-details summary").click()
        expect(page.locator("#viewer .raw-details pre")).to_contain_text('"model"')
        page.locator("#viewer .raw-details summary").click()
        expect(page.locator("#evaluation")).to_contain_text("Evaluation dataset not configured")
        with page.expect_popup() as popup:
            page.get_by_role("link", name="Открыть HTML-отчёт").click()
        report = popup.value
        report.wait_for_load_state()
        assert report.locator("body").inner_text()
        report.close()
        page.screenshot(path=str(folder / "web-ui.png"), full_page=True)
        evidence["run"] = page.request.get(f"{base}/api/runs/{first_id}").json()
        evidence["system"] = page.request.get(f"{base}/api/system/status").json()
        evidence["search"] = page.request.post(f"{base}/api/runs/{first_id}/search", headers={"X-RAG-Request": "1"}, data={"question": "Какой срок оплаты по договору?"}).json()
        # Repeat through the same browser form: exactly the same uploaded corpus.
        page.locator("#start-form button").click()
        expect(page.locator("#run-select")).not_to_have_value(first_id, timeout=20000)
        expect(page.locator("#run-status")).to_have_text("COMPLETED", timeout=180000)
        second_id = page.locator("#run-select").input_value()
        evidence["repeat"] = page.request.get(f"{base}/api/runs/{second_id}").json()
        assert evidence["repeat"]["status"] == "COMPLETED", evidence["repeat"]
        assert evidence["run"]["cache_misses"] == 1 and evidence["run"]["cache_hits"] == 0, evidence["run"]
        assert evidence["repeat"]["cache_hits"] == 1 and evidence["repeat"]["embeddings_created"] == 0, evidence["repeat"]
        assert evidence["sse_requested"] and "RUNNING" in evidence["states"]
        assert not evidence["browser_errors"] and not evidence["http_errors"], evidence
        evidence["elapsed_seconds"] = time.perf_counter() - started
        (folder / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    print(json.dumps({"status": "PASSED", "first_run": first_id, "repeat_run": second_id, "seconds": evidence["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
