import config
from typing import Annotated, Literal, TypedDict
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# Define the state
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

# Define the tool
from tools import get_menu_data, dish_data, dish_name_with_veg_nonveg_category, get_dish_counts, get_dish_ingredients, get_list_of_meal_category, get_menu_category_dish, get_list_of_dish_name_of_category
from recom_file import recommend_dishes


# Initialize the model based on config
if config.LLM_PROVIDER == "gemini":
    llm = ChatGoogleGenerativeAI(
        model=config.GEMINI_MODEL_NAME,
        google_api_key=config.GEMINI_API_KEY,
        temperature=0
    )
elif config.LLM_PROVIDER == "groq":
    llm = ChatGroq(
        model=config.GROQ_MODEL_NAME,
        api_key=config.GROQ_API_KEY,
        temperature=0
    )
else:
    raise ValueError(f"Unknown LLM_PROVIDER: {config.LLM_PROVIDER}")

# Bind tools to the model
tools = [get_menu_data, dish_data, dish_name_with_veg_nonveg_category, get_dish_counts, recommend_dishes, get_dish_ingredients, get_list_of_meal_category, get_menu_category_dish, get_list_of_dish_name_of_category]
llm_with_tools = llm.bind_tools(tools)

# Define the chatbot node
def chatbot(state: AgentState):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

# Define the graph
builder = StateGraph(AgentState)
builder.add_node("chatbot", chatbot)
builder.add_node("tools", ToolNode(tools))

builder.add_edge(START, "chatbot")

def should_continue(state: AgentState) -> Literal["tools", END]:
    messages = state["messages"]
    last_message = messages[-1]
    if last_message.tool_calls:
        return "tools"
    return END

builder.add_conditional_edges("chatbot", should_continue)
builder.add_edge("tools", "chatbot")

# Compile the graph
graph = builder.compile()
