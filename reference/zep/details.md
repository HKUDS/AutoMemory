# Details for Graphiti (Zep)

Implementation-oriented reference for the Graphiti temporal knowledge graph design.

---

## 1. System architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  add_episode() flow:                                             │
│    EpisodicNode (raw) → extract_nodes (LLM + reflexion)           │
│    → resolve_extracted_nodes (hybrid search + LLM dedupe)         │
│    → extract_edges (LLM + reflexion)                              │
│    → resolve_extracted_edges (dedupe, contradiction → invalid_at) │
│    → extract_attributes_from_nodes                               │
│    → create embeddings → add_nodes_and_edges_bulk                 │
│    → [optional] update_community / build_communities              │
├─────────────────────────────────────────────────────────────────┤
│  search() flow:                                                  │
│    Parallel: edge_search, node_search, episode_search,            │
│              community_search                                     │
│    Each: BM25 + cosine + BFS → RRF/MMR → optional reranker       │
│    → SearchResults                                               │
└─────────────────────────────────────────────────────────────────┘
```

**Components:**
- **Graphiti**: Main class; add_episode, search, retrieve_episodes.
- **Node operations**: extract_nodes, resolve_extracted_nodes, extract_attributes_from_nodes.
- **Edge operations**: extract_edges, resolve_extracted_edges, resolve_extracted_edge; invalidation.
- **Community operations**: label_propagation, get_community_clusters, build_community, update_community.
- **Search**: search.py (edge_search, node_search, episode_search, community_search); search_utils (BM25, similarity, BFS, RRF, MMR, rerankers).

---

## 2. Data model and schema

**EpisodicNode:**
- content, source (message/text/json), source_description, valid_at, created_at; entity_edges (MENTIONS).

**EntityNode:**
- name, name_embedding, summary, attributes (JSON), labels (entity types).

**CommunityNode:**
- name, name_embedding, summary; HAS_MEMBER edges to EntityNodes.

**EpisodicEdge (MENTIONS):** EpisodicNode → EntityNode.

**EntityEdge (RELATES_TO):**
- name (relation type), fact, fact_embedding, valid_at, invalid_at, episodes, attributes.

**CommunityEdge (HAS_MEMBER):** CommunityNode → EntityNode.

**Update semantics:** Append EpisodicNode; insert EntityNode/EntityEdge or merge with existing (resolve); set invalid_at for contradicted edges.

---

## 3. Storage and indexing

- **Graph DB**: Neo4j, FalkorDB, Kuzu, Neptune; Cypher queries.
- **Full-text**: Lucene syntax; BM25 on fact (edges), name+summary (nodes), content (episodes), name (communities).
- **Vector**: Embeddings for name_embedding (nodes), fact_embedding (edges); cosine similarity.
- **Neptune**: OpenSearch Serverless for full-text when using Neptune.
- **group_id**: Partition key for multi-tenant isolation.

---

## 4. Retrieval logic

1. **Candidate generation**: 
   - BM25: fulltext_query (Lucene); edge_fulltext_search, node_fulltext_search, episode_fulltext_search, community_fulltext_search.
   - Vector: edge_similarity_search, node_similarity_search, community_similarity_search; cosine.
   - BFS: edge_bfs_search, node_bfs_search; from origin node, max depth.
2. **Filtering**: group_ids; SearchFilters (time range, entity types, etc.).
3. **Ranking**: RRF; MMR; cross-encoder; node_distance_reranker; episode_mentions_reranker.
4. **Config**: SearchConfig with edge_config, node_config, episode_config, community_config; recipes (EDGE_HYBRID_SEARCH_RRF, etc.).

---

## 5. Store and update logic

**Store triggers:** add_episode per message, document chunk, or JSON record.

**Extraction:** LLM with structured output (ExtractedEntities, ExtractedEdges); reflexion checks for missed entities/facts; iterates up to MAX_REFLEXION_ITERATIONS.

**Deduplication:** resolve_extracted_nodes (hybrid search for similar nodes; LLM for uncertain); resolve_extracted_edges (same endpoints + similarity; LLM for duplicate/contradiction).

**Contradiction handling:** invalidate_edges prompt; set invalid_at on contradicted edges.

---

## 6. Consolidation and lifecycle

- **No automatic consolidation**: Each episode processed independently.
- **Community update**: update_community when new nodes added; build_communities for full rebuild.
- **Edge invalidation**: Temporal contradiction; invalid_at set; no deletion.
- **EpisodicNode**: Immutable; retains raw content.

---

## 7. Important implementation details

**Key classes:**
- `Graphiti.add_episode()`: Full pipeline; sequential episode recommended.
- `resolve_extracted_edge()`: LLM decides duplicate vs contradicted vs new.
- `label_propagation()`: Community detection; weighted by edge count.

**Reflexion:** extract_nodes reflexion checks missed entities; extract_edges reflexion checks missing facts.

**Bulk:** add_episodes_bulk, extract_nodes_and_edges_bulk for batch ingestion.

---

## 8. Transferability notes

**Reusable:**
- Bi-temporal edge model.
- Reflexion in extraction.
- Hybrid search (BM25 + vector + BFS) + multiple rerankers.
- Community nodes via Label Propagation.

**Tightly coupled:**
- Graph DB (Neo4j/FalkorDB/Kuzu/Neptune).
- Structured output LLM (OpenAI, Gemini).

**Best fit:** Agent memory, dynamic graph RAG, temporal knowledge graphs.
