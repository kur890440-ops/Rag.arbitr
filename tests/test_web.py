"""HTTP/application contracts: real pipeline + local Qdrant, injected recognition/embedding."""
import io
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from conftest import FakeEmbedding, make_scanned_pdf
from test_recognition import FakeRuntime
from rag_arbiter.pipeline import Pipeline
from rag_arbiter.recognition import Qwen3VLRecognitionProvider
from rag_arbiter.web.app import create_app
from rag_arbiter.application.runs import RunService, TERMINAL
from rag_arbiter.application.uploads import contained

HEADERS = {"X-RAG-Request": "1"}


def test_fatal_error_is_not_hidden_by_previous_document_error(config):
    class FailingPipeline:
        def __init__(self, cfg, reporter=None):
            self.reporter = reporter

        def day21(self, *args, **kwargs):
            self.reporter.emit("document_error", "RECOGNITION", "Bad page",
                               error="Duplicate reading order across blocks/tables", document="first.pdf")
            self.reporter.emit("stage_started", "REPORTING", "Report")
            raise OSError("Synthetic disk write failure")

        def close(self):
            pass

    with TestClient(create_app(config, FailingPipeline), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config)
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        run = wait_run(client, rid)
        assert run["status"] == "FAILED" and run["stage"] == "REPORTING"
        assert "Qwen" not in run["last_message"]
        with client.app.state.runs.db() as store:
            errors = [e for e in store.all("processing_run_errors") if e["run_id"] == rid and e.get("fatal")]
        assert errors[0]["error_type"] == "OSError"
        assert "error" not in errors[0]  # Raw exception stays in local server logs.


def test_retry_endpoint_reuses_uploads_and_success_pages(completed):
    client,runtime,config,run=completed
    calls=runtime.calls
    rid=run['run_id']
    result=client.post(f'/api/runs/{rid}/retry',json={'mode':'failed'})
    assert result.status_code==200
    repeated=wait_run(client,result.json()['run_id'])
    assert repeated['status']=='COMPLETED' and runtime.calls==calls
    assert repeated['upload_ids']==run['upload_ids']
    assert client.post(f'/api/runs/{rid}/retry',json={'mode':'force'}).status_code==400
    page=client.get(f'/api/runs/{rid}/pages').json()[0]
    forced=client.post(f'/api/runs/{rid}/retry',json={'mode':'force','document_id':page['document_id'],'page_number':page['page_number']})
    assert wait_run(client,forced.json()['run_id'])['status']=='COMPLETED'
    assert runtime.calls==calls+1


def test_cli_redirected_output_accepts_unicode_filenames(monkeypatch):
    import sys
    from rag_arbiter.cli import main
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1251", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    filename = "Отчет_о_своеи\u0306_деятельности.pdf"
    print(filename)
    stream.flush()
    assert filename in buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize("data", [{}, {"upload_ids": "[]"}, {"upload_ids": "{bad"}, {"upload_ids": "42"}])
def test_start_without_uploaded_selection_has_actionable_error(web, data):
    client, _, _ = web
    response = client.post("/ui/runs", data=data)
    assert response.status_code == 400
    assert "файл" in response.json()["detail"]
    assert client.get("/api/runs").json() == []
    html = client.get("/").text
    assert 'type="submit" disabled>Запустить обработку' in html


def factory(runtime):
    return lambda cfg, reporter=None: Pipeline(cfg, FakeEmbedding(), Qwen3VLRecognitionProvider(cfg, runtime), reporter)


@pytest.fixture
def web(config):
    runtime = FakeRuntime()
    app = create_app(config, factory(runtime))
    with TestClient(app, base_url="http://127.0.0.1", headers=HEADERS) as client:
        yield client, runtime, config


def upload_pdf(client, config, pages=1):
    path = config.corpus_path / "sample.pdf"
    make_scanned_pdf(path, pages)
    result = client.post("/api/uploads", files={"files": (path.name, path.read_bytes(), "application/pdf")})
    assert result.status_code == 201, result.text
    return result.json()["uploads"][0]["upload_id"]


