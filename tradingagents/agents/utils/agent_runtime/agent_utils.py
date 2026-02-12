from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.market_data.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.market_data.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.market_data.price_action_tools import (
    get_price_action_summary
)
from tradingagents.agents.utils.market_data.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.market_data.news_data_tools import (
    get_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news,
    get_company_news_window,
)

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]
        
        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        
        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")
        
        return {"messages": removal_operations + [placeholder]}
    
    return delete_messages


        
