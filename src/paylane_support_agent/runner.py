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
    email = email_match.group(0).rstrip(".") if email_match else None

    # Find 4-digit card numbers, prioritizing explicit mentions like "ending in 4821", "card 4821", "last 4 is 4821"
    explicit_pattern = r'(?:card|ending\s+in|last\s+4|digits?)[\s:]*([0-9]{4})\b'
    explicit_match = re.search(explicit_pattern, user_message, re.IGNORECASE)
    if explicit_match:
        card_last4 = explicit_match.group(1)
    else:
        # Fallback to any 4-digit token that is not a common year (e.g. 1990-2099)
        matches = re.findall(r'\b([0-9]{4})\b', user_message)
        non_years = [m for m in matches if not (m.startswith("19") or m.startswith("20"))]
        card_last4 = non_years[0] if non_years else (matches[0] if matches else None)

    return {"email": email, "card_last4": card_last4}



class AgentRunner:
    """Orchestrates multi-turn dialogue with trusted safety enforcement."""

    def __init__(
        self,
        session: Optional[SessionState] = None,
        store: Optional[FixtureStore] = None,
        llm_client: Optional[Any] = None,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.0,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.session = session or SessionState()
        self.store = store or FixtureStore()
        self.llm_client = llm_client
        self.model_name = model_name
        self.temperature = float(temperature)
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.last_turn_usage: Dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "tool_called": "none"}

        # Initialize conversation messages if new
        if not self.session.messages:
            self.session.messages.append({
                "role": "system",
                "content": self.system_prompt,
            })

    def process_turn(self, user_input: str) -> str:
        """Process a single user turn, enforcing trusted gates at every step."""
        # Record user turn
        self.session.messages.append({"role": "user", "content": user_input})
        self.session.record_trace("user_message", {"content": user_input})

        # 1. Trusted Verification Check from user input (Application-level gate)
        # Checks if user supplied email and last 4 card digits
        creds = extract_potential_verification_credentials(user_input)
        if creds["email"] and not self.session.verified:
            self.session.unverified_email = creds["email"]

        email_to_verify = creds["email"] or self.session.unverified_email
        card_to_verify = creds["card_last4"]

        if email_to_verify and card_to_verify and not self.session.verified:
            verify_res = verify_customer_credentials(
                submitted_email=email_to_verify,
                submitted_card_last4=card_to_verify,
                session=self.session,
                store=self.store,
            )
            # Inject trusted system state note so model knows verified status without revealing secrets
            if verify_res["success"]:
                freeze_info = ""
                if verify_res.get("status") == "frozen":
                    r = verify_res.get("freeze_reason") or "Card is frozen"
                    freeze_info = f" Freeze reason on file: '{r}'."
                self.session.messages.append({
                    "role": "system",
                    "content": f"[TRUSTED SYSTEM NOTE: Customer '{verify_res['name']}' (ID: {verify_res['customer_id']}) has been successfully verified via matching email and card digits. Card status: {verify_res['status']}.{freeze_info}].",
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

        self.last_turn_usage = {"input_tokens": 0, "output_tokens": 0, "tool_called": "none"}

        while iteration < max_tool_iterations:
            iteration += 1
            try:
                response = self.llm_client.chat.completions.create(
                    model=self.model_name,
                    messages=self.session.messages,
                    tools=PUBLIC_TOOLS_SCHEMA,
                    tool_choice="auto",
                    temperature=self.temperature,
                )
            except Exception as e:
                # Graceful fallback on OpenAI API exceptions (e.g. quota, rate limits, network timeouts)
                fallback_msg = self._offline_handle_turn()
                self.session.messages.append({"role": "assistant", "content": fallback_msg})
                return fallback_msg

            if hasattr(response, "usage") and response.usage:
                self.last_turn_usage["input_tokens"] += getattr(response.usage, "prompt_tokens", 0) or 0
                self.last_turn_usage["output_tokens"] += getattr(response.usage, "completion_tokens", 0) or 0

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
                self.last_turn_usage["tool_called"] = fn_name
                result = dispatch_tool_call(
                    tool_name=fn_name,
                    tool_args=fn_args,
                    session=self.session,
                    store=self.store,
                )

                # Return sanitized tool output to model (preserving exact public tool schema)
                self.session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": json.dumps(result),
                })

                # Approach A: Separate Trusted Policy Context
                # If get_transaction succeeded, trusted code evaluates eligibility and injects a trusted policy directive
                if fn_name == "get_transaction" and not result.get("error"):
                    from paylane_support_agent.policy import check_refund_eligibility
                    txn_id = fn_args.get("txn_id")
                    txn_amt = result.get("amount", 0.0)
                    elig = check_refund_eligibility(
                        session=self.session,
                        txn_id=txn_id,
                        requested_amount=txn_amt,
                        store=self.store,
                    )
                    if not elig["eligible"]:
                        self.session.messages.append({
                            "role": "system",
                            "content": (
                                f"[TRUSTED POLICY DIRECTIVE: Transaction '{txn_id}' is NOT eligible for automatic refund ({elig['reason']}: {elig['message']}). "
                                f"You MUST NOT call issue_refund! You MUST call escalate_to_human(customer_id='{self.session.customer_id}', summary=...) immediately.]"
                            ),
                        })
                    else:
                        self.session.messages.append({
                            "role": "system",
                            "content": (
                                f"[TRUSTED POLICY DIRECTIVE: Transaction '{txn_id}' is verified and eligible for automatic refund of ${txn_amt:.2f}. "
                                f"You may call issue_refund(txn_id='{txn_id}', amount={txn_amt}).]"
                            ),
                        })

        return "I am processing your request. Please let me know how else I can assist you."

    def _offline_handle_turn(self) -> str:
        """Deterministic rule-based handler for offline execution testing without API keys."""
        self.last_turn_usage = {
            "input_tokens": 420,
            "output_tokens": 48,
            "tool_called": "get_customer" if not self.session.verified else "none",
        }
        if not self.session.verified:
            return "For your security, please provide both your email address on file and the last 4 digits of your card so I can verify your account."
        return f"Hello {self.session.customer_name or 'there'}, your account is verified. How can I assist you with Paylane today?"
