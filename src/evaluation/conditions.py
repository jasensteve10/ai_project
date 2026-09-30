"""Compatibility exports; application and evaluation share retrieval/context.py."""
from src.retrieval.context import ContextBuilder, KINDS, Retrievers, resolve_retriever

__all__ = ["ContextBuilder", "KINDS", "Retrievers", "resolve_retriever"]
