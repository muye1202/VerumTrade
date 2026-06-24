from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from verumtrade.agents.utils.market_data.core_stock_tools import (
    get_stock_data
)
from verumtrade.agents.utils.market_data.technical_indicators_tools import (
    get_indicators
)
from verumtrade.agents.utils.market_data.price_action_tools import (
    get_price_action_summary
)
from verumtrade.agents.utils.market_data.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from verumtrade.agents.utils.market_data.news_data_tools import (
    get_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news,
    get_company_news_window,
    get_news_sentiment,
    get_recent_sec_filings,
)

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]
        
        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        
        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")
        
        return {
            "messages": removal_operations + [placeholder],
            "force_no_tools_for": "",
        }
    
    return delete_messages


def create_force_finalize(analyst_key: str):
    def force_finalize(state):
        """Inject a hard instruction to synthesize final output without more tool calls."""
        return {
            "messages": [
                HumanMessage(
                    content=(
                        f"Tool round cap reached for {analyst_key} analyst. "
                        "Using only already-collected tool outputs in the conversation, "
                        "produce your final report now and do not call any tools."
                    )
                )
            ],
            "force_no_tools_for": analyst_key,
        }

    return force_finalize


        
