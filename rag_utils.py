import os
import logging
from typing import Optional
import requests
import chromadb
from pathlib import Path
from db_utils import get_db
from config import (
    CHROMA_PATH,
    OBSIDIAN_VAULT,
    OLLAMA_EMBED_URL,
    EMBED_MODEL,
    RAG_TOP_K,
    RAG_MAX_DISTANCE,
    CHROMA_COLLECTION_NAME
)

logger = logging.getLogger(__name__)

# Initialize ChromaDB persistent client and collection
CHROMA_PATH.mkdir(parents=True, exist_ok=True)
chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
collection = chroma_client.get_or_create_collection(
    name=CHROMA_COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)


def get_embedding(text: str, is_query: bool = False) -> list[float]:
    """Generate a vector embedding using Ollama nomic-embed-text with appropriate task prefix."""
    prefix = "search_query: " if is_query else "search_document: "
    prompt_text = f"{prefix}{text}"
    try:
        res = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": prompt_text},
            timeout=15,
        )
        if res.status_code == 200:
            return res.json().get("embedding", [])
        else:
            logger.warning(f"Embedding request returned status {res.status_code}: {res.text}")
            return []
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        return []


def cleanup_stale_embeddings():
    """Removes embeddings from ChromaDB and vault_index for notes that were deleted or renamed."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM vault_index")
        rows = cursor.fetchall()
        
        stale_paths = []
        stale_ids = []
        for row in rows:
            p = Path(row["file_path"])
            if not p.exists():
                stale_paths.append(row["file_path"])
                try:
                    rel_id = str(p.relative_to(OBSIDIAN_VAULT))
                except ValueError:
                    rel_id = str(p)
                stale_ids.append(rel_id)

        if stale_ids:
            try:
                collection.delete(ids=stale_ids)
            except Exception as e:
                logger.warning(f"Failed deleting stale ChromaDB IDs: {e}")

            cursor.executemany("DELETE FROM vault_index WHERE file_path = ?", [(p,) for p in stale_paths])
            logger.info(f"   │  🧹 [Vector RAG] Cleaned up {len(stale_ids)} stale document embedding(s).")


def sync_vault_index_incremental():
    """Scans the Obsidian vault, embeds ONLY new or modified Markdown files, and purges deleted notes."""
    if not OBSIDIAN_VAULT.exists():
        logger.warning(f"Obsidian vault path not found: {OBSIDIAN_VAULT}")
        return

    # First clean up deleted notes
    cleanup_stale_embeddings()

    updated_count = 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, last_mtime FROM vault_index")
        indexed_files = {row["file_path"]: row["last_mtime"] for row in cursor.fetchall()}

        for md_path in OBSIDIAN_VAULT.glob("**/*.md"):
            path_str = str(md_path)
            try:
                current_mtime = os.path.getmtime(md_path)
            except OSError:
                continue

            # Skip unchanged files
            if path_str in indexed_files and indexed_files[path_str] == current_mtime:
                continue

            try:
                content = md_path.read_text(encoding="utf-8").strip()
                if not content:
                    continue

                embedding = get_embedding(content[:2000], is_query=False)
                if not embedding:
                    continue

                rel_id = str(md_path.relative_to(OBSIDIAN_VAULT))

                collection.upsert(
                    ids=[rel_id],
                    embeddings=[embedding],
                    documents=[content[:1500]],
                    metadatas=[{"title": md_path.stem, "path": path_str}]
                )

                cursor.execute("""
                    INSERT INTO vault_index (file_path, last_mtime, indexed_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(file_path) DO UPDATE SET
                        last_mtime = excluded.last_mtime,
                        indexed_at = CURRENT_TIMESTAMP
                """, (path_str, current_mtime))

                updated_count += 1
            except Exception as e:
                logger.error(f"Failed indexing '{md_path.name}': {e}")

    if updated_count > 0:
        logger.info(f"   │  🧠 [Vector RAG] Indexed/updated {updated_count} vault document(s).")


def add_note_to_vector_db(md_path: Path):
    """Embeds a single newly created Markdown note into ChromaDB immediately."""
    try:
        content = md_path.read_text(encoding="utf-8").strip()
        if not content:
            return

        embedding = get_embedding(content[:2000], is_query=False)
        if not embedding:
            return

        rel_id = str(md_path.relative_to(OBSIDIAN_VAULT))

        collection.upsert(
            ids=[rel_id],
            embeddings=[embedding],
            documents=[content[:1500]],
            metadatas=[{"title": md_path.stem, "path": str(md_path)}]
        )

        mtime = os.path.getmtime(md_path)
        with get_db() as conn:
            conn.execute("""
                INSERT INTO vault_index (file_path, last_mtime, indexed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_path) DO UPDATE SET
                    last_mtime = excluded.last_mtime,
                    indexed_at = CURRENT_TIMESTAMP
            """, (str(md_path), mtime))

    except Exception as e:
        logger.error(f"Failed to index generated note '{md_path.name}': {e}")


def remove_note_from_vector_db(md_path: Path):
    """Deletes a removed or renamed Markdown note embedding from ChromaDB and SQLite index."""
    try:
        try:
            rel_id = str(md_path.relative_to(OBSIDIAN_VAULT))
        except ValueError:
            rel_id = str(md_path)

        try:
            collection.delete(ids=[rel_id])
        except Exception:
            pass

        with get_db() as conn:
            conn.execute("DELETE FROM vault_index WHERE file_path = ?", (str(md_path),))
        logger.debug(f"Removed '{md_path.name}' from vector DB index.")
    except Exception as e:
        logger.warning(f"Failed removing '{md_path.name}' from vector index: {e}")


def retrieve_relevant_context(
    raw_transcript: str,
    top_k: int = RAG_TOP_K,
    max_distance: float = RAG_MAX_DISTANCE,
    exclude_paths: Optional[list[str]] = None
) -> str:
    """Retrieves top matching vault notes from ChromaDB filtered by distance threshold and excluding self-references."""
    if collection.count() == 0:
        return ""

    query_vector = get_embedding(raw_transcript, is_query=True)
    if not query_vector:
        return ""

    # Fetch extra candidates if we need to exclude some
    fetch_k = top_k + (len(exclude_paths) if exclude_paths else 0)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=min(fetch_k, collection.count()),
        include=["documents", "metadatas", "distances"]
    )

    norm_excludes = [p.lower().replace("\\", "/") for p in (exclude_paths or [])]

    context_snippets = []
    if results and "documents" in results and results["documents"]:
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results.get("distances", [[]])[0] if "distances" in results and results["distances"] else [0.0] * len(documents)

        for doc, meta, dist in zip(documents, metadatas, distances):
            meta_path = meta.get("path", "").lower().replace("\\", "/")
            meta_title = meta.get("title", "").lower()

            # Skip self-references
            if any(exc in meta_path or exc == meta_title for exc in norm_excludes):
                continue

            if dist <= max_distance:
                title = meta.get("title", "Note")
                context_snippets.append(f"--- Vault Context Note: [[{title}]] (Relevance Distance: {dist:.2f}) ---\n{doc}\n")
                if len(context_snippets) >= top_k:
                    break
            else:
                logger.debug(f"Filtered out note [[{meta.get('title')}]] due to distance {dist:.3f} > {max_distance}")

    return "\n".join(context_snippets)


def query_vault_detailed(query_text: str, top_k: int = 5) -> list[dict]:
    """Queries ChromaDB and returns structured matching results with distances, titles, paths, and document snippets."""
    if collection.count() == 0:
        return []

    query_vector = get_embedding(query_text, is_query=True)
    if not query_vector:
        return []

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"]
    )

    items = []
    if results and "documents" in results and results["documents"]:
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results.get("distances", [[]])[0] if "distances" in results and results["distances"] else [0.0] * len(documents)
        ids = results.get("ids", [[]])[0]

        for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            items.append({
                "id": doc_id,
                "title": meta.get("title", "Unknown"),
                "path": meta.get("path", ""),
                "distance": float(dist),
                "similarity": max(0.0, 1.0 - float(dist)),
                "snippet": doc
            })
    return items


def get_chroma_stats() -> dict:
    """Returns general statistics and document summary from ChromaDB collection."""
    count = collection.count()
    sample = collection.peek(min(5, count)) if count > 0 else {}
    return {
        "collection_name": collection.name,
        "count": count,
        "metadata": collection.metadata,
        "sample_ids": sample.get("ids", []) if sample else []
    }

