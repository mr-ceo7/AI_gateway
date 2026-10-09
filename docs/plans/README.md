# Improvement plans (not implemented)

These documents are proposals written earlier for hardening and scaling the gateway: rate limiting, MIME validation, structured logging, a process manager, and more. **The running gateway does not implement them.** `config.py` and the modules in `utils/` other than `account_pool.py` were written for this plan and are not used by `app.py` yet.

| File | Contents |
|---|---|
| [IMPROVEMENTS.md](IMPROVEMENTS.md) | The full list of proposed changes with code |
| [IMPROVEMENTS_SUMMARY.md](IMPROVEMENTS_SUMMARY.md) | Short summary of the issues and fixes |
| [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) | Step-by-step order for applying them |
| [QUICK_REFERENCE.md](QUICK_REFERENCE.md) | Checklist |
| [requirements_improved.txt](requirements_improved.txt) | Extra packages the plan needs |
