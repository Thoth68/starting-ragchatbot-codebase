import anthropic
import asyncio
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, AsyncGenerator
from datetime import datetime


class ConversationState(Enum):
    """States in the conversation state machine"""
    INITIAL = auto()
    AWAITING_TOOL_DECISION = auto()
    EXECUTING_TOOLS = auto()
    AWAITING_FOLLOW_UP = auto()
    COMPLETED = auto()
    ERROR = auto()


@dataclass
class StateTransition:
    """Represents a state transition with metadata"""
    from_state: ConversationState
    to_state: ConversationState
    trigger: str
    data: Optional[Dict[str, Any]] = None


@dataclass
class ConversationContext:
    """Maintains rich context throughout state transitions"""
    messages: List[Dict[str, Any]]
    system_prompt: str
    tool_call_count: int = 0
    tool_call_history: List[Dict] = field(default_factory=list)
    intermediate_responses: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_tool_call(self, tool_name: str, tool_input: Dict, result: str):
        """Track each tool call with metadata"""
        self.tool_call_count += 1
        self.tool_call_history.append({
            "index": self.tool_call_count,
            "tool_name": tool_name,
            "input": tool_input,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })


class AIGenerator:
    """Handles interactions with Anthropic's Claude API for generating responses"""
    
    # Static system prompt to avoid rebuilding on each call
    SYSTEM_PROMPT = """ You are an AI assistant specialized in course materials and educational content with access to tools for searching course content and retrieving course outlines.

Tool Usage:
- **Course Outline Tool**: Use for questions about course structure, curriculum, lesson list, or "what lessons are in [course]"
  - Returns: Course title, course link, and complete list of lessons with numbers and titles
  - Always present the full outline when retrieved, including the course link and all lesson details

- **Search Content Tool**: Use for questions about specific course content or detailed educational materials
  - **One search per query maximum**
  - Synthesize search results into accurate, fact-based responses
  - If search yields no results, state this clearly without offering alternatives

Response Protocol:
- **General knowledge questions**: Answer using existing knowledge without searching
- **Course outline/structure questions**: Use the outline tool to retrieve course details
- **Course-specific content questions**: Search first, then answer
- **No meta-commentary**:
 - Provide direct answers only — no reasoning process, search explanations, or question-type analysis
 - Do not mention "based on the search results" or "using the outline tool"


All responses must be:
1. **Brief, Concise and focused** - Get to the point quickly
2. **Educational** - Maintain instructional value
3. **Clear** - Use accessible language
4. **Example-supported** - Include relevant examples when they aid understanding
Provide only the direct answer to what was asked.
"""
    
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.async_client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

        # Pre-build base API parameters
        self.base_params = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 800
        }

        # Enable iteration logging for debugging
        self.enable_iteration_logging = True
    
    def generate_response(self, query: str,
                         conversation_history: Optional[str] = None,
                         tools: Optional[List] = None,
                         tool_manager=None) -> str:
        """
        Generate AI response with optional tool usage and conversation context.
        
        Args:
            query: The user's question or request
            conversation_history: Previous messages for context
            tools: Available tools the AI can use
            tool_manager: Manager to execute tools
            
        Returns:
            Generated response as string
        """
        
        # Build system content efficiently - avoid string ops when possible
        system_content = (
            f"{self.SYSTEM_PROMPT}\n\nPrevious conversation:\n{conversation_history}"
            if conversation_history 
            else self.SYSTEM_PROMPT
        )
        
        # Prepare API call parameters efficiently
        api_params = {
            **self.base_params,
            "messages": [{"role": "user", "content": query}],
            "system": system_content
        }
        
        # Add tools if available
        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = {"type": "auto"}
        
        # Get response from Claude
        response = self.client.messages.create(**api_params)
        
        # Handle tool execution if needed
        if response.stop_reason == "tool_use" and tool_manager:
            return self._handle_tool_execution(response, api_params, tool_manager)
        
        # Return direct response
        return response.content[0].text
    
    def _handle_tool_execution(self, initial_response, base_params: Dict[str, Any], tool_manager):
        """
        Handle execution of tool calls and get follow-up response.
        
        Args:
            initial_response: The response containing tool use requests
            base_params: Base API parameters
            tool_manager: Manager to execute tools
            
        Returns:
            Final response text after tool execution
        """
        # Start with existing messages
        messages = base_params["messages"].copy()
        
        # Add AI's tool use response
        messages.append({"role": "assistant", "content": initial_response.content})
        
        # Execute all tool calls and collect results
        tool_results = []
        for content_block in initial_response.content:
            if content_block.type == "tool_use":
                tool_result = tool_manager.execute_tool(
                    content_block.name, 
                    **content_block.input
                )
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": tool_result
                })
        
        # Add tool results as single message
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        
        # Prepare final API call without tools
        final_params = {
            **self.base_params,
            "messages": messages,
            "system": base_params["system"]
        }
        
        # Get final response
        final_response = self.client.messages.create(**final_params)
        return final_response.content[0].text

    async def _execute_tool_async(self, tool_block, tool_manager):
        """Execute a single tool asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            tool_manager.execute_tool,
            tool_block.name,
            *(),
            tool_block.input
        )

    async def _handle_initial_state(self, context: ConversationContext, tools: Optional[List]) -> StateTransition:
        """Handle the initial state - send first query to Claude"""
        api_params = {
            **self.base_params,
            "messages": context.messages,
            "system": context.system_prompt
        }

        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = {"type": "auto"}

        response = await self.async_client.messages.create(**api_params)

        return StateTransition(
            from_state=ConversationState.INITIAL,
            to_state=ConversationState.AWAITING_TOOL_DECISION,
            trigger="api_call_complete",
            data={"response": response}
        )

    async def _handle_tool_decision_state(self, response, context: ConversationContext) -> StateTransition:
        """Determine what to do based on stop_reason"""
        stop_reason = response.stop_reason

        if stop_reason == "tool_use":
            # Claude wants to use tools
            context.messages.append({
                "role": "assistant",
                "content": response.content
            })

            if self.enable_iteration_logging:
                tool_names = [block.name for block in response.content if hasattr(block, 'type') and block.type == "tool_use"]
                print(f"Tool iteration {context.tool_call_count + 1}: {tool_names}")

            return StateTransition(
                from_state=ConversationState.AWAITING_TOOL_DECISION,
                to_state=ConversationState.EXECUTING_TOOLS,
                trigger="tool_use",
                data={"response": response, "tool_blocks": response.content}
            )

        elif stop_reason == "end_turn":
            # Claude is providing final answer
            final_text = None
            for content_block in response.content:
                if hasattr(content_block, 'text'):
                    final_text = content_block.text
                    break

            return StateTransition(
                from_state=ConversationState.AWAITING_TOOL_DECISION,
                to_state=ConversationState.COMPLETED,
                trigger="end_turn",
                data={"final_text": final_text, "response": response}
            )

        elif stop_reason == "max_tokens":
            # Token limit reached
            partial_text = None
            for content_block in response.content:
                if hasattr(content_block, 'text'):
                    partial_text = content_block.text
                    break

            return StateTransition(
                from_state=ConversationState.AWAITING_TOOL_DECISION,
                to_state=ConversationState.ERROR,
                trigger="max_tokens",
                data={"error": "Token limit reached", "partial_text": partial_text}
            )

        else:
            # Unexpected stop reason
            return StateTransition(
                from_state=ConversationState.AWAITING_TOOL_DECISION,
                to_state=ConversationState.ERROR,
                trigger="unknown_stop_reason",
                data={"error": f"Unexpected stop_reason: {stop_reason}"}
            )

    async def _handle_tool_execution_state(self, tool_blocks, context: ConversationContext, tool_manager) -> StateTransition:
        """Execute tools and prepare for next API call"""
        tool_results = []

        for content_block in tool_blocks:
            if hasattr(content_block, 'type') and content_block.type == "tool_use":
                # Execute tool
                result = await self._execute_tool_async(content_block, tool_manager)

                # Track in context
                context.add_tool_call(
                    tool_name=content_block.name,
                    tool_input=content_block.input,
                    result=result
                )

                # Format result for API
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": result
                })

        # Add tool results as user message
        if tool_results:
            context.messages.append({
                "role": "user",
                "content": tool_results
            })

        return StateTransition(
            from_state=ConversationState.EXECUTING_TOOLS,
            to_state=ConversationState.AWAITING_FOLLOW_UP,
            trigger="tools_executed",
            data={"tool_results": tool_results}
        )

    async def _handle_follow_up_state(self, context: ConversationContext, tools: Optional[List]) -> StateTransition:
        """Send tool results back to Claude and get response"""
        api_params = {
            **self.base_params,
            "messages": context.messages,
            "system": context.system_prompt
        }

        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = {"type": "auto"}

        response = await self.async_client.messages.create(**api_params)

        stop_reason = response.stop_reason

        if stop_reason == "tool_use":
            # Claude wants to use more tools
            context.messages.append({
                "role": "assistant",
                "content": response.content
            })

            if self.enable_iteration_logging:
                tool_names = [block.name for block in response.content if hasattr(block, 'type') and block.type == "tool_use"]
                print(f"Tool iteration {context.tool_call_count + 1}: {tool_names}")

            return StateTransition(
                from_state=ConversationState.AWAITING_FOLLOW_UP,
                to_state=ConversationState.EXECUTING_TOOLS,
                trigger="tool_use",
                data={"response": response, "tool_blocks": response.content}
            )

        elif stop_reason == "end_turn":
            # Claude is done - extract final answer
            final_text = None
            for content_block in response.content:
                if hasattr(content_block, 'text'):
                    final_text = content_block.text
                    break

            return StateTransition(
                from_state=ConversationState.AWAITING_FOLLOW_UP,
                to_state=ConversationState.COMPLETED,
                trigger="end_turn",
                data={"final_text": final_text, "response": response}
            )

        elif stop_reason == "max_tokens":
            partial_text = None
            for content_block in response.content:
                if hasattr(content_block, 'text'):
                    partial_text = content_block.text
                    break

            return StateTransition(
                from_state=ConversationState.AWAITING_FOLLOW_UP,
                to_state=ConversationState.ERROR,
                trigger="max_tokens",
                data={"error": "Token limit reached", "partial_text": partial_text}
            )

        else:
            return StateTransition(
                from_state=ConversationState.AWAITING_FOLLOW_UP,
                to_state=ConversationState.ERROR,
                trigger="unknown_stop_reason",
                data={"error": f"Unexpected stop_reason: {stop_reason}"}
            )

    async def _dispatch_state(
        self,
        state: ConversationState,
        context: ConversationContext,
        tools: Optional[List],
        tool_manager,
        last_response=None
    ) -> StateTransition:
        """Dispatch to the appropriate state handler"""
        if state == ConversationState.INITIAL:
            return await self._handle_initial_state(context, tools)

        elif state == ConversationState.AWAITING_TOOL_DECISION:
            return await self._handle_tool_decision_state(last_response, context)

        elif state == ConversationState.EXECUTING_TOOLS:
            tool_blocks = last_response.content if last_response else []
            return await self._handle_tool_execution_state(tool_blocks, context, tool_manager)

        elif state == ConversationState.AWAITING_FOLLOW_UP:
            return await self._handle_follow_up_state(context, tools)

        else:
            raise ValueError(f"No handler for state: {state}")

    async def generate_response_stream(
        self,
        query: str,
        conversation_history: Optional[str] = None,
        tools: Optional[List] = None,
        tool_manager=None,
        max_iterations: int = 10
    ) -> AsyncGenerator[StateTransition, None]:
        """
        Generate AI response with sequential tool calling via state machine.
        Yields state transitions as they occur.

        Args:
            query: The user's question or request
            conversation_history: Previous messages for context
            tools: Available tools the AI can use
            tool_manager: Manager to execute tools
            max_iterations: Maximum tool-use cycles (default: 10)

        Yields:
            StateTransition objects as the conversation progresses
        """
        # Build system content
        system_content = (
            f"{self.SYSTEM_PROMPT}\n\nPrevious conversation:\n{conversation_history}"
            if conversation_history
            else self.SYSTEM_PROMPT
        )

        # Initialize context
        context = ConversationContext(
            messages=[{"role": "user", "content": query}],
            system_prompt=system_content
        )

        # State machine loop
        current_state = ConversationState.INITIAL
        iteration = 0
        last_response = None

        while current_state not in [ConversationState.COMPLETED, ConversationState.ERROR] and iteration < max_iterations:
            iteration += 1

            # Dispatch to appropriate state handler
            transition = await self._dispatch_state(
                current_state,
                context,
                tools,
                tool_manager,
                last_response
            )

            # Yield the transition for streaming/logging
            yield transition

            # Check for terminal states
            if transition.to_state in [ConversationState.COMPLETED, ConversationState.ERROR]:
                break

            # Update state and response
            current_state = transition.to_state
            if "response" in transition.data:
                last_response = transition.data["response"]

        # Safety: Max iterations exceeded
        if iteration >= max_iterations and current_state not in [ConversationState.COMPLETED, ConversationState.ERROR]:
            yield StateTransition(
                from_state=current_state,
                to_state=ConversationState.ERROR,
                trigger="max_iterations_exceeded",
                data={"error": f"Exceeded {max_iterations} iterations without completion"}
            )