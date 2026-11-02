# Clean Development companion tests

Run from the repository root:

```sh
npm run test:clean-development
```

The suite uses Node's built-in test runner and isolated temporary homes, data/config directories, managed roots, projects, and fake tools. It never reads or writes the caller's Clean Development configuration or project.

It verifies the packaged CLI's cold read-only status/doctor behavior, session preview, session-only npm-cache routing, explicit user cache preservation, refusal to create managed storage inside a project, and prune's unregistered-directory ownership boundary. It does not establish host-native integration, package-registry availability, or external tool acceptance.

Runtime provenance: source revision `d0bd0a488dd1ed6d705423b43b8725ee36552fe5`. Copied runtime file hashes are verified by `integration/package-provenance.test.mjs`.

Safe failure: before setup, `doctor --json` normally exits 1 while still returning useful JSON. A requested managed root that is unavailable or inside a project fails visibly; the CLI does not fall back to repository-local storage.
