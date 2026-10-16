import json
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def connect(config):
    params = StdioServerParameters(command=sys.executable, args=['-m', 'rag_arbiter.network.server',
        json.dumps({'settings': config.network_sources.model_dump(), 'sqlite_path': str(config.sqlite_path.resolve())})])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            yield session


async def call(session, name, **arguments):
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise ValueError(' '.join(c.text for c in result.content if hasattr(c, 'text')))
    return json.loads(next(c.text for c in result.content if hasattr(c, 'text')))
