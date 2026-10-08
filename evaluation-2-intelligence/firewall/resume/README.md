# Resume pre-ATS firewall

This package reads the raw text layer before an ATS does, separates content visible to a human from hidden parser-visible content, deterministically parses the visible resume, and emits integrity reason codes. It never scores writing style and intentionally has no AI-generated-text detector; polished or AI-assisted prose is not suspicious by itself.

## Python API

```python
from firewall.models import JobRequirements
from firewall.resume.service import analyze_resume

analysis = analyze_resume(file_bytes, "resume.pdf", JobRequirements(
    must_have_skills=["Python", "FastAPI"],
    nice_to_have=["Docker"],
    min_years=2,
))
```

`ResumeAnalysis` contains the parsed `Candidate`, reason dictionaries, human-visible text, the full naive-ATS text view, hidden spans, and `parsed_ok`. Extraction supports PDF, DOCX, and UTF-8 text. Invalid, empty, unsupported, corrupt, and password-protected inputs produce `parsed_ok=False` rather than raising from `analyze_resume`.

The optional Ollama structuring path is enabled with `FIREWALL_LLM=1` and uses `FIREWALL_LLM_MODEL` (default `llama3.2`) at `http://localhost:11434` with a five-second timeout. It only structures visible resume facts. Integrity rules and scores remain deterministic, and any Ollama failure silently falls back to heuristics.

## Intended upload contract

The future `POST /v1/applications/upload` endpoint should accept `multipart/form-data`:

- `file`: PDF, DOCX, or TXT resume bytes.
- `job_json`: JSON matching `firewall.models.JobRequirements`.
- `device_signals`: JSON matching the application's submission/device signal contract.

The route should call `analyze_resume(await file.read(), file.filename, job)`, reject or quarantine inputs that fail ordinary upload validation, and pass the visible parsed candidate plus integrity reasons into the existing decision engine. The raw `ats_view_text` is evidence for review and for demonstrating vulnerable ATS behavior; it must not replace `visible_text` as the trusted candidate source.

## Samples and tests

Regenerate the small fixtures with:

```bash
.venv/bin/python -m firewall.resume.samples.make_samples
```

Run the isolated suite explicitly because the root pytest configuration currently points at `tests/`:

```bash
.venv/bin/pytest -q tests_resume
```
