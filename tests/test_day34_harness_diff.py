import importlib.util
from pathlib import Path
import pytest
from rag_arbiter.storage import MetadataStore
spec=importlib.util.spec_from_file_location('diff_audit',Path(__file__).resolve().parents[1]/'.runtime/day34_harness_diff.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
@pytest.mark.parametrize('kind',['legacy','explicit','initial','missing','bad_hash','foreign','ambiguous'])
def test_diff_real_storage_schema(tmp_path,kind):
    store=MetadataStore(tmp_path/'metadata.db')
    try:
        old=dict(id='old',owner_id='owner',sha256='h1',task_id='A');new=dict(id='new',owner_id='owner',sha256='h2',previous_id=None if kind=='initial' else 'old',task_id='B')
        store.put('workspace_tasks','A',dict(id='A',owner_id='owner',files=['old']))
        store.put('workspace_tasks','B',dict(id='B',owner_id='owner',files=['new']))
        for row in [old,new]:store.put('workspace_files',row['id'],row);store.put('workspace_revisions',row['id'],dict(**row,file_id=row['id']))
        store.put('workspace_diffs','unrelated',dict(id='unrelated',owner_id='other',old_hash='h1',new_hash='h2',changes=[]))
        if kind not in ('missing','initial'):
            d=dict(id='new',owner_id='other' if kind=='foreign' else 'owner',old_hash='h1',new_hash='wrong' if kind=='bad_hash' else 'h2',changes=[{'change_type':'ADDED'}])
            if kind=='explicit':d.update(old_id='old',new_id='new')
            store.put('workspace_diffs','new',d)
            if kind=='ambiguous':store.put('workspace_diffs','comparison',dict(**{**d,'id':'comparison'},new_id='new',old_id='old'))
        result=module.inspect_diff(store,new)
        assert result['status']=={'legacy':'FOUND','explicit':'FOUND','initial':'NOT_APPLICABLE','missing':'MISSING','foreign':'MISSING','bad_hash':'ERROR','ambiguous':'ERROR'}[kind]
    finally:store.db.close()