def wait_run(client, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in TERMINAL:
            # Terminal event is persisted before the operation lock/resources are released.
            client.app.state.runs.futures[run_id].result(timeout=10)
            return run
        time.sleep(.02)
    raise AssertionError("Background run timed out")


@pytest.fixture
def completed(web):
    client, runtime, config = web
    upload_id = upload_pdf(client, config)
    response = client.post("/api/runs", json={"upload_ids": [upload_id]})
    assert response.status_code == 202
    run = wait_run(client, response.json()["run_id"])
    assert run["status"] == "COMPLETED", run
    return client, runtime, config, run


def test_app_starts_with_local_assets(web):
    client, _, _ = web
    html = client.get("/")
    assert html.status_code == 200 and "NO RUN YET" in html.text
    assert "Content-Security-Policy" in html.headers
    for name in ("htmx.min.js", "app.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200
    assert "https://" not in html.text


def test_default_cli_bind_is_loopback(config):
    from rag_arbiter.cli import main
    with patch("rag_arbiter.cli.Config.load", return_value=config), patch("uvicorn.run") as serve:
        assert main(["web"]) == 0
    assert serve.call_args.kwargs == {"host": "127.0.0.1", "port": 8765, "workers": 1}


def test_cli_network_bind(config):
    from rag_arbiter.cli import main
    with patch("rag_arbiter.cli.Config.load", return_value=config), patch("uvicorn.run") as serve:
        assert main(["web", "--host", "0.0.0.0"]) == 0
    assert serve.call_args.kwargs["host"] == "0.0.0.0"


def test_network_host_and_origin(config):
    from types import SimpleNamespace
    import socket
    config.web_host = "0.0.0.0"
    with patch("rag_arbiter.web.app.psutil.net_if_addrs", return_value={
        "LAN": [SimpleNamespace(family=socket.AF_INET, address="192.168.11.41")]
    }):
        app = create_app(config)
    with TestClient(app, base_url="http://192.168.11.41:8765") as client:
        assert client.get("/").status_code == 200
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
        headers = {"X-RAG-Request": "1", "Origin": "http://192.168.11.41:8765"}
        assert client.post("/api/runs", json={"upload_ids": []}, headers=headers).status_code == 400
        headers["Origin"] = "http://evil.example"
        assert client.post("/api/runs", json={"upload_ids": []}, headers=headers).status_code == 403


def test_upload_pdf_valid_and_original_separate(web):
    client, _, cfg = web
    uid = upload_pdf(client, cfg)
    assert (cfg.uploads_path / uid / "sample.pdf").exists()
    assert not list(cfg.cache_path.glob("**/sample.pdf"))


@pytest.mark.parametrize("name,mime,data", [
    ("bad.exe", "application/octet-stream", b"MZ"), ("../bad.pdf", "application/pdf", b"%PDF-"),
    ("..\\bad.pdf", "application/pdf", b"%PDF-"), ("CON.pdf", "application/pdf", b"%PDF-"),
    ("x.pdf", "image/png", b"%PDF-"), ("x.pdf", "application/pdf", b"<script>"),
    ("x.png", "image/png", b"broken"), ("x.pdf", "application/pdf", b"")])
def test_upload_invalid_filename_mime_content(web, name, mime, data):
    client, _, cfg = web
    assert client.post("/api/uploads", files={"files": (name, data, mime)}).status_code == 400
    with client.app.state.runs.db() as db:
        assert not db.all("uploads")


@pytest.mark.parametrize("extension,format,mime", [("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("jpeg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp")])
def test_supported_images(web, extension, format, mime):
    client, _, _ = web
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(buffer, format=format)
    response = client.post("/api/uploads", files={"files": (f"page.{extension}", buffer.getvalue(), mime)})
    assert response.status_code == 201
    rid = client.post("/api/runs", json={"upload_ids": [response.json()["uploads"][0]["upload_id"]]}).json()["run_id"]
    assert wait_run(client, rid)["processed_pages"] == 1


def test_upload_size_limit(web):
    client, _, cfg = web
    cfg.max_upload_size = 4
    response = client.post("/api/uploads", files={"files": ("x.pdf", b"%PDF-" + b"x"*100, "application/pdf")})
    assert response.status_code == 400 and "max_upload_size" in response.text
    assert not list(cfg.uploads_path.glob("*/*"))


def test_multiple_uploads_and_unknown_ids(web):
    client, _, cfg = web
    path = cfg.corpus_path / "source.pdf"
    make_scanned_pdf(path)
    response = client.post("/api/uploads", files=[("files", (name, path.read_bytes(), "application/pdf")) for name in ("a.pdf", "b.pdf")])
    assert len(response.json()["uploads"]) == 2
    for ids in ([], ["../../secrets"], ["a", "a"]):
        assert client.post("/api/runs", json={"upload_ids": ids}).status_code == 400


def test_host_and_cross_origin_guard(web):
    client, _, _ = web
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/runs", json={"upload_ids": []}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/runs", json={}, headers={"X-RAG-Request": ""}).status_code == 403


def test_explicit_upload_request_limit(config):
    config.max_upload_files = 2
    config.max_upload_size = 100
    with TestClient(create_app(config), base_url="http://127.0.0.1", headers=HEADERS) as client:
        assert client.post("/api/uploads", headers={"Content-Length": "999999999999"}).status_code == 413


def test_upload_and_process_43_documents(web):
    client, runtime, cfg = web
    assert cfg.max_upload_files == 0 and cfg.max_upload_size == 100 * 1024 * 1024
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(buffer, format="PNG")
    files = [("files", (f"page-{i}.png", buffer.getvalue(), "image/png")) for i in range(43)]
    response = client.post("/api/uploads", files=files)
    assert response.status_code == 201, response.text
    ids = [item["upload_id"] for item in response.json()["uploads"]]
    assert len(ids) == 43
    rid = client.post("/api/runs", json={"upload_ids": ids}).json()["run_id"]
    run = wait_run(client, rid)
    assert run["status"] == "COMPLETED", run
    assert run["processed_documents"] == run["processed_pages"] == 43
    assert runtime.calls == 43 and len(run["index_run_ids"]) == 2


def test_no_hidden_multipart_1000_file_limit(web):
    client, _, _ = web
    with patch.object(client.app.state.uploads, "save", return_value={"upload_id": "test"}) as save:
        response = client.post("/api/uploads", files=[("files", (f"file-{i}.pdf", b"test", "application/pdf")) for i in range(1001)])
    assert response.status_code == 201 and save.call_count == 1001


@pytest.mark.parametrize("size_mib,expected", [(61, 201), (101, 400)])
def test_unlimited_count_preserves_file_size_limit(web, size_mib, expected):
    client, _, _ = web
    # Removing the file-count cap must not remove the existing per-file size cap.
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(buffer, format="PNG")
    payload = buffer.getvalue() + b"\0" * (size_mib * 1024 * 1024)
    response = client.post("/api/uploads", files={"files": ("large.png", payload, "image/png")})
    assert response.status_code == expected, response.text
    if expected == 400:
        assert "max_upload_size" in response.text
    else:
        assert response.json()["uploads"][0]["size"] == len(payload)


def test_processing_run_and_progress_persist(completed):
    client, _, _, run = completed
    with client.app.state.runs.db() as db:
        saved = db.get("processing_runs", run["run_id"])
        events = db.events(run["run_id"])
    assert saved["status"] == "COMPLETED" and saved["progress_percent"] == 100
    assert saved["processed_pages"] == saved["total_pages"] == 1
    assert saved["fixed_chunks"] > 0 and saved["structure_chunks"] > 0
    assert {e["event_type"] for e in events} >= {"page_completed", "embedding_progress", "index_completed", "run_completed"}
    assert [e["progress"] for e in events] == sorted(e["progress"] for e in events)


def test_sse_persisted_events_and_reconnection(completed):
    client, _, _, run = completed
    url = f"/runs/{run['run_id']}/events"
    response = client.get(url)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: recognition_completed" in response.text and "event: run_completed" in response.text
    last = max(int(line[4:]) for line in response.text.splitlines() if line.startswith("id: "))
    assert client.get(url, headers={"Last-Event-ID": str(last)}).text == ""


def test_reload_recovers_run_selection(completed):
    client, _, _, run = completed
    assert f'data-run="{run["run_id"]}"' in client.get("/").text
    state = client.get(f"/api/runs/{run['run_id']}").json()
    assert state["status"] == "COMPLETED" and "config_json" not in state
    assert run["upload_ids"][0] in client.get("/").text


def test_cancel_between_pages_preserves_recognition_cache(config):
    entered, release = threading.Event(), threading.Event()
    class SlowRuntime(FakeRuntime):
        def generate(self, *args):
            entered.set()
            assert release.wait(10)
            return super().generate(*args)
    runtime = SlowRuntime()
    with TestClient(create_app(config, factory(runtime)), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config, pages=2)
        started = time.monotonic()
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        assert time.monotonic()-started < 2
        try:
            assert entered.wait(10)
            assert client.get(f"/api/runs/{rid}").json()["status"] == "RUNNING"
            assert client.post(f"/api/runs/{rid}/cancel").json()["cancel_requested"]
        finally:
            release.set()
        assert wait_run(client, rid)["status"] == "CANCELLED"
        assert runtime.calls == 1
        with client.app.state.runs.db() as db:
            assert len(db.all("recognition_metadata")) == 1
        rid2 = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        assert wait_run(client, rid2)["cache_hits"] == 1
        assert runtime.calls == 2


def test_restart_interrupts_only_dead_owner(web):
    client, _, _ = web
    service = client.app.state.runs
    dead = service.create(background=False)
    live = service.create(background=False)
    with service.db() as db:
        record = db.get("processing_runs", dead)
        record.update(owner_pid=2147483647, owner_started=0, status="RUNNING")
        db.put("processing_runs", dead, record)
    service.recover()
    assert service.get(dead)["status"] == "INTERRUPTED"
    assert service.get(live)["status"] == "QUEUED"


def test_system_status_actual_metadata(web):
    client, _, cfg = web
    with patch("rag_arbiter.application.status.OllamaRuntime.request", side_effect=ConnectionError):
        result = client.get("/api/system/status").json()
    assert result["sqlite"]["schema_version"] == 8 and result["sqlite"]["status"] == "available"
    assert result["qdrant"]["mode"] == "Local" and result["docling"]
    assert result["recognition_model"] == cfg.recognition.model
    assert result["runtime_status"]["status"] == "unavailable"


def test_page_image_normalized_and_lazy_raw(completed):
    client, _, _, run = completed
    rid = run["run_id"]
    page = client.get(f"/api/runs/{rid}/pages").json()[0]
    url = f"/api/runs/{rid}/pages/{page['recognition_id']}"
    assert client.get(url + "/image").content.startswith(b"\x89PNG")
    normalized = client.get(url).json()
    assert normalized["normalized"]["blocks"] and "raw_output" not in normalized["normalized"]
    html = client.get(f"/ui/runs/{rid}/pages/{page['recognition_id']}").text
    assert "<h4>Договор</h4>" in html and "<table>" in html and "<td>Хлеб</td>" in html
    assert 'class="raw-details"' in html and "Откройте, чтобы загрузить" in html
    assert '"message"' in client.get(url + "/raw").text
    assert client.get(f"/api/runs/{rid}/pages/unknown/image").status_code == 404


@pytest.mark.parametrize("strategy", ["fixed", "structure"])
def test_chunks_and_actual_stats(completed, strategy):
    client, _, _, run = completed
    result = client.get(f"/api/runs/{run['run_id']}/chunks?limit=1").json()[strategy]
    assert len(result["chunks"]) == 1 and result["total"] >= 1
    assert result["stats"]["avg_tokens"] > 0 and result["stats"]["duration"] >= 0
    assert result["chunks"][0]["page_start"] == 1
    assert client.get(f"/ui/runs/{run['run_id']}/chunks").status_code == 200


def test_search_both_indexes_and_source_navigation(completed):
    client, _, _, run = completed
    rid = run["run_id"]
    result = client.post(f"/api/runs/{rid}/search", json={"question": "Срок оплаты"}).json()
    assert set(result) == {"fixed", "structure"}
    for data in result.values():
        hit = data["hits"][0]
        assert hit["rank"] == 1 and hit["score"] and hit["source_pages"]
        source = hit["source_pages"][0]["recognition_id"]
        assert client.get(f"/api/runs/{rid}/pages/{source}/image").status_code == 200
        assert "provenance" not in hit
    html = client.post(f"/ui/runs/{rid}/search", data={"question": "Срок оплаты"}).text
    assert 'class="text-button source-page"' in html
    assert client.post(f"/api/runs/{rid}/search", json={"question": " "}).status_code == 400


def test_evaluation_unconfigured_and_actual_metrics(completed):
    client, _, cfg, run = completed
    rid = run["run_id"]
    assert client.get(f"/api/runs/{rid}/evaluation").json()["query_count"] == 0
    assert "Evaluation dataset not configured" in client.get(f"/ui/runs/{rid}/workspace").text
    snapshot = client.app.state.views.snapshot(rid)
    cfg.queries_path.write_text(json.dumps({"corpus_hash": snapshot["corpus"]["corpus_hash"], "queries": [{
        "query_id": "q1", "question": "оплата", "expected_document": "sample.pdf", "expected_page": 1, "expected_section": "Договор"}]}), encoding="utf-8")
    next_id = client.post("/api/runs", json={"upload_ids": run["upload_ids"]}).json()["run_id"]
    assert wait_run(client, next_id)["status"] == "COMPLETED"
    metrics = client.get(f"/api/runs/{next_id}/evaluation").json()
    assert metrics["query_count"] == 1
    for value in metrics["strategies"].values():
        assert value["Hit@1"] == value["Hit@5"] == value["page_hit"] == 1
        assert value["latency"] >= 0


def test_report_reuses_generator_and_download(completed):
    client, _, _, run = completed
    rid = run["run_id"]
    path = client.app.state.views.report(rid)
    response = client.get(f"/api/runs/{rid}/report")
    assert response.content == path.read_bytes()
    assert "sandbox" in response.headers["content-security-policy"]
    assert "attachment" in client.get(f"/api/runs/{rid}/report?download=true").headers["content-disposition"]
    with patch("rag_arbiter.application.views.generate_report", wraps=__import__("rag_arbiter.reporting", fromlist=["generate_report"]).generate_report) as generator:
        assert client.post(f"/api/runs/{rid}/report").status_code == 200
        generator.assert_called_once()


def test_shared_pipeline_recognizes_once_and_reuses_cache(completed):
    client, runtime, _, run = completed
    assert runtime.calls == 1 and len(run["index_run_ids"]) == 2
    second = client.post("/api/runs", json={"upload_ids": run["upload_ids"]}).json()["run_id"]
    repeated = wait_run(client, second)
    assert repeated["cache_hits"] == 1 and repeated["cache_misses"] == 0
    assert repeated["embeddings_created"] == 0 and repeated["embeddings_reused"] > 0
    assert runtime.calls == 1
    pid = client.get(f"/api/runs/{second}/pages").json()[0]["recognition_id"]
    assert client.get(f"/api/runs/{second}/pages/{pid}").json()["cache"] == "HIT"


def test_cli_uses_run_service_without_starting_fastapi(config):
    from rag_arbiter.cli import main
    with patch("rag_arbiter.cli.Config.load", return_value=config), patch("uvicorn.run") as server:
        assert main(["day21"]) == 0
        server.assert_not_called()
    from rag_arbiter.storage import MetadataStore
    db = MetadataStore(config.sqlite_path)
    try:
        assert db.all("processing_runs")[0]["status"] == "COMPLETED"
    finally:
        db.close()


def test_containment_does_not_accept_sibling_prefix(config):
    with pytest.raises(ValueError):
        contained(config.cache_path.parent / "cache-other" / "secret", config.cache_path)


def test_cross_process_operation_lock_rejects_queries(web):
    client, _, cfg = web
    with client.app.state.runs.operation_lock():
        with pytest.raises(ValueError, match="Qdrant"):
            client.app.state.runs.query(cfg, "query")


def test_failed_run_persists_safe_error(config):
    class BrokenRuntime(FakeRuntime):
        def preflight(self):
            raise RuntimeError("Ollama connection refused SECRET_VALUE")
    with TestClient(create_app(config, factory(BrokenRuntime())), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config)
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        run = wait_run(client, rid)
        assert run["status"] == "FAILED" and run["errors_count"] == 1
        assert "Ollama" in run["last_message"] and "SECRET_VALUE" not in json.dumps(run)
        assert run["errors"][0]["stage"] == "RECOGNITION"


def test_viewer_escapes_document_markup(config):
    from test_recognition import page_output
    output = page_output()
    output["blocks"][1]["text"] = '<script>alert("document")</script>'
    runtime = FakeRuntime(json.dumps(output))
    with TestClient(create_app(config, factory(runtime)), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config)
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        assert wait_run(client, rid)["status"] == "COMPLETED"
        pid = client.get(f"/api/runs/{rid}/pages").json()[0]["recognition_id"]
        html = client.get(f"/ui/runs/{rid}/pages/{pid}").text
        assert "<script>" not in html and "&lt;script&gt;" in html


def test_streamed_request_size_limit(web):
    client, _, _ = web
    def parts():
        yield b"x" * (1024 * 1024 + 1)
    # A small explicitly configured middleware cap, without a Content-Length header.
    from rag_arbiter.web.app import RequestSizeLimit
    from fastapi import FastAPI, Request
    app = FastAPI()
    app.add_middleware(RequestSizeLimit, limit=1024 * 1024)
    @app.post("/")
    async def receive(request: Request):
        await request.body()
        return {}
    with TestClient(app) as probe:
        assert probe.post("/", content=parts()).status_code == 413


def test_failed_second_page_keeps_input_page_total(config):
    class FailingPage(FakeRuntime):
        def generate(self, request, *args):
            if request.page_number == 2:
                self.content = "{bad json"
            return super().generate(request, *args)
    with TestClient(create_app(config, factory(FailingPage())), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config, pages=2)
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        run = wait_run(client, rid)
        assert run["status"] == "PARTIAL"
        assert run["total_pages"] == 2 and run["processed_pages"] == 1
        assert len(client.get(f"/api/runs/{rid}/pages").json()) == 2


def test_truncated_recognition_reports_actionable_failure(config):
    runtime = FakeRuntime()
    runtime.done_reason = "length"
    with TestClient(create_app(config, factory(runtime)), base_url="http://127.0.0.1", headers=HEADERS) as client:
        uid = upload_pdf(client, config)
        rid = client.post("/api/runs", json={"upload_ids": [uid]}).json()["run_id"]
        run = wait_run(client, rid)
        assert run["status"] == "FAILED" and run["stage"] == "RECOGNITION"
        assert "max_generation_tokens" in run["last_message"]
        assert runtime.calls == 1  # No unbounded automatic retry.
