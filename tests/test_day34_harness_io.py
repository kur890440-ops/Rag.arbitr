import importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('harness_io',Path(__file__).resolve().parents[1]/'.runtime/day34_harness_io.py')
io=importlib.util.module_from_spec(spec);spec.loader.exec_module(io)

def test_utf8_cyrillic_checkpoint(tmp_path):
    path=tmp_path/'state.json';value={'calls':[{'response':'субагентский договор ИП','usage':{'total_tokens':2379}}],'started_at':1792393626.9835193}
    io.write_json(path,value)
    assert json.loads(path.read_bytes().decode('utf-8'))==value
    assert io.read_json(path)==value
    value['calls'].append({'response':'читать документ','usage':{'total_tokens':42}})
    io.write_json(path,value)
    assert io.read_json(path)==value and sum(c['usage']['total_tokens'] for c in io.read_json(path)['calls'])==2421

def test_interruption_before_replace_preserves_checkpoint(tmp_path,monkeypatch):
    path=tmp_path/'state.json';old={'calls':1,'text':'исходный журнал'};io.write_json(path,old)
    def fail(*args):raise OSError('simulated interruption')
    monkeypatch.setattr(io.os,'replace',fail)
    with pytest.raises(OSError):io.write_json(path,{'calls':2,'text':'новый журнал'})
    assert io.read_json(path)==old
    assert list(tmp_path.iterdir())==[path]

def test_serialization_failure_preserves_checkpoint(tmp_path):
    path=tmp_path/'state.json';io.write_json(path,{'calls':1});loop=[];loop.append(loop)
    with pytest.raises(ValueError):io.write_json(path,loop)
    assert io.read_json(path)=={'calls':1}
