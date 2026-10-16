import pytest
from rag_arbiter.network.drive_paths import resolve_drive_path


def test_mapped_drive():
    assert resolve_drive_path(r'Z:\!\1', lambda d: r'\\server\share') == r'\\server\share\!\1'
    assert resolve_drive_path(r'z:\folder', lambda d: r'\\server\share') == r'\\server\share\folder'


def test_unmapped_and_unc_paths():
    assert resolve_drive_path(r'C:\folder', lambda d: None) == r'C:\folder'
    def unexpected(d):
        raise AssertionError('UNC and relative paths must not query drive mappings')
    assert resolve_drive_path(r'\\server\share\folder', unexpected) == r'\\server\share\folder'
    assert resolve_drive_path('Z:folder', unexpected) == 'Z:folder'


def test_mapping_cannot_traverse_or_redirect_to_local_path():
    with pytest.raises(ValueError):
        resolve_drive_path(r'Z:\..\other', lambda d: r'\\server\share')
    with pytest.raises(ValueError):
        resolve_drive_path(r'Z:\folder', lambda d: r'C:\Windows')
