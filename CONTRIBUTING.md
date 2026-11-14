# Contributing to AdaptiveCharge

## Ground Rules

### Branch Protection
- **`release`** (the default branch), **`develop`**, and any historical **`main`** branch are protected publication targets.
- All changes must go through a Pull Request from a feature branch.
- Direct pushes to these branches are not allowed.

### Pull Request Rules
1. **No analysis files**: Log files, history exports, energy dashboard CSVs, and similar analysis artifacts must **never** be merged into `release`, `develop` or `main`. These belong in feature branches only. The `.gitignore` is configured to exclude `*.csv` files automatically.
2. **Version bump required**: Every release must use a new version in the active integration's `custom_components/adaptive_charge_preview/manifest.json`. Version 5.0 was a read-only observer; 5.1 adds explicit opt-in control while preserving its domain and entity identities. v4 is archived under `legacy/v4/` and is not shipped by HACS. Use [Semantic Versioning](https://semver.org/), including prerelease suffixes for previews:
   - **Patch** (x.y.Z): Bug fixes, small improvements
   - **Minor** (x.Y.0): New features, new config options, new sensors
   - **Major** (X.0.0): Breaking changes (config format changes, entity ID changes)
3. **Tests must pass**: All existing tests must pass before merging. Run `pytest tests/` to verify.
4. **Descriptive commit messages**: Use clear, descriptive commit messages. Include the issue number if applicable.

### HACS distribution

- Keep exactly one integration under `custom_components/`; HACS manages only one
  integration per repository. Archived v4 code must stay outside that directory.
- Publish preview versions as GitHub pre-releases from a reviewed feature commit;
  keep the protected branches behind the normal PR process.
- Never claim live commissioning from a published release or passing software tests.
- The default-disabled path must issue no actuator services or source/helper writes.
  Only `control.py` may call the configured actuator allowlist after explicit
  Master/Start authority and native-controller exclusion. Never change phases,
  amperage, source values or another controller's helpers.
- This beta is distributed as a custom repository, not submitted to the default
  HACS catalog. Its repository description/topics are currently absent; the
  available GitHub credentials have write, not admin, access. The HACS Action
  explicitly excludes only those catalog metadata checks. Manifest, file layout,
  local brand assets and install compatibility still have to pass.
- Before publishing a beta, build `python -m tools.preview_bundle`, attach both
  `dist/adaptive_charge_preview-<version>.zip` and its `.json` proof to the release,
  and verify the asset digests and every listed file against the exact tag tree.
  Do not publish if a file or the proof is stale. Record live readback separately.

### Code Quality
- Type hints on all function signatures.
- Docstrings on all classes and public methods.
- No hardcoded values — use constants from `const.py`.
- Entity IDs must remain stable across versions (don't change `unique_id` patterns).

### Merge strategy

Use a feature-branch PR to `develop`, then a separate reviewed PR from `develop`
to the default `release` branch. Resolve conflicts on the feature branch without
rewriting a shared branch. A beta tag may point to the reviewed feature commit;
it does not imply a protected-branch merge or production acceptance.
