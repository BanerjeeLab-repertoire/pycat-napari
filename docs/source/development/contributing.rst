Contributing to PyCAT-Napari
============================

Thank you for your interest in contributing to PyCAT-Napari! We believe that the best tools are built by the community. Our goal is to make PyCAT a valuable resource that advances our understanding of biomolecular condensates and their complex biological processes. We welcome contributions of all kinds, from bug fixes to new features.

Getting Started
---------------

Basic Setup
^^^^^^^^^^^

1. Fork the repository
2. Clone your fork locally
3. Set up your development environment:

.. code-block:: bash

   git clone https://github.com/BanerjeeLab-repertoire/pycat-napari.git
   cd pycat-napari
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -e ".[dev]"

Development Environment
^^^^^^^^^^^^^^^^^^^^^^^

The project uses a src-layout and requires several development dependencies:

.. code-block:: bash

   # Install development dependencies
   pip install -e ".[dev]"

   # Install test dependencies
   pip install -e ".[test]"

Making Contributions
--------------------

Workflow Steps
^^^^^^^^^^^^^^

1. Create a new branch for your feature or fix:

   .. code-block:: bash

      git checkout -b feature/your-feature-name

2. Make your changes
3. Write or update tests as needed
4. Run the test suite
5. Push your changes and create a pull request

Branch Naming Conventions
^^^^^^^^^^^^^^^^^^^^^^^^^

Use these prefixes for your branches:

- ``feature/your-feature-name`` for new features
- ``bugfix/your-bugfix-name`` for bug fixes
- ``hotfix/your-hotfix-name`` for hotfixes

Commit Messages
^^^^^^^^^^^^^^^

Follow these guidelines for commit messages:

- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters
- Reference issues and pull requests when relevant

Example:

.. code-block:: text

   Add automatic contrast adjustment for microscopy images

   - Implement CLAHE algorithm for better contrast
   - Add user controls for adjustment parameters
   - Update documentation with new feature
   
   Fixes #123

Pull Requests
-------------

Guidelines
^^^^^^^^^^

When submitting a pull request:

1. Provide a clear description of your changes
2. Reference any related issues
3. Include screenshots if UI changes are involved
4. Ensure all tests pass
5. Update documentation as needed

Your PR description should answer:

- What changes were made?
- Why were these changes necessary?
- Are there any special notes for reviewers?

Code Review Process
^^^^^^^^^^^^^^^^^^^

- All submissions require review, including those from project members
- Reviews should be respectful and constructive
- Provide context for suggested changes
- Be responsive to reviewer comments

Code Style and Standards
------------------------

Style Guidelines
^^^^^^^^^^^^^^^^

- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Document new functions and classes using docstrings
- Keep functions focused and concise
- Add comments for complex logic

Testing Requirements
^^^^^^^^^^^^^^^^^^^^

- Add tests for new features
- Ensure all tests pass:

  .. code-block:: bash

     pytest tests/

- Maintain or improve test coverage
- Include both unit tests and integration tests where appropriate

Documentation
-------------

Requirements
^^^^^^^^^^^^

- Update docstrings for new functions and classes
- Add or update tutorials for new features
- Keep the README.md current
- Update CHANGELOG.md with your changes

Code of Conduct
---------------

By participating in this project, you agree to maintain a respectful and constructive environment for all contributors. Please report any unacceptable behavior to the project maintainers.

Getting Help
------------

If you need assistance:

- Open an issue for bugs or feature requests on our `GitHub Issues page <https://github.com/BanerjeeLab-repertoire/pycat-napari/issues>`_
- Contact the maintainers for other questions
- Check our :doc:`support` page for additional resources

.. note::
   Before starting work on a major feature, please discuss it with the maintainers through a GitHub issue to ensure it aligns with the project's goals.

Development Tips
----------------

- Use our pre-commit hooks to catch common issues before committing
- Run tests frequently during development
- Keep changes focused and atomic
- Document as you go rather than after the fact
- Ask questions early if you're unsure about something

Thank you for contributing to PyCAT-Napari! Your efforts help make this tool better for the entire research community.