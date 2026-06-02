"""
Lesson Memory — ChromaDB-backed vector storage for trade lessons.

Stores lessons with embeddings for semantic retrieval during future trading
decisions. Separate from the SQLite journal for vector-specific operations.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from opentrace.agents.journal.core.models import TradeLesson

logger = logging.getLogger(__name__)

# Default location for ChromaDB persistence
DEFAULT_CHROMADB_PATH = Path("./journal/lessons_chromadb")


class LessonMemory:
    """
    ChromaDB-backed memory for trade lessons.

    Provides semantic search over lessons to find relevant past experiences
    for current trading situations.
    """

    def __init__(
        self,
        persist_directory: Optional[str | Path] = None,
        collection_name: str = "trade_lessons",
        embedding_function: Optional[Any] = None,
    ):
        """
        Initialize the lesson memory.

        Args:
            persist_directory: Path to ChromaDB storage. Defaults to ./journal/lessons_chromadb
            collection_name: Name of the ChromaDB collection
            embedding_function: Custom embedding function. Defaults to OpenAI embeddings.
        """
        self.persist_directory = Path(persist_directory or DEFAULT_CHROMADB_PATH)
        self.collection_name = collection_name

        # Lazy initialization
        self._client = None
        self._collection = None
        self._embedding_function = embedding_function

    def _ensure_initialized(self) -> None:
        """Initialize ChromaDB client and collection on first use."""
        if self._client is not None:
            return

        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError(
                "chromadb is required for LessonMemory. "
                "Install with: pip install chromadb"
            )

        # Ensure directory exists
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # Initialize client with persistence
        self._client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=Settings(anonymized_telemetry=False),
        )

        # Get or create the embedding function
        if self._embedding_function is None:
            try:
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                import os

                api_key = os.getenv("OPENAI_API_KEY")
                if api_key:
                    self._embedding_function = OpenAIEmbeddingFunction(
                        api_key=api_key,
                        model_name="text-embedding-3-small",
                    )
                else:
                    # Fall back to default embedding
                    logger.warning("No OPENAI_API_KEY found, using default embeddings")
                    self._embedding_function = None
            except Exception as e:
                logger.warning(f"Could not create OpenAI embeddings: {e}")
                self._embedding_function = None

        # Get or create collection
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self._embedding_function,
            metadata={"description": "Trade lessons from reflection agent"},
        )

        logger.info(
            f"LessonMemory initialized at {self.persist_directory} "
            f"(collection: {self.collection_name}, {self._collection.count()} lessons)"
        )

    def add_lesson(self, lesson: TradeLesson) -> str:
        """
        Add a lesson to the memory.

        Args:
            lesson: The TradeLesson to store

        Returns:
            The lesson ID
        """
        self._ensure_initialized()

        # Generate embedding text
        embedding_text = lesson.to_embedding_text()

        # Prepare metadata (ChromaDB requires flat structure)
        metadata = {
            "thesis_id": lesson.thesis_id,
            "outcome_id": lesson.outcome_id,
            "ticker": lesson.ticker,
            "trade_date": lesson.trade_date,
            "action": lesson.action,
            "category": lesson.category,
            "realized_pl_pct": lesson.realized_pl_pct or 0.0,
            "exit_reason": lesson.exit_reason or "",
            "risk_multiple": lesson.risk_multiple or 0.0,
            "confidence": lesson.confidence,
            "regime_correct": lesson.regime_correct if lesson.regime_correct is not None else False,
            "catalyst_materialized": lesson.catalyst_materialized if lesson.catalyst_materialized is not None else False,
            "most_accurate_agent": lesson.most_accurate_agent or "",
            "least_accurate_agent": lesson.least_accurate_agent or "",
            "tags": json.dumps(lesson.tags),  # Store as JSON string
            "created_at": lesson.created_at,
        }

        # Add to collection
        self._collection.add(
            ids=[lesson.id],
            documents=[embedding_text],
            metadatas=[metadata],
        )

        logger.debug(f"Added lesson {lesson.id} for {lesson.ticker}")
        return lesson.id

    def query_similar(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query for similar lessons based on semantic similarity.

        Args:
            query: Natural language query describing the current situation
            n_results: Maximum number of results to return
            where: Optional filter conditions (e.g., {"ticker": "AAPL"})

        Returns:
            List of matching lessons with similarity scores
        """
        self._ensure_initialized()

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        # Format results
        lessons = []
        if results["ids"] and results["ids"][0]:
            for i, lesson_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                document = results["documents"][0][i] if results["documents"] else ""
                distance = results["distances"][0][i] if results["distances"] else 0.0

                # Parse tags back from JSON
                if "tags" in metadata:
                    try:
                        metadata["tags"] = json.loads(metadata["tags"])
                    except json.JSONDecodeError:
                        metadata["tags"] = []

                lessons.append({
                    "id": lesson_id,
                    "document": document,
                    "metadata": metadata,
                    "distance": distance,
                    "similarity": 1.0 - distance,  # Convert distance to similarity
                })

        return lessons

    def get_lessons_by_category(
        self,
        category: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get lessons filtered by category.

        Args:
            category: Category to filter by (e.g., "momentum_in_uptrend")
            limit: Maximum number of results

        Returns:
            List of lessons matching the category
        """
        self._ensure_initialized()

        results = self._collection.get(
            where={"category": category},
            limit=limit,
            include=["documents", "metadatas"],
        )

        lessons = []
        if results["ids"]:
            for i, lesson_id in enumerate(results["ids"]):
                metadata = results["metadatas"][i] if results["metadatas"] else {}
                document = results["documents"][i] if results["documents"] else ""

                if "tags" in metadata:
                    try:
                        metadata["tags"] = json.loads(metadata["tags"])
                    except json.JSONDecodeError:
                        metadata["tags"] = []

                lessons.append({
                    "id": lesson_id,
                    "document": document,
                    "metadata": metadata,
                })

        return lessons

    def get_lessons_by_ticker(
        self,
        ticker: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get lessons for a specific ticker."""
        self._ensure_initialized()

        results = self._collection.get(
            where={"ticker": ticker},
            limit=limit,
            include=["documents", "metadatas"],
        )

        lessons = []
        if results["ids"]:
            for i, lesson_id in enumerate(results["ids"]):
                metadata = results["metadatas"][i] if results["metadatas"] else {}
                document = results["documents"][i] if results["documents"] else ""

                if "tags" in metadata:
                    try:
                        metadata["tags"] = json.loads(metadata["tags"])
                    except json.JSONDecodeError:
                        metadata["tags"] = []

                lessons.append({
                    "id": lesson_id,
                    "document": document,
                    "metadata": metadata,
                })

        return lessons

    def get_winning_lessons(
        self,
        min_r_multiple: float = 1.0,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get lessons from profitable trades.

        Args:
            min_r_multiple: Minimum R-multiple to consider a "win"
            limit: Maximum number of results

        Returns:
            List of lessons from winning trades
        """
        self._ensure_initialized()

        results = self._collection.get(
            where={"risk_multiple": {"$gte": min_r_multiple}},
            limit=limit,
            include=["documents", "metadatas"],
        )

        lessons = []
        if results["ids"]:
            for i, lesson_id in enumerate(results["ids"]):
                metadata = results["metadatas"][i] if results["metadatas"] else {}
                document = results["documents"][i] if results["documents"] else ""

                if "tags" in metadata:
                    try:
                        metadata["tags"] = json.loads(metadata["tags"])
                    except json.JSONDecodeError:
                        metadata["tags"] = []

                lessons.append({
                    "id": lesson_id,
                    "document": document,
                    "metadata": metadata,
                })

        return lessons

    def get_lesson_by_id(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific lesson by ID."""
        self._ensure_initialized()

        results = self._collection.get(
            ids=[lesson_id],
            include=["documents", "metadatas"],
        )

        if results["ids"]:
            metadata = results["metadatas"][0] if results["metadatas"] else {}
            document = results["documents"][0] if results["documents"] else ""

            if "tags" in metadata:
                try:
                    metadata["tags"] = json.loads(metadata["tags"])
                except json.JSONDecodeError:
                    metadata["tags"] = []

            return {
                "id": results["ids"][0],
                "document": document,
                "metadata": metadata,
            }

        return None

    def delete_lesson(self, lesson_id: str) -> bool:
        """Delete a lesson by ID."""
        self._ensure_initialized()

        try:
            self._collection.delete(ids=[lesson_id])
            return True
        except Exception as e:
            logger.error(f"Failed to delete lesson {lesson_id}: {e}")
            return False

    def count(self) -> int:
        """Get the total number of lessons in memory."""
        self._ensure_initialized()
        return self._collection.count()

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the lesson memory."""
        self._ensure_initialized()

        total = self._collection.count()
        
        # Get category distribution (limited sample)
        sample = self._collection.get(
            limit=min(total, 1000),
            include=["metadatas"],
        )
        
        categories = {}
        tickers = {}
        winning = 0
        losing = 0
        
        if sample["metadatas"]:
            for metadata in sample["metadatas"]:
                cat = metadata.get("category", "uncategorized")
                categories[cat] = categories.get(cat, 0) + 1
                
                ticker = metadata.get("ticker", "unknown")
                tickers[ticker] = tickers.get(ticker, 0) + 1
                
                r_mult = metadata.get("risk_multiple", 0)
                if r_mult > 0:
                    winning += 1
                elif r_mult < 0:
                    losing += 1

        return {
            "total_lessons": total,
            "categories": categories,
            "tickers": tickers,
            "winning_trades": winning,
            "losing_trades": losing,
            "persist_directory": str(self.persist_directory),
        }
