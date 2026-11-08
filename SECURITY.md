# Security policy

## Reporting a vulnerability

For a vulnerability in this wrapper, contact the maintainer privately using
the contact form at [mojsadafi.ir](https://mojsadafi.ir/). If private
vulnerability reporting is enabled for the future GitHub repository, that is
also an appropriate channel. Include the affected version or commit, a minimal
reproduction with non-sensitive files, and the likely impact. Allow time for the
maintainer to investigate before publishing exploit details. Do not post API
keys, signed URLs, private PDFs, or confidential logs in a public issue.

## API keys and saved jobs

On Windows, the GUI stores API tokens protected with Windows Data Protection
under `%LOCALAPPDATA%\MinerU_PDF_Converter\tokens.dpapi`. Protection is tied to
the Windows user context and normally the same machine. An attacker running as
that user may be able to access the credentials. The app can also read the
`MINERU_API_TOKEN` environment variable. Avoid putting tokens in commands,
scripts, Git history, screenshots, issue reports, or output archives.

Clear the API field and click **Save tokens** to remove the stored value.
Revoke a token with the provider if you believe it was exposed. If a token has
been committed, removing it from a later commit does not revoke it.

`cloud_job.json` contains a temporary signed upload URL and batch identifier.
Treat it as sensitive while the URL is valid. Logs and `conversion.json` can
contain filenames and local paths. The `.gitignore` helps exclude default outputs
and job files, but files can still be shared manually or tracked before ignore
rules are added. Review what you publish.

## Documents and network use

Cloud and Auto modes can send the selected PDFs to MinerU's service. Review the
service terms and your right to upload each document. Local mode uses the local
model setup. The connectivity indicator periodically probes Microsoft Connect
Test and, when necessary, the MinerU website without sending API keys or PDFs.
Model installation and downloads require network access. An interrupted cloud
job can continue remotely after Auto mode begins a separate local conversion.

The app performs basic path checks and structured-output checks on result ZIPs.
The ZIPs, PDFs, extracted Markdown, and image assets should still be treated as
untrusted input. Review external dependencies and model licenses when deploying.

## Scope and support

This is an independent project. Report vulnerabilities in MinerU itself through
[MinerU's security policy](https://github.com/opendatalab/MinerU/security/policy).
No response-time or support guarantee has been established for this project.
