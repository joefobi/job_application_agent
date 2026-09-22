# Agent Instructions

## Python style

- Use Google-style docstrings for Python code.
- Include `Args:` and `Returns:` sections where they apply.
- Do not use `Raises:` sections.

## Pull request review workflow

- After every PR push, check the PR checks and reviews until Greptile has
  completed or posted feedback.
- If Greptile leaves actionable comments, address them, push the fixes, and
  repeat the check loop.
- Consider the work done only when Greptile has no unresolved actionable
  comments and required CI checks are passing.
- If Greptile does not report within 10 minutes, mention that in the final
  response instead of waiting indefinitely.
