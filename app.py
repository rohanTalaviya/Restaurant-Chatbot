import streamlit as st
import json
import config
from pymongo import MongoClient
from agent import graph
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from menu_processing import get_restaurant_data


# Page configuration
st.set_page_config(
    page_title="Nutrition Chatbot",
    page_icon="🍽️",
    layout="centered"
)

# Load user data
@st.cache_resource
def get_user_data():
    """Load user data from MongoDB"""
    client = MongoClient(config.MONGO_URI)
    db = client[config.DB_NAME]
    UserData = db["UserData"]
    
    # Get user data
    User = UserData.find_one({"_id": config.USER_ID})
    
    # Filter user data
    if User:
        filtered_user = {}
        fields_to_extract = [
            "name", "height", "dob", "gender", "weight", "life_routine", 
            "gym_or_yoga", "intensity", "hunger_level", "allergies", 
            "diet_preference", "city", "state", "_id"
        ]
        
        for field in fields_to_extract:
            if field in User:
                filtered_user[field] = User[field]
                
        if "goals" in User and isinstance(User["goals"], dict):
            filtered_user["goals"] = {
                "daily_goal": User["goals"].get("daily_goal"),
                "live_goal": User["goals"].get("live_goal")
            }
            
        return filtered_user
    return None

# Load the data
try:
    User = get_user_data()
    if not User:
        st.error("User not found")
        st.stop()
except Exception as e:
    st.error(f"Error loading data: {e}")
    st.stop()

# Initialize session state for chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# App header
st.title("🍽️ Nutrition Chatbot")
st.markdown("**Your personal nutritionist assistant**")
st.markdown("---")

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Ask me about nutrition and meal recommendations..."):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Generate and display assistant response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            # Prepare messages for the agent
            langchain_messages = []
            
            # Add system instruction
            restaurant_data = get_restaurant_data(config.RESTAURANT_ID)
            system_instruction = config.FULL_INSTRUCTION_TEMPLATE.format(
                user_data=User,
                restaurant_data=restaurant_data
            ) + "\n\n" + "This is Restaurant ID: " + config.RESTAURANT_ID + "\n\n"

            langchain_messages.append(SystemMessage(content=system_instruction))
            
            # Add chat history
            for msg in st.session_state.messages:
                if msg["role"] == "user":
                    langchain_messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    langchain_messages.append(AIMessage(content=msg["content"]))
            
            # Invoke the agent
            response_state = graph.invoke({"messages": langchain_messages})
            response_message = response_state["messages"][-1]
            response = response_message.content
            
            st.markdown(response)
    
    # Add assistant response to chat history
    st.session_state.messages.append({"role": "assistant", "content": response})
