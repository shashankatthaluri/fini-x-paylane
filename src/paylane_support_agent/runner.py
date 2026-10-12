"""Multi-turn conversation runner and runtime orchestration for Paylane Support Agent.

Manages conversation turns, trusted verification extraction, tool call dispatching,
truthfulness verification, and audit traces.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from paylane_support_agent.agent import (
    PUBLIC_TOOLS_SCHEMA,
    SYSTEM_PROMPT,
    dispatch_tool_call,
)
from paylane_support_agent.fixtures import FixtureStore
from paylane_support_agent.session import (
    SessionState,
    verify_customer_credentials,
)


def extract_potential_verification_credentials(user_message: str) -> Dict[str, Optional[str]]:
    """Extract candidate email and 4-digit card numbers from customer messages in trusted code.
    
    Ensures verification comparisons are done strictly in trusted code rather than trusting
    model statements.
    """
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    email_match = re.search(email_pattern, user_message)
    email = email_match.group(0) if email_match else None

    # Find 4-digit card numbers (e.g. "ending in 4821", "card 4821", "4821")
    card_pattern = r'(?:\b|ending\s+in\s+|card\s+|digits?\s+)(\d{4})\b'
    card_match = re.search(card_pattern, user_message, re.IGNORECASE)
    card_last4 = card_match.group(1) if card_match else None

    return {"email": email, "card_last4": card_last4}


class AgentRunner:
    """Orchestrates multi-turn dialogue with trusted safety enforcement."""

    def __init__(
        self,
        session: Optional[SessionState] = None,
        store: Optional[FixtureStore] = None,
        llm_client: Optional[Any] = None,
        model_name: str = "gpt-4o-mini",
    ) -> None:
        self.session = session or SessionState()
        self.store = store or FixtureStore()
        self.llm_client = llm_client
        self.model_name = model_name

        # Initialize conversation messages if new
        if not self.session.messages:
            self.session.messages.append({
                "role": "system",
                "content": SYSTEM_PROMPT,
            })

    def process_turn(self, user_input: str) -> str:
        """Process a single user turn, enforcing trusted gates at every step."""
        # Record user turn
        self.session.messages.append({"role": "user", "content": user_input})
        self.session.record_trace("user_message", {"content": user_input})

        # 1. Trusted Verification Check from user input (Application-level gate)
        # Checks if user supplied email and last 4 card digits
        creds = extract_potential_verification_credentials(user_input)
        if creds["email"] and creds["card_last4"]:
            verify_res = verify_customer_credentials(
                submitted_email=creds["email"],
                submitted_card_last4=creds["card_last4"],
                session=self.session,
                store=self.store,
            )
            # Inject trusted system state note so model knows verified status without revealing secrets
            if verify_res["success"]:
                self.session.messages.append({
                    "role": "system",
                    "content": f"[TRUSTED SYSTEM NOTE: Customer '{verify_res['name']}' (ID: {verify_res['customer_id']}) has been successfully verified via matching email and card digits. Status: {verify_res['status']}].",
                })
            else:
                self.session.messages.append({
                    "role": "system",
                    "content": "[TRUSTED SYSTEM NOTE: Submitted credentials did not match records. Customer is NOT verified. Do not disclose specific mismatch details].",
                })

        # 2. Invoke LLM if client is provided, otherwise return mock/offline response
        if not self.llm_client:
            # Deterministic fallback response when offline
            reply = self._offline_handle_turn()
            self.session.messages.append({"role": "assistant", "content": reply})
            return reply

        # Online LLM execution loop with function calling
        response_text = self._execute_llm_loop()
        return response_text

    def _execute_llm_loop(self) -> str:
        """Call LLM with tools, dispatching calls through trusted gate."""
        max_tool_iterations = 5
        iteration = 0

        while iteration < max_tool_iterations:
            iteration += 1
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=self.session.messages,
                tools=PUBLIC_TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.0,
            )

            assistant_msg = response.choices[0].message
            # Append message to session history
            tool_calls = assistant_msg.tool_calls

            if not tool_calls:
                # No tool calls, assistant produced final text
                final_text = assistant_msg.content or ""
                self.session.messages.append({"role": "assistant", "content": final_text})
                return final_text

            # Assistant requested tool calls
            msg_dict: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
            self.session.messages.append(msg_dict)

            # Process each tool call through trusted dispatcher
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except Exception:
                    fn_args = {}

                # DISPATCH THROUGH TRUSTED GATE
                result = dispatch_tool_call(
                    tool_name=fn_name,
                    tool_args=fn_args,
                    session=self.session,
                    store=self.store,
                )

                # Return sanitized tool output to model
                self.session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": json.dumps(result),
                })

        return "I am processing your request. Please let me know how else I can assist you."

    def _offline_handle_turn(self) -> str:
        """Deterministic rule-based handler for offline execution testing without API keys."""
        if not self.session.verified:
            return "For your security, please provide both your email address on file and the last 4 digits of your card so I can verify your account."
        return f"Hello {self.session.customer_name or 'there'}, your account is verified. How can I assist you with Paylane today?"
