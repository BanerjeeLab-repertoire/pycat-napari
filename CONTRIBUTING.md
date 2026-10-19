# Contributing to PyCAT-Napari

We appreciate your interest in contributing to PyCAT-Napari! This document provides guidelines and information about contributing to this project. The best tools are ones built by the community so it is my hope that PyCAT will be a valuable resource and that it will help to advance our understanding of the complex biological processes that underlie the formation and function of biomolecular condensates. I hope it is useful to the community and that others will contribute to its development.

## Table of Contents
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Contributions](#making-contributions)
- [Branch Naming Conventions](#branch-naming-conventions)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Pull Request Guidelines](#pull-request-guidelines)
- [Code Review Process](#code-review-process)
- [Style Guidelines](#style-guidelines)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)

## Code of Conduct
By participating in this project, you agree to maintain a respectful and constructive environment for all contributors. Please report any unacceptable behavior to the project maintainers.

## Getting Started
1. Fork the repository
2. Clone your fork locally
3. Create a virtual environment and install dependencies

```bash
git clone https://github.com/BanerjeeLab-repertoire/pycat-napari.git
cd pycat-napari
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## Development Setup
The project uses a src-layout and requires several development dependencies:

```bash
# Install development dependencies
pip install -e ".[dev]"

# Install test dependencies
pip install -e ".[test]"
```

## Making Contributions
1. Create a new branch for your feature or fix:
   ```bash
   git checkout -b feature-name
   ```
2. Make your changes
3. Write or update tests as needed
4. Run the test suite
5. Push your changes and create a pull request

## Branch Naming Conventions
- `feature/your-feature-name` for new features
- `bugfix/your-bugfix-name` for bug fixes
- `hotfix/your-hotfix-name` for hotfixes

## Commit Message Guidelines
- Use the present tense ("Add feature" not "Added feature")
- Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters or less
- Reference issues and pull requests liberally

## Pull Request Guidelines
- Provide a clear description of what the pull request does
- Reference any related issues or pull requests
- Include screenshots or gifs if the changes affect the UI
- Ensure all tests pass before submitting

## Code Review Process
- All submissions, including submissions by project members, require review
- Be respectful and constructive in your feedback
- Provide context for suggested changes

## Style Guidelines
- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Document new functions and classes using docstrings
- Keep functions focused and concise
- Add comments for complex logic

## Testing
- Add tests for new features
- Ensure all tests pass before submitting:
  ```bash
  pytest tests/
  ```
- **Then run what GitHub runs, before you push:**
  ```bash
  python tools/run_ci_locally.py            # all CI jobs; or name some: core wheel minimal
  ```
  `pytest tests/` in your dev environment is **not** the CI run, and passing it does not mean CI will
  pass. CI was red for many commits while every local run was green, because:

  | Local dev run | GitHub (`.github/workflows/core.yml`) |
  |---|---|
  | `pytest tests/` (everything) | `pytest -m "core or base"`; `minimal` runs `-m core` |
  | napari, Qt, bioio, h5py, cellpose, torch installed | only the packages the workflow installs: no napari, Qt, bioio or h5py |
  | the editable source tree | the editable source **and**, separately, the built wheel |
  | the working tree, untracked files included | a clean checkout of the commit |
  | Windows, cp1252 console | Linux, UTF-8 |

  The script reads `core.yml` and runs each job's own steps in a fresh-per-job venv and a copy of the
  repo built from the git index, so it cannot drift from the workflow. Only the OS still differs.
- A `core` or `base` test must not need the GUI stack or an optional package. If a test reaches Qt through
  a science module, fix the import at the source: move data or helpers to a Qt-free module, or import
  `pycat.file_io.stack_access` rather than `pycat.file_io.file_io`. If the test itself needs napari, h5py,
  bioio and the like, use `pytest.importorskip(...)`. A function-level `@pytest.mark.integration` does
  **not** remove a test from a file whose `pytestmark` is `base`.
- Maintain or improve test coverage

## Submitting Changes
1. Update the CHANGELOG.md with your changes
2. Ensure your code is properly formatted
3. Push your changes to your fork
4. Create a Pull Request with a clear description:
   - What changes were made
   - Why the changes were made
   - Any special notes for reviewers

## Questions or Problems?
- Open an issue for bugs or feature requests at: https://github.com/BanerjeeLab-repertoire/pycat-napari/issues
- Contact the maintainers for other questions

Thank you for contributing to PyCAT-Napari!