# 0006 NVIDIA Provider Boundary

## Status

Accepted.

## Context

The workflow needs provider-backed explanations and semantic policy retrieval while retaining
deterministic authorization, scoring, and workflow transitions. The original eight-dimensional
test vectors are incompatible with NVIDIA's selected 2,048-dimensional embedding model.

## Decision

Application code depends on chat and embedding protocols. `MODEL_PROVIDER=deterministic` is the
credential-free CI default. `MODEL_PROVIDER=nvidia_nim` selects NVIDIA NIM adapters for Mistral
Medium 3.5 128B chat and Nemotron Embed 1B v2 embeddings. The worker records bounded summaries,
usage, latency, and failures in `model_calls`; it never stores private reasoning.

The policy vector column is migrated to `halfvec(2048)`: pgvector permits HNSW indexing at this
dimension for `halfvec`, while the standard `vector` HNSW limit is lower. The migration replaces
old test vectors with zero vectors and marks them pending reindex, because changing dimensions
cannot preserve semantic similarity. `make reindex-policies` regenerates each document embedding
through the configured provider. Queries use the embedding model's `query` mode and documents use
`passage`.

## Consequences

Provider calls remain behind testable interfaces. Live access is opt-in and fails closed when
credentials or configuration are invalid. Deployments must run the reindex command after the
schema migration before relying on policy retrieval quality.
