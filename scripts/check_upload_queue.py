"""Browser regression: incremental upload, append, failure and retry (mock responses)."""
import json
from playwright.sync_api import sync_playwright, expect


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    calls = []
    fail = [False]

    def upload(route):
        calls.append(route.request.post_data_buffer)
        if fail[0]:
            route.fulfill(status=400, json={"detail": "Synthetic upload failure"})
            return
        ident = f"test-{len(calls)}"
        route.fulfill(content_type="text/html", body=(
            f'<input name="upload_ids" type="hidden" value=\'["{ident}"]\'>'
            f'<ul class="file-list"><li><strong>{ident}</strong>'
            f'<button data-remove-upload="{ident}">x</button></li></ul>'
        ))

    page.route("**/ui/uploads", upload)
    page.goto("http://127.0.0.1:8765/")
    page.wait_for_function("() => typeof fileList === 'function'")
    page.wait_for_function("() => !currentRun || document.getElementById('chunk-settings') !== null")
    page.evaluate("setTab('overview')")
    page.evaluate("document.getElementById('uploaded').replaceChildren()")
    files = [{"name": f"queue-{n}.png", "mimeType": "image/png", "buffer": b"synthetic"} for n in range(3)]
    page.locator("#files").set_input_files(files)
    expect(page.locator("#pending-files li")).to_have_count(3)
    page.locator("#upload-form [type=submit]").click()
    expect(page.locator("#pending-files li")).to_have_count(0)
    expect(page.locator("#uploaded li")).to_have_count(3)
    assert page.locator("#files").evaluate("el => el.files.length") == 0
    assert len(calls) == 3
    assert all(body.count(b'filename=') == 1 for body in calls)
    fail[0] = True
    page.locator("#files").set_input_files(files[:1])
    page.locator("#upload-form [type=submit]").click()
    expect(page.locator("#notification")).to_have_text("Synthetic upload failure")
    expect(page.locator("#pending-files li")).to_have_count(1)
    expect(page.locator("#uploaded li")).to_have_count(3)
    fail[0] = False
    page.locator("#upload-form [type=submit]").click()
    expect(page.locator("#pending-files li")).to_have_count(0)
    expect(page.locator("#uploaded li")).to_have_count(4)
    ids = json.loads(page.locator('#uploaded [name=upload_ids]').input_value())
    assert len(ids) == len(set(ids)) == 4
    expect(page.locator("#start-form button")).to_be_enabled()
    assert not errors, errors
    browser.close()
    print("PASS: per-file requests, cleared queue, append, failure retention and retry")
