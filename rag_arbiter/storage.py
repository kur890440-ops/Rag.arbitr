import json
import sqlite3


class MetadataStore:
    """SQLite is canonical for text and metadata; vectors live only in Qdrant/cache."""
    TABLES = ("consult_reservations", "workspace_tasks", "workspace_files", "workspace_revisions", "workspace_diffs", "workspace_validations", "workspace_proposals", "workspace_publications", "public_document_grants", "consult_usage", "document_versions", "document_version_contents", "document_version_indexes", "document_reviews", "documents", "document_pages", "document_blocks", "chunks", "corpora",
              "corpus_documents", "index_runs", "embeddings_metadata", "evaluation_queries",
              "evaluation_runs", "retrieval_results", "recognition_metadata", "processing_runs",
              "processing_run_errors", "uploads", "recognition_cache", "chunk_settings", "normalized_documents",
              "rag_evaluation_questions", "rag_comparison_runs", "rag_batches",
              "chat_discovery_decisions", "network_root_grants", "network_sources", "network_files", "network_scans", "network_provenance",
              "exhaustive_batch_results", "exhaustive_map_cache", "query_rewrite_cache",
              "local_llm_experiment_runs", "chat_sessions", "chat_messages", "chat_turns", "task_states", "dialogue_working_contexts")

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 11:
            raise ValueError("Unsupported database schema")
        for table in self.TABLES:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data)))")
        for table in ('document_versions','document_version_contents','workspace_proposals','workspace_revisions'):
            for action in ('UPDATE','DELETE'):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable document version'); END")
            self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_insert BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id) BEGIN SELECT RAISE(IGNORE); END")
        self.db.execute("CREATE TABLE IF NOT EXISTS processing_run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, data TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS events_by_run ON processing_run_events(run_id,id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS recognition_by_page ON recognition_metadata(json_extract(data,'$.document_id'),json_extract(data,'$.page_number'),json_extract(data,'$.image_hash'))")
        if version < 5:
            for key, raw in self.db.execute('SELECT id,data FROM chunks').fetchall():
                chunk = json.loads(raw)
                row = self.db.execute('SELECT data FROM documents WHERE id=?', (chunk['document_id'],)).fetchone()
                doc = json.loads(row[0]) if row else {}
                chunk.setdefault('source', 'uploaded document' if 'corpora' in doc.get('source_path','') or 'uploads' in doc.get('source_path','') else 'local file')
                chunk.setdefault('file_name', doc.get('file_name', chunk['document_id']))
                chunk.setdefault('title', doc.get('title') or chunk['file_name'])
                self.db.execute('UPDATE chunks SET data=? WHERE id=?', (json.dumps(chunk,ensure_ascii=False), key))
        if version < 7:
            from .application.full_document import full_document_defaults
            for key, raw in self.db.execute('SELECT id,data FROM rag_comparison_runs').fetchall():
                record={**full_document_defaults(),**json.loads(raw)}
                self.db.execute('UPDATE rag_comparison_runs SET data=? WHERE id=?',(json.dumps(record,ensure_ascii=False),key))
        if version < 8:
            for key, raw in self.db.execute('SELECT id,data FROM rag_comparison_runs').fetchall():
                r=json.loads(raw)
                r.setdefault('rag_scope','SELECTED_DOCUMENT' if r.get('document_id') else 'ALL_DOCUMENTS')
                r.setdefault('selected_document_id',r.get('document_id'))
                r.setdefault('corpus_id',(r.get('index_snapshot') or {}).get('corpus_id'))
                self.db.execute('UPDATE rag_comparison_runs SET data=? WHERE id=?',(json.dumps(r,ensure_ascii=False),key))
        self.db.execute("CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(json_extract(data,'$.session_id'),json_extract(data,'$.sequence'))")
        self.db.execute("CREATE INDEX IF NOT EXISTS chat_turns_session ON chat_turns(json_extract(data,'$.session_id'))")
        self.db.executescript("""
CREATE TABLE IF NOT EXISTS auth_users (
 id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL UNIQUE,
 username TEXT NOT NULL, display_name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('ADMIN','CLIENT')),
 status TEXT NOT NULL CHECK(status IN ('PENDING','ACTIVE','BLOCKED')),
 created_at REAL NOT NULL, updated_at REAL NOT NULL, last_login_at REAL);
CREATE TABLE IF NOT EXISTS auth_codes (
 nonce_hash TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL,
 expires REAL NOT NULL, user_id TEXT REFERENCES auth_users(id), consumed INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES auth_users(id),
 expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS auth_limits (id TEXT PRIMARY KEY, window REAL NOT NULL, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_state (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_outbox (id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL,
 text TEXT NOT NULL, created_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
""")
        self.db.execute("PRAGMA user_version=11")
        self.db.commit()

    def put(self, table, key, data):
        if table not in self.TABLES:
            raise ValueError(table)
        with self.db:
            self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)", (key, json.dumps(data, ensure_ascii=False)))

    def get(self, table, key):
        if table not in self.TABLES:
            raise ValueError(table)
        row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, table):
        if table not in self.TABLES:
            raise ValueError(table)
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT data FROM {table} ORDER BY rowid")]

    def document(self, doc):
        if doc.cache_identity:
            self.put("normalized_documents", doc.cache_identity, doc.model_dump())
        self.put("documents", doc.document_id, doc.model_dump())
        for p in doc.pages:
            self.put("document_pages", f"{p.document_id}:{p.page_number}", p.model_dump())
        for b in doc.blocks:
            self.put("document_blocks", b.block_id, b.model_dump())

    def page(self, page, blocks):
        # One page commit; preceding pages are independent durable commits.
        with self.db:
            for table, key, data in [('document_pages', page.recognition_id, page.model_dump()),
                                     ('document_pages', f'{page.document_id}:{page.page_number}', page.model_dump()),
                                     *[('document_blocks', b.block_id, b.model_dump()) for b in blocks]]:
                self.db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)', (key, json.dumps(data, ensure_ascii=False)))

    def close(self):
        self.db.close()

    def event(self, run_id, data):
        with self.db:
            cursor = self.db.execute("INSERT INTO processing_run_events(run_id,data) VALUES (?,?)", (run_id, json.dumps(data, ensure_ascii=False)))
        return cursor.lastrowid

    def events(self, run_id, after=0):
        return [{"id": row[0], **json.loads(row[1])} for row in self.db.execute(
            "SELECT id,data FROM processing_run_events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (run_id, after))]

    def trace_chunk(self, chunk_id):
        chunk = self.get("chunks", chunk_id)
        if not chunk:
            raise ValueError("Unknown chunk_id")
        trace = []
        for block_id in chunk["source_block_ids"]:
            block = self.get("document_blocks", block_id)
            if not block:
                raise ValueError("Missing source block")
            recognition = self.get("recognition_metadata", block.get("recognition_id", ""))
            if block.get('locator_kind') == 'structure':
                if not recognition:
                    raise ValueError('Missing structural normalization metadata')
                trace.append({'block_id': block_id, 'locator_kind':'structure',
                    'page': {'page_number':0, 'recognition_id':block['recognition_id'], 'locator_kind':'structure'},
                    'recognition':recognition, 'source_ref':block.get('source_ref')})
                continue
            # Recognition metadata identifies the historical page even after a provider switch.
            page = self.get("document_pages", block.get("recognition_id", ""))
            if not page:
                page = self.get("document_pages", f"{block['document_id']}:{block['page_number']}")
            if not recognition or not page:
                raise ValueError("Recognition trace unavailable for legacy chunk; re-ingest document")
            trace.append({"block_id": block_id, "page": page, "recognition": recognition})
        return {"chunk_id": chunk_id, "sources": trace}
