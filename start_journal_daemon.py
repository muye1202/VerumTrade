"""
Start the journal scheduler daemon with reflection enabled.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

from tradingagents.agents.journal import (
    JournalStore,
    JournalScheduler,
    LessonMemory,
    create_reflection_callback,
)
from tradingagents.execution import AlpacaExecutor

# Ensure journal directory exists
os.makedirs("journal", exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("journal/scheduler.log", mode="a"),
    ],
)

def main():
    # Initialize components
    store = JournalStore(db_path="./journal/trade_journal.db")
    
    # Initialize Alpaca executor (optional, for live position monitoring)
    try:
        executor = AlpacaExecutor(paper=True)
        print("✓ Alpaca executor initialized")
    except Exception as e:
        print(f"⚠ Running without Alpaca: {e}")
        executor = None

    # Initialize lesson memory (ChromaDB)
    memory = LessonMemory(persist_directory="./journal/lessons_chromadb")
    print(f"✓ Lesson memory initialized ({memory.count()} lessons)")

    # Create reflection callback
    callback = create_reflection_callback(lesson_memory=memory)
    print("✓ Reflection callback created")

    # Create scheduler with reflection enabled
    scheduler = JournalScheduler(
        store=store,
        executor=executor,
        market_interval_minutes=15,  # Check every 15 min during market hours
        on_outcome_recorded=callback,  # ← This enables automatic reflection
    )

    print("\n🚀 Starting journal scheduler daemon...")
    print("   - Monitoring active positions")
    print("   - Recording outcomes when positions close")
    print("   - Extracting lessons via LLM reflection")
    print("   - Storing lessons in ChromaDB\n")
    print("Press Ctrl+C to stop\n")

    # Run forever (blocking)
    scheduler.run_forever()

if __name__ == "__main__":
    main()
