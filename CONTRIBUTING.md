# Contributing to AdaptiveCharge

## Ground Rules

### Branch Protection
- **`main`** and **`develop`** branches are protected.
- All changes must go through a Pull Request from a feature branch.
- Direct pushes to `main` or `develop` are not allowed.

### Pull Request Rules
1. **No analysis files**: Log files, history exports, energy dashboard CSVs, and similar analysis artifacts must **never** be merged into `main` or `develop`. These belong in feature branches only. The `.gitignore` is configured to exclude `*.csv` files automatically.
2. **Version bump required**: Every PR merge to `develop` or `main` must include a version bump in `custom_components/adaptive_charge/manifest.json`. Use [Semantic Versioning](https://semver.org/):
   - **Patch** (x.y.Z): Bug fixes, small improvements
   - **Minor** (x.Y.0): New features, new config options, new sensors
   - **Major** (X.0.0): Breaking changes (config format changes, entity ID changes)
3. **Tests must pass**: All existing tests must pass before merging. Run `pytest tests/` to verify.
4. **Descriptive commit messages**: Use clear, descriptive commit messages. Include the issue number if applicable.

### Code Quality
- Type hints on all function signatures.
- Docstrings on all classes and public methods.
- No hardcoded values — use constants from `const.py`.
- Entity IDs must remain stable across versions (don't change `unique_id` patterns).

### Merge Strategy: develop → main
To merge `develop` into `main`:
```bash
git checkout main
git merge develop
git push origin main
```
If there are conflicts, resolve them locally and push. If `main` has diverged, use:
```bash
git checkout develop
git rebase main
# resolve any conflicts
git checkout main
git merge develop
git push origin main
```
