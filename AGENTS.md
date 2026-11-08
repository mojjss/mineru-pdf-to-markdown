# Instructions for coding agents

## Project scope

This is an independent Python/Tkinter desktop wrapper for PDF conversion using
MinerU's Precision API and an optional local MinerU installation. It supports
research workflows but does not establish research conclusions. Keep changes
small, follow the user's request, and read the current README before changing
behavior. Report any conflict between documentation and the implementation.

## Existing behavior to preserve

- Cloud, local and Auto modes; per-PDF output destinations; multiple tokens;
  per-token and per-output local page-budget accounting; saved job ownership.
- Existing completed-result detection, hashes, source identity, safe ZIP path
  checks, structured-output checks, and the original output directory layout.
- On a network error in Auto mode, preserve a submitted cloud job and use a
  separate local result. Do not resubmit an uncertain job blindly.
- Cloud-only and local-only behavior, token visibility toggle and Windows
  DPAPI-protected token storage. Avoid logging raw tokens or signed URLs.
- Local quality settings, model configuration, dark/light theme, connectivity
  indicator, live activity, elapsed times, pause and resume.
- Default source folders and user-selected destinations. Avoid destructive
  replacement of research PDFs, previous output, or user's environments.

## Research integrity

Treat Markdown, OCR, formulas, tables, page mappings and AI analysis as
unverified until checked against the source. Do not claim perfect OCR, universal
page coverage or tested performance without evidence. When changing export
formats or provenance, retain enough metadata to trace results to the source PDF.

## Development and verification

- Python 3.11–3.14. Local integration currently targets MinerU 4.1.7.
- The application is `converter.py`; GUI events use a queue and worker threads.
  Keep Tk operations on the GUI thread and keep long operations off it.
- Install `requirements.txt`, then run `python -m unittest discover -s tests -p
  "test_*.py"` and `python tests/smoke_gui.py` for simulated checks. Tests use
  stubbed cloud responses and do not verify real model inference or Windows DPAPI.
- For platform-specific changes, report what was actually tested. Do not claim
  Windows, cloud API, OCR, GPU, or local model validation without running it.
- Keep tests synthetic and distributable. Never add personal PDFs, private
  papers, credentials, user paths or real signed URLs to the repository.
- Preserve upstream attribution and separate dependency licenses. Do not assert
  that the MIT license on this wrapper relicenses MinerU or its models.
- Do not create GitHub repositories, post upstream or publish releases unless
  the user asks. Never commit credentials or generated output.
