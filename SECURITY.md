# Security

Please report vulnerabilities privately, through GitHub: **Security → Report a vulnerability** on this
repository. Don't open a public issue.

In scope, for example:

- proxy credentials leaking into logs, errors, the status tool or saved evidence;
- traffic bypassing the local relay (and so the proxy, the data limit or WebRTC blocking), exposing your IP;
- search-result text escaping the untrusted-content marking in a way that reaches an agent as instructions;
- anything written to the state folder that other local users can read or change.

Out of scope: Google blocking a search (that is expected behaviour, reported as an error) and the legal
questions covered in the README.

You'll get an answer within 7 days. Fixes ship as a new release, with credit if you want it.
