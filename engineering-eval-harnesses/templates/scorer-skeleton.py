"""Domain scorer skeleton — deterministic comparison against a reference.

THIS IS A TEMPLATE. Customize the TODO sections for your domain.

Architecture:
  - No AI in the scoring path. This is pure Python.
  - Load generated output + reference → compare across N dimensions → produce score_report.json.
  - Each dimension returns a 0-100 score. The composite is a weighted average.
  - Hard-fail thresholds catch non-negotiable quality floors.

Why deterministic: The same input must always produce the same score.
This makes regression detection reliable. An LLM scorer introduces
variance that makes "pipeline got worse" indistinguishable from
"scorer scored differently."

Usage:
  python scorer_{domain}.py <generated_path> <reference_path> [--output-dir <dir>]
"""
import argparse
import json
import sys
from pathlib import Path

# TODO: Import domain-specific libraries
# For DOCX: from docx import Document
# For XLSX: import openpyxl
# For PDF:  import fitz  # pymupdf
# For JSON: just use json (already imported)


# ============================================================
# Configuration — customize for your domain
# ============================================================

# Scoring dimensions: name, weight, hard_fail_threshold (or None)
DIMENSIONS = [
    # TODO: Define your scoring dimensions
    # ("data_accuracy", 0.40, None),
    # ("content_completeness", 0.30, None),
    # ("structural_fidelity", 0.20, None),
    # ("formatting_quality", 0.10, None),
]

# Tolerances for numeric comparison
# CURRENCY_TOLERANCE = 0.001   # 0.1% for dollar amounts
# PERCENTAGE_TOLERANCE = 0.001  # ±0.1% for calculated percentages


# ============================================================
# Loaders — read generated and reference outputs
# ============================================================

def load_generated(path: str) -> dict:
    """Load and parse the generated output.

    TODO: Implement for your output format.

    Returns a structured dict that the dimension scorers can work with.
    Keep this format-agnostic — parse once, score many times.
    """
    # Example for DOCX:
    # doc = Document(path)
    # return {
    #     "paragraphs": [p.text for p in doc.paragraphs],
    #     "tables": [[cell.text for cell in row.cells] for table in doc.tables for row in table.rows],
    #     "headings": [(p.text, p.style.name) for p in doc.paragraphs if "Heading" in (p.style.name or "")],
    # }
    raise NotImplementedError("Implement load_generated for your output format")


def load_reference(path: str) -> dict:
    """Load and parse the reference output.

    TODO: Implement for your reference format. Usually mirrors load_generated.
    """
    raise NotImplementedError("Implement load_reference for your reference format")


# ============================================================
# Dimension scorers — one function per dimension
# ============================================================

# TODO: Implement one function per dimension. Each returns 0.0-100.0.
# Keep these focused: one dimension, one concern.
#
# def score_data_accuracy(generated: dict, reference: dict) -> float:
#     """Cell-by-cell comparison of data tables.
#
#     Compare numeric values with tolerance. Compare text values exactly.
#     Return percentage of cells that match.
#     """
#     matched = 0
#     total = 0
#     for gen_table, ref_table in zip(generated["tables"], reference["tables"]):
#         for gen_cell, ref_cell in zip(gen_table, ref_table):
#             total += 1
#             if cells_match(gen_cell, ref_cell):
#                 matched += 1
#     return (matched / total * 100) if total else 0.0
#
#
# def score_content_completeness(generated: dict, reference: dict) -> float:
#     """Check that expected content sections are present and substantive."""
#     ...
#
#
# def score_structural_fidelity(generated: dict, reference: dict) -> float:
#     """Compare heading hierarchy, table count/placement, section ordering."""
#     ...


# ============================================================
# Composite calculation
# ============================================================

def compute_composite(dimension_scores: dict) -> dict:
    """Compute weighted composite from dimension scores.

    Returns dict with composite, per-dimension scores, and hard_fail flag.
    """
    if not DIMENSIONS:
        raise ValueError("DIMENSIONS is empty — define at least one scoring dimension")
    total_weight = sum(w for _, w, _ in DIMENSIONS)
    if abs(total_weight - 1.0) > 0.01:
        raise ValueError(f"DIMENSIONS weights sum to {total_weight:.3f}, expected 1.0")

    composite = 0.0
    for name, weight, hard_fail_threshold in DIMENSIONS:
        score = dimension_scores.get(name, 0.0)
        composite += score * weight

    result = {
        "composite": round(composite, 2),
        "dimensions": dimension_scores,
        "hard_fail": False,
        "hard_fail_reason": None,
    }

    # Check hard-fail thresholds
    for name, weight, hard_fail_threshold in DIMENSIONS:
        if hard_fail_threshold is not None:
            score = dimension_scores.get(name, 0.0)
            if score < hard_fail_threshold:
                result["hard_fail"] = True
                result["hard_fail_reason"] = f"{name}_below_{hard_fail_threshold}"
                break

    return result


# ============================================================
# Diff summary — human-readable gap analysis
# ============================================================

def generate_diff_summary(dimension_scores: dict, details: dict) -> str:
    """Produce a markdown summary of gaps, sorted by impact.

    The Analyzer reads this to decide what to fix.
    Include enough detail for root-cause reasoning but not so much
    that it overwhelms the Analyzer's context.
    """
    lines = ["# Diff Summary\n"]

    # Sort dimensions by score (worst first)
    sorted_dims = sorted(dimension_scores.items(), key=lambda x: x[1])

    for dim_name, score in sorted_dims:
        lines.append(f"## {dim_name}: {score:.1f}%\n")
        if dim_name in details:
            for detail in details[dim_name]:
                lines.append(f"- {detail}")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Score generated output against reference")
    parser.add_argument("generated", help="Path to generated output file")
    parser.add_argument("reference", help="Path to reference output file")
    parser.add_argument("--output-dir", default=".", help="Directory for score_report.json and diff_summary.md")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load both files
    generated = load_generated(args.generated)
    reference = load_reference(args.reference)

    # Score each dimension
    # TODO: Call your dimension scorers here
    dimension_scores = {}
    details = {}
    # Example:
    # dimension_scores["data_accuracy"] = score_data_accuracy(generated, reference)
    # dimension_scores["content_completeness"] = score_content_completeness(generated, reference)

    # Compute composite
    result = compute_composite(dimension_scores)

    # Write score report
    score_report = {
        **result,
        "generated_path": str(args.generated),
        "reference_path": str(args.reference),
    }
    with open(output_dir / "score_report.json", "w") as f:
        json.dump(score_report, f, indent=2)

    # Write diff summary
    diff_summary = generate_diff_summary(dimension_scores, details)
    with open(output_dir / "diff_summary.md", "w") as f:
        f.write(diff_summary)

    # Print summary to stdout
    print(f"Composite: {result['composite']:.1f}%")
    for name, score in dimension_scores.items():
        print(f"  {name}: {score:.1f}%")
    if result["hard_fail"]:
        print(f"HARD FAIL: {result['hard_fail_reason']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
