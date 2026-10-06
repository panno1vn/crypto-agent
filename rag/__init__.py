"""
rag/

Ngày 22-27 — RAG pipeline cho crypto-agent.

Modules:
    config       — cấu hình tập trung (Chroma host, model, batch size)
    enricher     — chuẩn bị text + metadata trước khi embed
    embedder     — wrapper SentenceTransformer (đa ngôn ngữ vi/en)
    vector_store — kết nối ChromaDB, quản lý collection
    ingestion    — pipeline PostgreSQL → ChromaDB
"""
