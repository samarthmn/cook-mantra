# Repository Guidelines

## Project Structure & Module Organization

Use the root `tmp/` directory for generated logs, downloads, screenshots, and other short-lived artifacts. The directory is ignored by Git. Remove files you create there when the task ends.

## Build, Test, and Development Commands

## Coding Style & Naming Conventions

Follow the formatter, linter and prettier. Until language-specific standards exist, use prettier config files. Name files consistently within each language ecosystem—for example, `kebab-case` for all business logic and backend stuff, `PascalCase` front end components, and `*.test.ts` tests.

## Testing Guidelines

Add tests with each behavior change and regression fix. Keep tests deterministic and independent of external services unless explicitly marked as integration tests. Mirror source structure where practical, and name tests after observable behavior. Document any coverage threshold when a test framework is introduced.

## Commit & Pull Request Guidelines

The repository has no commit history from which to infer a convention. Use short, imperative commit subjects; Conventional Commit prefixes such as `feat:`, `fix:`, and `docs:` are encouraged.

Pull requests should explain the problem and solution, list verification commands, and link relevant issues. Include screenshots or recordings for visible UI changes. Keep each pull request focused and note follow-up work separately.

## Security & Configuration

Never commit credentials, API keys, or local environment files. Provide redacted examples such as `example.env`, and document every required variable. Never read any environment variable files (which are `.*.env`) you are not allowed to read / print / console the variables, the only reference that you will be having of the environment variables will be present in `example.env` file.
