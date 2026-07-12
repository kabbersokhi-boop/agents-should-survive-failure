# Sandbox Boundary

`make sandbox-demo` invokes a local host-side broker which runs one command in the local
`agents-control-plane:local` image. The workload runs as UID/GID `65532`, with a read-only root
filesystem, a dedicated temporary writable workspace, `--network none`, no Linux capabilities,
`no-new-privileges`, CPU/memory/process limits, an execution timeout, and a bounded output stream.
Only allow-listed environment variables may enter the workload. The workload does not receive the
host Docker socket or any host directory other than its newly-created temporary workspace.

This is a local operator capability, not an HTTP endpoint and not an agent-accessible unrestricted
shell. The host-side broker necessarily has Docker access, so it is privileged and must remain
separate from untrusted workloads.

Docker isolation is not a complete hostile-code security boundary. A production deployment should
use a dedicated sandbox host or VM boundary, mandatory access controls, image provenance controls,
egress enforcement outside the workload, and continuous escape-risk patching. The current tests
verify command construction and policy denial; resource enforcement is demonstrated with the local
Docker command and must be revalidated on every target host.
