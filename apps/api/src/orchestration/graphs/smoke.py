"""Development-only LangGraph smoke graph."""

from typing import Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from core import Agent
from services.llm import get_model


class State(TypedDict):
    messages: Annotated[list, add_messages]


def chatbot(state: State):
    """Invoke the configured Master Chef model for one smoke-test turn."""
    model = get_model(Agent.MASTER_CHEF)
    return {"messages": [model.invoke(state["messages"])]}


graph = (
    StateGraph(State)
    .add_node("chatbot", chatbot)
    .add_edge(START, "chatbot")
    .add_edge("chatbot", END)
    .compile()
)
