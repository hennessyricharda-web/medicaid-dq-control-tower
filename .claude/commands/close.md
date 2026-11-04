---
description: Close a package. Update BACKLOG and the blackboard with evidence, list what to stage for the user's git, and sync if the package ran in the cloud
argument-hint: K<n>
---
You are the ORCHESTRATOR closing package $ARGUMENTS.

1. **Preconditions.**
   - The latest gate for this package is green and not void.
   - The after-build red team ran.
   - No Critical or Important findings are open, unless they are explicitly deferred to a backlog id with the user's agreement.
   - **(FB-2)** For any package that changed a screen: "Live-API smoke/screenshot pass run for this package's changed screens? yes/no + evidence, or explicit reason it's deferred." A missing or unanswered line means the package is not closeable.
2. **BACKLOG.**
   - Every row the package closes reads DONE, cites the receipt file and the test ids, and has an R5 UPHOLD.
   - Demoted rows show their true status.
   - New findings that were deferred get new rows.
3. **Blackboard.**
   - Add a closure entry: package, receipt, code hash, findings closed and deferred, decisions.
   - Update the "Active now" row in SYNC to the next package.
4. **What to stage.** Report it for the user, who runs git:
   - new files and modified files, grouped by area: backend, tests, tower, docs, config;
   - generated files that must not be staged: receipts JUnit (if the user keeps those out), `.smoke-shots`, `.pgdata`, `.venv`, `.gate.lock`, caches;
   - confirmation that `test_generic_naming` passed in the gate;
   - confirmation that no file under `C:\demo\DQ` or the private tenant folder is included.

   Never run git.
5. **Sync (cloud only).**
   - Follow EXECUTION_PLAN §5.3: package the repo with a deletion manifest, write it to `C:\demo\_to_delete\`, and extract it in the bridge's Linux shell (`tar xzf --overwrite`).
   - Show the user the deletion manifest; the bridge cannot delete files.
   - Verify that the receipt and two changed files exist on the device, and (after G0) that the device's `gate_tree_sha256` equals the gate's.
6. Tell the user in two or three sentences:
   - what the package delivered;
   - where to see it (screen or command);
   - what is next.

   Then WAIT for their go.
