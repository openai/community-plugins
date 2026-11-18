# Distribution and third-party notices

This MIT plugin contains a launcher, manifests, lockfile and documentation.
It does not contain the BoundaryXP native Core source, binary, customer data,
credentials or existing installations. On first use, npm downloads the locked
Windows `boundaryxp-mcp@0.4.0-rc.10` or Linux x64
`boundaryxp-mcp@0.4.0-rc.10-linux.1` compiled distribution and its pinned dependencies.
Their npm integrity values are recorded in runtime/package-lock.json and
runtime/linux/package-lock.json respectively. The Windows lock is unchanged.

BoundaryXP MCP is distributed under MIT; its original Core implementation
source is private. Readable adapter/UI/resources ship in the npm package.
Transitive dependencies retain their own licenses and notices in the installed
node_modules tree; this wrapper does not relicense them. The package's complete
notices are at https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/NOTICES.md .

No native rebuild or source reproducibility claim is made for the private Core.
The integration suite tests the published compiled package as distributed.
