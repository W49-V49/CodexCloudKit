# Compatibility

The core package uses Python and explicit filesystem paths. The release CI records the checked Python versions, operating system and exact source commit.

| Layer | Preparation |
| --- | --- |
| Core CLI | Python 3.11 or newer, as declared by `pyproject.toml`; writable runtime and state directories |
| Repository attachment | Existing project checkout; Git is used when available for local generated-file excludes |
| File inventory | Readable project source and writable external state |
| Queue profile | Python 3.11+, pinned RQ/redis clients and a configured Redis/Valkey service |
| Memory profile | Python 3.12 or newer; prepared native backend, compatible parser/model fingerprint and persistent project partition |
| Optional toolchains | Linux x86_64, Python 3.12+, profile-specific prerequisites and sufficient disk |
| Worker supervisor | Configured worker executable, actual authentication and independent acceptance commands |
| Codex Cloud | Prepared and published environment containing the selected repositories |
| Self-hosted cloud Runner | Compatible Linux/runtime, persistent storage and service lifecycle |

Core setup is intentionally small. Language ecosystems, browsers, databases, reverse-analysis suites and model runtimes are installed for the workloads that request them.

Current Cloud and Legacy settings differ. Use the [Cloud deployment guide](CLOUD_DEPLOYMENT_ZH.md) for the appropriate product configuration. A configured command is checked by invoking it; configuration presence alone does not establish model execution or project acceptance.

See [acceptance](ACCEPTANCE_ZH.md) for commands and the scope of actual release evidence.
