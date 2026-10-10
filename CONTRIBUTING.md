# Contributing to xBAKE

Thanks for your interest in improving xBAKE. Bug reports, new defect classes, better
segmentation, hardware drivers, datasets tooling, and documentation are all welcome.

All participants are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs

Search the [issue tracker](https://github.com/KOKUTBON/xBAKE/issues) first, then open an
issue with:

- What you did, what you expected, and what happened
- Your `config.json` (remove any `auth_token`) and relevant log output (`xbake -v run ...`)
- Python version, operating system, camera, and actuator hardware

Security problems should be reported privately; see [SECURITY.md](SECURITY.md).

## Proposing features

Open an issue describing the problem it solves and a sketch of the implementation, especially
for new configuration keys, API endpoints, or database columns, so the design can be agreed on
before you invest time.

## Development workflow

```bash
git clone https://github.com/<your-username>/xBAKE.git
cd xBAKE
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
git checkout -b feature/my-change
```

Before opening a pull request:

```bash
pytest -q
ruff check src tests tools
ruff format --check src tests tools
```

Then push your branch and open a pull request against `master` (or `main`).

## Guidelines

- **Keep layers separate**: vision, classification, hardware, storage, and web live in their
  own packages under `src/xbake/`. Core logic must not call physical hardware directly; go
  through `ActuatorInterface`.
- **Fail safe**: camera disconnects, GPIO faults, and network errors must be logged and
  handled, never crash the inspection loop. Hardware drivers fall back to mock behaviour when
  devices are missing.
- **Validate inputs**: any new runtime-tunable parameter must be added to `TUNABLE_PARAMS` in
  `config.py` with a type and range.
- **Test without hardware**: add tests using the synthetic footage helpers in
  `tests/synthetic.py`. Do not commit videos, databases, or other data files; the only
  images in the repository are README screenshots in `docs/images/`, captured from the
  synthetic clip produced by `tools/make_demo_video.py`.
- **Style**: PEP 8, type hints, and docstrings on public functions. Line length is 110.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).

## Community participation

Follow the [communication guidelines](COMMUNICATION.md) and [organizational governance](GOVERNANCE.md). Keep security and conduct reports confidential and use a verified private channel; never include sensitive details in a public issue. Maintainers verify the reporting contacts and channels listed in the [readiness checklist](RELEASE_CHECKLIST.md).

## KOKUTBON contribution principles

Contribute under the existing **MIT** license in [LICENSE](LICENSE). You retain copyright in your work. Submit only work you are entitled to share, identify third-party sources and preserve their notices. No copyright assignment or paid membership is required.

Open an issue for substantial proposals, then submit a focused pull request from a branch or fork. Describe the problem, implementation, relevant checks and any privacy, accessibility, migration or safety impact. Use synthetic fixtures and sanitized logs. Maintainer review and passing relevant CI checks are expected before merging. A sole maintainer may merge their own change after checks pass and must record the rationale; no fictitious second reviewer is required.

Small documentation, translation, accessibility and reproducibility improvements are welcome. Funding does not determine review priority. Follow [communication guidelines](COMMUNICATION.md) and [the sponsorship policy](SPONSORSHIP.md).
