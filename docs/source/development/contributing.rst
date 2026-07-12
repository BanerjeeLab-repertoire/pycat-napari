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

.. _codebase-pitfalls:

Codebase Pitfalls
-----------------

These are traps that have caused real, silent bugs in PyCAT more than once. They are
not obvious from reading the code, and none of them raise an exception — they produce
a plausible wrong answer. Read this section before writing analysis code.

Never call ``np.asarray()`` on a stack layer's data
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**This is the single most dangerous pattern in the codebase.**

PyCAT loads large time-series and z-stacks through lazy wrappers
(``_TiffPageStack``, the zarr-backed readers). Their ``__array__`` method is
**deliberately truncated to return only frame 0**, so that napari's incidental array
requests (thumbnails, for example) never materialise a multi-gigabyte movie.

The consequence:

.. code-block:: python

   # WRONG — silently returns a single 2D frame from a (T, H, W) stack.
   stack = np.asarray(layer.data)
   # stack.ndim == 2, and every frame after the first has vanished.

Nothing errors. The analysis runs. It analyses frame 0 and reports the result as if
it were the whole movie. This has shipped as a bug at least three times (the
temperature workflow, the VPT pipeline, and colocalization).

.. code-block:: python

   # RIGHT — for code that needs every frame:
   from pycat.file_io.file_io import materialize_stack
   stack = materialize_stack(layer.data)        # real (T, H, W) array

   # RIGHT — for single-pass/sequential access (preferred: never materialises):
   from pycat.file_io.file_io import iter_frames
   for t, frame in iter_frames(layer.data):
       ...

   # RIGHT — for a 2D analysis that must take ONE plane from a possibly-lazy layer:
   from pycat.file_io.file_io import layer_is_stack, extract_2d_plane
   if layer_is_stack(layer.data):
       frame_index = viewer.dims.current_step[0]   # the frame the USER is viewing
   plane = extract_2d_plane(layer.data, frame_index=frame_index)

The last case matters: a 2D analysis handed a stack should use **the frame the user
is looking at**, not silently frame 0.

Choose lazy versus materialised by *access pattern*
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The default lean is lazy/streaming, but the decision rule is the access pattern, not
a preference:

* **Single-pass / sequential** → stream with ``iter_frames``. Never materialise.
* **Repeated / random access** → materialise deliberately with ``materialize_stack``,
  once, and reuse it.

Explicit streaming is preferred over fake-array ``__array__`` wrappers, precisely
because the wrapper is what makes the trap above possible.

Do not measure intensities on an upscaled image
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Upscaling is interpolation. It adds no information, and measuring on it
**pseudoreplicates** (16× the "samples", zero new photons — the reported SEM comes
out ~1.5× too confident) and **biases small objects** in a size-dependent way.

If a mask was produced by segmenting an upscaled image, measure it against the
**original** image using ``pycat.toolbox.partial_volume_tools``, which maps the
high-resolution mask to the native grid as fractional-coverage weights. See
:doc:`../usage/measurement_guidance`.

Pixel size and frame interval are load-time concerns
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Metadata capture belongs at the **top-level load event**, not in each consumer. Every
physical unit downstream (µm, µm², Pa·s) depends on the pixel size and frame interval
being right. A workflow that re-derives them locally will drift out of sync with the
rest of the application.

Read them from ``data_repository`` (``microns_per_pixel_sq``,
``file_metadata['common']['frame_interval_s']``). If a workflow needs a pixel size and
none was found in the metadata, gate it behind ``add_pixel_size_gate`` rather than
silently defaulting to 1.0 — a default of 1.0 produces a plausible number in the
wrong units.

Never silently mix populations
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Where an analysis classifies objects into populations (for example VPT's
singlet / aggregate / out-of-plane bead classes), those populations must remain
**separately addressable** and must never be silently pooled into an ensemble
statistic. Pooling a mixed population produces an average of two different physical
things, which is a number with no referent.

Build incrementally and compile after each step
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Multi-file refactors that touch many modules at once have broken the build repeatedly.
Make the change in stages and run a syntax check after each one:

.. code-block:: bash

   python -c "import ast; ast.parse(open('src/pycat/…/file.py').read())"

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