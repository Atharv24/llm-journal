import re
import json
import logging
import requests
from config import OLLAMA_URL, OLLAMA_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)


def normalize_tags(raw_tags: list) -> list[str]:
    """Cleans and standardizes tags into kebab-case ('in-this-case') without duplicates."""
    formatted = []
    if not isinstance(raw_tags, list):
        return formatted

    for tag in raw_tags:
        tag_str = str(tag).strip()
        if not tag_str:
            continue
        # Remove leading hashtags if present
        tag_str = tag_str.lstrip("#")
        # Replace spaces, underscores, slashes, and periods with a hyphen
        tag_str = re.sub(r'[\s_/\\]+', '-', tag_str)
        # Remove any characters that aren't alphanumeric or hyphen
        tag_str = re.sub(r'[^a-zA-Z0-9-]', '', tag_str)
        # Collapse consecutive hyphens
        tag_str = re.sub(r'-+', '-', tag_str)
        # Lowercase and strip outer hyphens
        clean_tag = tag_str.lower().strip('-')

        if clean_tag and clean_tag not in formatted:
            formatted.append(clean_tag)

    return formatted

def normalize_wiki_links(raw_links: list) -> list[str]:
    """Cleans and standardizes wiki links into '[[Note Title]]' format without duplicates."""
    formatted = []
    if not isinstance(raw_links, list):
        return formatted

    for link in raw_links:
        link_str = str(link).strip()
        if not link_str or link_str.lower() in ("none", "null", "[]"):
            continue
        # Strip extraneous surrounding quotes or brackets if malformed
        link_str = link_str.strip("\"'")
        if not (link_str.startswith("[[") and link_str.endswith("]]")):
            link_str = f"[[{link_str}]]"
        if link_str not in formatted:
            formatted.append(link_str)
    return formatted


def process_transcript_with_llm(raw_transcript: str, rag_context: str) -> dict:
    """Sends prompt with raw transcript and RAG context to Ollama, returning validated structured metadata."""
    prompt = f"""
You are an Obsidian knowledge assistant. Your job is to structure and format my voice note so it is easily searchable and organized.

--- Relevant Context From Existing Obsidian Vault Notes ---
{rag_context if rag_context else "No relevant existing notes found."}

--- Raw Audio Transcript ---
"{raw_transcript}"

Instructions:
1. TITLE: Create a clear, high-signal 3-6 word title that captures specific entities, technical terms, or concrete concepts discussed. NEVER use generic filler like "Voice Note", "Thoughts on...", "Quick Update", or "Discussion".
2. VAULT CONTEXT & RELATED NOTES: Review the provided context notes above. Identify the most relevant notes and list their exact wiki links in `wiki_links` (e.g. `["[[Exact Note Title]]"]`).
3. CATEGORY: Classify into exactly one of: "Projects", "Areas", "Resources", "Thoughts".

Return ONLY a raw JSON object matching this schema:
{{
  "title": "Clear 3-6 word title (e.g., 'Docker Compose GPU Pass-through Configuration')",
  "summary": "1-2 sentence summary of core message and takeaways",
  "category": "Projects",
  "tags": ["tag-1", "tag-2", "another-tag-example"],
  "wiki_links": ["[[Exact Note Title From Context]]"],
  "action_items": ["Action item 1"]
}}
"""
    try:
        res = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }, timeout=LLM_TIMEOUT)

        if res.status_code != 200:
            logger.error(f"Ollama request failed with status {res.status_code}: {res.text}")
            raise RuntimeError(f"Ollama error {res.status_code}: {res.text}")

        response_json = res.json()
        raw_response = response_json.get("response", "{}")
        parsed = json.loads(raw_response)

        # Normalize wiki links
        parsed["wiki_links"] = normalize_wiki_links(parsed.get("wiki_links", []))

        # Normalize tags
        parsed["tags"] = normalize_tags(parsed.get("tags", []))
        return parsed

    except Exception as e:
        logger.error(f"LLM processing failed: {e}")
        return {
            "title": "Unprocessed Voice Note",
            "summary": "LLM processing failed.",
            "category": "Thoughts",
            "tags": ["unprocessed"],
            "wiki_links": [],
            "action_items": []
        }
