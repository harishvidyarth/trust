# Writing-style evaluation

Dataset: 45 synthetic resumes, seed 20261008.

## Results

| Mode | Status | Precision | Recall | Human false positives |
|---|---|---:|---:|---:|
| heuristic | measured | 1.0000 | 0.5000 | 0 (0.00%) |
| hybrid | measured | 0.9667 | 0.9667 | 1 (6.67%) |

Hybrid valid judgments: 45; heuristic fallbacks: 0.

The heuristic maps scores below 35 to human, 35–64 to AI-polished, and 65–100 to AI-generated. The hybrid maps LLM labels to 0, 60, and 100 before applying 0.4 × heuristic + 0.6 × LLM.

## LIMITS

- The resumes are synthetic and template-written.
- The dataset is small.
- Labels are assigned by construction, not independent annotation.
- The LLM judge may find patterns resembling its own writing easier to classify.
- These measurements do not support a production accuracy claim.
