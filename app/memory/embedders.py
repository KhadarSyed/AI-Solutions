"""Local embedder for Mem0.

Mem0's stock FastEmbed embedder returns a numpy ndarray, which its pgvector store
passes straight to psycopg — and psycopg can't adapt an ndarray (`cannot adapt type
'ndarray'`). This subclass returns a plain Python list so the vector round-trips. It is
registered as Mem0's `fastembed` provider in mem0_service so the fix is transparent to
the config.
"""

from mem0.embeddings.fastembed import FastEmbedEmbedding


class ListFastEmbed(FastEmbedEmbedding):
    def embed(self, text, memory_action=None):
        vec = super().embed(text, memory_action=memory_action)
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)
