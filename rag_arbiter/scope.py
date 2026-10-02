from enum import Enum


class RAGScope(str, Enum):
    ALL_DOCUMENTS = 'ALL_DOCUMENTS'
    SELECTED_DOCUMENT = 'SELECTED_DOCUMENT'


def retrieval_document(scope=RAGScope.ALL_DOCUMENTS, selected_document_id=None):
    scope=RAGScope(scope)
    if scope==RAGScope.SELECTED_DOCUMENT:
        if not selected_document_id:raise ValueError('DOCUMENT_REQUIRED: выберите файл справа')
        return selected_document_id
    return None
