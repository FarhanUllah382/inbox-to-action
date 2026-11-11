"""
Phase 1 Extraction Experiment Runner

Main entry point for Phase 1. Loads test emails, runs extraction, evaluates results.
"""

import json
import sys
from pathlib import Path

# Add project root to path to allow imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# The per-email progress markers below are U+2713/U+2717 (✓/✗); Windows'
# default console codepage (cp1252) can't encode them, which crashes this
# script the moment stdout is redirected (e.g. piped to a log file) rather
# than printed to an actual UTF-8-capable terminal. Force UTF-8 regardless
# of the console's codepage — output-only, doesn't change what's computed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from phase1_extraction.config import TEST_EMAILS_PATH, RESULTS_OUTPUT_PATH
from phase1_extraction.extractor import extract_email, ExtractionError
from phase1_extraction.evaluate import evaluate_extraction, print_metrics


def load_test_emails(path: str) -> list:
    """
    Load test emails from JSONL file.
    
    Args:
        path: path to test_emails.jsonl
    
    Returns:
        list of email dicts with 'id', 'from', 'subject', 'body', 'label'
    """
    emails = []
    with open(path, 'r') as f:
        for line in f:
            if line.strip():
                emails.append(json.loads(line))
    return emails


def run_experiment():
    """
    Run the full Phase 1 extraction experiment.
    """
    print("\n" + "=" * 70)
    print("PHASE 1: EXTRACTION EXPERIMENT")
    print("=" * 70)
    
    # Load test emails
    print(f"\nLoading test emails from {TEST_EMAILS_PATH}...")
    try:
        test_emails = load_test_emails(TEST_EMAILS_PATH)
    except FileNotFoundError:
        print(f"ERROR: Test emails file not found at {TEST_EMAILS_PATH}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in test emails file: {e}")
        sys.exit(1)
    
    print(f"Loaded {len(test_emails)} test emails")
    
    # Run extraction on each email
    print("\nRunning extraction on each email...")
    predictions = []
    errors = []
    
    for i, email in enumerate(test_emails):
        email_id = email.get("id", i + 1)
        print(f"  [{i+1:2d}/{len(test_emails)}] Email #{email_id}...", end=" ", flush=True)
        
        try:
            email_data = {
                "from": email.get("from", ""),
                "subject": email.get("subject", ""),
                "body": email.get("body", "")
            }
            result = extract_email(email_data, thread_context="")
            predictions.append(result)
            print("✓")
        except ExtractionError as e:
            print("✗")
            errors.append({
                "email_id": email_id,
                "error": str(e)
            })
            # Use a default response for this email
            predictions.append({
                "actionable": False,
                "task": None,
                "deadline": None,
                "assignee": None,
                "reason": f"Extraction error: {str(e)[:50]}",
                "confidence": 0.0
            })
    
    print(f"\nExtraction complete. Errors: {len(errors)}")
    if errors:
        print("\nERRORS:")
        for err in errors:
            print(f"  Email #{err['email_id']}: {err['error'][:100]}")
    
    # Extract labels from test emails
    labels = [email.get("label", {}) for email in test_emails]
    
    # Evaluate
    print("\nEvaluating predictions against labels...")
    metrics, detailed_results = evaluate_extraction(predictions, labels)
    
    # Print metrics
    print_metrics(metrics)
    
    # Save detailed results
    output_data = {
        "summary": {
            "total_emails": metrics.total_emails,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1_score": metrics.f1_score,
            "false_positives": metrics.false_positives,
            "false_negatives": metrics.false_negatives,
            "avg_confidence": metrics.avg_confidence,
            "high_confidence_accuracy": metrics.high_confidence_accuracy,
            "confidence_distribution": metrics.confidence_distribution,
        },
        "detailed_results": detailed_results,
    }
    
    output_path = Path(RESULTS_OUTPUT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)
    
    print(f"\nDetailed results saved to {RESULTS_OUTPUT_PATH}")
    
    # Print summary of failures
    print("\n" + "=" * 70)
    print("EXTRACTION FAILURES (For Debugging)")
    print("=" * 70)
    
    failures = [r for r in detailed_results if not r["correct"]]
    if failures:
        print(f"\nFound {len(failures)} incorrect predictions:\n")
        for result in failures:
            print(f"Email #{result['email_id']}:")
            print(f"  Predicted actionable: {result['pred_actionable']}")
            print(f"  True actionable:      {result['true_actionable']}")
            print(f"  Confidence:           {result['confidence']:.2f}")
            if result['true_actionable']:
                print(f"  Predicted task:       {result['pred_task']}")
                print(f"  True task:            {result['true_task']}")
                print(f"  Predicted deadline:   {result['pred_deadline']}")
                print(f"  True deadline:        {result['true_deadline']}")
            print()
    else:
        print("\n✓ All predictions correct!")
    
    print("=" * 70)
    
    return metrics


if __name__ == "__main__":
    try:
        metrics = run_experiment()
        sys.exit(0)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
