# Contributing

Contributions that improve PDF extraction workflows, accessibility, platform
support, tests, documentation and reproducibility are welcome.

1. Search existing issues and discuss substantial changes before implementing
   them where practical. Use a small, focused change and explain its motivation.
2. Set up Python 3.11–3.14 and install `requirements.txt`. On Windows,
   `SETUP_WINDOWS.cmd` creates the app's virtual environment. Local MinerU and
   its model weights are needed only for real local-inference tests.
3. Run the simulated test suite and GUI callback smoke test.

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
   .\.venv\Scripts\python.exe tests\smoke_gui.py
   ```

4. Describe the platform, commands run, results, and what remains untested.
   For OCR or formulas, provide a small redistributable PDF or a generated test
   fixture and specify the pages checked. Do not upload private articles or keys.
5. Submit a pull request to this project's repository once it exists. Changes
   to MinerU itself belong upstream and may require their contributor agreement.

This project uses the [MIT License](LICENSE) for its original code. MinerU and
other third-party components have their own terms described in [NOTICE.md](NOTICE.md).
By contributing, submit only material you have the right to share under the
project's license. Please be respectful and constructive.

Report sensitive vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
