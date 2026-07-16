# Limitations

This repository is a production-style reference implementation, not a production-ready hosted
platform and not a compliance product. Vendor onboarding is the mature flagship workflow. The
managed-agent SDK/runtime is a preview, and its public contracts may change before a stable release.

Delegation is experimental and is not release-proven. External agents are trusted,
operator-installed Python packages; discovery does not make third-party code safe. The local Docker
sandbox demonstration is not a complete hostile-code isolation boundary.

The integration suite uses deterministic local providers. NVIDIA NIM live testing is manual and
credential-gated. The release does not claim multi-tenancy, high availability, remote installation,
enterprise identity, billing, Kubernetes operation, or production compliance controls.
