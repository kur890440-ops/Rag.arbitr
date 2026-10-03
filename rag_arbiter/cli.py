import argparse
import json
import sys
from .config import Config


def main(argv=None):
    # Windows redirected streams otherwise use the legacy system code page.
    # A Unicode filename in a progress message must never abort ingestion.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="rag-arbiter")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--recognition-provider", choices=["qwen3_vl", "classic_ocr"])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("day21", "ingest", "evaluate", "report"):
        sub.add_parser(command)
    recognition = sub.add_parser("recognition")
    recognition.add_argument("action", choices=["status"])
    web = sub.add_parser("web")
    web.add_argument("--port", type=int)
    web.add_argument("--host")
    web.add_argument("--open-browser", action="store_true")
    rechunk = sub.add_parser('rechunk')
    rechunk.add_argument('--run-id', required=True)
    rechunk.add_argument('--document-id')
    rechunk.add_argument('--strategy', choices=['fixed', 'structure', 'both'], default='both')
    rag = sub.add_parser('rag')
    rag.add_argument('question')
    rag.add_argument('--run-id', required=True)
    rag.add_argument('--document-id')
    rag.add_argument('--rag-scope',choices=['ALL_DOCUMENTS','SELECTED_DOCUMENT'],default='ALL_DOCUMENTS')
    rag.add_argument('--rag-mode', choices=['BASELINE','RERANK','REWRITE_RERANK'],default='BASELINE')
    rag.add_argument('--rerank-threshold',type=float,default=None)
    rag.add_argument('--point-only',action='store_true')
    rag.add_argument('--strategy', choices=['fixed','structure'], default='structure')
    rag.add_argument('--top-k', type=int, default=None, help='Legacy alias for max-context-sources')
    rag.add_argument('--candidate-top-n', type=int, default=None)
    rag.add_argument('--max-context-sources', type=int, default=None)
    rag.add_argument('--context-token-budget', type=int, default=None)
    corpus = sub.add_parser("corpus")
    corpus.add_argument("action", choices=["build"])
    index = sub.add_parser("index")
    mode = index.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--strategy", choices=["fixed", "structure"])
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--compare", action="store_true")
    query.add_argument("--strategy", choices=["fixed", "structure"], default="fixed")
    args = parser.parse_args(argv)
    application = None
    try:
        config = Config.load(args.config)
        if args.recognition_provider:
            config.recognition.provider = args.recognition_provider
        if args.command == "web":
            import uvicorn
            from .web.app import create_app
            port = args.port or config.web_port
            config.web_host = args.host or config.web_host
            if not 1 <= port <= 65535:
                raise ValueError("Invalid port")
            print(f"rag.арбитр Web: http://127.0.0.1:{port}")
            if args.open_browser:
                import threading, webbrowser
                threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
            uvicorn.run(create_app(config), host=config.web_host, port=port, workers=1)
            return 0
        if args.command == "recognition":
            from .recognition import create_provider
            recognizer = create_provider(config)
            try:
                print(json.dumps(recognizer.preflight(), ensure_ascii=False, indent=2))
            finally:
                recognizer.close()
            return 0
        from .application import RunService
        application = RunService(config)
        if args.command == 'rag':
            from .application.rag import RAGComparisonService
            service = RAGComparisonService(application)
            key = service.start(args.run_id, question=args.question, document_id=args.document_id,rag_scope=args.rag_scope,
                                rag_pipeline_mode=args.rag_mode,rerank_threshold=args.rerank_threshold,point_only=args.point_only,strategy=args.strategy, top_k=args.top_k, candidate_top_n=args.candidate_top_n, max_context_sources=args.max_context_sources, context_token_budget=args.context_token_budget, background=False)
            result = service.get(key)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status']=='COMPLETED' else 2
        if args.command == 'rechunk':
            from .application.rechunk import RechunkService
            strategies = ['fixed','structure'] if args.strategy == 'both' else [args.strategy]
            run_id = RechunkService(application).start(args.run_id, args.document_id, strategies, background=False)
            application.execute(run_id)
            print(f"ProcessingRun: {run_id} {application.get(run_id)['status']}")
            return 0 if application.get(run_id)['status'] == 'COMPLETED' else 2
        if args.command != "query":
            run_type = "ingest" if args.command == "corpus" else args.command
            if args.command == "index" and not args.all:
                run_type = f"index:{args.strategy}"
            run_id = application.create(run_type=run_type, background=False)
            result = application.execute(run_id)
            record = application.get(run_id)
            print(f"ProcessingRun: {run_id} {record['status']}")
            if record["status"] in {"FAILED", "CANCELLED", "PARTIAL"}:
                print(record["last_message"])
                return 2
            if args.command == "report":
                print(config.report_path.resolve())
            if args.command == "evaluate" and result:
                print(json.dumps({k: v for k, v in result.get('evaluation', {}).items() if k != 'queries'}, ensure_ascii=False, indent=2))
            result = result or {}
        else:
            result = application.query(config, args.question, compare=args.compare, strategy=args.strategy)
            for strategy, data in result.items():
                print(strategy.upper())
                for hit in data["hits"]:
                    print(f"{hit['rank']}. {hit['score']:.5f} {hit['file_name']} pages {hit['page_start']}-{hit['page_end']} {hit['section']}\n{hit['text'][:config.preview_chars]}\n")
        failed = result.get("status") in {"FAILED", "PARTIAL", "INVALID"}
        invalid_evaluation = result.get("evaluation", {}).get("status") == "INVALID"
        return 2 if failed or invalid_evaluation else 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        if application:
            application.close()
