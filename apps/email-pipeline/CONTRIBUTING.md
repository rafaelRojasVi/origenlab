## Contributing

Thanks for contributing.

### Development setup

- **Python**: 3.12
- **Env/deps**: `uv`

```bash
uv python install 3.12
uv sync --group dev
```

### Running checks

From the **repo root** (canonical — same as CI):

```bash
make setup      # once: installs all dependencies and pre-commit hooks
make db-up      # once per session: starts disposable test databases
make check      # only the suites your change needs, in parallel
```

See [root `AGENTS.md` → *Testing*](../../AGENTS.md) for the full reference.

### Data safety

- **Do not** commit PSTs, mbox files, SQLite DBs, JSONL exports, or client report outputs.
- Keep real data **outside the repo** (see `README.md` and `.env.example`).

