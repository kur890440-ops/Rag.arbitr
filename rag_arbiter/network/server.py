"""Official MCP SDK stdio server; only read-only source capabilities are exposed."""
import json
import sys
import time
from uuid import uuid4
from mcp.server.fastmcp import FastMCP
from ..config import NetworkSourcesConfig
from ..storage import MetadataStore
from .connector import Connector, SourceError, error_code


def create_server(settings, sqlite_path):
    server = FastMCP('rag-arbiter-network-sources')
    def source(source_id):
        store = MetadataStore(sqlite_path)
        try:
            row = store.get('network_sources', source_id)
            if not row:
                raise ValueError('ACCESS_DENIED')
            return row
        finally:
            store.close()
    connector = Connector(settings, source)
    cursors = {}

    def guarded(function, *args):
        try:
            return function(*args)
        except (OSError, SourceError) as exc:
            raise ValueError(error_code(exc)) from exc

    @server.tool()
    def list_sources() -> dict:
        store = MetadataStore(sqlite_path)
        try:
            return {'sources': store.all('network_sources')}
        finally:
            store.close()

    @server.tool()
    def list_directory(source_id: str, relative_path: str = '', offset: int = 0) -> dict:
        return guarded(connector.directory, source_id, relative_path, offset)

    @server.tool()
    def get_file_metadata(source_id: str, relative_path: str) -> dict:
        return guarded(connector.metadata, source_id, relative_path)

    @server.tool()
    def read_file_chunk(source_id: str, relative_path: str, offset: int = 0, length: int = 262144) -> dict:
        return guarded(connector.read, source_id, relative_path, offset, length)

    @server.tool()
    def scan_source(source_id: str, cursor: str = '') -> dict:
        if not cursor:
            if len(cursors) >= 4:
                raise ValueError('SCAN_BUSY')
            cursor = uuid4().hex
            cursors[cursor] = (source_id, connector.scan(source_id))
        if cursor not in cursors or cursors[cursor][0] != source_id:
            raise ValueError('ACCESS_DENIED')
        rows = []
        try:
            deadline = time.monotonic() + 2
            for _ in range(10):
                rows.append(next(cursors[cursor][1]))
                if time.monotonic() >= deadline:
                    break
        except StopIteration:
            del cursors[cursor]
            return {'files': rows, 'cursor': None}
        except (OSError, SourceError) as exc:
            del cursors[cursor]
            raise ValueError(error_code(exc)) from exc
        except Exception:
            del cursors[cursor]
            raise
        return {'files': rows, 'cursor': cursor}
    return server


if __name__ == '__main__':
    data = json.loads(sys.argv[1])
    create_server(NetworkSourcesConfig(**data['settings']), data['sqlite_path']).run(transport='stdio')
