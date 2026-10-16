from unittest.mock import patch
from test_network_sources import setup, scan, png
from rag_arbiter.network.notifications import updates, cursor, publish


def test_notifications_sync(config, tmp_path):
    root, runs, sync, source, runtime = setup(config, tmp_path)
    try:
        for i in range(5): png(root / f'{i}.png')
        first = scan(sync, source)
        batch = updates(runs, 0)
        assert [e['count'] for e in batch['events']] == [5]
        after = batch['cursor']
        assert first['ingestion_completed'] == 5
        with runs.db() as store: publish(store, first)
        assert cursor(runs) == after
        assert scan(sync, source)['unchanged_count'] == 5
        assert not updates(runs, after)['events']
        png(root / '0.png', 'black')
        assert scan(sync, source)['ingestion_completed'] == 1
        assert not updates(runs, after)['events']
        png(root / 'failed.png')
        with patch.object(runs, 'execute', side_effect=RuntimeError('failure')):
            assert scan(sync, source)['error_count'] == 1
        assert not updates(runs, after)['events']
        assert scan(sync, source)['ingestion_completed'] == 1
        assert [e['count'] for e in updates(runs, after)['events']] == [1]
        latest = cursor(runs)
        assert not updates(runs, latest)['events']
        config.network_sources.allowed_roots = []
        assert not updates(runs, 0)['events']
        assert updates(runs, 0)['cursor'] == latest
    finally:
        sync.close(); runs.close()
