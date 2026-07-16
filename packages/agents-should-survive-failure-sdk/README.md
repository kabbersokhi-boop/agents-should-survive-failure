# Agents Should Survive Failure SDK

`agents-should-survive-failure-sdk` is the public, server-independent contract package for
managed agents. Agents declare immutable metadata and implement `ManagedAgent.run`; the platform
supplies a constrained `RunContext` at execution time.

The SDK follows SemVer. Backward-incompatible changes to public types require a major version.

