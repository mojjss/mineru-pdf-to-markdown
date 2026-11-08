# MinerU PDF to Markdown Converter

A research-oriented desktop interface for converting PDFs into machine-readable
Markdown and structured output with [MinerU](https://github.com/opendatalab/MinerU).
It supports one paper or a batch, user-selected output folders, the MinerU
Precision API, and a separately installed local parser. The interface is designed
for Windows and uses Python and Tkinter.

This is an independent community wrapper by Mojtaba Sadafi. MinerU provides the
underlying document-parsing technology. The wrapper is not an official MinerU
product. See [Third-party notices](NOTICE.md) for attribution and licensing.

## At a glance

| Task | How the app helps |
| --- | --- |
| Convert research papers | Sends PDFs to the MinerU cloud API or a local MinerU installation and saves Markdown, JSON and image assets. |
| Process scanned pages and formulas | Uses MinerU's document-parsing capabilities, including mathematical and scanned content. The current GUI has no control to force OCR; see [Scans and equations](#scans-and-equations). |
| Work through a batch | Adds individual PDFs or recursively finds PDFs in folders. Each PDF or group can have a different output folder. Files are processed sequentially. |
| Continue interrupted work | Keeps per-document job records and hashes. Completed matching outputs can be skipped on resume. |
| Work with limited connectivity | Auto mode can switch to installed local models after connection failures or specified cloud limits. |
| Review progress | Shows cloud stages, local output, elapsed time, internet-check status and each PDF's status. |

**Review the output against the original PDF.** Mathematical signs, subscripts,
superscripts, Greek letters, table cells, reading order and page references can
be misrecognized. A successful conversion is a structural check, not a
scientific validation. Accuracy varies with the document, source quality,
settings, backend and model version.

## 1. Requirements

- **Windows 10 or 11** and Python **3.11–3.14**, including Tkinter. Python 3.12
  is a practical starting point. The Windows launchers are the supported setup
  path; other platforms require manual setup and have not been validated here.
- An internet connection and a MinerU API token for cloud conversion.
- For local conversion, internet for the initial installation and model download,
  sufficient storage and compatible hardware. Runtime and memory use vary by
  document and engine. CPU conversion may take considerable time.
- PDFs you are permitted to use. Cloud modes upload selected PDFs to MinerU.
  Check the service terms and your document permissions before upload.

The app itself requires `requests` and `pypdf`. Local setup separately installs
`mineru==4.1.7` and its dependencies. The package and models have their own
licenses and may have additional platform requirements. [MinerU quick start](https://opendatalab.github.io/MinerU/quick_start/) · [MinerU license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)

## 2. Install and open on Windows

1. Extract the project ZIP to a folder such as `D:\Tools\MinerU_PDF_Converter`.
   Open the extracted folder that contains `SETUP_WINDOWS.cmd`.
2. Install Python if necessary from the [Python downloads page](https://www.python.org/downloads/windows/).
   Include the Python launcher and Tcl/Tk. Setup also checks an active Conda
   environment available as `python`.
3. Double-click **SETUP_WINDOWS.cmd**. It creates a `.venv` environment,
   installs the app dependencies and opens the interface.
4. For later launches, double-click **START.cmd**.

If setup cannot find a compatible interpreter, install Python 3.12 with the
launcher and Tcl/Tk. If a terminal reports a setup error, retain the final error
lines when asking for help.

## 3. Convert your first paper

1. Sign in to [MinerU API management](https://mineru.net/apiManage/token) and
   create a token.
2. In the app, select **Cloud only**. Paste the token into **API tokens**. Use
   **Show tokens** to inspect the field and **Save tokens** to save it for the
   current Windows user. Starting a conversion also attempts to save the field.
3. Set **Default output** to a convenient folder. Click **Add PDFs** and select
   one article.
4. Click **Start / Resume**. Follow the row status and **Live activity**. Wait
   for **Done (cloud)**, then click **Open output**.
5. Open the generated Markdown in an editor or Markdown viewer. Keep its
   companion `images` folder and structured data with it.
6. Compare a representative equation, table, two-column passage and the last
   page with the original PDF before relying on the extraction.

The app accepts multiple tokens separated by semicolons, for example
`TOKEN_A;TOKEN_B`. These are illustrative placeholders. Duplicate tokens are
ignored. The app's page-budget counter is a local estimate, not a live query of
the provider's remaining account quota. It tracks each token separately within
each selected output folder.

## 4. Use local conversion and automatic fallback

1. **Close the app** and double-click **INSTALL_LOCAL.cmd**. It installs MinerU
   4.1.7 and downloads and verifies the Standard model configuration. Leave the
   window open until it reports **Local models verified**.
2. Open **START.cmd**, select **Local only**, and convert one small PDF. This
   checks that local inference works on your machine. Model verification alone
   does not establish that a full conversion succeeds.
3. To use the cloud when available and local conversion when needed, choose
   **Auto: cloud, then local if needed**. Provide a cloud token before starting.
   Auto tries the next entered token when a daily limit or the configured local
   page budget is reached. It uses local conversion when applicable tokens are
   exhausted, the file exceeds supported cloud limits, or the cloud connection
   fails or times out.
4. Watch **Internet** and **Live activity**. The connectivity label is an
   endpoint check and can say unavailable when the check endpoints are blocked.
   A network request may need time to time out before fallback starts.

Cloud-only mode reports a connection error without starting local conversion.
A cloud job already submitted may continue remotely after Auto begins a local
conversion. Its job record is kept. To retrieve the cloud result later, use
**Cloud only** with the same output folder and original token. Local and cloud
results are stored separately. In Auto mode a completed local result can cause a
matching file to be skipped on a later run.

The default 2,000-page-per-token UTC budget is the wrapper's adjustable local
policy. Set it to **0** to remove this local cap. MinerU's documented 2,000-page
allowance concerns highest-priority processing; later pages may receive lower
priority. The provider may impose separate limits and change its policies.
The counter cannot see usage in another output folder or another tool. [MinerU API documentation](https://mineru.net/doc/docs/index_en/) · [Rate limits](https://mineru.net/doc/docs/limit_en/)

## 5. Convert a batch to different destinations

1. Click **Add PDFs** to add files or **Add folder** to include PDFs in a folder
   and its subfolders. Repeat to build the list.
2. Added rows initially show **Default** under **Output folder**. Select one or
   more rows using Ctrl or Shift, then click **Set selected output** and choose
   their destination. Repeat for each group.
3. Use **Use source folder** to assign each selected PDF's own parent folder.
   The app creates a separate name-and-hash subfolder there. **Use default**
   returns selected rows to the current default output folder.
4. Click **Start / Resume**. **Open output** opens the first selected row's
   destination, or the default folder when no row is selected.

Each destination has its own local page-budget record. The per-token counter is
not combined across the different output folders. **Pause** preserves saved
cloud jobs. A local job is stopped and starts again from that PDF when resumed.
Keep the same destination and original token when resuming an existing cloud job.

## 6. Scans and equations

MinerU supports document OCR and mathematical content recognition. This wrapper
requests cloud `vlm` extraction with formulas and tables enabled. It currently
sends `language="en"` and **does not expose an OCR switch** in the GUI. The
cloud request does not explicitly set `is_ocr`; the provider's documented
default applies. The local GUI invokes the chosen MinerU tier without an
explicit `--ocr-mode` argument. Test your own scanned pages, languages and
formula layouts before processing a large corpus. [MinerU API options](https://mineru.net/doc/docs/index_en/) · [MinerU parsing tiers](https://opendatalab.github.io/MinerU/usage/tiers/)

For an image-only PDF, a practical first test is to attempt text selection in a
PDF viewer. If text cannot be selected, OCR is likely needed. Inspect one clear
scan and one difficult scan for missed pages, merged columns and characters such
as `0/O`, `1/l`, minus signs, subscripts and superscripts. Images and equations
may be represented differently across backends and documents. Handwritten and
low-quality source material needs particular care.

If local setup succeeded, MinerU's command line offers an explicit OCR mode.
Run the following in **PowerShell from the folder containing START.cmd**, and
replace the two example paths. This runs the CLI outside the GUI's queue and
resume records. It uses your active MinerU configuration: if a remote VLM is
configured, that service may be used. The environment settings below do not
by themselves override a configured remote service.

```powershell
$env:MINERU_MODEL_SOURCE = 'local'
$env:MINERU_MODEL_SMALL_BACKEND = 'onnx'
$env:MINERU_MODEL_VLM_ENGINE = 'llama-cpp'
& '.\.venv\Scripts\mineru-kit.exe' parse 'C:\path\scan.pdf' -o 'C:\path\scan-result.zip' --tier standard --format zip --pages all --ocr-mode ocr
```

The exact command and model availability depend on the installed MinerU version
and configuration. Check `.\.venv\Scripts\mineru-kit.exe parse --help` for your
installation. ZIP output retains Markdown and assets together. Compare the
result with the original before treating extracted equations as evidence.
[MinerU quick start](https://opendatalab.github.io/MinerU/quick_start/)

## 7. Understand and use the output

Each result lives under a paper-specific name-and-hash folder in the chosen
output directory. Exact files vary between cloud and local MinerU versions.

| Item | Purpose |
| --- | --- |
| `cloud-vlm/content/` or `local-standard/content/` | Extracted Markdown, JSON and image assets. Keep the whole folder together so image links remain valid. |
| `result.zip` | Original conversion archive. Preserve it if you need the raw returned package. |
| `conversion.json` | Source path, SHA-256 hashes, source page count, backend, requested model or tier, and output file hashes. |
| `cloud_job.json` | Saved cloud batch and temporary signed upload URL for resume. Treat it as sensitive. |
| `local.log` | Local parser output when a local job runs. |

Markdown image links normally point to companion files. Copying only the `.md`
file can break images. Some mathematical expressions use LaTeX syntax and need a
viewer that renders it. Structured JSON may include source-page and block
information, depending on the backend and output. Verify exact locations in the
original PDF.

For a literature review, keep the PDF, raw output package and a separate checked
note. Give a local AI tool access to the PDF and full `content` folder. Ask it
to cite page, equation or table numbers and mark unresolved details as uncertain.
Check those references against the PDF before using claims or comparison-table
values. If using an online AI service, review its document handling terms before
uploading the PDF or extracted material.

## 8. Controls and troubleshooting

- **Dark theme** switches between dark and light, with dark selected on launch.
  **Show tokens** reveals or hides the token field.
- **Internet** probes Microsoft Connect Test and, if needed, the MinerU website
  about every 10 seconds. The check sends no API token or PDF. It does not prove
  that all services are reachable or that a cloud conversion will succeed.
- **Local MinerU missing** means the executable or models are unavailable at the
  configured path. Run `INSTALL_LOCAL.cmd` while the app is closed and confirm
  model verification, then test **Local only**.
- **WinError 32 during installation** means a process is using an executable.
  Close the converter and active MinerU processes, then rerun the installer.
- **Cloud authentication error** calls for checking the token in API management.
  HTTP 429 receives delayed retries. An HTTP service error can leave a job to
  check later. Do not publish tokens, signed URLs or private PDF content in logs.
- **Upload link expired** can require a fresh cloud upload or local conversion.
  A paper's `cloud_job.json` contains its saved job. Preserve it when the remote
  task may still be running. Contact a maintainer before deleting a job record
  you may need to recover.
- **No pictures in Markdown** usually means the image assets were not moved with
  the Markdown, or the viewer does not resolve the relative paths. Keep `content`
  intact and use a viewer with Markdown image and mathematical rendering.

See [SECURITY.md](SECURITY.md) for token storage, signed URLs, network use and
private vulnerability reporting.

## 9. Develop and test

The source is `converter.py`. Windows launchers and `requirements.txt` are in
this folder. Simulated tests use synthetic PDFs and stubbed network responses.
They cover conversion routing, tokens, interruptions, output validation and GUI
callbacks. From the project folder after setup, run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe tests\smoke_gui.py
```

The smoke test simulates widgets. It does not validate actual Windows layout,
DPAPI, real cloud requests, offline inference or OCR quality. Record platform,
model version, sample type and actual results when performing those checks.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## 10. Attribution, citation and licenses

Original wrapper code and documentation are offered under the [MIT License](LICENSE).
The separately installed MinerU package uses its own custom license, based on
Apache 2.0 with additional conditions. Model weights, dependencies, the cloud
service and source PDFs may have separate terms. See [NOTICE.md](NOTICE.md) and
the [upstream license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md).

[CITATION.cff](CITATION.cff) supplies project citation metadata. When publishing
research, also cite the relevant MinerU publications and the original papers you
analyzed. Consult the upstream [MinerU citation information](https://github.com/opendatalab/MinerU#citation).

Maintainer: [Mojtaba Sadafi](https://mojsadafi.ir/) · [GitHub](https://github.com/mojjss)
#   m i n e r u - p d f - t o - m a r k d o w n  
 