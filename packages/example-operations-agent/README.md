# Example Operations Agent

This independently installable reference package uses only
`agents-should-survive-failure-sdk`. Platform operators install its wheel into
the worker environment, then discover and register its standard Python entry
point. It is trusted operator-installed code, not a sandbox for arbitrary
third-party wheels.
