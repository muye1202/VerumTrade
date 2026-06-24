import os
import hashlib
import math
import re
import chromadb
from chromadb.config import Settings
from openai import OpenAI


class FinancialSituationMemory:
    def __init__(self, name, config):
        self.embedding_backend = "local_hash"
        self.embedding = None

        backend_url = config.get("backend_url", "")
        llm_provider = config.get("llm_provider", "").lower()

        # Use Ollama's OpenAI-compatible embeddings endpoint when applicable.
        if backend_url == "http://localhost:11434/v1":
            self.embedding_backend = "openai_compatible"
            self.embedding = "nomic-embed-text"
        # Use OpenAI embeddings only for the actual OpenAI API; many OpenAI-compatible
        # providers (DashScope, OpenRouter) do not expose OpenAI embedding models.
        elif llm_provider == "openai" and backend_url.startswith("https://api.openai.com/"):
            self.embedding_backend = "openai_compatible"
            self.embedding = os.getenv("VERUMTRADE_EMBEDDING_MODEL", "text-embedding-3-small")

        self.client = None
        if self.embedding_backend == "openai_compatible":
            api_key = None
            if llm_provider == "qwen3-cn":
                api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
            self.client = OpenAI(base_url=backend_url, api_key=api_key)

        self.chroma_client = chromadb.Client(Settings(allow_reset=True))

        # Chroma clients can share state within a process. When running multi-ticker
        # analysis sequentially, re-initializing this memory with the same collection
        # name would otherwise raise "Collection already exists". We prefer a clean
        # slate per run, so delete+recreate if needed.
        try:
            self.chroma_client.delete_collection(name=name)
        except Exception:
            pass
        self.situation_collection = self.chroma_client.create_collection(name=name)

    def _hash_embed(self, text: str, dim: int = 256) -> list[float]:
        """Deterministic local embedding to keep memory working on non-OpenAI backends."""
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        vec = [0.0] * dim
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            n = int.from_bytes(h, "big", signed=False)
            idx = n % dim
            sign = 1.0 if (n & 1) == 0 else -1.0
            vec[idx] += sign

        # L2 normalize to make cosine distance behave sensibly.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def get_embedding(self, text):
        """Get OpenAI embedding for a text"""
        if self.embedding_backend == "openai_compatible":
            response = self.client.embeddings.create(model=self.embedding, input=text)
            return response.data[0].embedding

        return self._hash_embed(text)

    def add_situations(self, situations_and_advice):
        """Add financial situations and their corresponding advice. Parameter is a list of tuples (situation, rec)"""

        situations = []
        advice = []
        ids = []
        embeddings = []

        offset = self.situation_collection.count()

        for i, (situation, recommendation) in enumerate(situations_and_advice):
            situations.append(situation)
            advice.append(recommendation)
            ids.append(str(offset + i))
            embeddings.append(self.get_embedding(situation))

        self.situation_collection.add(
            documents=situations,
            metadatas=[{"recommendation": rec} for rec in advice],
            embeddings=embeddings,
            ids=ids,
        )

    def get_memories(self, current_situation, n_matches=1):
        """Find matching recommendations using OpenAI embeddings"""
        query_embedding = self.get_embedding(current_situation)

        results = self.situation_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_matches,
            include=["metadatas", "documents", "distances"],
        )

        matched_results = []
        for i in range(len(results["documents"][0])):
            matched_results.append(
                {
                    "matched_situation": results["documents"][0][i],
                    "recommendation": results["metadatas"][0][i]["recommendation"],
                    "similarity_score": 1 - results["distances"][0][i],
                }
            )

        return matched_results


if __name__ == "__main__":
    # Example usage
    matcher = FinancialSituationMemory()

    # Example data
    example_data = [
        (
            "High inflation rate with rising interest rates and declining consumer spending",
            "Consider defensive sectors like consumer staples and utilities. Review fixed-income portfolio duration.",
        ),
        (
            "Tech sector showing high volatility with increasing institutional selling pressure",
            "Reduce exposure to high-growth tech stocks. Look for value opportunities in established tech companies with strong cash flows.",
        ),
        (
            "Strong dollar affecting emerging markets with increasing forex volatility",
            "Hedge currency exposure in international positions. Consider reducing allocation to emerging market debt.",
        ),
        (
            "Market showing signs of sector rotation with rising yields",
            "Rebalance portfolio to maintain target allocations. Consider increasing exposure to sectors benefiting from higher rates.",
        ),
    ]

    # Add the example situations and recommendations
    matcher.add_situations(example_data)

    # Example query
    current_situation = """
    Market showing increased volatility in tech sector, with institutional investors 
    reducing positions and rising interest rates affecting growth stock valuations
    """

    try:
        recommendations = matcher.get_memories(current_situation, n_matches=2)

        for i, rec in enumerate(recommendations, 1):
            print(f"\nMatch {i}:")
            print(f"Similarity Score: {rec['similarity_score']:.2f}")
            print(f"Matched Situation: {rec['matched_situation']}")
            print(f"Recommendation: {rec['recommendation']}")

    except Exception as e:
        print(f"Error during recommendation: {str(e)}")
