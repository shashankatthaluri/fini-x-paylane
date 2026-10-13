"""CLI runner to execute the full evaluation with the live OpenAI LLM.
   Results are saved to evaluation/live_run/ directory.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key or api_key == "your-openai-api-key-here":
    print("ERROR: No valid OPENAI_API_KEY found in .env")
    sys.exit(1)

from openai import OpenAI

client = OpenAI(api_key=api_key)

# Import evaluation harness
from evaluation.run_evaluation import run_full_evaluation

scenarios_dir = Path("evaluation/scenarios")
output_dir = Path("evaluation/live_run")

print("=" * 60)
print("  Paylane AI Support Agent — LIVE LLM Evaluation")
print("  Model: gpt-4o-mini | Mode: live_llm")
print("  Scenarios:", len(list(scenarios_dir.glob("*.json"))))
print("=" * 60)

summary = run_full_evaluation(
    scenarios_dir=scenarios_dir,
    output_dir=output_dir,
    llm_client=client,
    execution_mode="live_llm",
)

print()
print("=" * 60)
print(f"  RESULT: {summary['total_passed']} / {summary['total_scenarios']} scenarios PASSED")
print(f"  Pass Rate: {summary['pass_rate_percentage']}%")
print(f"  Critical Safety Violations: {summary['total_critical_violations']}")
print("=" * 60)

# Print per-scenario breakdown
print()
print(f"{'Scenario':<40} {'Status':<8} {'Violations'}")
print("-" * 70)
for sc in summary["scenarios"]:
    status = "PASSED" if sc["passed"] else "FAILED"
    crit = str(len(sc["critical_violations"])) if sc["critical_violations"] else "None"
    print(f"  {sc['scenario_id']:<38} {status:<8} {crit}")

print()
print(f"  Report written to: evaluation/live_run/report.md")
print(f"  Results JSON:      evaluation/live_run/results.json")
print(f"  Transcripts:       evaluation/live_run/transcripts/")
